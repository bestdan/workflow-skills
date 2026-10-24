"""The fleet view's Needs you page (`/needs`), the fleet instance's home page.

From dev_docs/designs/2026-10-23-fleet-view.md, "Principles" 1 and "Views"
item 1: one decision first, the rest collapsed, plain approvals batched. It
reads SQLite only, and links to where each item is acted on, never acting
itself.

WHAT IT COLLECTS, each keyed by its overlay item `(source, key)`:

- **Sessions waiting on me.** A session-state hook row (sessions.py,
  `source = 'claude-hook'`) whose last event is a turn end or a notification
  with no prompt since (`status_bucket = 'waiting'`), active within
  fleet_views.SESSION_IDLE_DAYS. A session quiet for longer is Stale's, and an
  empty spawn (never prompted) is Stale's too. And, with `claude_remote`
  enabled, a cloud session row (claude_remote.py) at `need_input` /
  `needs_action`. The wait starts at the hook row's `waiting_since`; a cloud
  row has none, so its `updated` stands in.
- **PRs in the review queue**: the `in_queue` rows of
  gauges.review_queue_rows, the reader the header gauge counts, so the page
  and the gauge agree. The wait starts when the PR was opened: when it entered
  the queue is not recorded.
- **Issues at `auto:human-review-needed`** (github_fleet's `issue` rows),
  waiting since they were opened, for the same reason.
- **Drain items marked "needs you"**: drain_items(), an empty hook point
  until a drain tracking issue is configured.

ORDER. Pinned items first, in the overlay's order (overlay.Overlay.pin_rank),
then the longest wait first. A snoozed item is left out and counted.

APPROVALS. A session waiting on a harness permission prompt (`need_input` on
a hook row) is a plain approval, and so is one whose brief ends in a yes/no
question about a routine step (approval_brief). Those go into one collapsed
Approvals group, out of the main list. No session source carries a post-turn
summary yet (`brief` is null on both), so today only permission prompts land
there. A brief-carrying source, or the `asked` flag proposed in
dev_docs/designs/2026-10-24-workdesk-borrowings.md, slots in at
approval_brief() and at _session(): `asked` would also let a turn that ended
without a question leave the main list.

FOCUS. While the overlay has a focus item, only that item's own needs show:
the item itself, and what is tied to it (related()). The rest are counted as
held and never listed.
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
.needs .approvals details.group > summary h2 { display:inline; }
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


# --- approvals ---------------------------------------------------------------

# A yes/no question about a routine step: "Open the PR?", "Commit and push?",
# "Shall I merge it?", "(y/n)". A question offering a choice ("A or B?") is a
# decision, not an approval. This is not snapshot.approval_shaped, which
# classifies the owner's reply ("y", "go ahead"), not the agent's question.
_YN = re.compile(r"\(?\[?\b(y/n|yes/no)\b\]?\)?\s*\??\s*$", re.IGNORECASE)
_ROUTINE = re.compile(
    r"^(?:ok(?:ay)?,?\s+)?(?:shall i|should i|ok(?:ay)? to|want me to|do you want me"
    r" to|may i|can i|ready to|go ahead|proceed|commit|push|open (?:the|a) pr|merge"
    r"|ship|continue)\b",
    re.IGNORECASE,
)
_ROUTINE_WORDS = 15


def approval_brief(brief: "str | None") -> bool:
    """True when a brief's last line is a plain yes/no approval question."""
    lines = [ln.strip() for ln in (brief or "").splitlines() if ln.strip()]
    if not lines:
        return False
    last = lines[-1]
    if _YN.search(last):
        return True
    words = last.split()
    return (
        last.endswith("?")
        and len(words) <= _ROUTINE_WORDS
        and " or " not in f" {last.lower()} "
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
    last = score.parse_ts(row.get("updated"))
    if last is None or now - last > timedelta(days=fleet_views.SESSION_IDLE_DAYS):
        return None
    need_input = row.get("status_category") == "need_input"
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
        kind="session",
        title=row.get("title") or f"session {row.get('session_id')}",
        since=score.parse_ts(row.get("waiting_since")) or last,
        url=CODE_URL,
        state="permission prompt" if need_input else "turn ended",
        where=" · ".join(where),
        brief=brief,
        approval=need_input or approval_brief(brief),
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
        kind="session",
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
        title=f"{row.get('repo') or ''}#{row.get('number')} {row.get('title') or ''}",
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
        title=f"{row.get('repo') or ''}#{row.get('number')} {row.get('title') or ''}",
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
    fleet = sync.fleet_enabled(cfg)
    for row in gauges.review_queue_rows(conn, fleet) or []:
        if row.get("in_queue"):
            out.append(_pr(row))
    if fleet:
        out.extend(_issue(row) for row in _rows(conn, github_fleet.SOURCE, "issue"))
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

    The item itself; anything in a focused design's repo (as Since you were
    away's design block); a session on a focused PR's head branch, or a PR on
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


def _link(n: Need) -> str:
    if n.url and n.url.startswith("https://"):
        return f'<a href="{_e(n.url)}">{_e(n.title)}</a>'
    return _e(n.title)


def _meta(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    pin = '<span class="pin">pinned</span> · ' if ov.is_pinned(n.item) else ""
    bits = [_e(n.state)] + ([_e(n.where)] if n.where else [])
    return f'{pin}<span class="wait">{_e(waited(n.since, now))}</span> · ' + " · ".join(
        bits
    )


def _attrs(n: Need, cls: str) -> str:
    since = n.since.strftime("%Y-%m-%dT%H:%M:%SZ") if n.since else ""
    return (
        f'class="card need{cls}" data-source="{_e(n.item[0])}"'
        f' data-key="{_e(n.item[1])}" data-kind="{_e(n.kind)}" data-since="{since}"'
    )


def _body(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    """The brief, or the fallback (title, age, state), then where to act."""
    if n.brief:
        brief = f'<div class="brief">{_e(n.brief)}</div>'
    else:
        opened = fleet_views.ago(n.since, now)
        brief = (
            f'<div class="brief" data-fallback="1">{_e(n.title)}\n'
            f"since {_e(opened)} · {_e(n.state)}</div>"
        )
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
        f"<div {_attrs(n, ' first')}>{_link(n)}"
        f'<div class="meta">{_meta(n, now, ov)}</div>{_body(n, now, ov)}</div>'
    )


def collapsed(n: Need, now: datetime, ov: overlay.Overlay) -> str:
    return (
        f"<details {_attrs(n, '')}><summary>{_e(n.title)}"
        f' <span class="meta">{_meta(n, now, ov)}</span></summary>'
        f"{_body(n, now, ov)}</details>"
    )


@dataclass
class Page:
    main: list[Need]
    approvals: list[Need]
    held: int
    snoozed: int
    focus: "Need | None"


def build(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime, ov: overlay.Overlay
) -> Page:
    needs = collect(conn, cfg, now)
    snoozed = [n for n in needs if ov.is_snoozed(n.item, now)]
    needs = [n for n in needs if not ov.is_snoozed(n.item, now)]
    focus = None
    held = 0
    if ov.focus is not None:
        focus = focus_need(conn, ov.focus)
        mine = [n for n in needs if related(n, focus)]
        held = len(needs) - len(mine)
        needs = mine
    ordered = order(needs, ov)
    return Page(
        main=[n for n in ordered if not n.approval],
        approvals=[n for n in ordered if n.approval],
        held=held,
        snoozed=len(snoozed),
        focus=focus,
    )


def render(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> str:
    ov = overlay.load(conn)
    p = build(conn, cfg, now, ov)
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
    empty = (
        "Nothing of the focus item needs you."
        if p.focus is not None
        else "Nothing needs you."
    )
    body = "".join(cards) if cards else f'<p class="empty">{_e(empty)}</p>'
    parts.append(
        f'<section class="lane" data-section="needs">'
        f"<h2>Needs you ({len(p.main)})</h2>{body}</section>"
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
    if p.snoozed:
        parts.append(
            f'<p class="note" data-snoozed="{p.snoozed}">{p.snoozed} snoozed.</p>'
        )
    return f'<div class="fleet needs">{"".join(parts)}</div>'
