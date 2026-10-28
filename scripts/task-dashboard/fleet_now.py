"""Now (`/now`, and `/` on a fleet instance): what to do next.

Every instance's Now is this one ranker, over every source the instance has
(dev_docs/designs/2026-10-27-shared-now-ranker.md). Needs you (needs.py) is
the full list of what waits on the owner; Now is the short queue drawn from
it, ranked, with the owner's own work added.

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
- on a `github.scope = "me"` instance, which has no fleet read, the PRs
  asking for my review (`others_pr`): the `prs` table's reviewer rows. Its
  author rows are already my own PRs above, through fleet_tabs.pull_requests;
- my open Jira tickets, and any open one I pinned (jira_category): in
  progress is `started`, and to do is `ready` in the active sprint. Each
  brings score.score's priority, deadline, unblocks and stuck terms
  (`[score.weights]`), added to its category's;
- close-outs (close_out.py, `close_out`): a finished session, on evidence
  that its work is done (its PR merged, its branch landed, or it ended), and a
  workstream in the Complete lane. Each card carries its checklist, ticked
  from evidence, and leaves once every step with evidence is ticked.

An item already collected keeps its first category: a session that asked is
`asked`, never a close-out.

FOLDING (fold). A Ship PR whose `links` (pr_links: the Jira key in its title
or branch, and each GitHub issue it closes) name an item Ship also ranks has
no card of its own: it folds into that item, the first such link winning, and
shows on its card as a `↳` line with its why. The parent takes the PR's tier
when that is ready to merge or the review queue and beats its own, blocked
included, and its why line names the PR (`ship: ready to merge via o/r#12`).
Every overlay action on the card acts on the parent's key, so snoozing it
hides the PR too. A PR whose parent Ship does not rank keeps its own card,
and so does one whose parent the include filter leaves out (_visible).
A Jira ticket's card also lists, as `↳` lines that change nothing, every
other PR joined to it (joined_pr): a draft, or one waiting on CI or review.

THE SOURCE SEAM (candidate.py). Each collector above, and each of Little's,
is a source that returns candidate.Candidate rows: the item, its category,
its why, the score terms it has data for, and its features. rank() reads
those only, and every ordering and render below reads its Ranked.

MODES (dev_docs/designs/2026-10-26-now-modes.md). The owner picks one with the
mode button at the end of the page header bar, after the filter (mode_button,
which the server puts there on Now only). It reads the mode's name, cycles
overlay.NOW_MODES and saves the pick in the overlay; `?mode=` shows one mode
once and saves nothing. With none saved the
mode is Ship. Everything below this paragraph is Ship, except LITTLE, DEEP
and TIDY.

ORDER is Ship's (the design's "Ship"). Pinned items first, in the overlay's order
(overlay.Overlay.pin_rank). Then the fixed TIERS, in order: a session that
asked; close-outs, at most CLOSE_OUT_CAP of them (pins aside), oldest first;
ready to merge (`quick_win`, and `own_approved`: my PR approved with CI
green); the review queue; started issues and tickets; then the rest; and
last, blocked, whatever the category. Blocked is a Jira ticket score.is_blocked
calls blocked, or a GitHub issue with the `blocked` label or an open native
`blocked_by` (issue_blocked); its why line reads `ship: blocked`. Within a tier,
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

DEEP ranks the questions that need thinking, not doing (deep_sources): a
proposed design (workstreams.mirrored_designs), an issue that blocks open
issues (`blocks`, from GitHub's native dependencies), one at
`status:1_needs_refinement`, and a `status:2_ready` one estimated at
`est:DEEP_EST` or more. They go by the open issues each blocks, most first,
then `prio`, then the larger estimate, then the longer wait, with no score.
An item with no `blocks` evidence (a design, or an issue synced before the
field) shows no blocks term and sorts as blocking none. DEEP_LIMIT are shown,
pins aside: one to take up and two alternates.

TIDY ranks the backlog that rots (tidy_sources): an issue labelled
auto:human-review-needed, `status:0_untriaged` or `status:1_needs_refinement`,
and my own PR open longer than fleet_views.OPEN_ITEM_DAYS, drafts and pending
CI included. Strictly oldest first, with no score; age is the point. The
action is to close it, supersede it, or decide it.

SHARED BEHAVIOUR, as on every view and in every mode: pinned items lead,
the include filter, ignores and snoozes apply, and a snoozed item is left out
and counted. Outside Ship a pinned item Ship ranks shows too, under its Ship
why line. Below Ship's list, "N more on Needs you" counts the Needs you items
the list did not reach; below another mode's, MORE_IN_MODE counts its own
items the list did not reach. A card's
controls are the ordinary ones, except that Snooze first asks "why not
now?" (whynot.py, which also notes each day's shown items as its base rate).
"""

import html
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import candidate
import close_out
import fleet_tabs
import fleet_views
import gauges
import github_fleet
import icons
import include
import needs
import overlay
import score
import sync
import view_model
import views
import whynot
import workstreams

_e = html.escape

LIMIT = 10
# Close-outs shown at once, pins aside: the oldest first.
CLOSE_OUT_CAP = 3

# Ship's tiers, first to last: (label, categories). A category in none of
# them is in REST, and a blocked item, whatever its category, in BLOCKED,
# the last tier of all.
TIERS = (
    ("asked", ("asked",)),
    ("close out", ("close_out",)),
    ("ready to merge", ("quick_win", "own_approved")),
    ("review queue", ("review_queue",)),
    ("started", ("started",)),
)
REST = "the rest"
BLOCKED = "blocked"
# The categories whose tier a folded PR lends its parent: ready to merge,
# and the review queue.
RAISES = ("quick_win", "own_approved", "review_queue")

MODES = overlay.NOW_MODES
SHIP, LITTLE, DEEP, TIDY = "ship", "little", "deep", "tidy"
# What each mode answers: the mode button's title, and the line beside it.
QUESTIONS = {
    SHIP: "What is closest to done?",
    LITTLE: "What can I knock out fast?",
    DEEP: "Which decision unblocks the most?",
    TIDY: "What is rotting?",
}
assert set(QUESTIONS) == set(MODES)
# Each mode's icon on the mode button (icons.PATHS).
MODE_ICONS = {SHIP: "rocket", LITTLE: "zap", DEEP: "brain", TIDY: "sparkles"}
assert set(MODE_ICONS) == set(MODES)
# Below a mode's list: "<N> <what>", the items it ranks that it did not show.
MORE_IN_MODE = {LITTLE: "more small items", DEEP: "more to think about",
                TIDY: "more to tidy"}  # fmt: skip
# The list's empty line, per mode outside Ship.
EMPTY = {LITTLE: "Nothing small is waiting", DEEP: "Nothing needs deciding",
         TIDY: "Nothing is rotting"}  # fmt: skip

# Deep: how many it shows, pins aside, and the estimate at which a ready
# issue is big enough to be one.
DEEP_LIMIT = 3
DEEP_EST = 8

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
.now .card .why { margin-top:2px; }
.now .wait { color:var(--fg); font-weight:600; }
.now .more { margin-top:8px; font-size:12px; }
.now .checklist { margin:4px 0; font-size:12px; color:var(--fg); }
.now .folded { margin:2px 0; font-size:12px; color:var(--muted); }
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
    # The linked PRs folded into this item (rank): no card of their own.
    folded: "list[Ranked]" = field(default_factory=list)

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
        if self.mode in (DEEP, TIDY):
            return 0, self.mode  # one tier: the mode's own order
        raised = self.raised_by
        if raised is not None:
            return tier_of(raised.category)
        if self.candidate.blocked:
            return len(TIERS) + 1, BLOCKED
        return tier_of(self.category)

    @property
    def linked(self) -> list[dict[str, Any]]:
        """The candidate's related lines less the ones folded in, which the
        card already shows."""
        folded = {f.need.item[1] for f in self.folded}
        return [x for x in self.candidate.related if x.get("url") not in folded]

    @property
    def raised_by(self) -> "Ranked | None":
        """The folded PR whose tier this item takes: the highest RAISES
        tier among them when it beats the item's own, else None. A raise
        lifts a blocked item too: its PR is ready, so the work is done."""
        own = tier_of(self.category)[0]
        best = min(
            (f for f in self.folded if f.category in RAISES),
            key=lambda f: tier_of(f.category)[0],
            default=None,
        )
        if (
            best is None
            or tier_of(best.category)[0] >= own
            and not self.candidate.blocked
        ):
            return None
        return best


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
    more: "dict[str, Any] | None" = None,
    **kw: Any,
) -> candidate.Candidate:
    """A GitHub or session item as a candidate: its wait, its `prio:` label
    and any `more` terms, its row's features, and, for an issue, whether it
    is blocked (issue_blocked)."""
    terms = {candidate.SINCE: need.since, candidate.PRIO: prio, **(more or {})}
    blocked = need.kind == "issue" and issue_blocked(need.row or {})
    links = kw.pop("links", None)
    if links is None and need.kind == "pr":
        links = pr_links(need.row or {})
    return candidate.Candidate(
        need,
        category,
        why,
        terms={k: v for k, v in terms.items() if v is not None},
        blocked=blocked,
        features=whynot.row_features(need, category),
        links=links or (),
        **kw,
    )


def pr_links(row: dict[str, Any], issue_key: "str | None" = None) -> tuple:
    """The overlay items a PR row folds into: the Jira ticket its title or
    branch names (`issue_key`), and each GitHub issue it closes (`closes`,
    on its fleet row)."""
    links = []
    key = issue_key or row.get("issue_key")
    if key:
        links.append(("jira", str(key)))
    closes = row.get("closes") or (row.get("awaiting") or {}).get("closes") or []
    for c in closes:
        if c.get("repo") and c.get("number") is not None:
            url = f"https://github.com/{c['repo']}/issues/{c['number']}"
            links.append((github_fleet.SOURCE, url))
    return tuple(links)


def issue_blocked(row: dict[str, Any]) -> bool:
    """A GitHub issue row is blocked: it carries the `blocked` label, or an
    open issue blocks it through GitHub's native dependencies."""
    return "blocked" in (row.get("labels") or []) or bool(row.get("blocked_by"))


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
    # A fleet PR row carries no Jira key; the `prs` row for its URL does.
    keys = dict(
        conn.execute("SELECT url, issue_key FROM prs WHERE issue_key IS NOT NULL")
    )
    out = []
    for n in needs.collect(conn, cfg, now):
        category, why = need_category(n)
        prio = score.prio_of((n.row or {}).get("labels")) if n.kind == "issue" else None
        links = pr_links(n.row or {}, keys.get(n.item[1])) if n.kind == "pr" else None
        out.append(_candidate(n, category, why, prio, links=links, from_needs=True))
    return out


def _own_prs(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    if not cfg["github"]["enabled"]:
        return []
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


def _review_requests(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    """PRs asking for my review, from the `prs` table, on an instance with no
    fleet read (`github.scope = "me"`). With one, Needs you's `others_pr`
    rows are the review requests, and this source adds none."""
    if sync.fleet_enabled(cfg) or not cfg["github"]["enabled"]:
        return []
    out = []
    for p in fleet_tabs.pull_requests(conn, cfg):
        if p.get("role") != "reviewer" or p.get("is_draft"):
            continue
        need = needs.Need(
            item=(github_fleet.SOURCE, str(p["url"])),
            kind="pr",
            title=p.get("title") or "",
            ref=f"{p.get('repo') or ''}#{p.get('number')}",
            since=score.parse_ts(p.get("created")),
            url=p.get("url"),
            state=f"review requested · CI {p.get('ci_state') or 'unknown'}",
            repo=fleet_views._name(p.get("repo")),
            number=p.get("number"),
            hint="review it",
            row=p,
        )
        out.append(_candidate(need, "others_pr", "review requested"))
    return out


# A Jira ticket's status category -> its category, and the score.score terms
# it brings; its review and blocked terms are left out, because tiers now do
# what they did.
_JIRA_CATEGORY = {"indeterminate": "started", "new": "ready"}
JIRA_TERMS = ("priority", "deadline", "unblocks", "stuck")


def jira_category(
    issue: dict[str, Any], pinned: bool, mine: bool = True
) -> "tuple[str, str] | None":
    """(category, why) for an open Jira ticket, else None: in progress is
    `started`, and to do is `ready` when it is in the active sprint or
    pinned. A ticket that is not `mine` is named a pinned ticket."""
    cat = (issue.get("status_category") or "").lower()
    status = issue.get("status_name") or cat
    whose = "your ticket" if mine else "pinned ticket"
    if cat == "indeterminate":
        return "started", f"{whose}, {status}"
    if cat == "new" and (pinned or (issue.get("sprint_state") or "") == "active"):
        sprint = issue.get("sprint_name")
        return "ready", f"{whose}, {status}" + (f" in {sprint}" if sprint else "")
    return None


def _jira_tickets(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[candidate.Candidate]:
    """My open tickets, and any open one I pinned, as views.Mirror scores
    them. A pinned ticket is mine for Now even when it is assigned elsewhere."""
    if not sync.jira_enabled(cfg):
        return []
    factory = conn.row_factory
    try:
        m = views.Mirror(conn, cfg, now)
    finally:
        conn.row_factory = factory  # Mirror leaves it None
    issues = {i["key"]: i for i in m.open_mine()}
    for key in m.pins:
        i = m.issues.get(key)
        if i and not score.is_done(i["status_category"]) and not m.is_ignored(i):
            issues.setdefault(key, i)
    out = []
    for key, issue in issues.items():
        found = jira_category(issue, key in m.pins, m.is_mine(issue))
        if found is None:
            continue
        category, why = found
        blocked, signals = m.blocked[key]
        if blocked:
            why += f"; blocked: {', '.join(signals)}"
        need = needs.Need(
            item=("jira", key),
            kind="ticket",
            title=issue.get("summary") or "",
            ref=key,
            since=score.parse_ts(issue.get("status_category_changed")),
            url=f"https://{cfg['jira']['site']}/browse/{key}",
            state=" · ".join(views.issue_meta(issue)),
            hint="carry on with it" if category == "started" else "start it",
            row=issue,
        )
        scored = [t for t in m.scores[key][1] if t[0] in JIRA_TERMS]
        out.append(
            candidate.Candidate(
                need,
                category,
                why,
                terms={candidate.SCORED: scored},
                blocked=blocked,
                features={
                    "project": key.split("-", 1)[0],
                    "priority": issue.get("priority"),
                    "status": issue.get("status_name"),
                    "blocked": ",".join(signals) or None,
                    "own": "yes" if m.is_mine(issue) else "no",
                },
                related=[joined_pr(p) for p in m.prs_by_issue.get(key, [])],
            )
        )
    return out


def joined_pr(p: dict[str, Any]) -> dict[str, Any]:
    """A `prs` row joined to a ticket, as a related line: `{url, ref, why}`,
    the why reading `draft · CI failure · review required`."""
    bits = (["draft"] if p.get("is_draft") else []) + [
        f"CI {p.get('ci_state') or 'unknown'}"
    ]
    if p.get("review_decision"):
        bits.append(str(p["review_decision"]).lower().replace("_", " "))
    if p.get("role") == "reviewer":
        bits.append("asks for your review")
    return {
        "url": p.get("url"),
        "ref": f"{p.get('repo') or ''}#{p.get('number')}",
        "why": " · ".join(bits),
    }


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
        + _review_requests(conn, cfg)
        + _own_issues(conn, cfg)
        + _jira_tickets(conn, cfg, now)
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
        total, terms = score.fleet_score(
            {
                "category": r.candidate.category,
                "why": r.candidate.why,
                "prio": r.candidate.terms.get(candidate.PRIO),
                "since": r.candidate.terms.get(candidate.SINCE),
            },
            now,
            weights,
        )
        scored = r.candidate.terms.get(candidate.SCORED)
        if scored:
            terms = terms + list(scored)
            total = score._round(math.fsum(v for _, v, _ in terms))
        r.total, r.terms = total, terms
    # Folded after scoring, so a PR _visible hands back has its own score.
    return fold(out)


def fold(ranked: list[Ranked]) -> list[Ranked]:
    """`ranked` less each Ship PR that links to a Ship item ranked here (its
    first such link), which goes into that item's `folded` instead."""
    parents = {
        r.candidate.item: r for r in ranked if r.mode == SHIP and r.need.kind != "pr"
    }
    out = []
    for r in ranked:
        parent = None
        if r.mode == SHIP and r.need.kind == "pr":
            parent = next((parents[i] for i in r.candidate.links if i in parents), None)
        if parent is None:
            out.append(r)
        else:
            parent.folded.append(r)
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


def est_of(labels: "list[str] | None") -> "int | None":
    """N of the largest `est:N` label, or None for an unsized issue."""
    sizes = [
        int(lb[4:]) for lb in labels or [] if lb.startswith("est:") and lb[4:].isdigit()
    ]
    return max(sizes) if sizes else None


def _issue_need(row: dict[str, Any], state: str, hint: str) -> needs.Need:
    return needs.Need(
        item=(github_fleet.SOURCE, str(row.get("key") or row.get("url"))),
        kind="issue",
        title=row.get("title") or "",
        ref=f"{row.get('repo') or ''}#{row.get('number')}",
        since=score.parse_ts(row.get("created")),
        url=row.get("url"),
        state=state,
        repo=fleet_views._name(row.get("repo")),
        number=row.get("number"),
        hint=hint,
        row=row,
    )


def _status(labels: list[str]) -> "str | None":
    return next((lb for lb in labels if lb.startswith("status:")), None)


def deep_category(row: dict[str, Any]) -> "tuple[str, str] | None":
    """(category, why) for an issue Deep ranks, else None."""
    labels = row.get("labels") or []
    blocks = row.get("blocks")
    est = est_of(labels)
    if blocks:
        return "blocker", f"blocks {blocks} open issue{'' if blocks == 1 else 's'}"
    if "status:1_needs_refinement" in labels:
        return "refinement", "needs refinement"
    if est is not None and est >= DEEP_EST and "status:2_ready" in labels:
        return "big", f"est:{est}, ready"
    return None


def _deep_issues(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    if gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is None:
        return []
    out = []
    for row in needs._rows(conn, github_fleet.SOURCE, "issue"):
        found = deep_category(row)
        if found is None:
            continue
        category, why = found
        labels = row.get("labels") or []
        need = _issue_need(
            row,
            _status(labels) or "no status",
            "decide it, or split it with `/break-down-task`",
        )
        more = {candidate.BLOCKS: row.get("blocks"), candidate.SIZE: est_of(labels)}
        out.append(
            _candidate(need, category, why, score.prio_of(labels), more, mode=DEEP)
        )
    return out


def _deep_designs(conn: sqlite3.Connection) -> list[candidate.Candidate]:
    out = []
    for d in workstreams.mirrored_designs(conn):
        need = needs.Need(
            item=(github_fleet.SOURCE, str(d.get("key") or d.get("url"))),
            kind="design",
            title=d.get("title") or "",
            ref=d.get("path"),
            since=score.parse_ts(d.get("created")),
            url=d.get("url"),
            state="design · proposed",
            repo=fleet_views._name(d.get("repo")),
            hint="accept it, change it, or drop it",
            row=d,
        )
        out.append(_candidate(need, "design", "proposed design", mode=DEEP))
    return out


def deep_sources(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[candidate.Candidate]:
    """Every candidate Deep ranks."""
    return _deep_designs(conn) + _deep_issues(conn, cfg)


# Tidy's issue labels, first match wins: label -> (category, why).
_TIDY_ISSUE = (
    (github_fleet.HUMAN_LABEL, ("human_issue", github_fleet.HUMAN_LABEL)),
    ("status:0_untriaged", ("untriaged", "untriaged")),
    ("status:1_needs_refinement", ("refinement", "needs refinement")),
)


def _tidy_issues(
    conn: sqlite3.Connection, cfg: dict[str, Any]
) -> list[candidate.Candidate]:
    if gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is None:
        return []
    out = []
    for row in needs._rows(conn, github_fleet.SOURCE, "issue"):
        labels = row.get("labels") or []
        found = next((cw for lb, cw in _TIDY_ISSUE if lb in labels), None)
        if found is None:
            continue
        category, why = found
        need = _issue_need(
            row, _status(labels) or "no status", "close it, supersede it, or decide it"
        )
        out.append(_candidate(need, category, why, score.prio_of(labels), mode=TIDY))
    return out


def _tidy_prs(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[candidate.Candidate]:
    old = now - timedelta(days=fleet_views.OPEN_ITEM_DAYS)
    out = []
    for p in fleet_tabs.pull_requests(conn, cfg):
        created = score.parse_ts(p.get("created"))
        if p.get("role") != "author" or created is None or created > old:
            continue
        draft = "draft, " if p.get("is_draft") else ""
        need = needs.Need(
            item=(github_fleet.SOURCE, str(p["url"])),
            kind="pr",
            title=p.get("title") or "",
            ref=f"{p.get('repo') or ''}#{p.get('number')}",
            since=created,
            url=p.get("url"),
            state=f"your PR · {draft}CI {p.get('ci_state') or 'unknown'}",
            repo=fleet_views._name(p.get("repo")),
            number=p.get("number"),
            hint="merge it, close it, or say what it waits on",
            row={**(p.get("awaiting") or {}), **p},
        )
        why = f"your PR, open over {fleet_views.OPEN_ITEM_DAYS}d"
        out.append(_candidate(need, "stale_pr", why, mode=TIDY))
    return out


def tidy_sources(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[candidate.Candidate]:
    """Every candidate Tidy ranks."""
    return _tidy_issues(conn, cfg) + _tidy_prs(conn, cfg, now)


def mode_candidates(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime, mode: str
) -> list[Ranked]:
    """What `mode` ranks, unordered and unfiltered."""
    if mode == LITTLE:
        return little_candidates(conn, cfg, now)
    if mode == DEEP:
        return rank(deep_sources(conn, cfg, now), conn, cfg, now)
    if mode == TIDY:
        return rank(tidy_sources(conn, cfg, now), conn, cfg, now)
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


def _pins_then(ranked: list[Ranked], ov: overlay.Overlay, key: Any) -> list[Ranked]:
    """Pinned first in the overlay's order, then the rest by `key`."""
    pinned = sorted(
        (r for r in ranked if ov.is_pinned(r.need.item)),
        key=lambda r: ov.pin_rank(r.need.item),
    )
    rest = sorted((r for r in ranked if not ov.is_pinned(r.need.item)), key=key)
    return pinned + rest


def _prio_key(r: Ranked) -> int:
    return -score.PRIO_LEVEL.get(r.prio or "", -1)


def little_order(ranked: list[Ranked], ov: overlay.Overlay) -> list[Ranked]:
    """Pinned first in the overlay's order, then Little's tiers; within a
    tier the more urgent `prio`, then the longer wait."""
    return _pins_then(ranked, ov, lambda r: (r.tier[0], _prio_key(r), *_wait_key(r)))


def deep_order(ranked: list[Ranked], ov: overlay.Overlay) -> list[Ranked]:
    """Pinned first in the overlay's order, then the most open issues
    blocked, the more urgent `prio`, the larger estimate, the longer wait."""

    def key(r: Ranked) -> tuple:
        terms = r.candidate.terms
        return (
            -(terms.get(candidate.BLOCKS) or 0),
            _prio_key(r),
            -(terms.get(candidate.SIZE) or 0),
            *_wait_key(r),
        )

    return _pins_then(ranked, ov, key)


def tidy_order(ranked: list[Ranked], ov: overlay.Overlay) -> list[Ranked]:
    """Pinned first in the overlay's order, then strictly oldest first."""
    return _pins_then(ranked, ov, _wait_key)


MODE_ORDER = {LITTLE: little_order, DEEP: deep_order, TIDY: tidy_order}


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
    more_in_mode: int = 0  # outside Ship: its items the list did not reach
    closed_today: "int | None" = None  # Little's header figure


def _visible(
    ranked: list[Ranked],
    ov: overlay.Overlay,
    now: datetime,
    inc: "include.Include | None",
) -> "tuple[list[Ranked], int]":
    """`ranked` less the ignored, the filtered and the snoozed; and how many
    were snoozed. A parent the include filter leaves out hands its folded PRs
    back as cards of their own, unless it is snoozed, which hides them too."""
    ranked = [r for r in ranked if r.need.item not in ov.ignored]
    for r in ranked:
        r.folded = [f for f in r.folded if f.need.item not in ov.ignored]
    if inc is not None:
        kept = []
        for r in ranked:
            if inc.shows(r.need.kind):
                kept.append(r)
            elif not ov.is_snoozed(r.need.item, now):
                kept += [f for f in r.folded if inc.shows(f.need.kind)]
        ranked = kept
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
    if mode != SHIP:
        return _build_mode(conn, cfg, now, ov, inc, mode)
    ranked, snoozed = _visible(candidates(conn, cfg, now), ov, now, inc)
    ranked, more_close_outs = cap_close_outs(order(ranked, ov), ov)
    pins = sum(ov.is_pinned(r.need.item) for r in ranked)
    shown = ranked[: max(LIMIT, pins)]
    more = sum(r.from_needs for r in ranked[len(shown) :])
    return Page(shown, more, snoozed, more_close_outs)


def _build_mode(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    ov: overlay.Overlay,
    inc: "include.Include | None",
    mode: str,
) -> Page:
    """A mode other than Ship: its own collector's items, in its own order."""
    own = mode_candidates(conn, cfg, now, mode)
    seen = {r.need.item for r in own}
    if ov.pinned_at:
        # Pins lead in every mode: a pinned item this mode does not rank
        # comes from Ship, with its Ship why line.
        own += [
            r
            for r in candidates(conn, cfg, now)
            if ov.is_pinned(r.need.item) and r.need.item not in seen
        ]
    ranked, snoozed = _visible(own, ov, now, inc)
    ranked = MODE_ORDER[mode](ranked, ov)
    pins = sum(ov.is_pinned(r.need.item) for r in ranked)
    # Deep's limit counts only the unpinned; the others' counts pins too.
    shown = ranked[: pins + DEEP_LIMIT if mode == DEEP else max(LIMIT, pins)]
    return Page(
        shown, 0, snoozed,
        mode=mode,
        more_in_mode=len(ranked) - len(shown),
        closed_today=closed_today(conn, now) if mode == LITTLE else None,
    )  # fmt: skip


# --- render ------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:+.2f}".rstrip("0").rstrip(".").replace("-", "−")


def why_line(r: Ranked) -> str:
    """`ship: ready to merge · score 10.3: quick_win +10 (bot PR …) · age +0.3
    (waiting 2d)`: the tier, then the terms with a value, as text. Another
    mode's item has no score, only its mode, why and `prio`: `little · PR, 2
    files, +14/−3 · prio:1`, `deep · blocks 4 open issues · prio:1`."""
    if r.mode != SHIP:
        return " · ".join([r.mode, r.why] + ([r.prio] if r.prio else []))
    bits = [f"{name} {_fmt(value)} ({why})" for name, value, why in r.terms if value]
    total = f"{r.total:g}".replace("-", "−")
    raised = r.raised_by
    via = f" via {raised.need.ref or raised.need.title}" if raised else ""
    head = f"ship: {r.tier[1]}{via} · score {total}"
    return f"{head}: " + " · ".join(bits) if bits else head


def _folded_html(f: Ranked) -> str:
    """A folded PR on its parent's card: its ref, linked, then its why."""
    name = f.need.ref or f.need.title
    url = f.need.url or ""
    ref = view_model.link(url, name) if url.startswith("https://") else _e(name)
    return (
        f'<div class="folded" data-key="{_e(f.need.item[1])}">'
        f"↳ {ref}: {_e(f.why)}</div>"
    )


def _linked_html(x: dict[str, Any]) -> str:
    """A related PR on its ticket's card: evidence only, never ranked."""
    url = str(x.get("url") or "")
    name = str(x.get("ref") or url)
    ref = view_model.link(url, name) if url.startswith("https://") else _e(name)
    return (
        f'<div class="folded linked" data-key="{_e(url)}">'
        f"↳ {ref}: {_e(str(x.get('why') or ''))}</div>"
    )


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
    folded = "".join(_folded_html(f) for f in r.folded) + "".join(
        _linked_html(x) for x in r.linked
    )
    return (
        f'<div {overlay.card_attrs(n.item, ov, now)} data-kind="{_e(n.kind)}"'
        f' data-category="{r.category}" data-tier="{_e(r.tier[1])}"'
        f' data-total="{r.total}" data-mode="{r.mode}">'
        f"{needs._title(n, n.url)}{needs.ask_html(n)}"
        f'<div class="meta">{meta}</div>{steps}{folded}'
        f'<div class="why" data-terms="{terms}">{_e(why_line(r))}</div>'
        f"{act_html}{overlay.card_controls(n.item, ov, now, reorder=False, why_not=True)}"
        f"{whynot.picker(overlay.snooze_until(now))}</div>"
    )


def lanes(p: Page, inc: "include.Include | None" = None) -> list[view_model.Lane]:
    """The page's one lane, Next."""
    filtered = inc is not None and not inc.everything
    if p.mode != SHIP:
        empty = EMPTY[p.mode] + (" in the filter." if filtered else ".")
    elif filtered:
        empty = "Nothing included by the filter is waiting."
    else:
        empty = "Nothing is waiting on you."
    return [view_model.Lane("next", "Next", p.shown, empty)]


def mode_button(mode: str) -> str:
    """The header's mode button: the mode's icon and name, its question on
    hover; a click saves the next mode."""
    nxt = next_mode(mode)
    title = f"{QUESTIONS[mode]} Click for {nxt.title()}: {QUESTIONS[nxt]}"
    return (
        f'<button type="button" id="mode" data-ov="mode" data-now="{mode}"'
        f' data-mode="{nxt}" title="{_e(title)}">'
        f"{icons.svg(MODE_ICONS[mode])}{mode.title()}</button>"
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
    parts = []
    if p.closed_today is not None:
        parts.append(
            f'<p class="note" data-closed-today="{p.closed_today}">'
            f"closed today: {p.closed_today}</p>"
        )
    if not cfg["github"]["enabled"]:
        parts.append(
            '<p class="note">github.enabled is false, so no PRs or issues are'
            " ranked here.</p>"
        )
    elif not sync.fleet_enabled(cfg):
        parts.append(
            '<p class="note">github.scope is not "owner", so GitHub issues are'
            " not ranked here; your PRs and review requests are.</p>"
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
            f"{p.more_in_mode} {MORE_IN_MODE[p.mode]}.</p>"
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
    return f'<div class="fleet now" data-mode="{mode}">{"".join(parts)}</div>'
