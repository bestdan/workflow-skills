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

ORDER is Ship's (dev_docs/designs/2026-10-26-now-modes.md, "Ship"), the only
mode so far. Pinned items first, in the overlay's order
(overlay.Overlay.pin_rank). Then the fixed TIERS, in order: a session that
asked; close-outs, at most CLOSE_OUT_CAP of them (pins aside), oldest first;
ready to merge (`quick_win`, and `own_approved`: my PR approved with CI
green); the review queue; started issues; then the rest. Within a tier,
score.fleet_score orders, highest first, and a tie goes to the longer wait, so
the rest keep the category order the `[score.fleet]` weights give. The list
stops at LIMIT items, pins aside: every pinned item is shown. Each card's why
line names its tier and the terms that put it there, and the terms add up to
its score.

SHARED BEHAVIOUR, as on every view: the include filter, ignores and snoozes
apply; a snoozed item is left out and counted. Below the list, "N more on
Needs you" counts the Needs you items the list did not reach. A card's
controls are the ordinary ones, except that Snooze first asks "why not
now?" (whynot.py, which also notes each day's shown items as its base rate).
"""

import html
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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

CSS = """
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
    need: needs.Need
    category: str
    why: str  # what put it in its category
    prio: "str | None" = None
    # The title of a workstream the item is in (workstreams.membership), or
    # None. Not scored: recorded as a feature so `tune` can propose it.
    workstream: "str | None" = None
    from_needs: bool = True  # Needs you lists it too
    total: float = 0.0
    terms: list[score.Term] = field(default_factory=list)
    # A close-out's steps that have evidence (close_out.Step); empty otherwise.
    # Quoted: close_out -> workstreams -> views -> fleet_now is a cycle, so
    # close_out is half-initialised when this class body runs.
    checklist: "list[close_out.Step]" = field(default_factory=list)

    @property
    def tier(self) -> "tuple[int, str]":
        return tier_of(self.category)


def tier_of(category: str) -> "tuple[int, str]":
    """(rank, label) of the Ship tier a category is in; REST is last."""
    for i, (label, categories) in enumerate(TIERS):
        if category in categories:
            return i, label
    return len(TIERS), REST


# --- collect -----------------------------------------------------------------


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


def _own_prs(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[Ranked]:
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
        out.append(Ranked(need, category, why, from_needs=False))
    return out


def _own_issues(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[Ranked]:
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
        out.append(Ranked(need, category, why, score.prio_of(labels), from_needs=False))
    return out


def _close_outs(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[Ranked]:
    return [
        Ranked(c.need, close_out.CATEGORY, c.why, from_needs=False, checklist=c.steps)
        for c in close_out.collect(conn, cfg, now)
    ]


def candidates(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[Ranked]:
    """Everything Now ranks, scored, unordered and unfiltered."""
    out = []
    for n in needs.collect(conn, cfg, now):
        category, why = need_category(n)
        prio = score.prio_of((n.row or {}).get("labels")) if n.kind == "issue" else None
        out.append(Ranked(n, category, why, prio))
    seen = {r.need.item for r in out}
    for r in _own_prs(conn, cfg) + _own_issues(conn, cfg) + _close_outs(conn, cfg, now):
        if r.need.item not in seen:
            seen.add(r.need.item)
            out.append(r)
    member_of = workstreams.membership(conn)
    for r in out:
        r.workstream = member_of.get(r.need.item)
    weights = cfg["score"]["fleet"]
    for r in out:
        r.total, r.terms = score.fleet_score(
            {
                "category": r.category,
                "why": r.why,
                "prio": r.prio,
                "since": r.need.since,
            },
            now,
            weights,
        )
    return out


def snapshot_of(
    conn: sqlite3.Connection, cfg: dict[str, Any], item: overlay.Item, now: datetime
) -> "dict[str, Any] | None":
    """The item as Now ranks it right now, for a "why not now" answer; None
    when Now no longer ranks it."""
    for r in candidates(conn, cfg, now):
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


@dataclass
class Page:
    shown: list[Ranked]
    more_on_needs: int  # Needs you items the list did not reach
    snoozed: int
    more_close_outs: int = 0  # close-outs past CLOSE_OUT_CAP


def build(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    ov: overlay.Overlay,
    inc: "include.Include | None" = None,
) -> Page:
    ranked = [r for r in candidates(conn, cfg, now) if r.need.item not in ov.ignored]
    if inc is not None:
        ranked = [r for r in ranked if inc.shows(r.need.kind)]
    snoozed = sum(ov.is_snoozed(r.need.item, now) for r in ranked)
    ranked = order([r for r in ranked if not ov.is_snoozed(r.need.item, now)], ov)
    ranked, more_close_outs = cap_close_outs(ranked, ov)
    pins = sum(ov.is_pinned(r.need.item) for r in ranked)
    shown = ranked[: max(LIMIT, pins)]
    more = sum(r.from_needs for r in ranked[len(shown) :])
    return Page(shown, more, snoozed, more_close_outs)


# --- render ------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:+.2f}".rstrip("0").rstrip(".").replace("-", "−")


def why_line(r: Ranked) -> str:
    """`ship: ready to merge · score 10.3: quick_win +10 (bot PR …) · age +0.3
    (waiting 2d)`: the tier, then the terms with a value, as text."""
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
        f' data-total="{r.total}">'
        f"{needs._title(n, n.url)}{needs.ask_html(n)}"
        f'<div class="meta">{meta}</div>{steps}'
        f'<div class="why" data-terms="{terms}">{_e(why_line(r))}</div>'
        f"{act_html}{overlay.card_controls(n.item, ov, now, reorder=False, why_not=True)}"
        f"{whynot.picker(overlay.snooze_until(now))}</div>"
    )


def lanes(p: Page, inc: "include.Include | None" = None) -> list[view_model.Lane]:
    """The page's one lane, Next."""
    if inc is not None and not inc.everything:
        empty = "Nothing included by the filter is waiting."
    else:
        empty = "Nothing is waiting on you."
    return [view_model.Lane("next", "Next", p.shown, empty)]


def render(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> str:
    ov = overlay.load(conn)
    p = build(conn, cfg, now, ov, inc)
    whynot.record_shown(conn, p.shown, now)
    parts = []
    if not sync.fleet_enabled(cfg):
        parts.append(
            '<p class="note">github.fleet is off, so no PRs or issues are'
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
