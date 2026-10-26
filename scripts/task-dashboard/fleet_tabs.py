"""The fleet instance's Pull requests (`/prs`) and Issues (`/issues`) tabs.

They replace Waiting on me and I'm waiting on in a fleet instance's nav: those
read Jira blocker links, which a fleet instance's GitHub work does not have.
Both read SQLite only, and both are fleet-only (fleet_views.ONLY).

PULL REQUESTS are every open PR in the configured owners that is mine, asks
for my review, or was opened on the owners' repos by someone else: the `prs`
mirror (github_gh.py, which holds my drafts and approved PRs too), joined by
URL to github_fleet's awaiting rows (`kind = pr`), which carry the
review-queue verdict and the `others` rows. Each PR goes in exactly one lane,
the first that fits:

1. **Ready for you**: in the review queue (`in_queue`), the set the header
   gauge counts.
2. **Review requested of me**: someone else's PR that asks for my review.
3. **From others on your repos**: someone else's PR, bot or human, with no
   review request. Green-CI bot PRs come first, as the quickest merges; a bot
   card carries a `bot` tag. Both this lane and the one above name the
   author, and each is drawn only when it has rows.
4. **Drafts.**
5. **CI failing**: CI is `failure`.
6. **Waiting on you**: every other PR, naming what keeps it out of the queue,
   from the awaiting row's `queue_misses`. An approved PR is never awaiting
   (github_fleet rule 1), so it says that instead.

The page draws them in the order Ready, Waiting on you, From others, Review
requested of me, CI failing, Drafts. Within a lane, oldest first.

ISSUES are github_fleet's `issue` rows, every open issue of the owners (its
MAX_OPEN_ISSUES cap is noted on the page when it bit), laned by the gh-issue
status ladder (commands/handlers/assets/labels.yml), the first that fits:
Needs you (`auto:human-review-needed`, the set Needs you lists), Needs review,
Started, Ready, then Untriaged or needs refinement, which also takes an issue
with no `status:` label.

Both take the include filter, the page's text filter (each card's `.title`
and `.ref`), ignores and snoozes as every other view does: an ignored item is
left out, a snoozed one is left out and counted.
"""

import html
import json
import sqlite3
from datetime import datetime
from typing import Any

import fleet_views
import gauges
import github_fleet
import include
import overlay
import score
import sync
import views  # card_title and _lane, read at call time: views imports this

_e = html.escape

PR_LANES = (
    ("ready", "Ready for you", "No PR is in the review queue."),
    ("waiting", "Waiting on you", "No PR is waiting out of the queue."),
    ("others", "From others on your repos", ""),
    ("review", "Review requested of me", ""),
    ("ci", "CI failing", "No PR has failing CI."),
    ("drafts", "Drafts", "No drafts."),
)
# Lanes drawn only when they have rows.
OPTIONAL_LANES = {"others", "review"}

MISSES = {
    "co_review": "no co-review stamp at its head",
    "milestone": "milestone not approved",
}

ISSUE_LANES = (
    ("needs-you", "Needs you", "No issue needs a human."),
    ("needs-review", "Needs review", "No issue is in review."),
    ("started", "Started", "No issue is started."),
    ("ready", "Ready", "No issue is ready."),
    ("untriaged", "Untriaged or needs refinement", "No issue is untriaged."),
)

# Status label -> lane, after the human-review label, which wins.
_STATUS_LANES = {
    "status:4_needs_review": "needs-review",
    "status:3_started": "started",
    "status:2_ready": "ready",
}


def _age(created: "str | None", now: datetime) -> str:
    return f"opened {fleet_views.ago(score.parse_ts(created), now)}"


def _hidden(ov: overlay.Overlay, item: overlay.Item, now: datetime) -> str:
    """`ignored`, `snoozed`, or "" for an item the page lists."""
    if item in ov.ignored:
        return "ignored"
    return "snoozed" if ov.is_snoozed(item, now) else ""


def _gate_note(cfg: dict[str, Any], synced: bool) -> str:
    if not sync.fleet_enabled(cfg):
        return '<p class="note">github.fleet is off, so nothing is shown here.</p>'
    if not synced:
        return '<p class="note">No fleet sync has succeeded yet.</p>'
    return ""


def _card(
    item: overlay.Item, ov: overlay.Overlay, now: datetime, head: str, meta: str
) -> str:
    return (
        f"<div {overlay.card_attrs(item, ov, now)}>{head}"
        f'<div class="meta">{meta}</div>'
        f"{overlay.card_controls(item, ov, now, reorder=False, pin=False)}</div>"
    )


def _snoozed_note(n: int) -> str:
    return f'<p class="note" data-snoozed="{n}">{n} snoozed.</p>' if n else ""


# --- Pull requests -----------------------------------------------------------


def pull_requests(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[dict]:
    """Every PR the tab knows: `prs` rows joined to the awaiting rows by URL.

    An awaiting row with no `prs` row (the two syncs ran at different times)
    still counts; its fields stand in for the missing ones.
    """
    awaiting = {
        str(r.get("url")): r
        for r in gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) or []
    }
    out: dict[str, dict] = {}
    for url, repo, number, title, draft, decision, ci, role, raw in conn.execute(
        "SELECT url, repo, number, title, is_draft, review_decision, ci_state,"
        " role, raw_json FROM prs ORDER BY repo, number"
    ):
        try:
            created = (json.loads(raw or "{}") or {}).get("createdAt")
        except ValueError:
            created = None
        out[url] = {
            "url": url,
            "repo": repo,
            "number": number,
            "title": title,
            "is_draft": bool(draft),
            "review_decision": decision,
            "ci_state": ci,
            "role": role,
            "created": created,
            "awaiting": awaiting.get(url),
        }
    for url, row in awaiting.items():
        p = out.setdefault(
            url,
            {
                "url": url,
                "repo": row.get("repo"),
                "number": row.get("number"),
                "title": row.get("title"),
                "is_draft": False,
                "review_decision": row.get("review_decision"),
                "ci_state": row.get("ci_state"),
                "role": row.get("role"),
                "created": None,
            },
        )
        p["awaiting"] = row
        # The fleet step's verdict was reached on its own CI read: show that one.
        p["ci_state"] = row.get("ci_state")
        p["created"] = row.get("created") or p["created"]
    return list(out.values())


def pr_lane(p: dict) -> "tuple[str, str]":
    """(lane, why): the one lane a PR goes in, and what keeps it there."""
    row = p.get("awaiting")
    if row and row.get("in_queue"):
        return "ready", ""
    if p.get("role") == "reviewer":
        return "review", "review requested"
    if p.get("role") == "others":
        return "others", ""
    if p.get("is_draft"):
        return "drafts", ""
    if p.get("ci_state") == "failure":
        return "ci", ""
    if row:
        misses = [
            f"CI {p.get('ci_state') or 'unknown'}" if m == "ci" else MISSES.get(m, m)
            for m in row.get("queue_misses") or []
        ]
        return "waiting", ("misses: " + ", ".join(misses)) if misses else ""
    if p.get("review_decision") == "APPROVED":
        return "waiting", "approved: merge it"
    return "waiting", "not mirrored as awaiting yet"


def _pr_card(p: dict, why: str, ov: overlay.Overlay, now: datetime) -> str:
    ref = f"{p.get('repo') or ''}#{p.get('number')}"
    row = p.get("awaiting") or {}
    bits = []
    if p.get("role") != "author" and row.get("author"):
        bits.append(f"by {row['author']}")
    bits += [f"CI {p.get('ci_state') or 'unknown'}", _age(p.get("created"), now)]
    bits.append(p.get("repo") or "")
    if why:
        bits.append(why)
    meta = " · ".join(_e(b) for b in bits if b)
    head = views.card_title(p["url"], p.get("title"), ref)
    if row.get("author_is_bot"):
        head += ' <span class="badge">bot</span>'
    item = (github_fleet.SOURCE, str(p["url"]))
    return _card(item, ov, now, head, meta)


def _lane_order(name: str, p: dict) -> tuple:
    """Oldest first; in the others lane, green-CI bot PRs (the quickest
    merges) before the rest."""
    created = p.get("created") or ""
    if name != "others":
        return (created,)
    bot = bool((p.get("awaiting") or {}).get("author_is_bot"))
    return (not (bot and p.get("ci_state") == "success"), created)


def render_prs(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> str:
    inc = inc or include.everything("prs")
    ov = overlay.load(conn)
    synced = gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is not None
    lanes: dict[str, list[tuple[Any, str]]] = {name: [] for name, _, _ in PR_LANES}
    snoozed = 0
    for p in pull_requests(conn, cfg):
        hidden = _hidden(ov, (github_fleet.SOURCE, str(p["url"])), now)
        snoozed += hidden == "snoozed"
        if hidden:
            continue
        lane, why = pr_lane(p)
        lanes[lane].append((p, why))
    group = include.group_of("pr")
    parts = [_gate_note(cfg, synced)]
    for name, title, empty in PR_LANES:
        rows = sorted(lanes[name], key=lambda t: _lane_order(name, t[0]))
        if name in OPTIONAL_LANES and not rows:
            continue
        cards = [_pr_card(p, why, ov, now) for p, why in rows]
        parts.append(views._lane(name, title, cards, empty, group, inc))
    if inc.has(group):
        parts.append(_snoozed_note(snoozed))
    return f'<div class="fleet">{"".join(parts)}</div>'


# --- Issues ------------------------------------------------------------------


def issue_lane(labels: "list[str]") -> str:
    if github_fleet.HUMAN_LABEL in labels:
        return "needs-you"
    for label in labels:
        if label in _STATUS_LANES:
            return _STATUS_LANES[label]
    return "untriaged"


def _issue_card(row: dict, ov: overlay.Overlay, now: datetime) -> str:
    labels = row.get("labels") or []
    ref = f"{row.get('repo') or ''}#{row.get('number')}"
    bits = [lb for lb in labels if lb.startswith(("prio:", "est:"))]
    bits += [_age(row.get("created"), now), row.get("repo") or ""]
    meta = " · ".join(_e(b) for b in bits if b)
    item = (github_fleet.SOURCE, str(row.get("key") or row.get("url")))
    head = views.card_title(str(row.get("url") or ""), row.get("title"), ref)
    return _card(item, ov, now, head, meta)


def _issues_truncated(conn: sqlite3.Connection) -> int:
    """Owners the last good fleet run's issue cap cut short."""
    row = conn.execute(
        "SELECT counts FROM sync_runs WHERE kind = ? AND outcome = 'ok'"
        " ORDER BY id DESC LIMIT 1",
        (github_fleet.RUN_KIND,),
    ).fetchone()
    try:
        counts = json.loads(row[0] or "{}") if row else {}
    except ValueError:
        return 0
    return int((counts or {}).get("issues_truncated") or 0)


def render_issues(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> str:
    inc = inc or include.everything("issues")
    ov = overlay.load(conn)
    # The same gate as Needs you's issues: none before a good fleet run.
    synced = gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is not None
    rows = (
        [
            fleet_views._raw(r[0])
            for r in conn.execute(
                "SELECT raw_json FROM issues WHERE source = ? AND kind = 'issue'"
                " ORDER BY created, key",
                (github_fleet.SOURCE,),
            )
        ]
        if synced
        else []
    )
    lanes: dict[str, list[str]] = {name: [] for name, _, _ in ISSUE_LANES}
    snoozed = 0
    for row in rows:
        item = (github_fleet.SOURCE, str(row.get("key") or row.get("url")))
        hidden = _hidden(ov, item, now)
        snoozed += hidden == "snoozed"
        if hidden:
            continue
        lanes[issue_lane(row.get("labels") or [])].append(_issue_card(row, ov, now))
    group = include.group_of("issue")
    parts = [_gate_note(cfg, synced)]
    cut = _issues_truncated(conn) if synced else 0
    if cut:
        parts.append(
            f'<p class="note" data-truncated="{cut}">{cut} owner'
            f"{'s have' if cut > 1 else ' has'} more than"
            f" {github_fleet.MAX_OPEN_ISSUES} open issues besides those needing"
            " a human; the rest are not mirrored.</p>"
        )
    for name, title, empty in ISSUE_LANES:
        parts.append(views._lane(name, title, lanes[name], empty, group, inc))
    if inc.has(group):
        parts.append(_snoozed_note(snoozed))
    return f'<div class="fleet">{"".join(parts)}</div>'
