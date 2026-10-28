"""The fleet view's Stale page, and the fleet rows Needs you reads.

From dev_docs/designs/2026-10-23-fleet-view.md, "Views" items 2 and 3. It
reads SQLite only: the `source = 'github'` rows github_fleet.py writes, the
`source = 'machine:<host>'` rows and the `session_events` / `history_entries`
deltas snapshot_adapter.py writes, and the overlay.

FINISHED SESSIONS (`finished_sessions`) are the sessions whose last turn ended
after a time without asking. Needs you (needs.py) lists them, after the
overlay's `last_visit`, in its "Finished since you looked" group. The `/away`
route redirects to `/needs`, where that group is.

STALE (`/stale`, under Jira's own stale lane on a fleet instance) closes by
evidence, never by age. Each row names its evidence and the action that
resolves it, and goes in one of two lanes:

- **Close** has evidence: a worktree whose branch landed (`branch-remove.sh
  --check`) and whose merged PR is mirrored, a handoff past its `expires` (a
  date, or "when #N merges" naming one #N, with #N merged), a session that
  never had a prompt (an empty spawn), or an idle session in such a worktree.
- **Check** has none: an idle session, a landed worktree with no mirrored
  merged PR or with uncommitted or unpushed work, a handoff whose expiry
  cannot be evaluated (a compound condition included), or a PR or issue open
  past the threshold.

Age orders each lane, oldest first, and is shown only as context. An ignored
item (overlay.py) leaves the lanes: a worktree or handoff under its mirror
key, a session, PR or issue under the key Needs you ignores it by.

Each row is a StaleItem of plain-text fields, its action structured
(view_model.Action); the card is rendered from those fields, and
`dashboard.py view stale` (agent_view.py) reads the same lanes as JSON.

A worktree row kept from a failed scan (snapshot_adapter.py, `kept`) is never
evidence: its landed verdict is as old as the snapshot that last saw it. It
goes under Check, naming the failure and how old the row is.
"""

import html
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

import github_fleet
import include
import lane_tabs
import overlay
import score
import sessions
import sync
import view_model

# Tabs that exist only on a fleet instance (views.nav hides them elsewhere).
ONLY = {"/needs", "/prs", "/issues", "/workstreams"}

SESSION_IDLE_DAYS = 3  # a session with no event for this long is idle
SESSION_FORGET_DAYS = 30  # and one quiet for this long has left the list
OPEN_ITEM_DAYS = 14  # a PR, issue or handoff older than this goes under Check

_e = html.escape

CSS = """
.fleet .card .why { color:var(--fg); font-size:12px; }
.fleet .card .act { font-size:12px; }
.fleet .card .act code { white-space:pre-wrap; word-break:break-all; }
"""


def enabled(cfg: dict[str, Any]) -> bool:
    """A fleet instance: it ingests snapshots from other hosts. The tabs, the
    home page and the Stale lanes ask fleet_pages() instead."""
    machines = (cfg.get("ingest") or {}).get("machines") or {}
    return bool(machines)


def fleet_pages(cfg: dict[str, Any]) -> bool:
    """The fleet's tabs (ONLY), Now as the home page, and the Stale lanes:
    on a fleet, or with GitHub's owner scope, which mirrors what they show.
    Kept apart from enabled() while the tabs question is open ("Not decided
    here" in dev_docs/designs/2026-10-27-shared-now-ranker.md)."""
    return sync.fleet_enabled(cfg) or enabled(cfg)


# --- data --------------------------------------------------------------------


@dataclass
class Session:
    host: str
    session_id: str
    first: datetime
    last: datetime
    last_event: "str | None" = None
    notification: "str | None" = None
    cwd: "str | None" = None
    branch: "str | None" = None
    prompts: int = 0
    # The last Stop since the last prompt ended in a question (sessions.py).
    asked: bool = False

    @property
    def repo(self) -> "str | None":
        return repo_of_path(self.cwd)


@dataclass
class Fleet:
    """One read of the fleet rows the two pages need.

    Its `issue` rows are the human-review set only (github_fleet.needs_human):
    the mirror holds every open issue, which only the Issues tab lists.
    """

    github: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    worktrees: list[dict[str, Any]] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)

    def merged_pr(self, repo: "str | None", number: int) -> "dict[str, Any] | None":
        """The mirrored merged PR #number in a repo named `repo` (any owner)."""
        for pr in self.github.get("closed_pr", []):
            if pr.get("merged") and pr.get("number") == number:
                if repo is None or _name(pr.get("repo")) == repo:
                    return pr
        return None

    def merged_head(self, repo: str, branch: "str | None") -> "dict[str, Any] | None":
        """The mirrored merged PR whose head branch is `branch` in `repo`."""
        if not branch:
            return None
        found = [
            pr
            for pr in self.github.get("closed_pr", [])
            if pr.get("merged")
            and pr.get("head_ref") == branch
            and _name(pr.get("repo")) == repo
        ]
        return max(found, key=lambda pr: pr.get("closed_at") or "", default=None)


def _name(repo: "str | None") -> "str | None":
    """`owner/name` -> `name`; a bare name is returned as is."""
    return repo.rsplit("/", 1)[-1] if repo else None


def repo_of_path(path: "str | None") -> "str | None":
    """The repo a path is in, by the layout the snapshot covers:
    `.../worktrees/<repo>/...`, else `.../src/<repo>/...`."""
    if not path:
        return None
    parts = PurePosixPath(path).parts
    for anchor in ("worktrees", "src"):
        if anchor in parts:
            i = len(parts) - 1 - parts[::-1].index(anchor)
            if i + 1 < len(parts):
                return parts[i + 1]
    return None


def _raw(value: "str | None") -> dict[str, Any]:
    try:
        obj = json.loads(value or "{}")
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def load(conn: sqlite3.Connection, cfg: "dict[str, Any] | None" = None) -> Fleet:
    """The fleet rows. With `cfg` and `github.scope` not "owner", no github rows: the
    ones left from an earlier run are not current (as gauges.review_queue)."""
    f = Fleet()
    rows = (
        conn.execute(
            "SELECT kind, raw_json FROM issues WHERE source = ? ORDER BY key",
            (github_fleet.SOURCE,),
        )
        if cfg is None or sync.fleet_enabled(cfg)
        else []
    )
    for kind, raw in rows:
        row = _raw(raw)
        if kind != "issue" or github_fleet.needs_human(row):
            f.github.setdefault(kind, []).append(row)
    for source, kind, raw, synced in conn.execute(
        "SELECT source, kind, raw_json, synced_at FROM issues"
        " WHERE source LIKE 'machine:%' AND kind IN ('worktree', 'handoff')"
        " ORDER BY source, key"
    ):
        row = {**_raw(raw), "host": source.split(":", 1)[1], "synced_at": synced}
        (f.worktrees if kind == "worktree" else f.handoffs).append(row)
    sessions: dict[tuple[str, str], Session] = {}
    for host, sid, ts, event, cwd, branch, note, asked in conn.execute(
        "SELECT host, session_id, ts, event, cwd, branch, notification, asked"
        " FROM session_events WHERE session_id IS NOT NULL ORDER BY ts, id"
    ):
        at = score.parse_ts(ts)
        if at is None:
            continue
        s = sessions.get((host, sid))
        if s is None:
            s = sessions[(host, sid)] = Session(host, sid, at, at)
        s.first, s.last = min(s.first, at), max(s.last, at)
        if at >= s.last:
            s.last_event = event
            s.notification = note if event == "Notification" else None
        s.cwd = cwd or s.cwd
        s.branch = branch or s.branch
        if event == "UserPromptSubmit":
            s.prompts += 1
            s.asked = False
        elif event == "Stop":
            s.asked = asked == 1
        elif event == "Notification" and note == "permission_prompt":
            s.asked = False
    for host, sid, n in conn.execute(
        "SELECT host, session_id, COUNT(*) FROM history_entries"
        " WHERE session_id IS NOT NULL GROUP BY host, session_id"
    ):
        if (host, sid) in sessions:
            sessions[(host, sid)].prompts += n
    f.sessions = list(sessions.values())
    return f


# --- formatting --------------------------------------------------------------


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(then: "datetime | None", now: datetime) -> str:
    if then is None:
        return "at an unknown time"
    s = (now - then).total_seconds()
    if s < 60:
        return "just now"
    if s < 90 * 60:
        return f"{round(s / 60)}m ago"
    if s < 36 * 3600:
        return f"{round(s / 3600)}h ago"
    return f"{round(s / 86400)}d ago"


def _link(url: "str | None", text: str) -> str:
    """A card's title, as a `.title` the page's filter box searches."""
    if url and url.startswith("https://"):
        return view_model.link(url, text, "title")
    return f'<span class="title">{_e(text)}</span>'


def ref_suffix(ref: str) -> str:
    """The muted ` (ref)` that follows a card's title, as views.card_title's."""
    return f' <span class="ref">({_e(ref)})</span>'


def title_ref(url: "str | None", title: "str | None", ref: str) -> str:
    """The title, linked, then the muted ref; the ref alone when untitled."""
    return _link(url, title or ref) + (ref_suffix(ref) if title else "")


def _pr_merged(pr: dict[str, Any], at: "datetime | None", now: datetime) -> str:
    """`title (#N) merged <ago>`, or `PR #N merged <ago>` when untitled."""
    n = pr.get("number")
    title = pr.get("title")
    head = f"{title} (#{n})" if title else f"PR #{n}"
    return f"{head} merged {ago(at, now)}"


def _card(head: str, meta: str = "", why: str = "", act: str = "", **data: str) -> str:
    attrs = "".join(f' data-{k}="{_e(v)}"' for k, v in data.items())
    parts = [head]
    if meta:
        parts.append(f'<div class="meta">{meta}</div>')
    if why:
        parts.append(f'<div class="why">{why}</div>')
    if act:
        parts.append(f'<div class="act">→ {act}</div>')
    return f'<div class="card"{attrs}>{"".join(parts)}</div>'


def _section(
    name: str, title: str, cards: list[str], empty: str, tab: "str | None" = None
) -> str:
    """A lane; `tab` is a short label for its tab (lane_tabs.py)."""
    body = "".join(cards) if cards else f'<p class="empty">{_e(empty)}</p>'
    return (
        f'<section class="lane" data-section="{_e(name)}"{lane_tabs.tab_attr(tab)}>'
        f"<h2>{_e(title)} ({len(cards)})</h2>{body}</section>"
    )


def _session_where(s: Session) -> str:
    """Where a session ran, as plain text: the card escapes it."""
    bits = [f"on {s.host}"]
    if s.repo:
        bits.append(s.repo + (f": {s.branch}" if s.branch else ""))
    elif s.cwd:
        bits.append(s.cwd)
    return " · ".join(bits)


# --- finished sessions, for Needs you ----------------------------------------


def finished_sessions(f: Fleet, since: datetime) -> list[Session]:
    """Sessions whose last turn ended after `since` without asking, latest
    first: Needs you's "Finished since you looked" group.

    A turn that asked is not finished (it waits on me, and Needs you lists
    it), and neither is a session whose last event is a notification.
    """
    out = [
        s
        for s in f.sessions
        if s.last > since and s.last_event == "Stop" and not s.asked
    ]
    out.sort(key=lambda s: s.last, reverse=True)
    return out


# --- Stale, with evidence ----------------------------------------------------


# The kinds a Stale row can be, for the include filter's "switched off" note.
STALE_KINDS = ("worktree", "handoff", "session", "pr", "issue")


@dataclass
class StaleItem:
    """One Stale row, as plain text: the card is rendered from these fields."""

    lane: str  # close | check
    at: "datetime | None"  # orders the lane, oldest first; never a reason
    kind: str  # the row's mirror kind, for the include filter
    item: overlay.Item  # (source, key): the overlay identity
    title: str
    meta: str
    why: str  # the line the card shows: the evidence, and any risks
    action: view_model.Action
    evidence: str = ""  # the evidence alone, without the risks
    risks: list[str] = field(default_factory=list)
    url: "str | None" = None
    ref: "str | None" = None  # `repo#N`, shown muted after the title
    host: "str | None" = None
    path: "str | None" = None
    branch: "str | None" = None

    @property
    def html(self) -> str:
        head = (
            title_ref(self.url, self.title, self.ref)
            if self.ref is not None
            else _link(self.url, self.title)
        )
        return _card(head, _e(self.meta), _e(self.why), self.action.html())


# "when #N merges" is a merge condition only when it names exactly one #N. A
# compound one ("when #148 and #150 merge") is not met by its first PR alone,
# and evaluating the rest is beyond a regex, so it goes under Check.
_REF = re.compile(r"#(\d+)\b")
_MERGE = re.compile(r"\bmerge", re.IGNORECASE)


def _expiry(
    value: "str | None", repo: "str | None", f: Fleet, now: datetime
) -> "tuple[bool | None, str]":
    """(expired?, evidence). None when `expires` cannot be evaluated here.

    A date expires at the end of that local day. "when #N merges", naming
    exactly one #N, expires once PR #N of the handoff's repo is mirrored as
    merged; until then it is not known to have expired, since the closed-PR
    window may simply not reach it. Any other value cannot be evaluated.
    """
    if not value:
        return None, "no expires"
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        day = None
    if day is not None:
        end = score.local_day_start(day + timedelta(days=1))
        if now >= end:
            return True, f"expires {value}: passed {ago(end, now)}"
        return False, ""
    refs = set(_REF.findall(value))
    if len(refs) == 1 and _MERGE.search(value):
        (n,) = refs
        pr = f.merged_pr(repo, int(n))
        if pr is not None:
            at = score.parse_ts(pr.get("merged_at") or pr.get("closed_at"))
            return True, f"expires `{value}`: {_pr_merged(pr, at, now)}"
        return None, f"expires `{value}`: PR #{n} not seen merged"
    return None, f"expires `{value}` cannot be checked here"


def _active_in(f: Fleet, host: str, path: str, now: datetime) -> "Session | None":
    """A session on `host` whose cwd is in `path` and was active lately."""
    cutoff = now - timedelta(days=SESSION_IDLE_DAYS)
    prefix = path.rstrip("/") + "/"
    for s in f.sessions:
        cwd = (s.cwd or "").rstrip("/") + "/"
        if s.host == host and cwd.startswith(prefix) and s.last > cutoff:
            return s
    return None


@dataclass
class Landed:
    """Why a worktree's branch counts as landed, and when its PR merged."""

    pr: "dict[str, Any] | None"
    reason: str
    at: "datetime | None"
    kept: bool = False  # from a failed scan: not evidence

    def evidence(self, now: datetime) -> str:
        if self.pr is not None:
            return _pr_merged(self.pr, self.at, now)
        return self.reason


def _landed_worktrees(f: Fleet) -> "dict[tuple[str, str], Landed]":
    """(host, path) -> Landed, for every worktree the teardown gate calls landed.

    A checkout on `main` or `master` is skipped: the gate calls a branch whose
    tip is on origin/<default> landed, which the default branch always is.
    """
    out: dict[tuple[str, str], Landed] = {}
    for wt in f.worktrees:
        verdict = wt.get("landed") or {}
        branch = wt.get("branch")
        if verdict.get("state") != "landed" or branch in (None, "main", "master"):
            continue
        pr = f.merged_head(wt.get("repo") or "", branch)
        at = score.parse_ts(pr.get("merged_at") or pr.get("closed_at")) if pr else None
        reason = str(verdict.get("reason") or "")
        out[(wt["host"], wt["path"])] = Landed(pr, reason, at, bool(wt.get("kept")))
    return out


def kept_note(wt: dict[str, Any], now: datetime) -> str:
    """Why a kept row is not current: the failed scan, and when it was seen."""
    kept = wt.get("kept") or {}
    failed = score.parse_ts(kept.get("taken_at"))
    seen = score.parse_ts(wt.get("synced_at"))
    return (
        f"kept from a failed scan ({kept.get('step')} failed {ago(failed, now)});"
        f" last listed {ago(seen, now)}"
    )


def stale_items(
    f: Fleet, now: datetime, ignored: "set[overlay.Item] | None" = None
) -> list[StaleItem]:
    """The Stale lanes' items. `ignored` (the overlay's) drops the sessions,
    PRs and issues the user ignored on Needs, under the same overlay keys, and
    a worktree or handoff ignored under its mirror key, kept from a failed
    scan or not."""
    ignored = ignored or set()
    out: list[StaleItem] = []
    landed = _landed_worktrees(f)

    for wt in f.worktrees:
        key = (wt["host"], wt["path"])
        item = (f"machine:{wt['host']}", f"worktree:{wt['path']}")
        if key not in landed or item in ignored:
            continue
        evidence, at = landed[key].evidence(now), landed[key].at
        risks = []
        if wt.get("dirty"):
            risks.append("uncommitted changes")
        if wt.get("unpushed"):
            risks.append(f"{wt['unpushed']} unpushed commit(s)")
        active = _active_in(f, wt["host"], wt["path"], now)
        if active is not None:
            risks.append(f"session {active.session_id} used it {ago(active.last, now)}")
        if landed[key].kept:
            lane = "check"
            why = f"{kept_note(wt, now)}; when last seen: {evidence}"
            if risks:
                why += f", and it had {', '.join(risks)}"
            review = "wait for a scan that lists it, or check the host"
        elif landed[key].pr is None:
            # The gate also calls a branch landed when its tip is on
            # origin/<default>, which a fresh worktree with no commits is.
            # Without a merged PR for the branch that is not evidence enough.
            lane = "check"
            why = f"no merged PR found for its branch; the gate says: {evidence}"
            if risks:
                why += f"; it has {', '.join(risks)}"
            review = "check it has nothing left to do"
        elif risks:
            lane = "check"
            why = f"{evidence}, but it has {', '.join(risks)}"
            review = "look before removing"
        else:
            lane, why, review = "close", evidence, ""
        out.append(
            StaleItem(
                lane, at, "worktree", item,
                title=f"worktree {wt['path']}",
                meta=f"on {wt['host']} · {wt.get('repo')}: {wt.get('branch')}",
                why=why,
                action=view_model.Action(review, ("worktree-remove.sh", wt["path"])),
                evidence=evidence,
                risks=risks,
                host=wt["host"],
                path=wt["path"],
                branch=wt.get("branch"),
            )
        )  # fmt: skip

    for ho in f.handoffs:
        path = ho.get("path") or ""
        item = (f"machine:{ho['host']}", f"handoff:{path}")
        if item in ignored:
            continue
        repo = PurePosixPath(path).parent.parent.name or None
        at = score.parse_ts(ho.get("created")) or score.parse_ts(ho.get("mtime"))
        expired, evidence = _expiry(ho.get("expires"), repo, f, now)
        if expired:
            lane, why = "close", evidence
            action = view_model.Action("read it", ("rm", path))
        elif (
            expired is None
            and at is not None
            and now - at > timedelta(days=OPEN_ITEM_DAYS)
        ):
            lane, why = "check", f"no evidence: {evidence}"
            action = view_model.Action("read it: still needed, or delete it")
        else:
            continue
        out.append(
            StaleItem(
                lane, at, "handoff", item,
                title=f"handoff {ho.get('aim') or PurePosixPath(path).name}",
                meta=f"on {ho['host']} · {path}",
                why=why,
                action=action,
                evidence=evidence,
                host=ho["host"],
                path=path,
            )
        )  # fmt: skip

    idle = now - timedelta(days=SESSION_IDLE_DAYS)
    forget = now - timedelta(days=SESSION_FORGET_DAYS)
    for s in f.sessions:
        if not forget < s.last <= idle or (sessions.SOURCE, s.session_id) in ignored:
            continue
        in_landed = next(
            (
                ev
                for (host, path), ev in landed.items()
                if host == s.host
                and ev.pr is not None
                and not ev.kept
                and ((s.cwd or "") + "/").startswith(path.rstrip("/") + "/")
            ),
            None,
        )
        if s.prompts == 0:
            lane = "close"
            why = f"no prompt was ever sent (first event {ago(s.first, now)})"
            review = "close the session: an empty spawn"
        elif in_landed is not None:
            lane = "close"
            why = "its worktree landed: " + in_landed.evidence(now)
            review = "close the session"
        else:
            lane = "check"
            why = "no evidence: idle, with nothing that says its work is done"
            review = "resume it, or close it"
        out.append(
            StaleItem(
                lane, s.last, "session", (sessions.SOURCE, s.session_id),
                title=f"session {s.session_id}",
                meta=f"{_session_where(s)} · last event {ago(s.last, now)}",
                why=why,
                action=view_model.Action(review),
                evidence=why,
                host=s.host,
                path=s.cwd,
                branch=s.branch,
            )
        )  # fmt: skip

    old = now - timedelta(days=OPEN_ITEM_DAYS)
    for kind, review in (
        ("pr", "review it: merge, close, or say what it waits on"),
        ("issue", "decide it, or say what it waits on"),
    ):
        for row in f.github.get(kind, []):
            at = score.parse_ts(row.get("created"))
            item = (github_fleet.SOURCE, str(row.get("key") or row.get("url")))
            if at is None or at > old or item in ignored:
                continue
            url = row.get("url")
            out.append(
                StaleItem(
                    "check", at, kind, item,
                    title=row.get("title") or "",
                    ref=f"{row.get('repo') or ''}#{row.get('number')}",
                    url=url,
                    meta=f"opened {ago(at, now)}",
                    why="no evidence: nothing mirrored says it can close",
                    action=view_model.Action(review, url=url),
                )
            )  # fmt: skip

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda i: (i.at is None, i.at or epoch))
    return out


STALE_LANES = (
    ("close", "Close: evidence found", "Nothing has evidence to close.", "Close"),
    (
        "check",
        "Check: no evidence, so not a reason to close",
        "Nothing old is waiting on a check.",
        "Check",
    ),
)


def stale_off(inc: "include.Include | None") -> bool:
    """Every group the Stale lanes hold is switched off."""
    groups = {include.group_of(k) for k in STALE_KINDS}
    return inc is not None and not any(inc.has(g) for g in groups)


def stale_lanes(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> list[view_model.Lane]:
    """The Close and Check lanes as the page shows them, ignores and the
    include filter applied; both are empty when every group is off."""
    items: list[StaleItem] = []
    if not stale_off(inc):
        items = stale_items(load(conn, cfg), now, overlay.load(conn).ignored)
        items = [i for i in items if inc is None or inc.shows(i.kind)]
    return [
        view_model.Lane(
            lane, title, [i for i in items if i.lane == lane], empty, tab=tab
        )
        for lane, title, empty, tab in STALE_LANES
    ]


def render_stale(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    inc: "include.Include | None" = None,
) -> str:
    lanes = stale_lanes(conn, cfg, now, inc)
    if stale_off(inc):
        # Every group these lanes hold is off: say so, as a Jira lane does.
        groups = sorted(
            {include.group_of(k) for k in STALE_KINDS}, key=include.GROUPS.index
        )
        note = include.off_note(*groups)
        off = "".join(
            f'<section class="lane" data-section="{lane.id}" data-filtered="all"'
            f' data-tab="{lane.tab}"><h2>{_e(lane.title or "")} (0)</h2>{note}'
            "</section>"
            for lane in lanes
        )
        return f'<div class="fleet">{off}</div>'
    body = "".join(
        _section(
            lane.id,
            lane.title or "",
            [i.html for i in lane.items],
            lane.empty,
            lane.tab,
        )
        for lane in lanes
    )
    return f'<div class="fleet">{body}</div>'
