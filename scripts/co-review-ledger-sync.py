#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Flush a redacted copy of this machine's co-review ledger to a shared repo.

Each machine writes only its own file, ledgers/<host>.jsonl, in the git repo
named by CO_REVIEW_LEDGER_REMOTE, and /co-review-stats reads the union. The
design, and why each rule below exists, is
dev_docs/designs/2026-10-18-co-review-ledger-sync.md.

It prints receipt lines and nothing else: never git's output, which can carry
the remote URL, and never ledger contents. That silence is the firewall.
--push and --pull always exit 0, so no flush caller can fail because of it;
--check exits 0 for PASS (including sync off) and 1 for WARN. A failure's reason is one
of a fixed set: unreachable, clone failed, push rejected twice, another flush
is running, git failed, ledger unreadable, internal error.

Usage:
  uv run scripts/co-review-ledger-sync.py --push    # send this machine's new runs
  uv run scripts/co-review-ledger-sync.py --pull    # --push, then fetch every host's file
  uv run scripts/co-review-ledger-sync.py --check   # one /doctor line

Environment:
  CO_REVIEW_LEDGER_REMOTE  the shared repo; unset means sync is off.
  CO_REVIEW_HOST           this machine's host name (default: the hostname).
  CO_REVIEW_SYNC_LOCK_TIMEOUT  seconds to wait for another flush (default 30).

--dir moves ~/.claude/co-review (the ledger, the clone, and the lock), for tests.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

DEFAULT_DIR = Path.home() / ".claude" / "co-review"
BRANCH = "main"
UPSTREAM = f"refs/remotes/origin/{BRANCH}"
NETWORK_TIMEOUT_S = 120
Key = Tuple[str, str]

# The allowlist. A field reaches the shared repo only by being named here, so a
# field added to the ledger later stays local until someone adds it on purpose.
RUN_FIELDS = (
    "type", "schema", "run_id", "host", "ts", "repo", "mode", "diff_lines",
    "co_review_version", "reconciler_model",
)  # fmt: skip
REVIEWER_FIELDS = ("name", "disposition", "model", "duration_s")
FINDING_FIELDS = ("type", "run_id", "host", "sources", "human_corroborated", "verdict")


class SyncError(Exception):
    """A failure whose message is one of the fixed receipt reasons."""


def say(line: str) -> None:
    print(f"ledger sync: {line}")


def resolve_host() -> str:
    return os.environ.get("CO_REVIEW_HOST") or socket.gethostname()


def file_name(host: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", host) + ".jsonl"


# --- git ---------------------------------------------------------------------


def git_env() -> Dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX")
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    # attempt() tells a rejected push from an unreachable remote by git's
    # English stderr, so a localized git must not translate it.
    env["LC_ALL"] = "C"
    if "GIT_SSH_COMMAND" not in env and "GIT_SSH" not in env:
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def git(repo: Path, *args: str, network: bool = False) -> subprocess.CompletedProcess:
    # Hooks and signing come from the user's config and could refuse or prompt
    # for a commit to main; this repo is ours alone, so neither applies.
    cmd = [
        "git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgsign=false", *args,
    ]  # fmt: skip
    try:
        return subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=git_env(),
            timeout=NETWORK_TIMEOUT_S if network else None,
        )
    except subprocess.TimeoutExpired:
        raise SyncError("unreachable")
    except OSError:
        raise SyncError("git failed")


def ok(repo: Path, *args: str) -> bool:
    return git(repo, *args).returncode == 0


def must(repo: Path, *args: str) -> str:
    proc = git(repo, *args)
    if proc.returncode != 0:
        raise SyncError("git failed")
    return proc.stdout


def rev(repo: Path, ref: str) -> Optional[str]:
    proc = git(repo, "rev-parse", "--verify", "-q", ref)
    return proc.stdout.strip() if proc.returncode == 0 else None


# --- the clone is disposable ---------------------------------------------------


def usable(clone: Path, remote: str) -> bool:
    """True when the clone can be used as-is (after a local reset)."""
    if not (clone / ".git").is_dir():
        return False
    top = git(clone, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != clone.resolve():
        return False
    gitdir = clone / ".git"
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD"):
        if (gitdir / marker).exists():
            return False
    # A clone of some other remote is treated as unusable and recloned rather
    # than repointed: repointing would keep that remote's history, and the
    # flush's resets would then act on rows that were never this remote's.
    url = git(clone, "config", "--get", "remote.origin.url")
    if url.returncode != 0 or url.stdout.strip() != remote:
        return False
    if git(clone, "symbolic-ref", "HEAD").stdout.strip() != f"refs/heads/{BRANCH}":
        return False
    # A local commit with no upstream is a first push that never landed. There
    # is nothing to reset it onto, so start over instead of building on it.
    if rev(clone, "HEAD") and not rev(clone, "@{u}"):
        return False
    return True


def discard(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def fresh_clone(clone: Path, tmp: Path, remote: str) -> None:
    """Clone into tmp and rename it into place only once it succeeded.

    init + fetch rather than `git clone -b main`: git before 2.45 refuses
    `clone -b main` of an empty remote, and an empty remote is a supported
    first state. The result is the same pinned-to-main clone either way.
    """
    discard(clone)
    discard(tmp)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "init", "-q", "-b", BRANCH, str(tmp)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=git_env(),
    )
    try:
        if proc.returncode != 0:
            raise SyncError("clone failed")
        if not ok(tmp, "remote", "add", "origin", remote):
            raise SyncError("clone failed")
        if git(tmp, "fetch", "-q", "--prune", "origin", network=True).returncode:
            raise SyncError("unreachable")
        if rev(tmp, UPSTREAM):
            must(tmp, "reset", "-q", "--hard", UPSTREAM)
        elif must(tmp, "for-each-ref", "refs/remotes/origin").strip():
            # The remote has branches but no main: never invent one beside them.
            raise SyncError("clone failed")
        must(tmp, "config", f"branch.{BRANCH}.remote", "origin")
        must(tmp, "config", f"branch.{BRANCH}.merge", f"refs/heads/{BRANCH}")
        tmp.rename(clone)
    except SyncError:
        discard(tmp)
        raise


def ensure_clone(clone: Path, tmp: Path, remote: str) -> None:
    if not usable(clone, remote):
        fresh_clone(clone, tmp, remote)
        return
    # A dirty tree is reset with no network; unpushed commits wait for step 7.
    if rev(clone, "HEAD"):
        must(clone, "reset", "-q", "--hard")
    must(clone, "clean", "-q", "-fdx")


def fetch(clone: Path, tmp: Path, remote: str) -> None:
    """Fetch, and start over when the remote's history was rewritten."""
    before = rev(clone, UPSTREAM)
    if git(clone, "fetch", "-q", "--prune", "origin", network=True).returncode:
        raise SyncError("unreachable")
    after = rev(clone, UPSTREAM)
    # Rewritten means the upstream we last saw is not in the new history. A
    # remote that merely moved ahead (another host pushed) passes this check.
    if before and (
        not after or not ok(clone, "merge-base", "--is-ancestor", before, after)
    ):
        fresh_clone(clone, tmp, remote)


# --- the local ledger ------------------------------------------------------------


def read_blocks(ledger: Path) -> List[Tuple[Key, List[dict]]]:
    """Every complete run block in the ledger, read under a shared lock."""
    try:
        fh = ledger.open("r", encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        raise SyncError("ledger unreadable")
    with fh:
        fcntl.flock(fh, fcntl.LOCK_SH)
        text = fh.read()
    blocks: List[Tuple[Key, List[dict]]] = []
    current: Optional[List[Tuple[str, Optional[dict]]]] = None

    def close(block) -> None:
        # A block with a torn or unparseable line is skipped whole, never
        # trimmed: a partial run must not be copied and then count as flushed.
        if not block or any(
            rec is None or not raw.endswith("\n") for raw, rec in block
        ):
            return
        head = block[0][1]
        blocks.append(((str(head["host"]), str(head["run_id"])), [r for _, r in block]))

    for raw in text.splitlines(keepends=True):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
            if not isinstance(rec, dict):
                rec = None
        except ValueError:
            rec = None
        if rec is not None and rec.get("type") == "run":
            close(current)
            ok_key = isinstance(rec.get("run_id"), str) and isinstance(
                rec.get("host"), str
            )
            current = [(raw, rec)] if ok_key else None
        elif current is not None:
            current.append((raw, rec))
    close(current)
    return blocks


def redact(rec: dict) -> Optional[dict]:
    if rec.get("type") == "run":
        out = {k: rec[k] for k in RUN_FIELDS if k in rec}
        reviewers = rec.get("reviewers")
        if isinstance(reviewers, list):
            out["reviewers"] = [
                {k: r[k] for k in REVIEWER_FIELDS if k in r}
                for r in reviewers
                if isinstance(r, dict)
            ]
        return out
    if rec.get("type") == "finding":
        return {k: rec[k] for k in FINDING_FIELDS if k in rec}
    return None


def keys_of(text: str) -> Set[Key]:
    keys: Set[Key] = set()
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == "run":
            keys.add((str(rec.get("host")), str(rec.get("run_id"))))
    return keys


# --- the flush ---------------------------------------------------------------------


def watermark(clone: Path, path: str) -> str:
    """The pushed copy of this host's file; empty with no upstream or no file."""
    if not rev(clone, "@{u}"):
        return ""
    if not ok(clone, "cat-file", "-e", f"@{{u}}:{path}"):
        return ""
    return must(clone, "show", f"@{{u}}:{path}")


def attempt(clone: Path, path: str, blocks) -> Tuple[int, str]:
    """Steps 4-7 once. Returns (runs pushed, push outcome)."""
    pushed = watermark(clone, path)
    have = keys_of(pushed)
    selected: List[List[dict]] = []
    for key, recs in blocks:
        if key not in have:
            have.add(key)
            selected.append(recs)
    if not selected:
        return 0, "up to date"
    upstream = rev(clone, "@{u}")
    if upstream:
        must(clone, "reset", "-q", "--hard", "@{u}")
    must(clone, "clean", "-q", "-fdx")
    lines = [r for recs in selected for r in (redact(x) for x in recs) if r is not None]
    body = pushed if not pushed or pushed.endswith("\n") else pushed + "\n"
    body += "".join(json.dumps(r, sort_keys=True) + "\n" for r in lines)
    target = clone / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    must(clone, "add", "--", path)
    host = path.rsplit("/", 1)[-1][: -len(".jsonl")]
    must(
        clone, "-c", "user.name=co-review-ledger-sync",
        "-c", "user.email=co-review-ledger-sync@localhost",
        "commit", "-q", "--no-verify", "-m", f"ledger: {host} +{len(selected)} runs",
    )  # fmt: skip
    args = ["push", "-q", "origin", f"HEAD:{BRANCH}"]
    if not upstream:
        args.insert(2, "-u")
    proc = git(clone, *args, network=True)
    if proc.returncode == 0:
        return len(selected), "pushed"
    msg = proc.stderr
    if "[rejected]" in msg or "non-fast-forward" in msg or "fetch first" in msg:
        return len(selected), "rejected"
    raise SyncError("unreachable")


def push(base: Path, ledger: Path, remote: str, host: str) -> str:
    clone, tmp = base / "shared", base / "shared.tmp"
    path = f"ledgers/{file_name(host)}"
    try:
        ensure_clone(clone, tmp, remote)
        blocks = read_blocks(ledger)
        n, outcome = attempt(clone, path, blocks)
        if outcome == "rejected":
            # The reset in step 7 moves the branch onto the other machine's
            # commit; this machine's rows are re-appended from the new watermark.
            fetch(clone, tmp, remote)
            n, outcome = attempt(clone, path, blocks)
            if outcome == "rejected":
                raise SyncError("push rejected twice")
    except SyncError:
        discard(clone)
        raise
    return "up to date" if outcome == "up to date" else f"pushed {n} runs"


def pull(base: Path, remote: str) -> str:
    clone, tmp = base / "shared", base / "shared.tmp"
    try:
        ensure_clone(clone, tmp, remote)
        fetch(clone, tmp, remote)
        if rev(clone, "@{u}"):
            must(clone, "reset", "-q", "--hard", "@{u}")
        must(clone, "clean", "-q", "-fdx")
    except SyncError:
        discard(clone)
        raise
    hosts = sorted((clone / "ledgers").glob("*.jsonl"))
    return f"pulled {len(hosts)} host files"


def locked(lock_path: Path):
    """An exclusive flock on the lock file, or None after the timeout."""
    try:
        timeout = float(os.environ.get("CO_REVIEW_SYNC_LOCK_TIMEOUT", "30"))
    except ValueError:
        timeout = 30.0
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.monotonic() >= deadline:
                fh.close()
                return None
            time.sleep(0.1)


def check(base: Path, remote: Optional[str]) -> int:
    """One /doctor line. Exit 0 for PASS, 1 for WARN."""
    name = "co-review ledger sync"
    if not remote:
        print(f"PASS  {name} — off (CO_REVIEW_LEDGER_REMOTE unset)")
        return 0
    clone = base / "shared"
    try:
        is_usable = clone.exists() and usable(clone, remote)
    except SyncError:
        is_usable = False
    if not clone.exists():
        clone_note: Optional[str] = "no clone yet (the next flush makes one)"
    elif is_usable:
        clone_note = "clone ok"
    else:
        clone_note = None
    try:
        cwd = base if base.is_dir() else Path.home()
        reach = git(cwd, "ls-remote", "-q", "--heads", remote, network=True)
        reachable = reach.returncode == 0
    except SyncError:
        reachable = False
    if not reachable:
        print(
            f"WARN  {name} — remote unreachable; check network and access to CO_REVIEW_LEDGER_REMOTE"
        )
        return 1
    if clone_note is None:
        print(
            f"WARN  {name} — {clone} is not a usable clone; the next flush reclones it"
        )
        return 1
    print(f"PASS  {name} — {clone_note}, remote reachable")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--push", action="store_true")
    mode.add_argument("--pull", action="store_true")
    mode.add_argument("--check", action="store_true")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = ap.parse_args(argv)
    base = args.dir.expanduser()
    remote = os.environ.get("CO_REVIEW_LEDGER_REMOTE") or None
    if args.check:
        return check(base, remote)
    if not remote:
        say("off")
        return 0
    try:
        lock = locked(base / "shared.lock")
    except OSError:
        say("❌ internal error")
        return 0
    if lock is None:
        say("❌ another flush is running")
        return 0
    with lock:
        lines: List[str] = []
        try:
            lines.append(push(base, base / "findings.jsonl", remote, resolve_host()))
        except SyncError as exc:
            lines.append(f"❌ {exc}")
        except Exception:
            lines.append("❌ internal error")
        if args.pull:
            try:
                lines.append(pull(base, remote))
            except SyncError as exc:
                lines.append(f"❌ {exc}")
            except Exception:
                lines.append("❌ internal error")
    for line in lines:
        say(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
