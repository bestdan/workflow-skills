"""The fleet instance's Now (`/now`, and `/` there): what to do next.

Needs you (needs.py) is the full list of what waits on the owner; Now is the
short queue drawn from it, ranked, with the owner's own work added. A Jira
instance's Now is views.render_now's and does not read this module.

WHAT IT RANKS, each keyed by its overlay item `(source, key)` and put in one
score.FLEET_CATEGORIES category:

- everything Needs you collects (needs.collect): a session that asked or
  waits on a permission prompt (`asked`), a review-queue PR (`review_queue`),
  a PR from someone else on my repos (`quick_win` when a bot opened it and
  its CI is green, else `others_pr`), an `auto:human-review-needed` issue
  and a drain item (`human_issue`);
- my own PRs that wait on something I can do (own_pr_category): CI failing
  (`own_ci_failing`), approved, or green but short of a co-review stamp or an
  approved milestone (`own_pr`). A draft, a PR in the queue (Needs you has
  it), and one whose CI is pending or absent wait on something else and are
  left out;
- my open issues at `status:3_started` (`started`) and `status:2_ready`
  (`ready`), from the widened `issues` fetch, behind the same gate as Needs
  you's issues: none before a successful fleet run. One that carries the
  human-review label is Needs you's `human_issue` instead;
- close-outs (close_out.py, `close_out`): a finished session, on evidence
  that its work is done (its PR merged, its branch landed, or it ended), and a
  workstream in the Complete lane. Each card carries its checklist, ticked
  from evidence, and leaves once every step with evidence is ticked.

An item already collected keeps its first category: a session that asked is
`asked`, never a close-out.

THE SOURCE SEAM (candidate.py). Each collector above, and each of Little's,
is a source that returns candidate.Candidate rows: the item, its category,
its why, the score terms it has data for, and its features. rank() reads
those only, and every ordering and render below reads its Ranked.

MODES (dev_docs/designs/2026-10-26-now-modes.md). The owner picks one with the
button at the top, which cycles overlay.NOW_MODES and saves the pick in the
overlay; `?mode=` shows one mode once and saves nothing. With none saved the
mode is Ship. Everything below this paragraph is Ship, except LITTLE.

ORDER is Ship's (the design's "Ship"). Pinned items first, in the overlay's order
(overlay.Overlay.pin_rank). Then the fixed TIERS, in order: a session that
asked; close-outs, at most CLOSE_OUT_CAP of them (pins aside), oldest first;
ready to merge (`quick_win`, and `own_approved`: my PR approved with CI
green); the review queue; started issues; then the rest. Within a tier,
score.fleet_score orders, highest first, and a tie goes to the longer wait, so
the rest keep the category order the `[score.fleet]` weights give. The list
stops at LIMIT items, pins aside: every pinned item is shown. Each card's why
line names its tier and the terms that put it there, and the terms add up to
its score.

LITTLE ranks its own collector's items (little_candidates), not the ones
above: a PR or an issue that passes the small test. A PR is small when a bot
opened it, when it is docs-only (every changed path `*.md` or under `docs/`
or `dev_docs/`), or when it changes at most LITTLE_FILES files and
LITTLE_LINES lines; a draft, and one whose CI is anything but green or
absent (no checks at all), are left out. An issue is small when it carries `est:1` or `est:2` and is
not `status:3_started`; an unsized one is not small. They go by LITTLE_TIERS,
smallest first, then `prio`, then the longer wait, with no score. The header
reads "closed today: N" from the fleet step's `closed_today` row, and only
when that row counts from today's local midnight.

SHARED BEHAVIOUR, as on every view and in every mode: pinned items lead,
the include filter, ignores and snoozes apply, and a snoozed item is left out
and counted. In Little a pinned item Ship ranks shows too, under its Ship why
line. Below Ship's list, "N more on Needs you" counts the Needs you items the
list did not reach; below Little's, "N more small items". A card's
controls are the ordinary ones, except that Snooze first asks "why not
now?" (whynot.py, which also notes each day's shown items as its base rate).
"""

import html
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import candidate
import close_out
import fleet_tabs
import fleet_views
import gauges
import github_fleet
import include
import needs
import overlay
import score
import sync
import view_model
import whynot
import workstreams

_e = html.escape

LIMIT = 10
# Close-outs shown at once, pins aside: the oldest first.
CLOSE_OUT_CAP = 3

# Ship's tiers, first to last: (label, categories). A category in none of
# them is in the last tier, REST.
TIERS = (
    ("asked", ("asked",)),
    ("close out", ("close_out",)),
    ("ready to merge", ("quick_win", "own_approved")),
    ("review queue", ("review_queue",)),
    ("started", ("started",)),
)
REST = "the rest"

MODES = overlay.NOW_MODES
SHIP, LITTLE = "ship", "little"
# What each mode answers: the mode button's title, and the line beside it.
QUESTIONS = {
    SHIP: "What is closest to done?",
    LITTLE: "What can I knock out fast?",
}
assert set(QUESTIONS) == set(MODES)

# Little's small test, and its tiers, smallest first: (label, category).
LITTLE_FILES = 3
LITTLE_LINES = 50
LITTLE_TIERS = (
    ("bot PR", "bot_pr"),
    ("docs-only PR", "docs_pr"),
    ("est:1 issue", "est1_issue"),
    ("small PR", "small_pr"),
    ("est:2 issue", "est2_issue"),
)
_EST = {"est:1": "est1_issue", "est:2": "est2_issue"}
_DOCS_DIRS = ("docs/", "dev_docs/")
# CI states a small PR may have: green, or no checks at all.
_LITTLE_CI = ("success", "none")

CSS = """
.now .modes { display:flex; gap:8px; align-items:baseline; margin:4px 0 8px; }
.now .modes button { font:inherit; padding:2px 10px; border-radius:4px;
  border:1px solid var(--line); background:var(--bg); color:var(--fg); }
.now .modes .q { color:var(--muted); }
.now .card .why { margin-top:2px; }
.now .wait { color:var(--fg); font-weight:600; }
.now .more { margin-top:8px; font-size:12px; }
.now .checklist { margin:4px 0; font-size:12px; color:var(--fg); }
"""

# Status label -> the own-work category it puts an issue in.
_OWN_ISSUE = {"status:3_started": "started", "status:2_ready": "ready"}

MISS_ACTS = {
    "co_review": ("no co-review stamp at its head", "run `/co-review`"),
    "milestone": ("milestone not approved", "approve its milestone"),
}


@dataclass
class Ranked:
    """A candidate as the ranker placed it (rank)."""

    candidate: "candidate.Candidate"
    # The title of a workstream the item is in (workstreams.membership), or
    # None. Not scored: recorded as a feature so `tune` can propose it.
    workstream: "str | None" = None
    total: float = 0.0
    terms: list[score.Term] = field(default_factory=list)  # the scored terms

    @property
    def need(self) -> needs.Need:
        return self.candidate.need

    @property
    def category(self) -> str:
        return self.candidate.category

    @property
    def why(self) -> str:
        return self.candidate.why

    @property
    def prio(self) -> "str | None":
        return self.candidate.terms.get(candidate.PRIO)

    @property
    def from_needs(self) -> bool:
        return self.candidate.from_needs

    @property
    def checklist(self) -> "list[close_out.Step]":
        return self.candidate.checklist

    @property
    def mode(self) -> str:
        return self.candidate.mode

    @property
    def tier(self) -> "tuple[int, str]":
        if self.mode == LITTLE:
            return little_tier(self.category)
        return tier_of(self.category)


def tier_of(category: str) -> "tuple[int, str]":
    """(rank, label) of the Ship tier a category is in; REST is last."""
    for i, (label, categories) in enumerate(TIERS):
        if category in categories:
            return i, label
    return len(TIERS), REST


def little_tier(category: str) -> "tuple[int, str]":
    """(rank, label) of a Little category's tier."""
    for i, (label, cat) in enumerate(LITTLE_TIERS):
        if cat == category:
            return i, label
    raise ValueError(f"not a Little category: {category!r}")


class ModeError(ValueError):
    """A `?mode=` or `--mode` that is not one of MODES."""


def resolve_mode(asked: "str | None", ov: overlay.Overlay) -> str:
    """The mode to show: `asked` (a `?mode=` or `--mode`), else the saved one,
    else Ship. Raises ModeError on an unknown `asked`."""
    if asked:
        if asked not in MODES:
            raise ModeError(f"mode must be one of {', '.join(MODES)}, got {asked!r}")
        return asked
    return ov.now_mode or MODES[0]


def next_mode(mode: str) -> str:
    """The mode the button moves to from `mode`."""
    return MODES[(MODES.index(mode) + 1) % len(MODES)]


# --- collect -----------------------------------------------------------------
# Each collector is a source: it returns candidate.Candidate rows, and rank()
# reads nothing else.


def _candidate(
    need: needs.Need,
    category: str,
    why: str,
    prio: "str | None" = None,
    **kw: Any,
) -> candidate.Candidate:
    """A GitHub or session item as a candidate: its wait and `prio:` label
    as score terms, and its row's features."""
    terms = {candidate.SINCE: need.since, candidate.PRIO: prio}
    return candidate.Candidate(
        need,
        category,
        why,
        terms={k: v for k, v in terms.items() if v is not None},
        features=whynot.row_features(need, category),
        **kw,
    )


def need_category(n: needs.Need) -> "tuple[str, str]":
    """(category, why) for an item Needs you collected."""
    row = n.row or {}
    if n.kind == "pr":
        if row.get("role") == "others":
            who = row.get("author") or "someone else"
            ci = row.get("ci_state") or "unknown"
            if row.get("author_is_bot") and ci == "success":
                return "quick_win", f"bot PR from {who}, CI green: one click to merge"
            bot = "bot " if row.get("author_is_bot") else ""
            return "others_pr", f"{bot}PR from {who}, CI {ci}"
        return "review_queue", "in the review queue: CI green and co-reviewed"
    if n.kind == "issue":
        return "human_issue", github_fleet.HUMAN_LABEL
    if n.kind == "drain":
        return "human_issue", "a drain item marked needs you"
    return "asked", f"session: {n.state}"


def own_pr_category(p: dict[str, Any]) -> "tuple[str, str, str] | None":
    """(category, why, hint) for one of my PRs that waits on me, else None.

    `p` is a fleet_tabs.pull_requests() row.
    """
    if p.get("role") != "author" or p.get("is_draft"):
        return None
    row = p.get("awaiting") or {}
    if row.get("in_queue"):
        return None  # Needs you's, as a review-queue PR
    if p.get("ci_state") == "failure":
        return "own_ci_failing", "your PR, CI failing", "fix its CI"
    if p.get("review_decision") == "APPROVED":
        if p.get("ci_state") == "success":
            why = "your PR, approved, CI green: merge it"
            return "own_approved", why, "merge it on GitHub"
        return "own_pr", "your PR, approved: merge it", "merge it on GitHub"
    misses = row.get("queue_misses") or []
    if p.get("ci_state") == "success" and misses and set(misses) <= set(MISS_ACTS):
        why = ", ".join(MISS_ACTS[m][0] for m in misses)
        hint = ", then ".join(MISS_ACTS[m][1] for m in misses)
        return "own_pr", f"your PR, out of the queue: {why}", hint
    return None


def _needs_you(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[candidate.Candidate]:
    out = []
    for n in needs.collect(conn, cfg, now):
        category, why = need_category(n)
        prio = score.prio_of((n.row or {}).get("labels")) if n.kind == "issue" else None
        out.append(_candidate(n, category, why, prio, from_needs=True))
    return out


def _own_prs(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    out = []
    for p in fleet_tabs.pull_requests(conn, cfg):
        found = own_pr_category(p)
        if found is None:
            continue
        category, why, hint = found
        ref = f"{p.get('repo') or ''}#{p.get('number')}"
        need = needs.Need(
            item=(github_fleet.SOURCE, str(p["url"])),
            kind="pr",
            title=p.get("title") or "",
            ref=ref,
            since=score.parse_ts(p.get("created")),
            url=p.get("url"),
            state=f"your PR · CI {p.get('ci_state') or 'unknown'}",
            repo=fleet_views._name(p.get("repo")),
            number=p.get("number"),
            hint=hint,
            row=p,
        )
        out.append(_candidate(need, category, why))
    return out


def _own_issues(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    if gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is None:
        return []
    out = []
    for row in needs._rows(conn, github_fleet.SOURCE, "issue"):
        labels = row.get("labels") or []
        if github_fleet.needs_human(row):
            continue  # Needs you's, as a human_issue
        category = next((_OWN_ISSUE[lb] for lb in labels if lb in _OWN_ISSUE), None)
        if category is None:
            continue
        status = next(lb for lb in labels if lb in _OWN_ISSUE)
        hint = (
            "carry on with it"
            if category == "started"
            else "start it, or leave it to `/do-tasks`"
        )
        need = needs.Need(
            item=(github_fleet.SOURCE, str(row.get("key") or row.get("url"))),
            kind="issue",
            title=row.get("title") or "",
            ref=f"{row.get('repo') or ''}#{row.get('number')}",
            since=score.parse_ts(row.get("created")),
            url=row.get("url"),
            state=status,
            repo=fleet_views._name(row.get("repo")),
            number=row.get("number"),
            hint=hint,
            row=row,
        )
        why = f"your issue, {status.split(':', 1)[1].split('_', 1)[1]}"
        out.append(_candidate(need, category, why, score.prio_of(labels)))
    return out


def _close_outs(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[candidate.Candidate]:
    return [
        _candidate(c.need, close_out.CATEGORY, c.why, checklist=c.steps)
        for c in close_out.collect(conn, cfg, now)
    ]


def ship_sources(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[candidate.Candidate]:
    """Every candidate Ship ranks, Needs you's first."""
    return (
        _needs_you(conn, cfg, now)
        + _own_prs(conn, cfg)
        + _own_issues(conn, cfg)
        + _close_outs(conn, cfg, now)
    )


def rank(
    cands: list[candidate.Candidate],
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
) -> list[Ranked]:
    """The candidates, each once (an item keeps the first category it was
    collected under), with its workstream; a Ship candidate scored."""
    out, seen = [], set()
    for c in cands:
        if c.item not in seen:
            seen.add(c.item)
            out.append(Ranked(c))
    member_of = workstreams.membership(conn)
    weights = cfg["score"]["fleet"]
    for r in out:
        r.workstream = member_of.get(r.candidate.item)
        if r.mode != SHIP:
            continue
        r.total, r.terms = score.fleet_score(
            {
                "category": r.candidate.category,
                "why": r.candidate.why,
                "prio": r.candidate.terms.get(candidate.PRIO),
                "since": r.candidate.terms.get(candidate.SINCE),
            },
            now,
            weights,
        )
    return out


def candidates(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[Ranked]:
    """Everything Ship ranks, scored, unordered and unfiltered."""
    return rank(ship_sources(conn, cfg, now), conn, cfg, now)


def docs_only(paths: "list[str] | None") -> bool:
    """Every changed path is `*.md` or under `docs/` or `dev_docs/`. None
    (the list was cut short) and an empty list are not docs-only."""
    return bool(paths) and all(
        p.endswith(".md") or p.startswith(_DOCS_DIRS) for p in paths or []
    )


def _lines(size: dict[str, Any]) -> str:
    files = size["files"]
    plural = "file" if files == 1 else "files"
    return f"{files} {plural}, +{size['additions']}/−{size['deletions']}"


def little_pr(p: dict[str, Any]) -> "tuple[str, str] | None":
    """(category, why) for a PR that passes the small test, else None.

    `p` is a fleet_tabs.pull_requests() row.
    """
    if p.get("is_draft") or p.get("ci_state") not in _LITTLE_CI:
        return None
    awaiting = p.get("awaiting") or {}
    size = p.get("size")
    if awaiting.get("author_is_bot"):
        return "bot_pr", f"bot PR from {awaiting.get('author') or 'a bot'}"
    if size is None:
        return None  # synced before the size fields: unknown is not little
    if docs_only(size["paths"]):
        return "docs_pr", f"docs-only PR, {_lines(size)}"
    if (
        size["files"] <= LITTLE_FILES
        and size["additions"] + size["deletions"] <= LITTLE_LINES
    ):
        return "small_pr", f"PR, {_lines(size)}"
    return None


_LITTLE_HINT = {
    "author": "finish it and merge it",
    "reviewer": "review it",
    "others": "review it and merge it",
}


def _little_prs(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    out = []
    for p in fleet_tabs.pull_requests(conn, cfg):
        found = little_pr(p)
        if found is None:
            continue
        category, why = found
        role = p.get("role") or "others"
        need = needs.Need(
            item=(github_fleet.SOURCE, str(p["url"])),
            kind="pr",
            title=p.get("title") or "",
            ref=f"{p.get('repo') or ''}#{p.get('number')}",
            since=score.parse_ts(p.get("created")),
            url=p.get("url"),
            state=f"{'your ' if role == 'author' else ''}PR · CI {p.get('ci_state')}",
            repo=fleet_views._name(p.get("repo")),
            number=p.get("number"),
            hint=_LITTLE_HINT.get(role, _LITTLE_HINT["others"]),
            row={**(p.get("awaiting") or {}), **p},
        )
        out.append(_candidate(need, category, why, mode=LITTLE))
    return out


def _little_issues(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    if gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is None:
        return []
    out = []
    for row in needs._rows(conn, github_fleet.SOURCE, "issue"):
        labels = row.get("labels") or []
        if "status:3_started" in labels:
            continue
        # The smaller label wins when an issue carries both.
        est = next((lb for lb in _EST if lb in labels), None)
        if est is None:
            continue
        prio = score.prio_of(labels)
        status = next((lb for lb in labels if lb.startswith("status:")), None)
        need = needs.Need(
            item=(github_fleet.SOURCE, str(row.get("key") or row.get("url"))),
            kind="issue",
            title=row.get("title") or "",
            ref=f"{row.get('repo') or ''}#{row.get('number')}",
            since=score.parse_ts(row.get("created")),
            url=row.get("url"),
            state=status or "no status",
            repo=fleet_views._name(row.get("repo")),
            number=row.get("number"),
            hint="do it now, or leave it to `/do-tasks`",
            row=row,
        )
        why = f"{est} issue"
        out.append(_candidate(need, _EST[est], why, prio, mode=LITTLE))
    return out


def little_sources(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    """Every candidate Little ranks."""
    return _little_prs(conn, cfg) + _little_issues(conn, cfg)


def little_candidates(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[Ranked]:
    """Everything Little ranks, unordered and unfiltered."""
    return rank(little_sources(conn, cfg), conn, cfg, now)


def mode_candidates(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime, mode: str
) -> list[Ranked]:
    """What `mode` ranks, unordered and unfiltered."""
    if mode == LITTLE:
        return little_candidates(conn, cfg, now)
    return candidates(conn, cfg, now)


def snapshot_of(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    item: overlay.Item,
    now: datetime,
    mode: "str | None" = None,
) -> "dict[str, Any] | None":
    """The item as Now ranks it right now in `mode` (Ship when None or not a
    mode), for a "why not now" answer; None when Now no longer ranks it. A
    pinned Ship item snoozed from another mode is found under Ship."""
    modes = [mode] if mode in MODES and mode != SHIP else []
    for m in modes + [SHIP]:
        for r in mode_candidates(conn, cfg, now, m):
            if r.need.item == item:
                return whynot.snapshot(r, now)
    return None


def _wait_key(r: Ranked) -> tuple:
    since = r.need.since
    return (since is None, since.timestamp() if since else 0.0, r.need.item)


def order(ranked: list[Ranked], ov: overlay.Overlay) -> list[Ranked]:
    """Pinned first in the overlay's order, then Ship's tiers; within a tier
    the highest score, a tie going to the longer wait. Close-outs go oldest
    first, so the cap keeps the ones that have waited longest."""
    pinned = sorted(
        (r for r in ranked if ov.is_pinned(r.need.item)),
        key=lambda r: ov.pin_rank(r.need.item),
    )
    rest = sorted(
        (r for r in ranked if not ov.is_pinned(r.need.item)),
        key=lambda r: (
            r.tier[0],
            0.0 if r.category == close_out.CATEGORY else -r.total,
            *_wait_key(r),
        ),
    )
    return pinned + rest


def cap_close_outs(
    ranked: list[Ranked], ov: overlay.Overlay
) -> "tuple[list[Ranked], int]":
    """Every close-out past the first CLOSE_OUT_CAP unpinned ones dropped, and
    how many were."""
    out, kept, dropped = [], 0, 0
    for r in ranked:
        if r.category == close_out.CATEGORY and not ov.is_pinned(r.need.item):
            if kept >= CLOSE_OUT_CAP:
                dropped += 1
                continue
            kept += 1
        out.append(r)
    return out, dropped


def little_order(ranked: list[Ranked], ov: overlay.Overlay) -> list[Ranked]:
    """Pinned first in the overlay's order, then Little's tiers; within a
    tier the more urgent `prio`, then the longer wait."""
    pinned = sorted(
        (r for r in ranked if ov.is_pinned(r.need.item)),
        key=lambda r: ov.pin_rank(r.need.item),
    )
    rest = sorted(
        (r for r in ranked if not ov.is_pinned(r.need.item)),
        key=lambda r: (
            r.tier[0],
            -score.PRIO_LEVEL.get(r.prio or "", -1),
            *_wait_key(r),
        ),
    )
    return pinned + rest


def closed_today(conn: sqlite3.Connection, now: datetime) -> "int | None":
    """The fleet step's closed-today count, or None when it has none that
    counts from `now`'s local midnight (no row yet, or one from yesterday)."""
    rows = needs._rows(conn, github_fleet.SOURCE, github_fleet.CLOSED_TODAY)
    if not rows:
        return None
    since = score.parse_ts(rows[0].get("since"))
    count = rows[0].get("count")
    if since is None or not isinstance(count, int):
        return None
    if since.astimezone().date() != now.astimezone().date():
        return None
    return count


@dataclass
class Page:
    shown: list[Ranked]
    more_on_needs: int  # Needs you items the list did not reach
    snoozed: int
    more_close_outs: int = 0  # close-outs past CLOSE_OUT_CAP
    mode: str = SHIP
    more_in_mode: int = 0  # Little: its items the list did not reach
    closed_today: "int | None" = None  # Little's header figure


def _visible(
    ranked: list[Ranked],
    ov: overlay.Overlay,
    now: datetime,
    inc: "include.Include | None",
) -> "tuple[list[Ranked], int]":
    """`ranked` less the ignored, the filtered and the snoozed; and how many
    were snoozed."""
    ranked = [r for r in ranked if r.need.item not in ov.ignored]
    if inc is not None:
        ranked = [r for r in ranked if inc.shows(r.need.kind)]
    snoozed = sum(ov.is_snoozed(r.need.item, now) for r in ranked)
    return [r for r in ranked if not ov.is_snoozed(r.need.item, now)], snoozed


def build(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    ov: overlay.Overlay,
    inc: "include.Include | None" = None,
    mode: str = SHIP,
) -> Page:
    if mode == LITTLE:
        return _build_little(conn, cfg, now, ov, inc)
    ranked, snoozed = _visible(candidates(conn, cfg, now), ov, now, inc)
    ranked, more_close_outs = cap_close_outs(order(ranked, ov), ov)
    pins = sum(ov.is_pinned(r.need.item) for r in ranked)
    shown = ranked[: max(LIMIT, pins)]
    more = sum(r.from_needs for r in ranked[len(shown) :])
    return Page(shown, more, snoozed, more_close_outs)


def _build_little(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    ov: overlay.Overlay,
    inc: "include.Include | None",
) -> Page:
    little = little_candidates(conn, cfg, now)
    seen = {r.need.item for r in little}
    if ov.pinned_at:
        # Pins lead in every mode: a pinned item Little does not rank comes
        # from Ship, with its Ship why line.
        little += [
            r
            for r in candidates(conn, cfg, now)
            if ov.is_pinned(r.need.item) and r.need.item not in seen
        ]
    ranked, snoozed = _visible(little, ov, now, inc)
    ranked = little_order(ranked, ov)
    pins = sum(ov.is_pinned(r.need.item) for r in ranked)
    shown = ranked[: max(LIMIT, pins)]
    return Page(
        shown, 0, snoozed,
        mode=LITTLE,
        more_in_mode=len(ranked) - len(shown),
        closed_today=closed_today(conn, now),
    )  # fmt: skip


# --- render ------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:+.2f}".rstrip("0").rstrip(".").replace("-", "−")


def why_line(r: Ranked) -> str:
    """`ship: ready to merge · score 10.3: quick_win +10 (bot PR …) · age +0.3
    (waiting 2d)`: the tier, then the terms with a value, as text. A Little
    item has no score: `little · PR, 2 files, +14/−3 · prio:1`."""
    if r.mode == LITTLE:
        return " · ".join(["little", r.why] + ([r.prio] if r.prio else []))
    bits = [f"{name} {_fmt(value)} ({why})" for name, value, why in r.terms if value]
    total = f"{r.total:g}".replace("-", "−")
    head = f"ship: {r.tier[1]} · score {total}"
    return f"{head}: " + " · ".join(bits) if bits else head


def card(r: Ranked, now: datetime, ov: overlay.Overlay) -> str:
    n = r.need
    pin = '<span class="pin">pinned</span> · ' if ov.is_pinned(n.item) else ""
    bits = [_e(n.state)] + ([_e(n.where)] if n.where else [])
    meta = (
        f'{pin}<span class="wait">{_e(needs.waited(n.since, now))}</span> · '
        + " · ".join(bits)
    )
    terms = ",".join(name for name, value, _ in r.terms if value)
    link = (
        view_model.link(n.url, needs.open_label(n, "open it"))
        if n.url and n.url.startswith("https://")
        else ""
    )
    act = " · ".join(x for x in (link, needs.pr_link(n), view_model.code(n.hint)) if x)
    act_html = f'<div class="act">→ {act}</div>' if act else ""
    steps = (
        f'<div class="checklist">{_e(close_out.checklist_text(r.checklist))}</div>'
        if r.checklist
        else ""
    )
    return (
        f'<div {overlay.card_attrs(n.item, ov, now)} data-kind="{_e(n.kind)}"'
        f' data-category="{r.category}" data-tier="{_e(r.tier[1])}"'
        f' data-total="{r.total}" data-mode="{r.mode}">'
        f"{needs._title(n, n.url)}{needs.ask_html(n)}"
        f'<div class="meta">{meta}</div>{steps}'
        f'<div class="why" data-terms="{terms}">{_e(why_line(r))}</div>'
        f"{act_html}{overlay.card_controls(n.item, ov, now, reorder=False, why_not=True)}"
        f"{whynot.picker(overlay.snooze_until(now))}</div>"
    )


def lanes(p: Page, inc: "include.Include | None" = None) -> list[view_model.Lane]:
    """The page's one lane, Next."""
    filtered = inc is not None and not inc.everything
    if p.mode == LITTLE:
        empty = "Nothing small is waiting" + (" in the filter." if filtered else ".")
    elif filtered:
        empty = "Nothing included by the filter is waiting."
    else:
        empty = "Nothing is waiting on you."
    return [view_model.Lane("next", "Next", p.shown, empty)]


def mode_bar(mode: str) -> str:
    """The mode button, which saves the next mode, and the mode's question."""
    nxt = next_mode(mode)
    return (
        f'<div class="modes" data-mode="{mode}">'
        f'<button type="button" data-ov="mode" data-mode="{nxt}"'
        f' title="Switch to {nxt.title()}: {_e(QUESTIONS[nxt])}">'
        f"Mode: {mode.title()}</button>"
        f'<span class="q">{_e(QUESTIONS[mode])}</span></div>'
    )


def render(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
    mode: "str | None" = None,
) -> str:
    """The page body in `mode` (a `?mode=`), else in the saved mode. Raises
    ModeError on an unknown `mode`."""
    ov = overlay.load(conn)
    mode = resolve_mode(mode, ov)
    p = build(conn, cfg, now, ov, inc, mode)
    whynot.record_shown(conn, p.shown, now)
    parts = [mode_bar(mode)]
    if p.closed_today is not None:
        parts.append(
            f'<p class="note" data-closed-today="{p.closed_today}">'
            f"closed today: {p.closed_today}</p>"
        )
    if not sync.fleet_enabled(cfg):
        parts.append(
            '<p class="note">The owner-wide GitHub read is off (github.enabled is false, or github.scope is not "owner"), so no PRs or issues are'
            " ranked here.</p>"
        )
    for lane in lanes(p, inc):
        body = "".join(card(r, now, ov) for r in lane.items)
        body = body or f'<p class="empty">{_e(lane.empty)}</p>'
        parts.append(
            f'<section class="lane" data-section="{lane.id}">'
            f"<h2>{lane.title} ({len(lane.items)})</h2>{body}</section>"
        )
    if p.more_on_needs:
        parts.append(
            f'<p class="more" data-more="{p.more_on_needs}"><a href="/needs">'
            f"{p.more_on_needs} more on Needs you</a></p>"
        )
    if p.more_in_mode:
        parts.append(
            f'<p class="more" data-more-in-mode="{p.more_in_mode}">'
            f"{p.more_in_mode} more small items.</p>"
        )
    if p.more_close_outs:
        parts.append(
            f'<p class="note" data-more-close-outs="{p.more_close_outs}">'
            f"{p.more_close_outs} more to close out, after these.</p>"
        )
    if p.snoozed:
        parts.append(
            f'<p class="note" data-snoozed="{p.snoozed}">{p.snoozed} snoozed.</p>'
        )
    return f'<div class="fleet now">{"".join(parts)}</div>'
