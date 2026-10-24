"""The fleet view's Since you were away and Stale pages.

From dev_docs/designs/2026-10-23-fleet-view.md, "Views" items 2 and 3. Both
read SQLite only: the `source = 'github'` rows github_fleet.py writes, the
`source = 'machine:<host>'` rows and the `session_events` / `history_entries`
deltas snapshot_adapter.py writes, and the overlay.

SINCE YOU WERE AWAY (`/away`) lists what changed after the overlay's
`last_visit`: my PRs merged or closed, sessions whose last turn ended, and what
newly waits on me (a session's notification, an awaiting PR, an issue needing a
human, a newly proposed design). Loading the page records nothing. Only its
"Mark as read" button does, through overlay's `visit` action with the page's
render time, so a phone preview does not swallow the digest and an item that
arrives while the page is open is still new next time. With no visit recorded,
the page covers github_fleet's closed-PR window. When the overlay's focus item
is a design, the items in that design's repo come first, in a section of their
own: the weekend design block opens there.

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

Age orders each lane, oldest first, and is shown only as context.
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
import overlay
import score
import sessions
import sync

# Tabs that exist only on a fleet instance (views.nav hides them elsewhere).
ONLY = {"/needs", "/away"}

SESSION_IDLE_DAYS = 3  # a session with no event for this long is idle
SESSION_FORGET_DAYS = 30  # and one quiet for this long has left the list
OPEN_ITEM_DAYS = 14  # a PR, issue or handoff older than this goes under Check

_e = html.escape

CSS = """
.fleet .card .why { color:var(--fg); font-size:12px; }
.fleet .card .act { font-size:12px; }
.fleet .card .act code { white-space:pre-wrap; word-break:break-all; }
.fleet .visit { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
  margin-bottom:12px; color:var(--muted); }
.fleet .visit button { font:inherit; padding:4px 12px; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); }
"""


def enabled(cfg: dict[str, Any]) -> bool:
    """A fleet instance: the GitHub fleet step is on, or machines can ingest."""
    machines = (cfg.get("ingest") or {}).get("machines") or {}
    return sync.fleet_enabled(cfg) or bool(machines)


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

    @property
    def repo(self) -> "str | None":
        return repo_of_path(self.cwd)


@dataclass
class Fleet:
    """One read of the fleet rows the two pages need."""

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
    """The fleet rows. With `cfg` and `github.fleet` off, no github rows: the
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
        f.github.setdefault(kind, []).append(_raw(raw))
    for source, kind, raw, synced in conn.execute(
        "SELECT source, kind, raw_json, synced_at FROM issues"
        " WHERE source LIKE 'machine:%' AND kind IN ('worktree', 'handoff')"
        " ORDER BY source, key"
    ):
        row = {**_raw(raw), "host": source.split(":", 1)[1], "synced_at": synced}
        (f.worktrees if kind == "worktree" else f.handoffs).append(row)
    sessions: dict[tuple[str, str], Session] = {}
    for host, sid, ts, event, cwd, branch, note in conn.execute(
        "SELECT host, session_id, ts, event, cwd, branch, notification"
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
    if url and url.startswith("https://"):
        return f'<a href="{_e(url)}">{_e(text)}</a>'
    return _e(text)


def _ref(row: dict[str, Any]) -> str:
    """`repo#N title`, linked: a PR or issue row."""
    label = f"{row.get('repo') or ''}#{row.get('number')}"
    return f"{_link(row.get('url'), label)} {_e(row.get('title') or '')}"


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


def _section(name: str, title: str, cards: list[str], empty: str) -> str:
    body = "".join(cards) if cards else f'<p class="empty">{_e(empty)}</p>'
    return (
        f'<section class="lane" data-section="{_e(name)}">'
        f"<h2>{_e(title)} ({len(cards)})</h2>{body}</section>"
    )


def _session_where(s: Session) -> str:
    bits = [f"on {s.host}"]
    if s.repo:
        bits.append(s.repo + (f": {s.branch}" if s.branch else ""))
    elif s.cwd:
        bits.append(s.cwd)
    return " · ".join(_e(b) for b in bits)


# --- Since you were away -----------------------------------------------------


@dataclass
class AwayItem:
    section: str  # landed | finished | waiting
    at: datetime
    repo: "str | None"  # bare repo name, for the design-focus section
    html: str


SECTIONS = (
    ("landed", "Landed", "No PR of yours merged or closed."),
    ("finished", "Sessions that finished", "No session finished a turn."),
    ("waiting", "Newly waiting on you", "Nothing new is waiting on you."),
)


def away_items(f: Fleet, since: datetime, now: datetime) -> list[AwayItem]:
    out: list[AwayItem] = []
    for pr in f.github.get("closed_pr", []):
        at = score.parse_ts(pr.get("closed_at"))
        if at is None or at <= since:
            continue
        what = "merged" if pr.get("merged") else "closed without merging"
        card = _card(
            _ref(pr), f"{what} {_e(ago(at, now))}", key=str(pr.get("url") or "")
        )
        out.append(AwayItem("landed", at, _name(pr.get("repo")), card))
    for s in f.sessions:
        if s.last <= since or s.last_event not in ("Stop", "Notification"):
            continue
        if s.last_event == "Stop":
            card = _card(
                _e(f"session {s.session_id}"),
                f"{_session_where(s)} · turn ended {_e(ago(s.last, now))}",
                key=s.session_id,
            )
            out.append(AwayItem("finished", s.last, s.repo, card))
        else:
            kind = s.notification or "notification"
            card = _card(
                _e(f"session {s.session_id}"),
                f"{_session_where(s)} · {_e(kind)} {_e(ago(s.last, now))}",
                key=s.session_id,
            )
            out.append(AwayItem("waiting", s.last, s.repo, card))
    for kind, label in (("pr", "PR awaiting you"), ("issue", "issue needs a human")):
        for row in f.github.get(kind, []):
            at = score.parse_ts(row.get("created"))
            if at is None or at <= since:
                continue
            card = _card(
                _ref(row),
                f"{label} · opened {_e(ago(at, now))}",
                key=str(row.get("url") or ""),
            )
            out.append(AwayItem("waiting", at, _name(row.get("repo")), card))
    since_day = since.astimezone().date()
    for d in f.github.get("design", []):
        created = d.get("created")
        try:
            day = date.fromisoformat(str(created)[:10])
        except ValueError:
            continue
        if day < since_day:
            continue
        card = _card(
            f"{_link(d.get('url'), d.get('title') or d.get('path') or '')}",
            f"design proposed {_e(str(created))} · {_e(d.get('repo') or '')}",
            key=str(d.get("url") or ""),
        )
        at = score.local_day_start(day)
        out.append(AwayItem("waiting", at, _name(d.get("repo")), card))
    out.sort(key=lambda i: i.at, reverse=True)
    return out


def design_focus(conn: sqlite3.Connection, ov: overlay.Overlay) -> "dict | None":
    """The overlay's focus item when it is a mirrored design, else None."""
    if ov.focus is None:
        return None
    row = conn.execute(
        "SELECT kind, raw_json FROM issues WHERE source = ? AND key = ?", ov.focus
    ).fetchone()
    if row is None or row[0] != "design":
        return None
    return _raw(row[1])


def render_away(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> str:
    ov = overlay.load(conn)
    f = load(conn, cfg)
    window = timedelta(days=github_fleet.CLOSED_WINDOW_DAYS)
    last = score.parse_ts(ov.last_visit)
    since = last or now - window
    if last is None:
        note = (
            f"No visit recorded yet, so this covers the last"
            f" {github_fleet.CLOSED_WINDOW_DAYS} days."
        )
    else:
        note = f"Since your last visit, {ago(last, now)} ({ov.last_visit})."
        if now - last > window:
            note += (
                f" Closed PRs are fetched for the last"
                f" {github_fleet.CLOSED_WINDOW_DAYS} days only."
            )
    parts = [
        '<div class="visit">'
        f'<span data-last-visit="{_e(ov.last_visit or "")}">{_e(note)}</span>'
        f'<button type="button" data-ov="visit" data-at="{_stamp(now)}">'
        "Mark as read</button></div>"
    ]
    if not sync.fleet_enabled(cfg):
        parts.append(
            '<p class="note">github.fleet is off, so no PRs, issues or designs'
            " are shown here.</p>"
        )
    items = away_items(f, since, now)
    focus = design_focus(conn, ov)
    if focus is not None:
        repo = _name(focus.get("repo"))
        mine = [i for i in items if i.repo == repo]
        items = [i for i in items if i.repo != repo]
        head = _card(
            _link(focus.get("url"), focus.get("title") or ""),
            f"design focus · {_e(focus.get('repo') or '')}",
        )
        parts.append(
            f'<section class="lane" data-section="focus" data-repo="{_e(repo or "")}">'
            f"<h2>Design focus: {_e(focus.get('repo') or '')} ({len(mine)})</h2>"
            f"{head}{''.join(i.html for i in mine)}</section>"
        )
    for name, title, empty in SECTIONS:
        cards = [i.html for i in items if i.section == name]
        parts.append(_section(name, title, cards, empty))
    return f'<div class="fleet">{"".join(parts)}</div>'


# --- Stale, with evidence ----------------------------------------------------


@dataclass
class StaleItem:
    lane: str  # close | check
    at: "datetime | None"  # orders the lane, oldest first; never a reason
    html: str


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
            return True, f"expires `{value}`: PR #{n} merged {ago(at, now)}"
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

    def evidence(self, now: datetime) -> str:
        if self.pr is not None:
            return f"PR #{self.pr.get('number')} merged {ago(self.at, now)}"
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
        out[(wt["host"], wt["path"])] = Landed(pr, str(verdict.get("reason") or ""), at)
    return out


def stale_items(
    f: Fleet, now: datetime, ignored: "set[overlay.Item] | None" = None
) -> list[StaleItem]:
    """The Stale lanes' items. `ignored` (the overlay's) drops the sessions,
    PRs and issues the user ignored on Needs, under the same overlay keys."""
    ignored = ignored or set()
    out: list[StaleItem] = []
    landed = _landed_worktrees(f)

    for wt in f.worktrees:
        key = (wt["host"], wt["path"])
        if key not in landed:
            continue
        evidence, at = landed[key].evidence(now), landed[key].at
        head = _e(f"worktree {wt['path']}")
        meta = _e(f"on {wt['host']} · {wt.get('repo')}: {wt.get('branch')}")
        risks = []
        if wt.get("dirty"):
            risks.append("uncommitted changes")
        if wt.get("unpushed"):
            risks.append(f"{wt['unpushed']} unpushed commit(s)")
        active = _active_in(f, wt["host"], wt["path"], now)
        if active is not None:
            risks.append(f"session {active.session_id} used it {ago(active.last, now)}")
        cmd = f"<code>worktree-remove.sh {_e(wt['path'])}</code>"
        if landed[key].pr is None:
            # The gate also calls a branch landed when its tip is on
            # origin/<default>, which a fresh worktree with no commits is.
            # Without a merged PR for the branch that is not evidence enough.
            why = f"no merged PR found for its branch; the gate says: {_e(evidence)}"
            if risks:
                why += f"; it has {_e(', '.join(risks))}"
            act = f"check it has nothing left to do; then {cmd}"
            out.append(StaleItem("check", at, _card(head, meta, why, act)))
        elif risks:
            why = f"{_e(evidence)}, but it has {_e(', '.join(risks))}"
            act = f"look before removing; then {cmd}"
            out.append(StaleItem("check", at, _card(head, meta, why, act)))
        else:
            out.append(StaleItem("close", at, _card(head, meta, _e(evidence), cmd)))

    for ho in f.handoffs:
        path = ho.get("path") or ""
        repo = PurePosixPath(path).parent.parent.name or None
        at = score.parse_ts(ho.get("created")) or score.parse_ts(ho.get("mtime"))
        expired, evidence = _expiry(ho.get("expires"), repo, f, now)
        head = _e(f"handoff {ho.get('aim') or PurePosixPath(path).name}")
        meta = _e(f"on {ho['host']} · {path}")
        if expired:
            act = f"read it, then delete <code>{_e(path)}</code>"
            out.append(StaleItem("close", at, _card(head, meta, _e(evidence), act)))
        elif (
            expired is None
            and at is not None
            and now - at > timedelta(days=OPEN_ITEM_DAYS)
        ):
            why = f"no evidence: {_e(evidence)}"
            act = "read it: still needed, or delete it"
            out.append(StaleItem("check", at, _card(head, meta, why, act)))

    idle = now - timedelta(days=SESSION_IDLE_DAYS)
    forget = now - timedelta(days=SESSION_FORGET_DAYS)
    for s in f.sessions:
        if not forget < s.last <= idle or (sessions.SOURCE, s.session_id) in ignored:
            continue
        head = _e(f"session {s.session_id}")
        meta = f"{_session_where(s)} · last event {_e(ago(s.last, now))}"
        in_landed = next(
            (
                ev
                for (host, path), ev in landed.items()
                if host == s.host
                and ev.pr is not None
                and ((s.cwd or "") + "/").startswith(path.rstrip("/") + "/")
            ),
            None,
        )
        if s.prompts == 0:
            why = f"no prompt was ever sent (first event {ago(s.first, now)})"
            act = "close the session: an empty spawn"
            out.append(StaleItem("close", s.last, _card(head, meta, _e(why), act)))
        elif in_landed is not None:
            why = "its worktree landed: " + in_landed.evidence(now)
            out.append(
                StaleItem(
                    "close", s.last, _card(head, meta, _e(why), "close the session")
                )
            )
        else:
            why = "no evidence: idle, with nothing that says its work is done"
            act = "resume it, or close it"
            out.append(StaleItem("check", s.last, _card(head, meta, why, act)))

    old = now - timedelta(days=OPEN_ITEM_DAYS)
    for kind, act in (
        ("pr", "review it: merge, close, or say what it waits on"),
        ("issue", "decide it, or say what it waits on"),
    ):
        for row in f.github.get(kind, []):
            at = score.parse_ts(row.get("created"))
            item = (github_fleet.SOURCE, str(row.get("key") or row.get("url")))
            if at is None or at > old or item in ignored:
                continue
            meta = f"opened {_e(ago(at, now))}"
            why = "no evidence: nothing mirrored says it can close"
            out.append(StaleItem("check", at, _card(_ref(row), meta, why, act)))

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda i: (i.at is None, i.at or epoch))
    return out


def render_stale(conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime) -> str:
    items = stale_items(load(conn, cfg), now, overlay.load(conn).ignored)
    close = [i.html for i in items if i.lane == "close"]
    check = [i.html for i in items if i.lane == "check"]
    return (
        '<div class="fleet">'
        + _section(
            "close", "Close: evidence found", close, "Nothing has evidence to close."
        )
        + _section(
            "check",
            "Check: no evidence, so not a reason to close",
            check,
            "Nothing old is waiting on a check.",
        )
        + "</div>"
    )
