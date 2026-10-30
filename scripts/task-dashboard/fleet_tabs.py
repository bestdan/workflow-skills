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

Both take a `?ws=<id>` filter (workstreams.py), which lists only that
workstream's members, and the include filter, the page's text filter (each
card's `.title` and `.ref`), ignores and snoozes as every other view does: an
ignored item is left out, a snoozed one is left out and counted.
"""

import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import fleet_views
import gauges
import github_fleet
import github_gh
import include
import overlay
import score
import sync
import view_model
import views  # card_title and _lane, read at call time: views imports this
import workstreams

_e = html.escape

PR_LANES = (
    ("ready", "Ready for you", "No PR is in the review queue."),
    ("waiting", "Waiting on you", "No PR is waiting out of the queue."),
    ("others", "From others on your repos", ""),
    ("review", "Review requested of me", ""),
    ("ci", "CI failing", "No PR has failing CI."),
    ("drafts", "Drafts", "No drafts."),
)
# Short tab labels (lane_tabs.py) for the lanes whose title is long.
PR_TABS = {
    "ready": "Ready",
    "waiting": "Waiting",
    "others": "From others",
    "review": "Review requested",
}
ISSUE_TABS = {"untriaged": "Untriaged"}
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
        return '<p class="note">The owner-wide GitHub read is off (github.enabled is false, or github.scope is not "owner"), so nothing is shown here.</p>'
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
    for url, repo, number, title, draft, decision, ci, role, key, raw in conn.execute(
        "SELECT url, repo, number, title, is_draft, review_decision, ci_state,"
        " role, issue_key, raw_json FROM prs ORDER BY repo, number"
    ):
        try:
            node = json.loads(raw or "{}") or {}
        except ValueError:
            node = {}
        created = node.get("createdAt")
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
            "size": github_gh.pr_size(node),
            "issue_key": key,  # the Jira key in its title or branch (sync.py)
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
                "size": None,
            },
        )
        p["awaiting"] = row
        # The fleet step's verdict was reached on its own CI read: show that one.
        p["ci_state"] = row.get("ci_state")
        p["created"] = row.get("created") or p["created"]
        p["size"] = row.get("size") or p["size"]
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


def pr_meta(p: dict, why: str, now: datetime) -> list[str]:
    """A PR card's meta line, as its plain-text parts."""
    row = p.get("awaiting") or {}
    bits = []
    if p.get("role") != "author" and row.get("author"):
        bits.append(f"by {row['author']}")
    bits += [f"CI {p.get('ci_state') or 'unknown'}", _age(p.get("created"), now)]
    bits.append(p.get("repo") or "")
    if why:
        bits.append(why)
    return [b for b in bits if b]


def _pr_card(p: dict, why: str, ov: overlay.Overlay, now: datetime) -> str:
    ref = f"{p.get('repo') or ''}#{p.get('number')}"
    row = p.get("awaiting") or {}
    meta = " · ".join(_e(b) for b in pr_meta(p, why, now))
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


@dataclass
class Tab:
    """A tab's lanes as the page shows them, and what the page notes."""

    lanes: list[view_model.Lane]
    snoozed: int  # left out and counted
    synced: bool  # a fleet sync has succeeded


def pr_lanes(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    ws: "workstreams.Filter | None" = None,
) -> Tab:
    """The Pull requests lanes, each item a (PR, why) pair. Ignored and
    snoozed PRs are left out, and so is an optional lane with no rows. With
    `ws` (`?ws=`), only that workstream's members are listed."""
    ov = overlay.load(conn)
    synced = gauges.review_queue_rows(conn, sync.fleet_enabled(cfg)) is not None
    rows: dict[str, list[tuple[Any, str]]] = {name: [] for name, _, _ in PR_LANES}
    snoozed = 0
    for p in pull_requests(conn, cfg):
        if ws is not None and not ws.keeps((github_fleet.SOURCE, str(p["url"]))):
            continue
        hidden = _hidden(ov, (github_fleet.SOURCE, str(p["url"])), now)
        snoozed += hidden == "snoozed"
        if hidden:
            continue
        lane, why = pr_lane(p)
        rows[lane].append((p, why))
    group = include.group_of("pr")
    lanes = []
    for name, title, empty in PR_LANES:
        items = sorted(rows[name], key=lambda t: _lane_order(name, t[0]))
        if name in OPTIONAL_LANES and not items:
            continue
        tab = PR_TABS.get(name)
        lanes.append(view_model.Lane(name, title, items, empty, group, tab))
    return Tab(lanes, snoozed, synced)


def waiting_count(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> "int | None":
    """The Waiting on you lane's count, which the header's review gauge
    shows; None before a good fleet sync, as the lane itself shows nothing."""
    tab = pr_lanes(conn, cfg, now)
    if not tab.synced:
        return None
    return next((len(lane.items) for lane in tab.lanes if lane.id == "waiting"), 0)


def _render_lanes(tab: Tab, card: Any, inc: include.Include) -> list[str]:
    return [
        views._lane(
            lane.id,
            lane.title,
            [card(x) for x in lane.items],
            lane.empty,
            lane.group,
            inc,
            lane.tab,
        )
        for lane in tab.lanes
    ]


def render_prs(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
    ws: "workstreams.Filter | None" = None,
) -> str:
    inc = inc or include.everything("prs")
    ov = overlay.load(conn)
    tab = pr_lanes(conn, cfg, now, ws)
    parts = [_gate_note(cfg, tab.synced), workstreams.filter_note(ws, "/prs")]
    parts += _render_lanes(tab, lambda t: _pr_card(t[0], t[1], ov, now), inc)
    if inc.has(include.group_of("pr")):
        parts.append(_snoozed_note(tab.snoozed))
    return f'<div class="fleet">{"".join(parts)}</div>'


# --- Issues ------------------------------------------------------------------


def issue_lane(labels: "list[str]") -> str:
    if github_fleet.HUMAN_LABEL in labels:
        return "needs-you"
    for label in labels:
        if label in _STATUS_LANES:
            return _STATUS_LANES[label]
    return "untriaged"


def issue_meta(row: dict, now: datetime) -> list[str]:
    """An issue card's meta line, as its plain-text parts."""
    labels = row.get("labels") or []
    bits = [lb for lb in labels if lb.startswith(("prio:", "est:"))]
    bits += [_age(row.get("created"), now), row.get("repo") or ""]
    return [b for b in bits if b]


def _issue_card(row: dict, ov: overlay.Overlay, now: datetime) -> str:
    ref = f"{row.get('repo') or ''}#{row.get('number')}"
    meta = " · ".join(_e(b) for b in issue_meta(row, now))
    item = (github_fleet.SOURCE, str(row.get("key") or row.get("url")))
    head = views.card_title(str(row.get("url") or ""), row.get("title"), ref)
    return _card(item, ov, now, head, meta)


def _issues_truncated(conn: sqlite3.Connection) -> int:
    """Owners the last good fleet run's issue cap cut short."""
    marks = ", ".join("?" * len(sync.SUCCESS_OUTCOMES))
    row = conn.execute(
        f"SELECT counts FROM sync_runs WHERE kind = ? AND outcome IN ({marks})"
        " ORDER BY id DESC LIMIT 1",
        (github_fleet.RUN_KIND, *sync.SUCCESS_OUTCOMES),
    ).fetchone()
    try:
        counts = json.loads(row[0] or "{}") if row else {}
    except ValueError:
        return 0
    return int((counts or {}).get("issues_truncated") or 0)


def issue_lanes(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    ws: "workstreams.Filter | None" = None,
) -> Tab:
    """The Issues lanes, each item a mirror row; ignored and snoozed issues
    are left out. With `ws` (`?ws=`), only that workstream's members."""
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
    by_lane: dict[str, list[dict]] = {name: [] for name, _, _ in ISSUE_LANES}
    snoozed = 0
    for row in rows:
        item = (github_fleet.SOURCE, str(row.get("key") or row.get("url")))
        if ws is not None and not ws.keeps(item):
            continue
        hidden = _hidden(ov, item, now)
        snoozed += hidden == "snoozed"
        if hidden:
            continue
        by_lane[issue_lane(row.get("labels") or [])].append(row)
    group = include.group_of("issue")
    lanes = [
        view_model.Lane(name, title, by_lane[name], empty, group, ISSUE_TABS.get(name))
        for name, title, empty in ISSUE_LANES
    ]
    return Tab(lanes, snoozed, synced)


def render_issues(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
    ws: "workstreams.Filter | None" = None,
) -> str:
    inc = inc or include.everything("issues")
    ov = overlay.load(conn)
    tab = issue_lanes(conn, cfg, now, ws)
    parts = [_gate_note(cfg, tab.synced), workstreams.filter_note(ws, "/issues")]
    cut = _issues_truncated(conn) if tab.synced else 0
    if cut:
        parts.append(
            f'<p class="note" data-truncated="{cut}">{cut} owner'
            f"{'s have' if cut > 1 else ' has'} more than"
            f" {github_fleet.MAX_OPEN_ISSUES} open issues besides those needing"
            " a human; the rest are not mirrored.</p>"
        )
    parts += _render_lanes(tab, lambda row: _issue_card(row, ov, now), inc)
    if inc.has(include.group_of("issue")):
        parts.append(_snoozed_note(tab.snoozed))
    return f'<div class="fleet">{"".join(parts)}</div>'
