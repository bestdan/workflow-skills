"""Close-outs: finished work that waits on the owner to close it out.

Now's Ship order (fleet_now.py) puts them second, after a session that asked.
The spec is "What closing out means" in
dev_docs/designs/2026-10-26-now-modes.md. It reads SQLite only and acts on
nothing: every step is ticked from evidence the dashboard already mirrors,
never from a click.

WHAT ENTERS, each keyed by its overlay item `(source, key)`:

- **A finished session**, a hook session row (sessions.py) whose last event is
  within WINDOW_DAYS, that is not working (its last event a prompt) and not
  waiting on a question or a prompt (Needs you's), and has evidence that its
  work is finished: its PR merged (a mirrored merged `closed_pr` whose head is
  the session's branch, in its repo), its branch landed (a worktree row at the
  session's cwd whose `landed` verdict is `landed`, from a scan that did not
  fail), or the session ended (sessions.py `ended`). A turn that merely ended
  is not enough. Unlike Needs you's Finished lane there is no `since_looked`
  window: a close-out stays until it is closed out. An empty spawn never
  enters. Its item is the session's, so a snooze or ignore on Needs you
  carries over.
- **A complete workstream** (workstreams.py's Complete lane: every issue
  closed, no open PR) whose last activity is within WINDOW_DAYS, keyed
  `(WS_SOURCE, its id)` so the ordinary snooze and ignore apply to it.

THE CHECKLIST. A session's steps, in the owner's order:

1. **PR merged**: its open PR (needs.open_prs, by repo and head branch) is an
   unticked step; a merged one is ticked. A session with neither has no step.
2. **Task closed**: the merged PR's closing issues (`closes`, from
   github_fleet.CLOSED_QUERY's `closingIssuesReferences`), ticked when every
   one is CLOSED. No closing issue, or no merged PR, is no evidence. Absence
   from the open-issue mirror is never evidence.
3. **Worktree removed**: the host has a snapshot taken after the session's
   last event, and the session's repo is one that snapshot scanned (it lists a
   worktree of that repo from a scan that did not fail). Ticked when no
   worktree in it is at the session's cwd (or holds it). A cwd on the repo's
   main checkout has no worktree to remove, so no step.
4. **Session ended**: the session row's `ended`.

A workstream has one step, **milestone closed**, unticked while it is in the
Complete lane: closing the milestone takes it out of the mirror, and the card
with it.

A step without evidence is left out, not counted open. The card leaves when
every step left is ticked, or when WINDOW_DAYS pass after its last activity,
whichever comes first.

THE ACTION resumes the session to finish its own close-out, with the steps
left as its prompt: by its Remote Control link when its process is live and
has one; a live process without one has no command, only where it runs (its
host, and its tmux target when the registry names one), since resuming would
start a second process on its open transcript; else
`claude --resume <id> '<prompt>'`, run in its cwd on its host.
A workstream's action is to close the milestone.
"""

import shlex
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

import fleet_views
import github_fleet
import needs
import score
import sessions
import workstreams

WINDOW_DAYS = github_fleet.CLOSED_WINDOW_DAYS
CATEGORY = "close_out"
WS_SOURCE = "workstreams"
DEFAULT_BRANCHES = ("main", "master")

PR_MERGED = "PR merged"
TASK_CLOSED = "task closed"
WORKTREE_REMOVED = "worktree removed"
SESSION_ENDED = "session ended"
MILESTONE_CLOSED = "milestone closed"


@dataclass
class Step:
    name: str
    done: bool
    todo: str = ""  # what finishes it, for the resume prompt


@dataclass
class CloseOut:
    need: needs.Need
    why: str  # the evidence that put it here
    steps: list[Step]


def checklist_text(steps: list[Step]) -> str:
    """`✓ PR merged · ○ task closed`, the steps with evidence."""
    return " · ".join(f"{'✓' if s.done else '○'} {s.name}" for s in steps)


def prompt(steps: list[Step]) -> str:
    """The resume prompt: the steps left, in order."""
    left = [s.todo for s in steps if not s.done and s.todo]
    return "Finish closing out this session: " + "; then ".join(left) + "."


# --- evidence ----------------------------------------------------------------


def _snapshots(conn: sqlite3.Connection) -> dict[str, "datetime | None"]:
    """host -> when its latest snapshot was taken."""
    return {
        host: score.parse_ts(taken)
        for host, taken in conn.execute("SELECT host, last_snapshot_at FROM machines")
    }


def _holds(path: "str | None", cwd: str) -> bool:
    if not path:
        return False
    p, c = PurePosixPath(path), PurePosixPath(cwd)
    return p == c or p in c.parents


def _worktree_at(rows: list[dict[str, Any]], cwd: str) -> "dict[str, Any] | None":
    """The host's worktree row at `cwd`, or the deepest one holding it."""
    found = [wt for wt in rows if _holds(wt.get("path"), cwd)]
    return max(found, key=lambda wt: len(str(wt.get("path"))), default=None)


def _pr_steps(
    f: fleet_views.Fleet,
    prs: needs.PrIndex,
    repo: "str | None",
    branch: "str | None",
) -> "tuple[list[Step], dict[str, Any] | None]":
    """The PR merged and task closed steps, and the merged PR (or None)."""
    if not (repo and branch):
        return [], None
    open_pr = needs.session_pr(prs, repo, branch)
    if open_pr is not None:
        n = open_pr["number"]
        return [Step(PR_MERGED, False, f"merge PR #{n}")], None
    merged = f.merged_head(repo, branch)
    if merged is None:
        return [], None
    steps = [Step(PR_MERGED, True)]
    closes = [c for c in merged.get("closes") or [] if isinstance(c, dict)]
    if closes:
        left = [c for c in closes if str(c.get("state") or "").upper() != "CLOSED"]
        refs = ", ".join(f"{c.get('repo') or ''}#{c.get('number')}" for c in left)
        steps.append(Step(TASK_CLOSED, not left, f"close {refs}" if left else ""))
    return steps, merged


def _worktree_step(
    rows: list[dict[str, Any]],
    taken: "datetime | None",
    last: datetime,
    cwd: "str | None",
) -> "Step | None":
    if not cwd or taken is None or taken <= last:
        return None
    repo = fleet_views.repo_of_path(cwd)
    scanned = {wt.get("repo") for wt in rows if not wt.get("kept")}
    if repo is None or repo not in scanned:
        return None
    wt = _worktree_at(rows, cwd)
    if wt is not None and (wt.get("kept") or wt.get("branch") in DEFAULT_BRANCHES):
        return None
    todo = "leave the worktree for main, remove it, and refresh main"
    return Step(WORKTREE_REMOVED, wt is None, todo)


def _entry(
    row: dict[str, Any],
    merged: "dict[str, Any] | None",
    wt: "dict[str, Any] | None",
) -> str:
    """Why a session counts as finished, or "" when nothing says so."""
    if merged is not None:
        return f"finished: PR #{merged.get('number')} merged"
    verdict = (wt or {}).get("landed") or {}
    if (
        wt is not None
        and not wt.get("kept")
        and verdict.get("state") == "landed"
        and wt.get("branch") not in (None, *DEFAULT_BRANCHES)
    ):
        return f"finished: branch {wt.get('branch')} landed"
    if row.get("ended"):
        return "finished: the session ended"
    return ""


def _resume(
    row: dict[str, Any], sid: str, steps: list[Step]
) -> "tuple[str, tuple[str, ...] | None, str | None]":
    """(hint, command, url): the Remote Control link with the prompt to give
    it; else, while its process is live, where it runs and no command, since
    `claude --resume` would start a second process on its open transcript;
    else `claude --resume` with the prompt as its argument."""
    text = prompt(steps)
    ended = row.get("ended")
    bridge = None if ended else needs.valid_bridge(row.get("bridge_session_id"))
    if bridge:
        return f"open the session and send: {text}", None, needs.session_url(bridge)
    host = row.get("host") or "an unknown host"
    if not ended and row.get("pid") is not None:  # in its host's registry
        tmux = f" in tmux {row['tmux']}" if row.get("tmux") else ""
        return f"still running{tmux} on {host}; send it: {text}", None, None
    if needs.RESUME_ID.fullmatch(sid) is None:
        return text, None, None
    cwd = row.get("cwd")
    where = f" in {cwd}" if cwd else ""
    cmd = f"claude --resume {sid} {shlex.quote(text)}"
    return (
        f"resume with `{cmd}`{where} on {host}",
        ("claude", "--resume", sid, text),
        None,
    )


# --- collect -----------------------------------------------------------------


def _sessions(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[CloseOut]:
    if not sessions.enabled(cfg):
        return []  # as Needs you: the sessions mirror is off
    cutoff = now - timedelta(days=WINDOW_DAYS)
    f = fleet_views.load(conn, cfg)
    by_host: dict[str, list[dict[str, Any]]] = {}
    for w in f.worktrees:
        by_host.setdefault(str(w.get("host")), []).append(w)
    taken = _snapshots(conn)
    prs = needs.open_prs(conn, cfg)
    # `ended` is evidence only on a host whose snapshot carries the registry;
    # elsewhere the step has no evidence and is skipped, not left open.
    registry = sessions.registry_hosts(conn)
    out = []
    for row in needs._rows(conn, sessions.SOURCE, sessions.KIND):
        last = score.parse_ts(row.get("updated"))
        if last is None or last < cutoff or row.get("empty_spawn"):
            continue
        ended = bool(row.get("ended"))
        if not ended and (
            row.get("status_bucket") != "waiting"
            or row.get("status_category") == "need_input"
        ):
            continue  # working, or waiting on me: not finished
        sid = str(row.get("key") or row.get("session_id"))
        host, cwd, branch = str(row.get("host")), row.get("cwd"), row.get("branch")
        repo = fleet_views.repo_of_path(cwd)
        rows = by_host.get(host, [])
        steps, merged = _pr_steps(f, prs, repo, branch)
        wt = _worktree_at(rows, cwd) if cwd else None
        why = _entry(row, merged, wt)
        if not why:
            continue
        wt_step = _worktree_step(rows, taken.get(host), last, cwd)
        steps += [wt_step] if wt_step else []
        if host in registry:
            steps.append(Step(SESSION_ENDED, ended, "end this session"))
        if all(s.done for s in steps):
            continue
        hint, command, url = _resume(row, sid, steps)
        extra = needs.transcript_fields(row, row.get("title") or f"session {sid}", prs)
        bridge = extra.pop("bridge_session_id")
        need = needs.Need(
            item=(sessions.SOURCE, sid),
            kind=sessions.KIND,
            since=last,
            url=url,
            state="ended" if ended else "turn ended",
            where=f"on {host}",
            repo=repo,
            branch=branch,
            hint=hint,
            command=command,
            host=row.get("host"),
            path=cwd,
            bridge_session_id=None if ended else bridge,
            **extra,
        )
        out.append(CloseOut(need, why, steps))
    return out


def _workstreams(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[CloseOut]:
    if not (workstreams.enabled(cfg) and workstreams.synced(conn)):
        return []
    cutoff = now - timedelta(days=WINDOW_DAYS)
    out = []
    for c in workstreams.cards(conn, cfg, now):
        if c.lane != "complete":
            continue
        if c.last_activity is not None and c.last_activity < cutoff:
            continue
        url = c.ws.get("url")
        need = needs.Need(
            item=(WS_SOURCE, c.id),
            kind=workstreams.KIND,
            title=str(c.ws.get("title") or c.id),
            since=c.last_activity,
            url=url if isinstance(url, str) else None,
            state=workstreams.progress_text(c),
            ref=workstreams.ref(c.ws),
            hint="close the milestone",
            repo=fleet_views._name(c.ws.get("repo")),
        )
        why = f"workstream complete: {workstreams.progress_text(c)}"
        out.append(CloseOut(need, why, [Step(MILESTONE_CLOSED, False)]))
    return out


def collect(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime
) -> list[CloseOut]:
    """Every close-out with a step left, unordered."""
    return _sessions(conn, cfg, now) + _workstreams(conn, cfg, now)
