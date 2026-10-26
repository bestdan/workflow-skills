"""Machine snapshot for the fleet view: the state only this machine can see.

Prints one JSON document on stdout. The fleet view's collector ships it to the
Mac mini's ingest endpoint; the design is
``dev_docs/designs/2026-10-23-fleet-view.md`` ("Deployment", "Sources").

Run under uv (Python 3.11+), as the rest of the dashboard is::

    uv run --no-project --python '>=3.11' python scripts/task-dashboard/snapshot.py

DOCUMENT — ``schema: 5``; ``validate()`` below is the executable form of it.
It also accepts ``schema: 4``, the same document without ``transcripts``,
``schema: 3``, without a registry entry's ``cwd`` either, ``schema: 2``,
without ``registry`` at all, and ``schema: 1``, without ``scan_errors``
either, so a collector that has not been updated yet still reaches ingest.
Update the ingest server before any collector where you can: an older server
rejects a newer schema with a 400. The collector resends a snapshot refused
that way one schema lower (``downgrade``), as far as schema 3, so a collector
updated first loses only what the server cannot read; it drops a snapshot
refused for any other reason, deltas included.

    schema     5
    host       this machine's short host name
    taken_at   UTC ISO 8601, ``Z`` suffix
    worktrees  one entry per ``git worktree list --porcelain`` record, for every
               repo directly under ``$HOME/src``: ``repo``, ``path``,
               ``branch`` (null when detached), ``head``, ``dirty`` and
               ``unpushed`` (commits on no remote ref; both null when the path
               is gone), and ``landed`` — ``{state, reason}`` where state is
               ``landed`` | ``not_landed`` | ``unknown``.
    scan_errors  (schema 2) one entry per repo whose worktrees could not be
               listed: ``repo`` (the same basename as ``worktrees[].repo``)
               and ``step`` (``worktree_list``), never the command's output.
               Ingest keeps that repo's previous rows rather than reading the
               failure as "no worktrees".
    handoffs   ``$HOME/src/worktrees/*/.handoffs/*.md``: ``path``, ``mtime``
               and the front-matter keys ``aim``, ``branch``, ``pr``,
               ``expires``, ``created`` (null when absent). The body is never
               read.
    tmux       ``name``, ``created``, ``attached``; empty when no server runs.
    registry   (schema 3) Claude Code's live process registry,
               ``~/.claude/sessions/<pid>.json``: one entry per session whose
               process is alive — ``session_id``, ``bridge_session_id`` (the
               Remote Control session), ``name``, ``status``, ``pid``,
               ``updated_at``, ``tmux`` and (schema 4) ``cwd``, the process's
               working directory (null when absent). Two files can
               name one session when an old process's file lingers after a
               resume: the live one wins, then the newest ``updatedAt``. The
               socket path, the launch arguments and every other key stay here.
               No directory is an empty registry. A directory that cannot be
               listed sends the document as schema 2, without ``registry``:
               ingest reads a hook session missing from a registry as ended,
               and a failed scan must not end them all.
    transcripts  (schema 5) what each recent session is about, from its
               transcript, ``~/.claude/projects/<cwd slug>/<session id>.jsonl``:
               one entry per session that is in ``registry`` or whose
               session-state hook file changed in the last TRANSCRIPT_DAYS
               days (TRANSCRIPT_MAX at most) and whose transcript could be
               read. ``session_id``; ``first_prompt``, the first prompt's first
               PROMPT_CHARS characters; ``last_ask``, the last ASK_CHARS of
               the last assistant text; ``branch``, the latest ``gitBranch``
               (null for ``HEAD``); and ``bridge_session_id``, the
               transcript's Remote Control id (``session_`` or ``cse_``), null
               when the registry has one for the session. Whitespace is
               collapsed. TRANSCRIPTS, below, says what is never read.
    sessions   session-state hook lines (``scripts/session-state-hook.py``,
               format v1 or v2) appended since the last snapshot, each with
               its own ``v``. Only v2 ``Stop`` lines carry ``asked``.
    history    ``~/.claude/history.jsonl`` prompts since the last snapshot,
               reduced to ``ts``, ``session_id``, ``project`` and
               ``approval_shaped``.

PROMPT TEXT NEVER LEAVES THIS MACHINE, but for ``transcripts``. A history
entry is built from an allowlist; the prompt and any pasted content are read
only to classify them, in memory, and are dropped. Hook lines are allowlisted
the same way.

TRANSCRIPTS are that exception, and it is kept narrow. A transcript is read
only from its start up to the first prompt (HEAD_BYTES at most) and over its
last TAIL_BYTES. Of the main conversation's records (never a subagent's), only
a ``user`` record whose content is a plain string, the text blocks of an
``assistant`` record, ``gitBranch`` and a ``bridge-session`` record's id are
looked at. Tool calls, tool results, attachments, thinking, system records and
every other field are never copied. What is copied is capped, and
``validate()`` refuses anything longer, so ingest enforces the caps too. A
transcript that is missing or unreadable sends no entry.

CURSOR. ``sessions`` and ``history`` are deltas. Per-file byte offsets (and the
inode, so a replaced file is read from the start) are kept in
``~/.local/state/fleet/snapshot-cursor.json`` and advanced only after the
document has been written. Only complete lines are consumed: a line the hook is
still writing is picked up next time. ``--no-advance`` leaves the cursor alone.

LANDED-PR EVIDENCE is not derived here. It is ``scripts/branch-remove.sh
--check``, the read-only mode of the plugin's teardown gate (exit 0 landed,
3 not, with the reason on its output).

Every external tool — ``git``, ``tmux``, ``branch-remove.sh`` — goes through one
runner seam, so tests substitute a fake. No root is needed, and nothing here is
macOS-specific.
"""

import argparse
import json
import os
import re
import socket
import sys
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from github_gh import Runner, run  # noqa: E402

SCHEMA_VERSION = 5
ACCEPTED_SCHEMAS = (1, 2, 3, 4, 5)
# How ingest's 400 names a schema it does not accept. The collector matches
# it to resend a snapshot one schema lower, and deployed servers already send
# it, so the text must not change.
UNSUPPORTED_SCHEMA = "unsupported schema"
SCAN_STEPS = ("worktree_list",)
# The hook formats read; a line of any other ``v`` is skipped. v2 added
# ``asked`` on Stop lines, so a machine whose hook is older still ships v1.
HOOK_FORMAT_VERSIONS = (1, 2)
GIT_TIMEOUT = 20.0
LANDED_TIMEOUT = 60.0
BRANCH_REMOVE = Path(__file__).resolve().parents[1] / "branch-remove.sh"
CURSOR_NAME = "snapshot-cursor.json"
HANDOFF_KEYS = ("aim", "branch", "pr", "expires", "created")
HOOK_KEYS = (
    "v", "ts", "event", "session_id", "cwd", "branch", "notification", "asked",
)  # fmt: skip
LANDED_STATES = ("landed", "not_landed", "unknown")
# A Remote Control session id, the path segment of https://claude.ai/code/<id>.
BRIDGE_ID = re.compile(r"session_[A-Za-z0-9_-]+")
# A transcript's ``bridge-session`` id. It can have the ``cse_`` form, which
# is not known to open as a claude.ai/code link: it is carried, and only an id
# that is also a BRIDGE_ID is ever linked.
TRANSCRIPT_BRIDGE_ID = re.compile(r"(?:session|cse)_[A-Za-z0-9_-]{1,120}")
# The session ids a transcript is looked up by: a file name, never a path.
SESSION_FILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
PROMPT_CHARS = 200
ASK_CHARS = 400
BRANCH_CHARS = 255
TAIL_BYTES = 256 * 1024
HEAD_BYTES = 256 * 1024
TRANSCRIPT_DAYS = 3
TRANSCRIPT_MAX = 50
TRANSCRIPT_KEYS = ("first_prompt", "last_ask", "branch", "bridge_session_id")
# What the harness writes as a user record's string content when the owner did
# not type as a prompt: a slash command, a shell escape, their output. A caveat
# is ``isMeta``.
_NOT_A_PROMPT = ("<command-", "<local-command-", "<bash-", "<system-reminder>")

Classifier = Callable[[str], bool]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (
        dt.microsecond // 1000
    )


def _iso_epoch(seconds: float) -> str:
    return _iso(datetime.fromtimestamp(seconds, UTC))


# --- approval-shaped classifier ---------------------------------------------
#
# INTERIM. The issue (#1102) asks for the classifier the work-in-progress reset
# design's retro script uses, whose location only the owner knows. Until it is
# wired in, this is a conservative stand-in: a short prompt that is nothing but
# an assent. It is a parameter of build(), so swapping it is a one-line change.

_ASSENTS = {
    "y", "yes", "yep", "yeah", "yup", "ok", "okay", "k", "sure", "go", "go ahead",
    "do it", "proceed", "continue", "approve", "approved", "lgtm", "ship it",
    "sounds good", "please do", "yes please", "yes do it", "yes go ahead",
    "ok go ahead", "ok do it", "sure go ahead", "go for it", "yes proceed",
}  # fmt: skip
_NON_WORD = re.compile(r"[^a-z ]+")


def approval_shaped(text: str) -> bool:
    """True for a bare assent ("y", "yes", "go ahead", "lgtm", ...)."""
    words = _NON_WORD.sub(" ", text.lower()).split()
    return 0 < len(words) <= 4 and " ".join(words) in _ASSENTS


# --- worktrees ----------------------------------------------------------------


def _git(runner: Runner, path: str, *args: str) -> tuple[int, str]:
    rc, out, _ = runner(["git", "-C", path, *args], GIT_TIMEOUT)
    return rc, out


def parse_porcelain(text: str) -> list[dict[str, Any]]:
    """``git worktree list --porcelain`` records, as dicts."""
    records: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    for line in text.splitlines() + [""]:
        if not line:
            if cur:
                records.append(cur)
            cur = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            cur["path"] = value
        elif key == "HEAD":
            cur["head"] = value
        elif key == "branch":
            cur["branch"] = value.removeprefix("refs/heads/")
        elif key in ("detached", "bare", "prunable", "locked"):
            cur[key] = True
    return records


def landed(runner: Runner, repo: str, branch: str | None) -> dict[str, str]:
    """The teardown gate's verdict on ``branch``, via its read-only mode."""
    if branch is None:
        return {"state": "unknown", "reason": "detached HEAD"}
    rc, out, err = runner(
        ["bash", str(BRANCH_REMOVE), "--check", branch, repo], LANDED_TIMEOUT
    )
    lines = [ln.strip(" .") for ln in (out + err).splitlines() if ln.strip()]
    reason = " ".join(lines)
    state = {0: "landed", 3: "not_landed"}.get(rc, "unknown")
    return {"state": state, "reason": reason or f"branch-remove.sh exited {rc}"}


def _probe(runner: Runner, repo: str, rec: dict[str, Any]) -> dict[str, Any]:
    path = rec["path"]
    branch = rec.get("branch")
    entry: dict[str, Any] = {
        "repo": os.path.basename(repo),
        "path": path,
        "branch": branch,
        "head": rec.get("head"),
        "dirty": None,
        "unpushed": None,
    }
    if os.path.isdir(path):
        rc, out = _git(runner, path, "status", "--porcelain")
        if rc == 0:
            entry["dirty"] = bool(out.strip())
        rc, out = _git(
            runner, path, "rev-list", "--count", "HEAD", "--not", "--remotes"
        )
        if rc == 0 and out.strip().isdigit():
            entry["unpushed"] = int(out.strip())
    entry["landed"] = landed(runner, repo, branch)
    return entry


def find_repos(src_root: Path) -> list[str]:
    """Main checkouts directly under ``src_root`` (``.git`` is a directory)."""
    if not src_root.is_dir():
        return []
    return sorted(str(p) for p in src_root.iterdir() if (p / ".git").is_dir())


def worktrees(
    runner: Runner, src_root: Path, workers: int = 8
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Every repo's worktrees, plus a ``scan_errors`` entry for each repo whose
    ``git worktree list`` failed (a timeout is rc 124). That repo contributes
    no worktrees, and the entry is what tells ingest it is not an absence."""
    jobs: list[tuple[str, dict[str, Any]]] = []
    errors: list[dict[str, str]] = []
    for repo in find_repos(src_root):
        rc, out = _git(runner, repo, "worktree", "list", "--porcelain")
        if rc != 0:
            errors.append({"repo": os.path.basename(repo), "step": "worktree_list"})
            continue
        jobs.extend((repo, rec) for rec in parse_porcelain(out) if not rec.get("bare"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda job: _probe(runner, *job), jobs)), errors


# --- handoffs -----------------------------------------------------------------


def front_matter(path: Path) -> dict[str, str | None]:
    """The allowlisted front-matter keys; reading stops at the closing ``---``."""
    found: dict[str, str | None] = dict.fromkeys(HANDOFF_KEYS)
    with path.open(encoding="utf-8", errors="replace") as fh:
        if fh.readline().strip() != "---":
            return found
        for line in fh:
            if line.strip() == "---":
                break
            key, sep, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if not sep or key not in found:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            found[key] = value or None
    return found


def handoffs(worktrees_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(worktrees_root.glob("*/.handoffs/*.md")):
        try:
            meta = front_matter(path)
            mtime = path.stat().st_mtime
        except OSError:
            continue
        out.append({"path": str(path), "mtime": _iso_epoch(mtime), **meta})
    return out


# --- tmux ---------------------------------------------------------------------


def tmux_sessions(runner: Runner) -> list[dict[str, Any]]:
    """Empty when tmux is absent or no server is running (both exit nonzero)."""
    fmt = "#{session_name}\t#{session_created}\t#{session_attached}"
    rc, out, _ = runner(["tmux", "list-sessions", "-F", fmt], GIT_TIMEOUT)
    if rc != 0:
        return []
    sessions = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        name, created, attached = parts
        sessions.append(
            {
                "name": name,
                "created": _iso_epoch(int(created)),
                "attached": attached.isdigit() and int(attached) > 0,
            }
        )
    return sessions


# --- Claude Code's process registry -------------------------------------------

Alive = Callable[[int], bool]


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists; signal 0 delivers nothing."""
    if pid <= 0:  # 0 and negatives name process groups, never one process
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it exists, under another user
    except OSError:
        return False
    return True


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _iso_ms(ms: float) -> str | None:
    """Epoch milliseconds as ``taken_at``'s form; None for 0 or out of range."""
    try:
        return _iso_epoch(ms / 1000) if ms > 0 else None
    except (OverflowError, OSError, ValueError):
        return None


def registry(
    registry_dir: Path, alive: Alive = pid_alive
) -> list[dict[str, Any]] | None:
    """One entry per live session in ``~/.claude/sessions/<pid>.json``.

    Each entry is built from an allowlist, so ``messagingSocketPath`` and the
    launch arguments never leave the file. An unreadable file, one without a
    ``sessionId`` or an integer ``pid``, and one whose process is gone are
    skipped. Of two live files for one session, the newest ``updatedAt`` wins.
    No directory (or a file in its place) is an empty registry; any other
    failure to list it is None, a failed scan.
    """
    best: dict[str, tuple[float, dict[str, Any]]] = {}
    try:
        names = os.listdir(registry_dir)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        return None
    paths = sorted(registry_dir / n for n in names if n.endswith(".json"))
    for path in paths:
        try:
            obj = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        sid, pid = obj.get("sessionId"), obj.get("pid")
        if not (isinstance(sid, str) and sid) or type(pid) is not int:
            continue
        if not alive(pid):
            continue
        updated = obj.get("updatedAt")
        stamp = (
            float(updated)
            if isinstance(updated, int | float) and not isinstance(updated, bool)
            else 0.0
        )
        bridge = _opt_str(obj.get("bridgeSessionId"))
        entry = {
            "session_id": sid,
            # A malformed id is dropped; the rest of the entry still counts.
            "bridge_session_id": bridge
            if bridge and BRIDGE_ID.fullmatch(bridge)
            else None,
            "name": _opt_str(obj.get("name")),
            "status": _opt_str(obj.get("status")),
            "pid": pid,
            "updated_at": _iso_ms(stamp),
            "tmux": _opt_str(obj.get("tmux")),
            "cwd": _opt_str(obj.get("cwd")),
        }
        if sid not in best or stamp > best[sid][0]:
            best[sid] = (stamp, entry)
    return [
        entry for _, entry in sorted(best.values(), key=lambda b: b[1]["session_id"])
    ]


# --- transcripts --------------------------------------------------------------


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _record(raw: bytes) -> dict[str, Any] | None:
    """One transcript record of the main conversation; None for a subagent's."""
    obj = _json_line(raw)
    if obj is None or obj.get("isSidechain"):
        return None
    return obj


def _prompt_of(rec: dict[str, Any]) -> str | None:
    """A ``user`` record's prompt. Only string content is one: a tool result
    is a list of blocks."""
    if rec.get("type") != "user" or rec.get("isMeta"):
        return None
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None
    text = _collapse(content)
    if not text or text.startswith(_NOT_A_PROMPT):
        return None
    return text


def _texts_of(rec: dict[str, Any]) -> tuple[Any, list[str]]:
    """An ``assistant`` record's message id and the text of its text blocks."""
    if rec.get("type") != "assistant" or rec.get("isApiErrorMessage"):
        return None, []
    message = rec.get("message")
    if not isinstance(message, dict):
        return None, []
    content = message.get("content")
    if isinstance(content, str):
        return message.get("id"), [content]
    if not isinstance(content, list):
        return None, []
    texts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return message.get("id"), texts


def _branch_of(rec: dict[str, Any]) -> str | None:
    branch = rec.get("gitBranch")
    if not isinstance(branch, str) or branch in ("", "HEAD"):
        return None
    return branch[:BRANCH_CHARS]


def _bridge_of(rec: dict[str, Any]) -> str | None:
    if rec.get("type") != "bridge-session":
        return None
    bridge = rec.get("bridgeSessionId")
    if isinstance(bridge, str) and TRANSCRIPT_BRIDGE_ID.fullmatch(bridge):
        return bridge
    return None


def project_slug(cwd: str) -> str:
    """Claude Code's directory name for ``cwd`` under ``~/.claude/projects``."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def transcript_path(
    projects_dir: Path, session_id: str, cwd: str | None
) -> Path | None:
    """The session's transcript: under its cwd's directory, else under any
    project's (a long cwd's directory name is shortened); None when none is."""
    if not SESSION_FILE_ID.fullmatch(session_id):
        return None
    name = f"{session_id}.jsonl"
    if cwd:
        path = projects_dir / project_slug(cwd) / name
        if path.is_file():
            return path
    try:
        found = sorted(p for p in projects_dir.glob(f"*/{name}") if p.is_file())
    except OSError:
        return None
    return found[0] if found else None


def read_transcript(path: Path) -> dict[str, Any]:
    """The allowlisted fields of one transcript (TRANSCRIPTS in the module
    doc). Raises OSError when it cannot be read."""
    first: str | None = None
    branch: str | None = None
    bridge: str | None = None
    with path.open("rb") as fh:
        head = fh.read(HEAD_BYTES)
        if len(head) == HEAD_BYTES:
            head = head[: head.rfind(b"\n") + 1]  # complete lines only
        for raw in head.splitlines():
            rec = _record(raw)
            if rec is None:
                continue
            branch = _branch_of(rec) or branch
            bridge = _bridge_of(rec) or bridge
            first = _prompt_of(rec)
            if first is not None:
                break
        size = fh.seek(0, os.SEEK_END)
        start = max(0, size - TAIL_BYTES)
        # One byte early, so a window that starts on a record keeps it.
        fh.seek(max(0, start - 1))
        tail = fh.read(TAIL_BYTES + 1 if start else TAIL_BYTES)
    if start:
        tail = tail[tail.find(b"\n") + 1 :] if b"\n" in tail else b""
    last_id: Any = None
    texts: list[str] = []
    for raw in tail.splitlines():
        rec = _record(raw)
        if rec is None:
            continue
        branch = _branch_of(rec) or branch
        bridge = _bridge_of(rec) or bridge
        msg_id, got = _texts_of(rec)
        if not got:
            continue
        # The harness writes one message as several records, a block each.
        if msg_id is None or msg_id != last_id:
            texts = []
        texts.extend(got)
        last_id = msg_id
    ask = _collapse(" ".join(texts))
    return {
        "first_prompt": first[:PROMPT_CHARS] if first else None,
        "last_ask": ask[-ASK_CHARS:] if ask else None,
        "branch": branch,
        "bridge_session_id": bridge,
    }


def recent_hook_sessions(sessions_dir: Path, now: datetime) -> list[str]:
    """The session ids whose hook file changed in the last TRANSCRIPT_DAYS
    days, newest first."""
    cutoff = now.timestamp() - TRANSCRIPT_DAYS * 86400
    found: list[tuple[float, str]] = []
    paths = sorted(sessions_dir.glob("*.jsonl")) if sessions_dir.is_dir() else []
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            found.append((mtime, path.stem))
    return [sid for _, sid in sorted(found, reverse=True)]


def transcripts(
    projects_dir: Path, live: list[dict[str, Any]], recent: list[str]
) -> list[dict[str, Any]]:
    """An entry per session in ``live`` (the registry), then in ``recent``,
    TRANSCRIPT_MAX at most, whose transcript can be read; by session id."""
    by_id = {e["session_id"]: e for e in live}
    wanted = list(dict.fromkeys([*by_id, *recent]))[:TRANSCRIPT_MAX]
    out = []
    for sid in sorted(wanted):
        entry = by_id.get(sid) or {}
        try:
            path = transcript_path(projects_dir, sid, entry.get("cwd"))
            if path is None:
                continue
            got = read_transcript(path)
        except (OSError, ValueError, RecursionError):
            continue
        if entry.get("bridge_session_id"):
            got["bridge_session_id"] = None  # the registry's is the one sent
        out.append({"session_id": sid, **got})
    return out


# --- cursor-tracked tails -----------------------------------------------------


def load_cursor(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cursor(path: Path, cursor: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cursor, sort_keys=True) + "\n")
    os.replace(tmp, path)


def read_new_lines(path: Path, mark: Any) -> tuple[list[bytes], dict[str, int]]:
    """Complete lines after ``mark`` (``{offset, inode}``), and the new mark.

    A replaced file (new inode) or a truncated one (offset past the end) is
    read from the start. A trailing line with no newline is left for next time.
    """
    st = path.stat()
    offset = 0
    if isinstance(mark, dict) and mark.get("inode") == st.st_ino:
        prev = mark.get("offset")
        if isinstance(prev, int) and 0 <= prev <= st.st_size:
            offset = prev
    with path.open("rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    end = chunk.rfind(b"\n") + 1
    return chunk[:end].splitlines(), {"offset": offset + end, "inode": st.st_ino}


def _json_line(raw: bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _asked_ok(ev: dict[str, Any]) -> bool:
    """``asked`` appears only on v2 Stop lines, as a bool or null."""
    if "asked" not in ev:
        return True
    return (
        ev.get("v") == 2
        and ev.get("event") == "Stop"
        and isinstance(ev["asked"], (bool, type(None)))
    )


def _known_format(v: Any) -> bool:
    """A hook line's ``v`` is one this reads (``True`` is not ``1``)."""
    return type(v) is int and v in HOOK_FORMAT_VERSIONS


def hook_tails(
    sessions_dir: Path, cursor: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    marks = cursor.get("sessions")
    marks = marks if isinstance(marks, dict) else {}
    new_marks: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.glob("*.jsonl")) if sessions_dir.is_dir() else []:
        try:
            lines, new_marks[path.name] = read_new_lines(path, marks.get(path.name))
        except OSError:
            continue
        for raw in lines:
            obj = _json_line(raw)
            if obj is None or not _known_format(obj.get("v")):
                continue
            events.append({k: obj[k] for k in HOOK_KEYS if k in obj})
    events.sort(key=lambda e: str(e.get("ts", "")))
    return events, new_marks


def _prompt_text(obj: dict[str, Any]) -> str:
    display = obj.get("display")
    return display if isinstance(display, str) else ""


def history_delta(
    history: Path, cursor: dict[str, Any], classify: Classifier
) -> tuple[list[dict[str, Any]], Any]:
    """History entries since the cursor, with every prompt-text field dropped."""
    mark = cursor.get("history")
    if not history.is_file():
        return [], mark
    lines, new_mark = read_new_lines(history, mark)
    entries = []
    for raw in lines:
        obj = _json_line(raw)
        if obj is None:
            continue
        ts = obj.get("timestamp")
        if not isinstance(ts, int | float) or isinstance(ts, bool):
            continue
        session = obj.get("sessionId")
        project = obj.get("project")
        entries.append(
            {
                "ts": _iso_epoch(ts / 1000),
                "session_id": session if isinstance(session, str) else None,
                "project": project if isinstance(project, str) else None,
                "approval_shaped": classify(_prompt_text(obj)),
            }
        )
    return entries, new_mark


# --- the document -------------------------------------------------------------


def build(
    *,
    src_root: Path,
    state_dir: Path,
    history: Path,
    host: str,
    now: datetime,
    runner: Runner = run,
    classify: Classifier = approval_shaped,
    cursor: dict[str, Any] | None = None,
    registry_dir: Path | None = None,
    alive: Alive = pid_alive,
    projects_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The snapshot document and the cursor to save once it has been shipped.

    The deltas start at ``cursor``, else at the one saved in ``state_dir``. The
    collector passes the cursor of its newest unsent snapshot, so a snapshot
    waiting in its spool and the next one never carry the same lines.
    ``registry_dir`` is Claude Code's ``sessions`` directory. None reads none,
    and a registry that cannot be read is the same: either makes the document
    schema 2, without one, and so without ``transcripts``. ``projects_dir``
    is Claude Code's ``projects`` directory; None reads no transcripts.
    """
    if cursor is None:
        cursor = load_cursor(state_dir / CURSOR_NAME)
    sessions, session_marks = hook_tails(state_dir / "sessions", cursor)
    prompts, history_mark = history_delta(history, cursor, classify)
    trees, scan_errors = worktrees(runner, src_root)
    live = registry(registry_dir, alive) if registry_dir is not None else None
    doc = {
        "schema": SCHEMA_VERSION if live is not None else 2,
        "host": host,
        "taken_at": _iso(now),
        "worktrees": trees,
        "scan_errors": scan_errors,
        "handoffs": handoffs(src_root / "worktrees"),
        "tmux": tmux_sessions(runner),
        "registry": live,
        "transcripts": [],
        "sessions": sessions,
        "history": prompts,
    }
    if live is None:
        del doc["registry"], doc["transcripts"]
    elif projects_dir is not None:
        recent = recent_hook_sessions(state_dir / "sessions", now)
        doc["transcripts"] = transcripts(projects_dir, live, recent)
    new_cursor = {"v": 1, "sessions": session_marks, "history": history_mark}
    return doc, new_cursor


def _check(cond: bool, where: str, problems: list[str]) -> None:
    if not cond:
        problems.append(where)


def _fields(
    items: Any, name: str, spec: dict[str, tuple[type, ...]], problems: list[str]
) -> None:
    if not isinstance(items, list):
        problems.append(f"{name}: not a list")
        return
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"{name}[{i}]: not an object")
            continue
        for key in set(item) - set(spec):
            problems.append(f"{name}[{i}].{key}: unexpected key")
        for key, types in spec.items():
            if key not in item:
                problems.append(f"{name}[{i}].{key}: missing")
            elif not isinstance(item[key], types) or (
                isinstance(item[key], bool) and bool not in types
            ):
                problems.append(f"{name}[{i}].{key}: {type(item[key]).__name__}")


def downgrade(doc: dict[str, Any]) -> "dict[str, Any] | None":
    """``doc`` one schema lower, for a server that predates its schema: 5 as 4,
    without ``transcripts``; 4 as 3, without the registry's ``cwd``. None for
    any other schema."""
    if doc.get("schema") == 5:
        return {
            **{k: v for k, v in doc.items() if k != "transcripts"},
            "schema": 4,
        }
    if doc.get("schema") != 4:
        return None
    registry = [
        {k: v for k, v in entry.items() if k != "cwd"} for entry in doc["registry"]
    ]
    return {**doc, "schema": 3, "registry": registry}


_CAPS = {"first_prompt": PROMPT_CHARS, "last_ask": ASK_CHARS, "branch": BRANCH_CHARS}


def _transcript_ok(entry: Any) -> bool:
    """Every text within its cap, and an id of TRANSCRIPT_BRIDGE_ID's form."""
    if not isinstance(entry, dict):
        return True  # _fields reports it
    for key, cap in _CAPS.items():
        value = entry.get(key)
        if isinstance(value, str) and len(value) > cap:
            return False
    bridge = entry.get("bridge_session_id")
    return not isinstance(bridge, str) or bool(TRANSCRIPT_BRIDGE_ID.fullmatch(bridge))


def validate(doc: Any) -> list[str]:
    """Every way ``doc`` departs from its schema (1 to 5); empty when it conforms.

    Keys are closed: an unexpected key is a violation, which is what keeps a
    new field (prompt text, say) from shipping without a schema bump.
    """
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["document: not an object"]
    top = {
        "schema",
        "host",
        "taken_at",
        "worktrees",
        "handoffs",
        "tmux",
        "sessions",
        "history",
    }
    version = doc.get("schema")
    if version in (2, 3, 4, 5) and not isinstance(version, bool):
        top.add("scan_errors")
    if version in (3, 4, 5) and not isinstance(version, bool):
        top.add("registry")
    if version == 5 and not isinstance(version, bool):
        top.add("transcripts")
    for key in set(doc) ^ top:
        problems.append(f"{key}: {'unexpected' if key in doc else 'missing'}")
    _check(
        version in ACCEPTED_SCHEMAS and not isinstance(version, bool),
        "schema: not 1, 2, 3, 4 or 5",
        problems,
    )
    _check(isinstance(doc.get("host"), str), "host: not a string", problems)
    _check(isinstance(doc.get("taken_at"), str), "taken_at: not a string", problems)
    s, n, b, d = (str,), (str, type(None)), (bool,), (dict,)
    _fields(
        doc.get("worktrees"),
        "worktrees",
        {"repo": s, "path": s, "branch": n, "head": n, "dirty": (bool, type(None)),
         "unpushed": (int, type(None)), "landed": d},
        problems,
    )  # fmt: skip
    for idx, wt in enumerate(doc.get("worktrees") or []):
        lnd = wt.get("landed") if isinstance(wt, dict) else None
        if isinstance(lnd, dict):
            _check(
                set(lnd) == {"state", "reason"} and lnd.get("state") in LANDED_STATES
                and isinstance(lnd.get("reason"), str),
                f"worktrees[{idx}].landed: malformed",
                problems,
            )  # fmt: skip
    _fields(doc.get("handoffs"), "handoffs",
            {"path": s, "mtime": s, **dict.fromkeys(HANDOFF_KEYS, n)}, problems)  # fmt: skip
    _fields(doc.get("tmux"), "tmux", {"name": s, "created": s, "attached": b}, problems)
    if "registry" in top:
        _fields(
            doc.get("registry"),
            "registry",
            {"session_id": s, "bridge_session_id": n, "name": n, "status": n,
             "pid": (int,), "updated_at": n, "tmux": n,
             **({"cwd": n} if version in (4, 5) else {})},
            problems,
        )  # fmt: skip
    if "transcripts" in top:
        entries = doc.get("transcripts")
        _fields(entries, "transcripts",
                {"session_id": s, **dict.fromkeys(TRANSCRIPT_KEYS, n)}, problems)  # fmt: skip
        for idx, entry in enumerate(entries if isinstance(entries, list) else []):
            _check(_transcript_ok(entry), f"transcripts[{idx}]: over a cap", problems)
    if "scan_errors" in top:
        errors = doc.get("scan_errors")
        _fields(errors, "scan_errors", {"repo": s, "step": s}, problems)
        for idx, err in enumerate(errors if isinstance(errors, list) else []):
            if isinstance(err, dict) and err.get("step") not in SCAN_STEPS:
                problems.append(f"scan_errors[{idx}].step: unknown")
    _fields(
        doc.get("history"),
        "history",
        {"ts": s, "session_id": n, "project": n, "approval_shaped": b},
        problems,
    )
    sessions = doc.get("sessions")
    if not isinstance(sessions, list):
        problems.append("sessions: not a list")
    else:
        for idx, ev in enumerate(sessions):
            ok = isinstance(ev, dict) and set(ev) <= set(HOOK_KEYS)
            ok = (
                ok
                and _known_format(ev.get("v"))
                and isinstance(ev.get("ts"), str)
                and _asked_ok(ev)
            )
            _check(ok, f"sessions[{idx}]: malformed", problems)
    return problems


def iter_values(obj: Any) -> Iterable[Any]:
    """Every scalar in a JSON-shaped value, keys included."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from iter_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_values(v)
    else:
        yield obj


def main(argv: list[str] | None = None) -> int:
    home = Path.home()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src-root", type=Path, default=home / "src")
    ap.add_argument("--state-dir", type=Path, default=home / ".local/state/fleet")
    ap.add_argument("--history", type=Path, default=home / ".claude/history.jsonl")
    ap.add_argument(
        "--registry-dir", type=Path, default=home / ".claude/sessions",
        help="Claude Code's live session registry",
    )  # fmt: skip
    ap.add_argument(
        "--projects-dir", type=Path, default=home / ".claude/projects",
        help="Claude Code's session transcripts",
    )  # fmt: skip
    ap.add_argument("--host", default=socket.gethostname().split(".")[0])
    ap.add_argument(
        "--no-advance", action="store_true", help="print the delta, keep the cursor"
    )
    args = ap.parse_args(argv)
    doc, cursor = build(
        src_root=args.src_root,
        state_dir=args.state_dir,
        history=args.history,
        host=args.host,
        now=datetime.now(UTC),
        registry_dir=args.registry_dir,
        projects_dir=args.projects_dir,
    )
    problems = validate(doc)
    if problems:
        print(
            f"snapshot does not match schema {SCHEMA_VERSION}:",
            *problems,
            sep="\n  ",
            file=sys.stderr,
        )
        return 1
    json.dump(doc, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()
    if not args.no_advance:
        save_cursor(args.state_dir / CURSOR_NAME, cursor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
