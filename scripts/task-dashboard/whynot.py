"""Why not now: what the owner says when they snooze a card on the fleet Now,
and what `dashboard.py tune` makes of it.

ASKING. On the fleet Now (fleet_now.py), Snooze opens a small picker before
the snooze is applied: one click on a reason (overlay.WHY_NOT_REASONS), an
optional line of free text, or Skip, which snoozes without asking. A reason
is never required.

STORING. A reason posts the overlay's `whynot` action, which snoozes the
item and writes one `why_not` row in the same transaction
(overlay.why_not). The row snapshots the item as Now ranked it at that
moment (snapshot()): its fleet score, every score term, and its features
(features()), scored or not, so that a pattern nothing scores today can still
show up. Export and import carry these rows; they are overlay state.

THE BASE RATE. Whether snoozes lean towards some feature means nothing
without what Now showed. Each render records the items it showed, once per
item per local day (record_shown, the `now_shown` table), with the same
features, and prunes days older than RETAIN_DAYS.

SHOWING. On the fleet instance, Health gets a "Why not now" section (section()): the last
WINDOW_DAYS days' answers counted by reason against item kind and against the
answer's dominant score term, and the latest LATEST answers.

LEARNING, offline. tune() is what `dashboard.py tune --json` prints:

- every answer in the window, joined to its score terms;
- for each [score.fleet] term, how often it was the dominant term of a
  "wrong priority" answer, and a suggested lower weight when it dominates
  them (at least MIN_COUNT answers and DOMINANT_SHARE of them);
- candidate dimensions: feature values over-represented among the snoozes
  compared with what Now showed in the same window, each named as a possible
  new term, with the free text of the answers that carry it.

It only suggests. Nothing here rewrites config, and nothing calls a model:
the `/task-dashboard tune` skill subcommand reads this output and proposes
changes for a human to approve.
"""

import html
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import overlay
import score

WINDOW_DAYS = 30
LATEST = 20
RETAIN_DAYS = 90

# tune's thresholds. A term's weight is suggested lower once it is the
# dominant term of MIN_COUNT or more "wrong priority" answers and of at least
# DOMINANT_SHARE of them; the suggestion is the weight times LOWER_BY.
MIN_COUNT = 3
DOMINANT_SHARE = 0.5
LOWER_BY = 0.75
# A feature value is a candidate dimension once MIN_COUNT or more snoozes
# carry it, its share of the snoozes is at least LIFT times its share of what
# Now showed, and the two shares are at least GAP apart.
LIFT = 1.5
GAP = 0.15

WRONG_PRIORITY = "wrong_priority"

# Issue categories that are the owner's own work. A PR is own work only when
# the owner authored it (its row's role is `author`), whatever its category: a
# review-queue PR that requests the owner's review is someone else's.
OWN_ISSUE_CATEGORIES = frozenset({"started", "ready"})

# The features tune compares, in report order. `age_days` is recorded beside
# them but is a number; `age` is its bucket.
FEATURES = (
    "kind", "category", "repo", "author", "bot", "prio", "est", "status", "ci",
    "age", "own",
)  # fmt: skip
# Features a [score.fleet] term already reads, and which.
SCORED_BY = {"category": "the category terms", "prio": "prio", "age": "age"}

AGE_BUCKETS = ((1, "under 1d"), (7, "1-7d"), (28, "1-4w"))


def _label(labels: list[str], prefix: str) -> "str | None":
    found = sorted(lb for lb in labels if lb.startswith(prefix))
    return found[0] if found else None


def _age(days: "float | None") -> "str | None":
    if days is None:
        return None
    for limit, name in AGE_BUCKETS:
        if days < limit:
            return name
    return "over 4w"


def _own(r: Any, row: dict[str, Any]) -> bool:
    if r.need.kind == "pr":
        return row.get("role") == "author"
    return r.category in OWN_ISSUE_CATEGORIES


def features(r: Any, now: datetime) -> dict[str, Any]:
    """The features of a fleet_now.Ranked, scored or not."""
    n = r.need
    row = n.row or {}
    labels = [str(x) for x in row.get("labels") or []]
    author = row.get("author")
    days = None
    if n.since is not None:
        days = max(0.0, (now - n.since).total_seconds() / 86400)
    return {
        "kind": n.kind,
        "category": r.category,
        "repo": row.get("repo") or n.repo,
        "author": author,
        "bot": None
        if author is None
        else ("yes" if row.get("author_is_bot") else "no"),
        "prio": _label(labels, "prio:"),
        "est": _label(labels, "est:"),
        "status": _label(labels, "status:"),
        "ci": row.get("ci_state"),
        "age": _age(days),
        "age_days": None if days is None else round(days, 1),
        "own": "yes" if _own(r, row) else "no",
    }


def snapshot(r: Any, now: datetime) -> dict[str, Any]:
    """What a `why_not` row records about a fleet_now.Ranked."""
    return {
        "kind": r.need.kind,
        "title": r.need.title,
        "score": r.total,
        "terms": [list(t) for t in r.terms],
        "features": features(r, now),
    }


def _day(now: datetime) -> str:
    return now.astimezone().date().isoformat()


def record_shown(conn: sqlite3.Connection, shown: list[Any], now: datetime) -> None:
    """Note the items Now showed today, once each; a no-op when all are noted.

    A failed write (a sync holding the lock) loses one render's note and
    never the page, so it is swallowed.
    """
    day = _day(now)
    try:
        have = {
            (s, k)
            for s, k in conn.execute(
                "SELECT source, item_key FROM now_shown WHERE day = ?", (day,)
            )
        }
        new = [r for r in shown if r.need.item not in have]
        if not new:
            return
        cutoff = (now.astimezone().date() - timedelta(days=RETAIN_DAYS)).isoformat()
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO now_shown (day, source, item_key, features)"
                " VALUES (?, ?, ?, ?)",
                [
                    (day, *r.need.item, json.dumps(features(r, now), sort_keys=True))
                    for r in new
                ],
            )
            conn.execute("DELETE FROM now_shown WHERE day < ?", (cutoff,))
    except sqlite3.Error:
        if conn.in_transaction:
            conn.rollback()


# --- reading -----------------------------------------------------------------


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(text: "str | None", default: Any) -> Any:
    try:
        value = json.loads(text or "")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def answers(conn: sqlite3.Connection, since: datetime) -> list[dict[str, Any]]:
    """Every `why_not` row recorded at or after `since`, oldest first."""
    cols = ("id", *overlay.WHY_NOT_COLUMNS)
    out = []
    for row in conn.execute(
        f"SELECT {', '.join(cols)} FROM why_not WHERE recorded_at >= ?"
        " ORDER BY recorded_at, id",
        (_stamp(since),),
    ):
        a = dict(zip(cols, row))
        a["terms"] = _loads(a["terms"], [])
        a["features"] = _loads(a["features"], {})
        a["dominant"] = dominant(a["terms"])
        out.append(a)
    return out


def dominant(terms: list[Any]) -> "str | None":
    """The term that added most to the score; None when none added anything."""
    best: "tuple[float, str] | None" = None
    for t in terms:
        try:
            name, value = str(t[0]), float(t[1])
        except (IndexError, TypeError, ValueError):
            continue
        if value > 0 and (best is None or value > best[0]):
            best = (value, name)
    return best[1] if best else None


def shown(conn: sqlite3.Connection, since: datetime) -> list[dict[str, Any]]:
    """The features of every item-day Now showed from `since`'s local day on."""
    return [
        _loads(f, {})
        for (f,) in conn.execute(
            "SELECT features FROM now_shown WHERE day >= ?", (_day(since),)
        )
    ]


# --- tune ----------------------------------------------------------------------


def _share(n: int, of: int) -> "float | None":
    return None if of == 0 else round(n / of, 3)


def _pct(share: "float | None") -> str:
    return "?" if share is None else f"{round(share * 100)}%"


def weight_suggestions(
    rows: list[dict[str, Any]], weights: dict[str, Any]
) -> list[dict[str, Any]]:
    """One entry per [score.fleet] term: how often it dominated a "wrong
    priority" answer, and a lower weight when it dominates them."""
    wrong = [a for a in rows if a["reason"] == WRONG_PRIORITY]
    counts = Counter(a["dominant"] for a in wrong if a["dominant"])
    out = []
    for term in score.FLEET_TERMS:
        weight = float(weights.get(term, 0))
        n = counts.get(term, 0)
        share = _share(n, len(wrong))
        suggest = None
        if (
            n >= MIN_COUNT
            and share is not None
            and share >= DOMINANT_SHARE
            and weight > 0
        ):
            suggest = {
                "weight": round(weight * LOWER_BY, 2),
                "why": (
                    f"{term} was the largest term in {n} of {len(wrong)}"
                    f' "wrong priority" answers ({_pct(share)})'
                ),
            }
        out.append(
            {
                "term": term,
                "weight": weight,
                "wrong_priority_dominant": n,
                "share": share,
                "suggest": suggest,
            }
        )
    return out


def _value(v: Any) -> "str | None":
    return None if v is None else str(v)


def candidates(
    rows: list[dict[str, Any]], base: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Feature values over-represented among the snoozes against what Now
    showed, the largest gap first."""
    out: list[dict[str, Any]] = []
    for feature in FEATURES:
        snoozed = Counter(_value(a["features"].get(feature)) for a in rows)
        seen = Counter(_value(f.get(feature)) for f in base)
        for value, n in snoozed.items():
            if value is None or n < MIN_COUNT or not base:
                continue
            s_share = n / len(rows)
            b_share = seen.get(value, 0) / len(base)
            if s_share < LIFT * b_share or s_share - b_share < GAP:
                continue
            carrying = [a for a in rows if _value(a["features"].get(feature)) == value]
            out.append(
                {
                    "feature": feature,
                    "value": value,
                    "possible_term": f"{feature}:{value}",
                    "scored_by": SCORED_BY.get(feature),
                    "snoozes": n,
                    "snooze_share": round(s_share, 3),
                    "shown": seen.get(value, 0),
                    "shown_share": round(b_share, 3),
                    "summary": (
                        f"{_pct(s_share)} of snoozes are {feature} {value},"
                        f" which is {_pct(b_share)} of Now's items"
                    ),
                    "reasons": dict(Counter(a["reason"] for a in carrying)),
                    "free_text": [a["text"] for a in carrying if a["text"]],
                }
            )
    out.sort(
        key=lambda c: (
            -(float(c["snooze_share"]) - float(c["shown_share"])),
            c["feature"],
        )
    )
    return out


def tune(
    conn: sqlite3.Connection,
    weights: dict[str, Any],
    now: datetime,
    days: int = WINDOW_DAYS,
) -> dict[str, Any]:
    """The `tune --json` document. Read-only: it suggests, never writes."""
    since = now - timedelta(days=days)
    rows = answers(conn, since)
    base = shown(conn, since)
    return {
        "since": _stamp(since),
        "until": _stamp(now),
        "days": days,
        "answers": len(rows),
        "shown_item_days": len(base),
        "by_reason": dict(Counter(a["reason"] for a in rows)),
        "weights": weight_suggestions(rows, weights),
        "candidates": candidates(rows, base),
        "note": (
            "Suggestions only. Edit [score.fleet] in the config to apply a"
            " weight; a new term is a code change. Nothing here was written."
        ),
        "rows": [
            {
                "source": a["source"],
                "item_key": a["item_key"],
                "kind": a["kind"],
                "title": a["title"],
                "reason": a["reason"],
                "text": a["text"],
                "recorded_at": a["recorded_at"],
                "score": a["score"],
                "dominant": a["dominant"],
                "terms": {str(t[0]): t[1] for t in a["terms"] if t[1]},
                "features": a["features"],
            }
            for a in rows
        ],
    }


def text_report(doc: dict[str, Any]) -> str:
    """`tune` without --json: the suggestions as a few lines."""
    answers, shown = doc["answers"], doc["shown_item_days"]
    lines = [
        f"tune {doc['since']} -> {doc['until']}:"
        f" {answers} answer{'' if answers == 1 else 's'},"
        f" {shown} item-day{'' if shown == 1 else 's'} shown"
    ]
    for w in doc["weights"]:
        if w["suggest"]:
            lines.append(
                f"  weight {w['term']}: {w['weight']:g} -> {w['suggest']['weight']:g}"
                f" ({w['suggest']['why']})"
            )
    for c in doc["candidates"]:
        scored = f"; already read by {c['scored_by']}" if c["scored_by"] else ""
        lines.append(f"  candidate {c['possible_term']}: {c['summary']}{scored}")
    if not shown:
        lines.append(
            "  no base rate recorded yet (Now has shown nothing in this window),"
            " so candidate dimensions cannot be computed"
        )
    if len(lines) == 1:
        lines.append("  nothing to suggest yet")
    lines.append(f"  {doc['note']}")
    return "\n".join(lines) + "\n"


# --- rendering -----------------------------------------------------------------

_e = html.escape

CSS = """
.card .whynot:not([hidden]) { display:flex; flex-wrap:wrap; gap:6px; align-items:center;
  margin-top:4px; font-size:12px; }
.card .whynot button, .card .whynot input { font:inherit; padding:1px 6px; border-radius:4px;
  border:1px solid var(--line); background:var(--bg); color:var(--fg); }
.card .whynot input { flex:1 1 12em; }
.whynot-health table { border-collapse:collapse; margin-bottom:8px; }
.whynot-health th, .whynot-health td { text-align:left; padding:2px 8px;
  border-bottom:1px solid var(--line); }
.whynot-health td.n { text-align:right; }
.whynot-health .muted { color:var(--muted); font-size:12px; }
"""


def picker(until: str) -> str:
    """The card's hidden "why not now" picker; Snooze (data-why) shows it."""
    reasons = "".join(
        f'<button type="button" data-ov="whynot" data-reason="{code}">{_e(label)}</button>'
        for code, label in overlay.WHY_NOT_REASONS
    )
    return (
        f'<div class="whynot" hidden data-until="{_e(until)}">'
        f"<span>Why not now?</span>{reasons}"
        f'<input type="text" maxlength="{overlay.MAX_WHY_TEXT}"'
        ' placeholder="optional: say more" aria-label="Why not now, in your words">'
        f'<button type="button" data-ov="snooze" data-until="{_e(until)}"'
        ' title="Snooze without saying why">Skip</button></div>'
    )


def _matrix(rows: list[dict[str, Any]], by: str, title: str) -> str:
    cols = sorted({str(a[by] or "none") for a in rows})
    counts = Counter((a["reason"], str(a[by] or "none")) for a in rows)
    head = "".join(f"<th>{_e(c)}</th>" for c in cols)
    body = "".join(
        f"<tr><td>{_e(label)}</td>"
        + "".join(f'<td class="n">{counts.get((code, c), 0) or ""}</td>' for c in cols)
        + "</tr>"
        for code, label in overlay.WHY_NOT_REASONS
        if any(counts.get((code, c)) for c in cols)
    )
    return (
        f'<table data-by="{_e(by)}"><thead><tr><th>{_e(title)}</th>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def section(conn: sqlite3.Connection, now: datetime) -> str:
    """Health's "Why not now" section: the last WINDOW_DAYS days."""
    rows = answers(conn, now - timedelta(days=WINDOW_DAYS))
    labels = dict(overlay.WHY_NOT_REASONS)
    if not rows:
        body = (
            f'<p class="empty">No answers in the last {WINDOW_DAYS} days. Snooze a'
            " card on Now to give one.</p>"
        )
    else:
        latest = "".join(
            f'<li data-reason="{_e(a["reason"])}">{_e(a["recorded_at"][:10])} ·'
            f" <b>{_e(labels.get(a['reason'], a['reason']))}</b> ·"
            f" {_e(a['title'] or a['item_key'])}"
            + (f" — {_e(a['text'])}" if a["text"] else "")
            + (
                f' <span class="muted">score {a["score"]:g}'
                f"{', mostly ' + _e(a['dominant']) if a['dominant'] else ''}</span>"
                if a["score"] is not None
                else ""
            )
            + "</li>"
            for a in reversed(rows[-LATEST:])
        )
        body = (
            _matrix(rows, "kind", "reason by kind")
            + _matrix(rows, "dominant", "reason by largest score term")
            + f"<h3>Latest {min(LATEST, len(rows))}</h3><ul>{latest}</ul>"
            + '<p class="muted">Run <code>/task-dashboard tune</code> for suggested'
            " weight changes and new terms.</p>"
        )
    return (
        '<section class="lane whynot-health" data-section="why-not">'
        f"<h2>Why not now ({len(rows)} in {WINDOW_DAYS} days)</h2>{body}</section>"
    )
