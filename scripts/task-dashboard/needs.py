"""The fleet view's Needs you page (`/needs`): everything waiting on the owner.

Needs you is the full list. The fleet instance's home page is Now
(fleet_now.py), the short ranked queue drawn from this collection plus the
owner's own work.

Part of the fleet instance (dev_docs/task-dashboard.md, "Fleet views"): one
decision first, the rest collapsed, plain approvals batched. It
reads SQLite only, and links to where each item is acted on, never acting
itself.

WHAT IT COLLECTS, each keyed by its overlay item `(source, key)`:

- **Sessions waiting on me.** A session-state hook row (sessions.py,
  `source = 'claude-hook'`) at `need_input` with no prompt since: a harness
  permission prompt, or a turn that ended in a question (`asked`, labelled
  "asked (heuristic)": the hook's test is a trailing "?"). Active within
  fleet_views.SESSION_IDLE_DAYS. A turn that ended without a question is not
  listed, nor is one the hook could not judge (`asked` null, or a format-1
  hook): that is finished work, in the Finished lane below. A session quiet for
  longer is Stale's, and an empty spawn (never prompted) is Stale's too. A
  pending `AskUserQuestion` never reaches `Stop`, and the harness documents
  no notification for it, so it is not seen here. A hook session whose
  process is live in its host's registry (sessions.py) links to its Remote
  Control session and shows the registry's status; "idle: your turn" replaces
  "asked (heuristic)" when that status is idle. Without a Remote Control id
  the card links to the bare claude.ai/code and says so. With a transcript
  summary from its host (sessions.py `first_prompt`, `last_ask`), the card's
  title is the first prompt, with the old title as a small tag, and the last
  ask is quoted under it. An open PR in the mirror whose head is the
  session's branch (the transcript's, else the hook's), in its repo, is
  linked as "PR #N", summary or not. The transcript's own
  Remote Control id stands in for a missing registry one only when it is a
  `session_` id: the `cse_` form is not known to open. Without a summary the
  card is as it was. A session that has
  ended (sessions.py `ended`: its host sent a registry without it) is not
  listed: nobody can answer it in place, so it is in Finished, below. And, with `claude_remote`
  enabled, a cloud session row (claude_remote.py) at `need_input` /
  `needs_action`. The wait starts at the hook row's `waiting_since`; a cloud
  row has none, so its `updated` stands in.
- **PRs in the review queue**: the `in_queue` rows of
  gauges.review_queue_rows, the reader the header gauge counts, so the page
  and the gauge agree. The wait starts when the PR was opened: when it entered
  the queue is not recorded.
- **PRs from others on my repos** (github_fleet's `others` rows, bots
  included), stated as "PR from <author>". The queue rules are for agent PRs
  and do not apply to them, and the header gauge does not count them.
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
yes/no-shaped ask (approval_brief). Those go into the Approvals lane, a
tab of its own, out of the main list. No session source carries a post-turn
summary yet (`brief` is null on both), so today only permission prompts land
there, and an `asked` session stays in the main list: without the question's
text there is no telling a yes/no from a decision. A brief-carrying source
slots in at approval_brief().

NEW. A card whose wait began after the overlay's `last_visit` carries a `new`
badge, expanded, collapsed and approval cards alike; the order is unchanged.
With no visit recorded, nothing is new.

FINISHED SINCE YOU LOOKED. The last lane, a tab of its own, lists the sessions
whose last turn ended after `last_visit` without asking
(fleet_views.finished_sessions), one line each; with no visit recorded it
covers the last github_fleet.CLOSED_WINDOW_DAYS days. A turn that asked is in
the main list instead. An ended session is listed here too, when it was
active after that time or would otherwise wait on me, as "ended · resume with
`claude --resume <id>` in <cwd> on <host>"; its action carries that command as
argv (ended_need). The lane, and so its tab, is left out when it is empty. The include
filter, ignores and snoozes apply to it as to every other card.

MARK AS READ, in that lane, and "Mark all as read" under the Needs you
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
import snapshot
import sync
import view_model

_e = html.escape

CODE_URL = "https://claude.ai/code"
# A session card with no Remote Control id still links somewhere useful, and
# says why it is not the session itself.
NO_RC_LABEL = "open claude.ai/code (no Remote Control link)"
# An ended session's state (sessions.py `ended`), and the session ids its
# resume command may carry.
ENDED = "ended"
RESUME_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")

CSS = """
.needs .need { padding:6px 10px; }
.needs details.need > summary { cursor:pointer; list-style-position:inside; }
.needs .need.first { border-width:2px; }
.needs .brief { white-space:pre-line; margin:6px 0; }
.needs .wait { color:var(--fg); font-weight:600; }
.needs .new { font-size:11px; font-weight:600; color:var(--accent);
  border:1px solid var(--accent); border-radius:999px; padding:0 6px; }
.needs .visit { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
  margin:4px 0 8px; color:var(--muted); font-size:12px; }
.needs .visit button { font:inherit; padding:2px 10px; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); }
.fleet .ask { margin:4px 0; color:var(--fg); overflow-wrap:anywhere; }
.fleet .stag { font-size:11px; color:var(--muted); border:1px solid var(--line);
  border-radius:999px; padding:0 6px; }
"""


@dataclass
class Need:
    item: overlay.Item  # (source, key): the overlay identity
    kind: str  # session | pr | issue | drain
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
    # Where it is acted on, or the skill that resolves it: plain text, a
    # command in backticks (view_model.code).
    hint: str = ""
    ref: "str | None" = None  # `repo#N`, shown muted after the title
    row: "dict[str, Any] | None" = None  # the mirror row, for a PR or an issue
    host: "str | None" = None  # a session's machine
    path: "str | None" = None  # a session's working directory
    # A hook session's Remote Control id (sessions.py, from the registry).
    bridge_session_id: "str | None" = None
    # The command that resolves it, as argv: an ended session's resume.
    command: "tuple[str, ...] | None" = None
    # A hook session's transcript summary (sessions.py): the first prompt and
    # the end of the last assistant text; `tag` is the title the first prompt
    # replaced (the registry name, else the cwd and branch).
    first_prompt: "str | None" = None
    last_ask: "str | None" = None
    tag: "str | None" = None
    # The open PR whose head is the session's branch: {number, url, repo}.
    pr: "dict[str, Any] | None" = None

    @property
    def action(self) -> view_model.Action:
        return view_model.Action(self.hint, command=self.command, url=self.url)


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


def _waiting(row: dict[str, Any], now: datetime) -> bool:
    """Whether a hook session row's last turn waits on me, recently."""
    if row.get("status_bucket") != "waiting" or row.get("empty_spawn"):
        return False
    if row.get("status_category") != "need_input":
        return False
    last = score.parse_ts(row.get("updated"))
    return last is not None and now - last <= timedelta(
        days=fleet_views.SESSION_IDLE_DAYS
    )


PrIndex = dict[tuple[str, str], dict[str, Any]]


def open_prs(conn: sqlite3.Connection, cfg: dict[str, Any]) -> PrIndex:
    """The mirror's open PRs by (bare repo name, head branch): the `prs`
    table's and the review-queue rows'. Of two on one branch, the newest
    number wins. Empty unless `github.scope` is "owner", as the page shows no PRs then."""
    if not sync.fleet_enabled(cfg):
        return {}
    found: list[dict[str, Any]] = [
        {"url": url, "repo": repo, "number": number, "head_ref": head}
        for url, repo, number, head in conn.execute(
            "SELECT url, repo, number, head_ref FROM prs"
        )
    ]
    found.extend(gauges.review_queue_rows(conn, True) or [])
    out: PrIndex = {}
    for pr in found:
        name, head, number = (
            fleet_views._name(pr.get("repo")),
            pr.get("head_ref"),
            pr.get("number"),
        )
        url = pr.get("url")
        if not (name and head and type(number) is int and view_model.external(url)):
            continue
        key = (name, str(head))
        if key not in out or number > out[key]["number"]:
            out[key] = {"number": number, "url": url, "repo": pr.get("repo")}
    return out


def session_pr(
    prs: "PrIndex | None", repo: "str | None", branch: "str | None"
) -> "dict[str, Any] | None":
    """The open PR whose head is `branch` in `repo`, or None."""
    if not (prs and repo and branch):
        return None
    return prs.get((repo, branch))


def transcript_fields(
    row: dict[str, Any], title: str, prs: "PrIndex | None" = None
) -> dict[str, Any]:
    """A hook session's Need fields from its transcript summary (sessions.py):
    the first prompt as the title, with `title` as its tag; the last ask; the
    PR on its branch; and the Remote Control id, the registry's else the
    transcript's, only when it is linkable. A row with no summary keeps
    `title` and gets nothing else, so its card is unchanged."""
    first = (
        row.get("first_prompt") if isinstance(row.get("first_prompt"), str) else None
    )
    ask = row.get("last_ask") if isinstance(row.get("last_ask"), str) else None
    repo = fleet_views.repo_of_path(row.get("cwd"))
    return {
        "title": first or title,
        "tag": title if first else None,
        "first_prompt": first,
        "last_ask": ask,
        "pr": session_pr(prs, repo, row.get("branch")),
        "bridge_session_id": valid_bridge(row.get("bridge_session_id"))
        or valid_bridge(row.get("transcript_bridge_session_id")),
    }


def _session(
    row: dict[str, Any], now: datetime, prs: "PrIndex | None" = None
) -> "Need | None":
    """A hook session row waiting on me, or None. An ended session cannot be
    answered in place, so it is the Finished lane's (ended_need)."""
    if row.get("ended") or not _waiting(row, now):
        return None
    last = score.parse_ts(row.get("updated"))
    asked = bool(row.get("asked"))
    brief = row.get("brief")
    repo = fleet_views.repo_of_path(row.get("cwd"))
    extra = transcript_fields(
        row, row.get("title") or f"session {row.get('session_id')}", prs
    )
    bridge = extra.pop("bridge_session_id")
    return Need(
        item=(sessions.SOURCE, str(row.get("key") or row.get("session_id"))),
        kind=sessions.KIND,
        since=score.parse_ts(row.get("waiting_since")) or last,
        url=session_url(bridge),
        state=session_state(asked, row.get("live_status")),
        where=session_where(row, repo),
        brief=brief,
        approval=not asked or approval_brief(brief),
        repo=repo,
        branch=row.get("branch"),
        hint="answer it in the session",
        host=row.get("host"),
        path=row.get("cwd"),
        bridge_session_id=bridge,
        **extra,
    )


def valid_bridge(bridge: Any) -> "str | None":
    """`bridge` when it is a Remote Control session id, else None.

    It comes from another machine's file, and a `/`, `?` or `#` in it would
    move the link, so anything but `session_<letters, digits, _ or ->` is
    treated as no id at all.
    """
    if isinstance(bridge, str) and snapshot.BRIDGE_ID.fullmatch(bridge):
        return bridge
    return None


def session_url(bridge: "str | None") -> str:
    """The Remote Control session's own page, else the bare claude.ai/code."""
    valid = valid_bridge(bridge)
    return f"{CODE_URL}/{valid}" if valid else CODE_URL


def session_state(asked: bool, live: "str | None") -> str:
    """What a waiting hook session waits on, with the registry's live status.

    A turn that ended in a question while the process is idle is the owner's
    turn, whatever the "?" heuristic would call it. A permission prompt keeps
    its own label: idle is not what blocks it.
    """
    if asked and live == "idle":
        return "idle: your turn"
    state = "asked (heuristic)" if asked else "permission prompt"
    return f"{state} · {live}" if live else state


def session_where(row: dict[str, Any], repo: "str | None") -> str:
    """`on <host> · <repo>: <branch> · tmux: <target>`, as plain text."""
    where = [f"on {row.get('host') or 'an unknown host'}"]
    if repo or row.get("cwd"):
        where.append(
            (repo or row.get("cwd") or "")
            + (f": {row['branch']}" if row.get("branch") else "")
        )
    if row.get("tmux"):
        where.append(f"tmux: {row['tmux']}")
    return " · ".join(where)


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
    role = row.get("role")
    if role == "others":
        bits.insert(0, f"PR from {row.get('author') or 'someone else'}")
        if row.get("author_is_bot"):
            bits.insert(1, "bot")
    else:
        bits.append("your PR" if role == "author" else "review requested")
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
        row=row,
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
        hint="decide it, then `/promote-tasks`",
        row=row,
    )


def drain_items(conn: sqlite3.Connection, cfg: dict[str, Any]) -> list[Need]:
    """The monthly drain's "needs you" items. Empty: no drain issue is
    configured anywhere yet, and this is where its reader plugs in."""
    return []


def collect(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> list[Need]:
    """Everything waiting on me, unordered and unfiltered."""
    out: list[Need] = []
    if sessions.enabled(cfg):
        prs = open_prs(conn, cfg)
        for row in _rows(conn, sessions.SOURCE, sessions.KIND):
            need = _session(row, now, prs)
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
        if row.get("in_queue") or row.get("role") == "others":
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
    """The `.title`, linked to `url` when given, then the muted `.ref`, or a
    session's tag (its old title, when its first prompt is the title)."""
    if n.ref is not None:
        return fleet_views.title_ref(url, n.title, n.ref)
    tag = f' <span class="stag">{_e(n.tag)}</span>' if n.tag else ""
    return fleet_views._link(url, n.title) + tag


# The leading word of a cut ask, with the space after it: the cut fell
# somewhere in that word, or just before it, which the text cannot tell apart.
_PARTIAL_WORD = re.compile(r"\S*\s+(?=\S)")


def ask_excerpt(ask: str) -> str:
    """`ask` as a card quotes it. The collector keeps the last ASK_CHARS, so
    one that long was cut: it starts at its next word, after a `…`. With no
    space to cut at, it is kept whole."""
    if len(ask) < snapshot.ASK_CHARS:
        return ask
    m = _PARTIAL_WORD.match(ask)
    return "…" + (ask[m.end() :] if m else ask)


def ask_html(n: Need) -> str:
    """A session's last ask, quoted, or nothing (ask_excerpt)."""
    if not n.last_ask:
        return ""
    return f'<div class="ask">› {_e(ask_excerpt(n.last_ask))}</div>'


def pr_link(n: Need) -> str:
    """`PR #N`, linked to the open PR on a session's branch, or nothing."""
    if not n.pr:
        return ""
    return view_model.link(n.pr["url"], f"PR #{n.pr['number']}")


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
    link = (
        view_model.link(n.url, open_label(n))
        if n.url and n.url.startswith("https://")
        else ""
    )
    act = " · ".join(x for x in (link, pr_link(n), view_model.code(n.hint)) if x)
    act = f'<div class="act">→ {act}</div>' if act else ""
    return (
        ask_html(n)
        + brief
        + act
        + overlay.card_controls(n.item, ov, now, reorder=False)
    )


def open_label(n: Need, other: "str | None" = None) -> str:
    """A card's "open" link text. The bare claude.ai/code says it is not the
    session; a session's own page is "open the session"; anything else is
    `other`, else "open the <kind>"."""
    if n.url == CODE_URL:
        return NO_RC_LABEL
    if n.kind == sessions.KIND:
        return "open the session"
    what = {"pr": "the PR", "issue": "the issue"}
    return other or f"open {what.get(n.kind, 'it')}"


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
    """One line in the Finished lane: title or branch, host, the turn's end.
    An ended session says so and how to resume it, with no link."""
    ago = _e(fleet_views.ago(n.since, now))
    controls = overlay.card_controls(n.item, ov, now, reorder=False, pin=False)
    if n.state == ENDED:
        meta = f"{_e(n.where)} · last active {ago}"
        resume = view_model.code(n.hint) if n.hint else ""
        bits = [ENDED, pr_link(n), resume]
    else:
        meta = f"{_e(n.where)} · turn ended {ago}"
        link = view_model.link(n.url, open_label(n)) if n.url else ""
        bits = [link, pr_link(n)]
    act = " · ".join(x for x in bits if x)
    act = f'<div class="act">→ {act}</div>' if act else ""
    return (
        f"<details {_attrs(n, ' done')}><summary>{_title(n, None)}"
        f' <span class="meta">{meta}</span></summary>{ask_html(n)}{act}{controls}'
        "</details>"
    )


def finished(
    conn: sqlite3.Connection, cfg: dict[str, Any], since: datetime, now: datetime
) -> list[Need]:
    """The sessions that finished a turn after `since`, then the ended ones
    (ended_need), as Needs, latest first."""
    out = []
    prs = open_prs(conn, cfg)
    mirror = {str(r.get("key")): r for r in _rows(conn, sessions.SOURCE, sessions.KIND)}
    for row in mirror.values():
        need = ended_need(row, since, now, prs)
        if need is not None:
            out.append(need)
    for s in fleet_views.finished_sessions(fleet_views.load(conn, cfg), since):
        row = mirror.get(s.session_id) or {}
        if row.get("ended"):
            continue
        extra = transcript_fields(
            row,
            row.get("live_name")
            or sessions._label(s.cwd, s.branch)
            or f"session {s.session_id}",
            prs,
        )
        bridge = extra.pop("bridge_session_id")
        out.append(
            Need(
                item=(sessions.SOURCE, s.session_id),
                kind=sessions.KIND,
                since=s.last,
                url=session_url(bridge) if bridge else None,
                bridge_session_id=bridge,
                state="turn ended",
                where=f"on {s.host}",
                repo=s.repo,
                branch=s.branch,
                host=s.host,
                path=s.cwd,
                **extra,
            )
        )
    out.sort(key=lambda n: (n.since is not None, n.since or now), reverse=True)
    return out


def ended_need(
    row: dict[str, Any],
    since: datetime,
    now: datetime,
    prs: "PrIndex | None" = None,
) -> "Need | None":
    """An ended hook session (sessions.py `ended`) for the Finished lane, or
    None: one active after `since`, or one that would otherwise wait on me.

    Its process is gone, so there is no Remote Control link, whatever id its
    transcript names: the action is `claude --resume <id>`, run in its
    directory on its host. A session id that is not plain letters, digits,
    `_` and `-` gets no command, since it came from another machine and the
    command is meant to be pasted. Its transcript summary is shown as a live
    session's is (transcript_fields).
    """
    if not row.get("ended") or row.get("empty_spawn"):
        return None
    last = score.parse_ts(row.get("updated"))
    if last is None or not (last > since or _waiting(row, now)):
        return None
    sid = str(row.get("key") or row.get("session_id"))
    host = row.get("host") or "an unknown host"
    cwd = row.get("cwd")
    resumable = RESUME_ID.fullmatch(sid) is not None
    hint = ""
    if resumable:
        where = f" in {cwd}" if cwd else ""
        hint = f"resume with `claude --resume {sid}`{where} on {host}"
    extra = transcript_fields(row, row.get("title") or f"session {sid}", prs)
    extra.pop("bridge_session_id")
    return Need(
        item=(sessions.SOURCE, sid),
        kind=sessions.KIND,
        since=last,
        url=None,
        state=ENDED,
        where=f"on {host}",
        repo=fleet_views.repo_of_path(cwd),
        branch=row.get("branch"),
        hint=hint,
        command=("claude", "--resume", sid) if resumable else None,
        host=row.get("host"),
        path=cwd,
        **extra,
    )


def since_looked(ov: overlay.Overlay, now: datetime) -> datetime:
    """Where the Finished lane starts: the last visit, else the closed window."""
    last = score.parse_ts(ov.last_visit)
    return last or now - timedelta(days=github_fleet.CLOSED_WINDOW_DAYS)


@dataclass
class Page:
    main: list[Need]
    approvals: list[Need]
    snoozed: int
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
    done = finished(conn, cfg, since_looked(ov, now), now)
    done = [n for n in done if n.item not in ov.ignored]
    # Then the include filter: a need it switches off is neither snoozed nor
    # listed, so no count on the page reflects it.
    if inc is not None:
        needs = [n for n in needs if inc.shows(n.kind)]
        done = [n for n in done if inc.shows(n.kind)]
    snoozed = [n for n in needs + done if ov.is_snoozed(n.item, now)]
    needs = [n for n in needs if not ov.is_snoozed(n.item, now)]
    done = [n for n in done if not ov.is_snoozed(n.item, now)]
    ordered = order(needs, ov)
    return Page(
        main=[n for n in ordered if not n.approval],
        approvals=[n for n in ordered if n.approval],
        snoozed=len(snoozed),
        finished=done,
    )


def lanes(p: Page, inc: "include.Include | None" = None) -> list[view_model.Lane]:
    """The page's lanes in order: Needs you, then Approvals and Finished,
    each only when it has rows."""
    if p.approvals:
        empty = "No decisions waiting; only the plain approvals in Approvals."
    elif inc is not None and not inc.everything:
        empty = "Nothing included by the filter needs you."
    else:
        empty = "Nothing needs you."
    out = [view_model.Lane("needs", "Needs you", p.main, empty)]
    if p.approvals:
        out.append(view_model.Lane("approvals", "Approvals", p.approvals))
    if p.finished:
        title = "Finished since you looked"
        out.append(view_model.Lane("finished", title, p.finished, tab="Finished"))
    return out


def render(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> str:
    ov = overlay.load(conn)
    p = build(conn, cfg, now, ov, inc)
    parts = []
    if not sync.fleet_enabled(cfg):
        parts.append(
            '<p class="note">The owner-wide GitHub read is off (github.enabled is false, or github.scope is not "owner"), so no PRs or issues are'
            " shown here.</p>"
        )
    for lane in lanes(p, inc):
        if lane.id == "needs":
            cards = [
                expanded(n, now, ov) if i == 0 else collapsed(n, now, ov)
                for i, n in enumerate(lane.items)
            ]
            body = "".join(cards) or f'<p class="empty">{_e(lane.empty)}</p>'
            # Beside the heading, not in it: the page's text filter rewrites
            # the h2.
            mark_all = (
                '<div class="visit"><button type="button" data-ov="visit"'
                f' data-at="{fleet_views._stamp(now)}" title="Clear the new badges and the Finished group">'
                "Mark all as read</button></div>"
            )
            parts.append(
                f'<section class="lane" data-section="needs">'
                f"<h2>{lane.title} ({len(lane.items)})</h2>{mark_all}{body}</section>"
            )
        elif lane.id == "approvals":
            # A lane, so a tab of its own (lane_tabs.py), which opens first
            # when nothing else waits: it is then the first lane with rows.
            rows = "".join(collapsed(n, now, ov) for n in lane.items)
            parts.append(
                '<section class="lane approvals" data-section="approvals">'
                f"<h2>{lane.title} ({len(lane.items)})</h2>"
                '<p class="note">Plain approvals, batched: answer each in its'
                f" session.</p>{rows}</section>"
            )
        else:
            parts.append(finished_group(lane.items, now, ov))
    if p.snoozed:
        parts.append(
            f'<p class="note" data-snoozed="{p.snoozed}">{p.snoozed} snoozed.</p>'
        )
    return f'<div class="fleet needs">{"".join(parts)}</div>'


def finished_group(done: list[Need], now: datetime, ov: overlay.Overlay) -> str:
    """The "Finished since you looked (N)" lane, with Mark as read."""
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
        '<section class="lane finished" data-section="finished" data-tab="Finished">'
        f"<h2>Finished since you looked ({len(done)})</h2>"
        f'<div class="visit"><span data-last-visit="{_e(ov.last_visit or "")}">'
        f"{_e(note)}</span>"
        f'<button type="button" data-ov="visit" data-at="{fleet_views._stamp(now)}">'
        f"Mark as read</button></div>{rows}</section>"
    )
