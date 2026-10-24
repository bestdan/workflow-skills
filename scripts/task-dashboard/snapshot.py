"""Machine snapshot for the fleet view: the state only this machine can see.

Prints one JSON document on stdout. The fleet view's collector ships it to the
Mac mini's ingest endpoint; the design is
``dev_docs/designs/2026-10-23-fleet-view.md`` ("Deployment", "Sources").

Run under uv (Python 3.11+), as the rest of the dashboard is::

    uv run --no-project --python '>=3.11' python scripts/task-dashboard/snapshot.py

DOCUMENT — ``schema: 2``; ``validate()`` below is the executable form of it.
It also accepts ``schema: 1``, the same document without ``scan_errors``, so a
collector that has not been updated yet still reaches ingest. Update the ingest
server before any collector: an older server rejects ``schema: 2`` with a 400,
and the collector drops that snapshot, deltas included.

    schema     2
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
    sessions   session-state hook lines (``scripts/session-state-hook.py``,
               format v1) appended since the last snapshot.
    history    ``~/.claude/history.jsonl`` prompts since the last snapshot,
               reduced to ``ts``, ``session_id``, ``project`` and
               ``approval_shaped``.

PROMPT TEXT NEVER LEAVES THIS MACHINE. A history entry is built from an
allowlist; the prompt and any pasted content are read only to classify them,
in memory, and are dropped. Hook lines are allowlisted the same way.

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

SCHEMA_VERSION = 2
ACCEPTED_SCHEMAS = (1, 2)
SCAN_STEPS = ("worktree_list",)
HOOK_FORMAT_VERSION = 1
GIT_TIMEOUT = 20.0
LANDED_TIMEOUT = 60.0
BRANCH_REMOVE = Path(__file__).resolve().parents[1] / "branch-remove.sh"
CURSOR_NAME = "snapshot-cursor.json"
HANDOFF_KEYS = ("aim", "branch", "pr", "expires", "created")
HOOK_KEYS = ("v", "ts", "event", "session_id", "cwd", "branch", "notification")
LANDED_STATES = ("landed", "not_landed", "unknown")

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
            if obj is None or obj.get("v") != HOOK_FORMAT_VERSION:
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The snapshot document and the cursor to save once it has been shipped.

    The deltas start at ``cursor``, else at the one saved in ``state_dir``. The
    collector passes the cursor of its newest unsent snapshot, so a snapshot
    waiting in its spool and the next one never carry the same lines.
    """
    if cursor is None:
        cursor = load_cursor(state_dir / CURSOR_NAME)
    sessions, session_marks = hook_tails(state_dir / "sessions", cursor)
    prompts, history_mark = history_delta(history, cursor, classify)
    trees, scan_errors = worktrees(runner, src_root)
    doc = {
        "schema": SCHEMA_VERSION,
        "host": host,
        "taken_at": _iso(now),
        "worktrees": trees,
        "scan_errors": scan_errors,
        "handoffs": handoffs(src_root / "worktrees"),
        "tmux": tmux_sessions(runner),
        "sessions": sessions,
        "history": prompts,
    }
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


def validate(doc: Any) -> list[str]:
    """Every way ``doc`` departs from its schema (1 or 2); empty when it conforms.

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
    if version == 2:
        top.add("scan_errors")
    for key in set(doc) ^ top:
        problems.append(f"{key}: {'unexpected' if key in doc else 'missing'}")
    _check(
        version in ACCEPTED_SCHEMAS and not isinstance(version, bool),
        "schema: not 1 or 2",
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
    if version == 2:
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
                and ev.get("v") == HOOK_FORMAT_VERSION
                and isinstance(ev.get("ts"), str)
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
