"""The fleet view's Needs you page (`/needs`), the fleet instance's home page.

From dev_docs/designs/2026-10-23-fleet-view.md, "Principles" 1 and "Views"
item 1: one decision first, the rest collapsed, plain approvals batched. It
reads SQLite only, and links to where each item is acted on, never acting
itself.

WHAT IT COLLECTS, each keyed by its overlay item `(source, key)`:

- **Sessions waiting on me.** A session-state hook row (sessions.py,
  `source = 'claude-hook'`) at `need_input` with no prompt since: a harness
  permission prompt, or a turn that ended in a question (`asked`, labelled
  "asked (heuristic)": the hook's test is a trailing "?"). Active within
  fleet_views.SESSION_IDLE_DAYS. A turn that ended without a question is not
  listed, nor is one the hook could not judge (`asked` null, or a format-1
  hook): that is finished work, in the Finished group below. A session quiet for
  longer is Stale's, and an empty spawn (never prompted) is Stale's too. A
  pending `AskUserQuestion` never reaches `Stop`, and the harness documents
  no notification for it, so it is not seen here. And, with `claude_remote`
  enabled, a cloud session row (claude_remote.py) at `need_input` /
  `needs_action`. The wait starts at the hook row's `waiting_since`; a cloud
  row has none, so its `updated` stands in.
- **PRs in the review queue**: the `in_queue` rows of
  gauges.review_queue_rows, the reader the header gauge counts, so the page
  and the gauge agree. The wait starts when the PR was opened: when it entered
  the queue is not recorded.
- **Issues at `auto:human-review-needed`** (github_fleet's `issue` rows that
  carry it, github_fleet.needs_human), waiting since they were opened, for
  the same reason, and behind the same gate as the PRs: none until a fleet
  sync run has succeeded.
- **Drain items marked "needs you"**: drain_items(), an empty hook point
  until a drain tracking issue is configured.

ORDER. Pinned items first, in the overlay's order (overlay.Overlay.pin_rank),
then the longest wait first. A snoozed item is left out and counted. An
ignored item is left out entirely; the page's Ignored list can undo it.

APPROVALS. A session waiting on a harness permission prompt (`need_input` on
a hook row) is a plain approval, and so is one whose brief ends in any short
yes/no-shaped ask (approval_brief). Those go into one collapsed
Approvals group, out of the main list. No session source carries a post-turn
summary yet (`brief` is null on both), so today only permission prompts land
there, and an `asked` session stays in the main list: without the question's
text there is no telling a yes/no from a decision. A brief-carrying source
slots in at approval_brief().

FOCUS. While the overlay has a focus item, only that item's own needs show:
the item itself, and what is tied to it (related()). The rest are counted as
held and never listed, snoozed or not; a snoozed need of the focus item is
counted as snoozed. A focused design takes every need in its repo, in the
main list rather than a section of its own.

NEW. A card whose wait began after the overlay's `last_visit` carries a `new`
badge, expanded, collapsed and approval cards alike; the order is unchanged.
With no visit recorded, nothing is new.

FINISHED SINCE YOU LOOKED. A collapsed group at the bottom lists the sessions
whose last turn ended after `last_visit` without asking
(fleet_views.finished_sessions), one line each; with no visit recorded it
covers the last github_fleet.CLOSED_WINDOW_DAYS days. A turn that asked is in
the main list instead. The group is hidden when it is empty. The include
filter, ignores, snoozes and the focus apply to it as to every other card;
under a focus an unrelated finished session is left out, but not counted as
held, since it is not a need.

MARK AS READ, in that group, and "Mark all as read" under the Needs you
heading, record a visit through overlay's `visit` action with the page's
render time. Loading the page records nothing, so a phone preview does not
swallow the digest, and an item that arrives while the page is open is still
new next time. The page reloads after the action, which clears the badges.
"""

import html
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import claude_remote
import fleet_views
import gauges
import github_fleet
import include
import overlay
import score
import sessions
import sync

_e = html.escape

CODE_URL = "https://claude.ai/code"

CSS = """
.needs .need { padding:6px 10px; }
.needs details.need > summary { cursor:pointer; list-style-position:inside; }
.needs .need.first { border-width:2px; }
.needs .brief { white-space:pre-line; margin:6px 0; }
.needs .wait { color:var(--fg); font-weight:600; }
.needs .approvals details.group > summary h2,
.needs .finished details.group > summary h2 { display:inline; }
.needs .new { font-size:11px; font-weight:600; color:var(--accent);
  border:1px solid var(--accent); border-radius:999px; padding:0 6px; }
.needs .visit { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
  margin:4px 0 8px; color:var(--muted); font-size:12px; }
.needs .visit button { font:inherit; padding:2px 10px; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); }
"""


@dataclass
class Need:
    item: overlay.Item  # (source, key): the overlay identity
    # A collected need: session | pr | issue | drain. focus_need() instead
    # carries the focus row's mirror kind (design, ticket, ...; "" once gone).
    kind: str
    title: str
    since: "datetime | None"  # when the wait began; orders the list
    url: "str | None"
    state: str  # one line: what it is waiting on
    where: str = ""
    brief: "str | None" = None
    approval: bool = False
    repo: "str | None" = None  # bare repo name
    branch: "str | None" = None
    number: "int | None" = None
    hint: str = ""  # where it is acted on, or the skill that resolves it
    ref: "str | None" = None  # `repo#N`, shown muted after the title


# --- approvals ---------------------------------------------------------------

# Any short yes/no-shaped ask: "Open the PR?", "Commit and push?",
# "Should I refactor it first?", "(y/n)". Whether the step is routine is not
# judged. A question offering a choice ("A or B?") or an
# open question ("how should I resolve it?") is a decision, not an approval.
# This is not snapshot.approval_shaped, which classifies the owner's reply
# ("y", "go ahead"), not the agent's question.
_YN = re.compile(r"\(?\[?\b(y/n|yes/no)\b\]?\)?\s*\??\s*$", re.IGNORECASE)
_ROUTINE = re.compile(
    r"^(?:ok(?:ay)?,?\s+)?(?:shall i|should i|ok(?:ay)? to|want me to|do you want me"
    r" to|may i|can i|ready to|go ahead|proceed|commit|push|open (?:the|a) pr|merge"
    r"|ship|continue)\b",
    re.IGNORECASE,
)
_ROUTINE_WORDS = 15
_OPEN = re.compile(r"\b(how|what|which|why|where|when|who|whom|whose)\b", re.IGNORECASE)


def approval_brief(brief: "str | None") -> bool:
    """True when a brief's last line is a short yes/no-shaped ask."""
    lines = [ln.strip() for ln in (brief or "").splitlines() if ln.strip()]
    if not lines:
        return False
    last = lines[-1]
    if _OPEN.search(last) or " or " in f" {last.lower()} ":
        return False
    if _YN.search(last):
        return True
    return (
        last.endswith("?")
        and len(last.split()) <= _ROUTINE_WORDS
        and _ROUTINE.match(last) is not None
    )


# --- collect -----------------------------------------------------------------


def _rows(conn: sqlite3.Connection, source: str, kind: str) -> list[dict[str, Any]]:
    return [
        fleet_views._raw(r[0])
        for r in conn.execute(
            "SELECT raw_json FROM issues WHERE source = ? AND kind = ? ORDER BY key",
            (source, kind),
        )
    ]


def _session(row: dict[str, Any], now: datetime) -> "Need | None":
    """A hook session row waiting on me, or None."""
    if row.get("status_bucket") != "waiting" or row.get("empty_spawn"):
        return None
    if row.get("status_category") != "need_input":
        return None
    last = score.parse_ts(row.get("updated"))
    if last is None or now - last > timedelta(days=fleet_views.SESSION_IDLE_DAYS):
        return None
    asked = bool(row.get("asked"))
    brief = row.get("brief")
    repo = fleet_views.repo_of_path(row.get("cwd"))
    where = [f"on {row.get('host') or 'an unknown host'}"]
    if repo or row.get("cwd"):
        where.append(
            (repo or row.get("cwd") or "")
            + (f": {row['branch']}" if row.get("branch") else "")
        )
    return Need(
        item=(sessions.SOURCE, str(row.get("key") or row.get("session_id"))),
        kind=sessions.KIND,
        title=row.get("title") or f"session {row.get('session_id')}",
        since=score.parse_ts(row.get("waiting_since")) or last,
        url=CODE_URL,
        state="asked (heuristic)" if asked else "permission prompt",
        where=" · ".join(where),
        brief=brief,
        approval=not asked or approval_brief(brief),
        repo=repo,
        branch=row.get("branch"),
        hint="answer it in the session",
    )


def _remote(row: dict[str, Any]) -> "Need | None":
    """A cloud session row that needs action, or None."""
    if not (row.get("needs_action") or row.get("status_category") == "need_input"):
        return None
    sid = str(row.get("id") or row.get("key"))
    brief = row.get("brief")
    where = [row.get("machine") or "cloud"]
    if row.get("repository"):
        where.append(
            row["repository"] + (f": {row['branch']}" if row.get("branch") else "")
        )
    return Need(
        item=(claude_remote.SOURCE, sid),
        kind=claude_remote.KIND,
        title=row.get("title") or f"session {sid}",
        since=score.parse_ts(row.get("updated")),
        url=f"{CODE_URL}/{sid}" if sid.startswith("session_") else CODE_URL,
        state="needs action",
        where=" · ".join(where),
        brief=brief,
        approval=approval_brief(brief),
        repo=fleet_views._name(row.get("repository")),
        branch=row.get("branch"),
        hint="answer it in the session",
    )


def _pr(row: dict[str, Any]) -> Need:
    bits = [f"CI {row.get('ci_state') or 'unknown'}"]
    if row.get("review_decision"):
        bits.append(str(row["review_decision"]).lower().replace("_", " "))
    bits.append("your PR" if row.get("role") == "author" else "review requested")
    return Need(
        item=(github_fleet.SOURCE, str(row.get("key") or row.get("url"))),
        kind="pr",
        title=row.get("title") or "",
        ref=f"{row.get('repo') or ''}#{row.get('number')}",
        since=score.parse_ts(row.get("created")),
        url=row.get("url"),
        state=" · ".join(bits),
        brief=row.get("brief"),
        repo=fleet_views._name(row.get("repo")),
        branch=row.get("head_ref"),
        number=row.get("number"),
        hint="review and merge it on GitHub",
    )


def _issue(row: dict[str, Any]) -> Need:
    return Need(
        item=(github_fleet.SOURCE, str(row.get("key") or row.get("url"))),
        kind="issue",
        title=row.get("title") or "",
        ref=f"{row.get('repo') or ''}#{row.get('number')}",
        since=score.parse_ts(row.get("created")),
        url=row.get("url"),
        state=github_fleet.HUMAN_LABEL,
        repo=fleet_views._name(row.get("repo")),
        number=row.get("number"),
        hint="decide it, then <code>/promote-tasks</code>",
    )


def drain_items(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[Need]:
    """The monthly drain's "needs you" items. Empty: no drain issue is
    configured anywhere yet, and this is where its reader plugs in."""
    return []


def collect(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> list[Need]:
    """Everything waiting on me, unordered and unfiltered."""
    out: list[Need] = []
    if sessions.enabled(cfg):
        for row in _rows(conn, sessions.SOURCE, sessions.KIND):
            need = _session(row, now)
            if need is not None:
                out.append(need)
    if cfg["claude_remote"]["enabled"]:
        for row in _rows(conn, claude_remote.SOURCE, claude_remote.KIND):
            need = _remote(row)
            if need is not None:
                out.append(need)
    # Issue rows take the PR rows' gate: none before the first successful
    # fleet run, so leftovers from an earlier run are never shown as current.
    queue = gauges.review_queue_rows(conn, sync.fleet_enabled(cfg))
    for row in queue or []:
        if row.get("in_queue"):
            out.append(_pr(row))
    if queue is not None:
        out.extend(
            _issue(row)
            for row in _rows(conn, github_fleet.SOURCE, "issue")
            if github_fleet.needs_human(row)
        )
    out.extend(drain_items(conn, cfg))
    return out


def order(needs: list[Need], ov: overlay.Overlay) -> list[Need]:
    """Pinned first in the overlay's order, then the longest wait first."""
    pinned = sorted(
        (n for n in needs if ov.is_pinned(n.item)), key=lambda n: ov.pin_rank(n.item)
    )
    rest = sorted(
        (n for n in needs if not ov.is_pinned(n.item)),
        key=lambda n: (n.since is None, n.since or datetime.max, n.item),
    )
    return pinned + rest


# --- focus -------------------------------------------------------------------


def _task_number(branch: "str | None") -> "int | None":
    """`.../task-<n>` -> n, the gh-issue claim branch shape."""
    last = (branch or "").rsplit("/", 1)[-1]
    m = re.fullmatch(r"task-(\d+)", last)
    return int(m.group(1)) if m else None


def focus_need(conn: sqlite3.Connection, item: overlay.Item) -> Need:
    """The focus item as a Need, for related(); bare when it left the mirror."""
    row = conn.execute(
        "SELECT kind, raw_json FROM issues WHERE source = ? AND key = ?", item
    ).fetchone()
    kind, raw = (row[0], fleet_views._raw(row[1])) if row else ("", {})
    need = Need(item, kind, item[1], None, None, "", branch=raw.get("branch"))
    if item[0] == sessions.SOURCE:
        need.repo = fleet_views.repo_of_path(raw.get("cwd"))
    elif item[0] == claude_remote.SOURCE:
        need.repo = fleet_views._name(raw.get("repository"))
    elif item[0] == github_fleet.SOURCE:
        need.repo = fleet_views._name(raw.get("repo"))
        need.branch = raw.get("head_ref")
        need.number = raw.get("number")
    return need


def related(need: Need, focus: Need) -> bool:
    """Whether `need` is one of the focus item's own needs.

    The item itself; anything in a focused design's repo; a session on a
    focused PR's head branch, or a PR on
    a focused session's branch; for a focused issue, a PR titled `[#n]` or a
    session or PR on its `task-<n>` claim branch. All within one repo.
    """
    if need.item == focus.item:
        return True
    if focus.repo is None or need.repo != focus.repo:
        return False
    if focus.kind == "design":
        return True
    if focus.kind in ("pr", "session") and focus.branch:
        return need.kind != focus.kind and need.branch == focus.branch
    if focus.kind == "issue" and focus.number is not None:
        if need.kind == "pr" and f"[#{focus.number}]" in need.title:
            return True
        return _task_number(need.branch) == focus.number
    return False


# --- render ------------------------------------------------------------------


def waited(since: "datetime | None", now: datetime) -> str:
    if since is None:
        return "waiting for an unknown time"
    s = max(0.0, (now - since).total_seconds())
    if s < 60:
        return "waiting under a minute"
    if s < 90 * 60:
        return f"waiting {round(s / 60)}m"
    if s < 36 * 3600:
        return f"waiting {round(s / 3600)}h"
    return f"waiting {round(s / 86400)}d"


def _title(n: Need, url: "str | None") -> str:
    """The `.title`, linked to `url` when given, then the muted `.ref`."""
    if n.ref is not None:
        return fleet_views.title_ref(url, n.title, n.ref)
    return fleet_views._link(url, n.title)


def is_new(n: Need, ov: overlay.Overlay) -> bool:
    """Whether the wait began after the last visit; never with none recorded."""
    last = score.parse_ts(ov.last_visit)
    return last is not None and n.since is not None and n.since > last


def _meta(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    new = '<span class="new">new</span> · ' if is_new(n, ov) else ""
    pin = '<span class="pin">pinned</span> · ' if ov.is_pinned(n.item) else ""
    bits = [_e(n.state)] + ([_e(n.where)] if n.where else [])
    wait = f'<span class="wait">{_e(waited(n.since, now))}</span>'
    return f"{new}{pin}{wait} · " + " · ".join(bits)


def _attrs(n: Need, cls: str) -> str:
    since = n.since.strftime("%Y-%m-%dT%H:%M:%SZ") if n.since else ""
    return (
        f'class="card need{cls}" data-source="{_e(n.item[0])}"'
        f' data-key="{_e(n.item[1])}" data-kind="{_e(n.kind)}" data-since="{since}"'
    )


def _body(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    """The brief when there is one, then where to act.

    With no brief there is no brief block: the header already shows the
    title, the wait and the state.
    """
    brief = f'<div class="brief">{_e(n.brief)}</div>' if n.brief else ""
    what = {"pr": "the PR", "issue": "the issue", "session": "the session"}
    opens = (
        f"open {what.get(n.kind, 'it')}" if n.url != CODE_URL else "open claude.ai/code"
    )
    link = (
        f'<a href="{_e(n.url)}">{opens}</a>'
        if n.url and n.url.startswith("https://")
        else ""
    )
    act = " · ".join(x for x in (link, n.hint) if x)
    act = f'<div class="act">→ {act}</div>' if act else ""
    return brief + act + overlay.card_controls(n.item, ov, now, reorder=False)


def expanded(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    return (
        f"<div {_attrs(n, ' first')}>{_title(n, n.url)}"
        f'<div class="meta">{_meta(n, now, ov)}</div>{_body(n, now, ov)}</div>'
    )


def collapsed(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    return (
        f"<details {_attrs(n, '')}><summary>{_title(n, None)}"
        f' <span class="meta">{_meta(n, now, ov)}</span></summary>'
        f"{_body(n, now, ov)}</details>"
    )


def finished_row(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    """One line in the Finished group: title or branch, host, the turn's end."""
    meta = f"{_e(n.where)} · turn ended {_e(fleet_views.ago(n.since, now))}"
    controls = overlay.card_controls(n.item, ov, now, reorder=False, pin=False)
    return (
        f"<details {_attrs(n, ' done')}><summary>{_title(n, None)}"
        f' <span class="meta">{meta}</span></summary>{controls}</details>'
    )


def finished(
    conn: sqlite3.Connection, cfg: dict[str, Any], since: datetime
) -> list[Need]:
    """The sessions that finished a turn after `since`, as Needs."""
    out = []
    for s in fleet_views.finished_sessions(fleet_views.load(conn, cfg), since):
        out.append(
            Need(
                item=(sessions.SOURCE, s.session_id),
                kind=sessions.KIND,
                title=sessions._label(s.cwd, s.branch) or f"session {s.session_id}",
                since=s.last,
                url=None,
                state="turn ended",
                where=f"on {s.host}",
                repo=s.repo,
                branch=s.branch,
            )
        )
    return out


def since_looked(ov: overlay.Overlay, now: datetime) -> datetime:
    """Where the Finished group starts: the last visit, else the closed window."""
    last = score.parse_ts(ov.last_visit)
    return last or now - timedelta(days=github_fleet.CLOSED_WINDOW_DAYS)


@dataclass
class Page:
    main: list[Need]
    approvals: list[Need]
    held: int
    snoozed: int
    focus: "Need | None"
    finished: list[Need]


def build(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    ov: overlay.Overlay,
    inc: "include.Include | None" = None,
) -> Page:
    # An ignored item is gone from the page; the Ignored footer can undo it.
    needs = [n for n in collect(conn, cfg, now) if n.item not in ov.ignored]
    done = finished(conn, cfg, since_looked(ov, now))
    done = [n for n in done if n.item not in ov.ignored]
    # Then the include filter: a need it switches off is neither held,
    # snoozed nor listed, so no count on the page reflects it.
    if inc is not None:
        needs = [n for n in needs if inc.shows(n.kind)]
        done = [n for n in done if inc.shows(n.kind)]
    focus = None
    held = 0
    # Focus first: an unrelated item is held whether or not it is snoozed.
    if ov.focus is not None:
        focus = focus_need(conn, ov.focus)
        mine = [n for n in needs if related(n, focus)]
        held = len(needs) - len(mine)
        needs = mine
        done = [n for n in done if related(n, focus)]
    snoozed = [n for n in needs + done if ov.is_snoozed(n.item, now)]
    needs = [n for n in needs if not ov.is_snoozed(n.item, now)]
    done = [n for n in done if not ov.is_snoozed(n.item, now)]
    ordered = order(needs, ov)
    return Page(
        main=[n for n in ordered if not n.approval],
        approvals=[n for n in ordered if n.approval],
        held=held,
        snoozed=len(snoozed),
        focus=focus,
        finished=done,
    )


def render(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> str:
    ov = overlay.load(conn)
    p = build(conn, cfg, now, ov, inc)
    parts = []
    if p.focus is not None:
        row = conn.execute(
            "SELECT summary FROM issues WHERE source = ? AND key = ?", p.focus.item
        ).fetchone()
        name = (row[0] if row and row[0] else None) or p.focus.item[1]
        parts.append(
            f'<p class="note" data-focus-held="{p.held}">Focus: {_e(name)}. Showing'
            f" its needs only; {p.held} held.</p>"
        )
    if not sync.fleet_enabled(cfg):
        parts.append(
            '<p class="note">github.fleet is off, so no PRs or issues are'
            " shown here.</p>"
        )
    cards = []
    for i, n in enumerate(p.main):
        cards.append(expanded(n, now, ov) if i == 0 else collapsed(n, now, ov))
    if p.approvals:
        empty = "No decisions waiting; only the approvals below."
    elif p.focus is not None:
        empty = "Nothing of the focus item needs you."
    elif inc is not None and not inc.everything:
        empty = "Nothing included by the filter needs you."
    else:
        empty = "Nothing needs you."
    body = "".join(cards) if cards else f'<p class="empty">{_e(empty)}</p>'
    # Beside the heading, not in it: the page's text filter rewrites the h2.
    mark_all = (
        '<div class="visit"><button type="button" data-ov="visit"'
        f' data-at="{fleet_views._stamp(now)}" title="Clear the new badges and the Finished group">'
        "Mark all as read</button></div>"
    )
    parts.append(
        f'<section class="lane" data-section="needs">'
        f"<h2>Needs you ({len(p.main)})</h2>{mark_all}{body}</section>"
    )
    if p.approvals:
        rows = "".join(collapsed(n, now, ov) for n in p.approvals)
        is_open = "" if p.main else " open"
        parts.append(
            '<section class="lane approvals" data-section="approvals">'
            f'<details class="group"{is_open}>'
            f"<summary><h2>Approvals ({len(p.approvals)})</h2></summary>"
            '<p class="note">Plain approvals, batched: answer each in its'
            f" session.</p>{rows}</details></section>"
        )
    if p.finished:
        parts.append(finished_group(p.finished, now, ov))
    if p.snoozed:
        parts.append(
            f'<p class="note" data-snoozed="{p.snoozed}">{p.snoozed} snoozed.</p>'
        )
    return f'<div class="fleet needs">{"".join(parts)}</div>'


def finished_group(done: list[Need], now: datetime, ov: overlay.Overlay) -> str:
    """The closed "Finished since you looked (N)" group, with Mark as read."""
    last = score.parse_ts(ov.last_visit)
    if last is None:
        note = (
            "No visit recorded yet, so this covers the last"
            f" {github_fleet.CLOSED_WINDOW_DAYS} days."
        )
    else:
        note = f"Since your last visit, {fleet_views.ago(last, now)} ({ov.last_visit})."
    rows = "".join(finished_row(n, now, ov) for n in done)
    return (
        '<section class="lane finished" data-section="finished">'
        '<details class="group">'
        f"<summary><h2>Finished since you looked ({len(done)})</h2></summary>"
        f'<div class="visit"><span data-last-visit="{_e(ov.last_visit or "")}">'
        f"{_e(note)}</span>"
        f'<button type="button" data-ov="visit" data-at="{fleet_views._stamp(now)}">'
        f"Mark as read</button></div>{rows}</details></section>"
    )
