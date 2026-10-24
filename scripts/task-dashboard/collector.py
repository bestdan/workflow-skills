"""The fleet view's collector: snapshot this machine and push it to ingest.

`dashboard.py collect` runs this once; a LaunchAgent or a `systemd --user`
timer runs that every five minutes (collector_install.py). The design is
``dev_docs/designs/2026-10-23-fleet-view.md`` ("Deployment").

ONE RUN. Build a snapshot (snapshot.py), add it to the spool, then send the
spool oldest first. A snapshot the server answers for is deleted, and the
snapshot cursor is advanced to it. The first one that cannot be answered for
stops the run, and it and everything after it wait for the next run. Sending
in order matters: the server refuses a snapshot older than one it already has
(409), so a newer one sent first would make every older one unsendable.

THE CURSOR advances only after the server accepts. A spooled snapshot carries
the cursor it ends at, and the next snapshot's deltas start there, not at the
saved cursor, so two spooled snapshots never carry the same hook or history
lines. A full spool (``[collector] spool_max`` files) drops its oldest
snapshot. That snapshot's deltas are lost: the next one starts after them.
A new snapshot that fails validation is not spooled, the waiting ones are
still sent, and the run exits 1.

``[collector] enabled = false`` turns the collector off as well as the
session-state hook: ``collect`` builds and sends nothing and exits 0.

PRUNING. The session-state hook writes one file per session and nothing else
deletes them, so each run ends by deleting the session files that are both
FULLY INGESTED and OLD. Fully ingested is read from the saved cursor, never a
spooled one, because only the saved cursor has been accepted by the server: its
mark for the file must name the file's current inode at an offset equal to its
size. Old means the newest line's ``ts`` and the file's mtime are both further
back than ``[collector] retention_days`` (default 30). A line that is not a
hook record with a parseable ``ts`` is skipped and counted, never fatal.

The age window is what keeps an active session's file: the hook appends on
every prompt and turn end, so a live session's file is never that old. A
session resumed after its file was pruned gets a new file with a new inode,
which the cursor reads from the start, so nothing is lost or sent twice. The
one loss left is a hook that opened the file in the instant between the last
check and the delete; its line goes to the deleted file.

WHAT EACH ANSWER DOES:

    2xx                  accepted: delete, advance the cursor
    409 (stale)          the server already has this or newer; a resend of a
                         snapshot whose answer was lost lands here. Same as 2xx
    other 4xx but 401,   refused for good, and resending cannot change that:
    403, 408, 429        delete, advance the cursor, say why on stderr
    5xx, 3xx, 401, 403,  stop and keep it for the next run. 401, 403 and a
    408, 429, no answer  redirect are config errors, fixed in config, not by
                         waiting, and kept so nothing is lost meanwhile

THE TOKEN comes from ``[collector] token`` in the config file, which
config.load_collector() refuses unless it is mode 0600. It goes only into the
Authorization header: never into argv, a log line or an exception message.
Redirects are refused, so the header cannot follow one to another host.
"""

import fcntl
import http.client
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import snapshot
from github_gh import Runner, run

POST_TIMEOUT = 30.0
SPOOL_VERSION = 1
KEEP_STATUSES = frozenset({401, 403, 408, 429})

# (status or None when there was no answer, a one-line detail for stderr)
Poster = Callable[[str, str, bytes], tuple["int | None", str]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def post(url: str, token: str, body: bytes) -> tuple["int | None", str]:
    """POST one snapshot; the token is only ever the Authorization header."""
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with _OPENER.open(req, timeout=POST_TIMEOUT) as resp:
            return resp.status, resp.read(512).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        with exc:
            detail = exc.read(512).decode("utf-8", "replace") if exc.fp else ""
        return exc.code, detail or str(exc.reason)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", exc)
        return None, f"no answer from {url}: {reason}"


def verdict(status: "int | None") -> str:
    """``accepted`` | ``rejected`` | ``keep`` for one answer."""
    if status is None or status >= 500 or status in KEEP_STATUSES or status < 200:
        return "keep"
    if 300 <= status < 400:
        return "keep"  # a redirect is a wrong ingest_url; it is never followed
    if 200 <= status < 300 or status == 409:
        return "accepted"
    return "rejected"


# --- the spool ----------------------------------------------------------------


def _entry_name(taken_at: str) -> str:
    # taken_at is `YYYY-MM-DDTHH:MM:SS.fffZ`, so names sort in time order.
    stamp = "".join(c for c in taken_at if c.isalnum())
    return f"{stamp}-{os.getpid()}.json"


def spooled(spool: Path) -> list[Path]:
    """The spool's entries, oldest first."""
    return sorted(spool.glob("*.json")) if spool.is_dir() else []


def read_entry(path: Path) -> "dict[str, Any] | None":
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    ok = (
        isinstance(data, dict)
        and data.get("v") == SPOOL_VERSION
        and isinstance(data.get("doc"), dict)
        and isinstance(data.get("cursor"), dict)
    )
    return data if ok else None


def write_entry(spool: Path, doc: dict[str, Any], cursor: dict[str, Any]) -> Path:
    """Atomically add one snapshot; mode 0600, since it holds history metadata."""
    spool.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = spool / _entry_name(doc["taken_at"])
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"v": SPOOL_VERSION, "doc": doc, "cursor": cursor}, fh)
    os.replace(tmp, path)
    return path


def trim(spool: Path, keep: int, log: Callable[[str], None]) -> None:
    entries = spooled(spool)
    for path in entries[: max(0, len(entries) - keep)]:
        path.unlink(missing_ok=True)
        log(f"spool full ({keep}): dropped the oldest snapshot, {path.name}")


@contextmanager
def _locked(path: Path) -> Iterator[bool]:
    """True when this run holds the lock; False when another run does."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


# --- one run ------------------------------------------------------------------


def _stderr(msg: str) -> None:
    print(f"task-dashboard collect: {msg}", file=sys.stderr)


def collect(
    cfg: dict[str, Any],
    *,
    home: Path,
    now: datetime,
    runner: Runner = run,
    poster: Poster = post,
    log: Callable[[str], None] = _stderr,
) -> int:
    """0 when the spool is empty at the end, 1 when something waits on a retry
    or the new snapshot failed validation. 0, with nothing built or sent, when
    ``[collector] enabled`` is false."""
    if not cfg["enabled"]:
        log("[collector] enabled is false; nothing built or pushed")
        return 0
    state = home / ".local/state/fleet"
    spool = state / "spool"
    with _locked(state / "collect.lock") as held:
        if not held:
            log("another collect run holds the lock; skipping this one")
            return 0
        pending = [e for e in map(read_entry, spooled(spool)) if e is not None]
        base = pending[-1]["cursor"] if pending else None
        doc, cursor = snapshot.build(
            src_root=home / "src",
            state_dir=state,
            history=home / ".claude/history.jsonl",
            host=cfg["host"] or socket.gethostname().split(".")[0],
            now=now,
            runner=runner,
            cursor=base,
        )
        problems = snapshot.validate(doc)
        if problems:
            # Not spooled, but it must not block the snapshots already waiting:
            # they were valid when written and carry their own cursors.
            log("snapshot does not match schema 1, not spooled: "
                + "; ".join(problems[:5]))  # fmt: skip
            flush(cfg, spool, state, poster, log)
            prune(state, cfg["retention_days"], now, log)
            return 1
        write_entry(spool, doc, cursor)
        trim(spool, cfg["spool_max"], log)
        rc = flush(cfg, spool, state, poster, log)
        prune(state, cfg["retention_days"], now, log)
        return rc


def flush(
    cfg: dict[str, Any],
    spool: Path,
    state: Path,
    poster: Poster,
    log: Callable[[str], None],
) -> int:
    sent = 0
    for path in spooled(spool):
        entry = read_entry(path)
        if entry is None:
            path.unlink(missing_ok=True)
            log(f"dropped an unreadable spool entry, {path.name}")
            continue
        body = json.dumps(entry["doc"]).encode()
        status, detail = poster(cfg["ingest_url"], cfg["token"], body)
        outcome = verdict(status)
        if outcome == "keep":
            left = len(spooled(spool))
            log(
                f"{status or 'no answer'}: {detail.strip()[:200]}"
                f" — {left} snapshot(s) kept for the next run"
            )
            return 1
        if outcome == "rejected":
            log(
                f"{status}: server refused {path.name} for good: {detail.strip()[:200]}"
            )
        snapshot.save_cursor(state / snapshot.CURSOR_NAME, entry["cursor"])
        path.unlink(missing_ok=True)
        sent += 1
    print(f"task-dashboard collect: pushed {sent} snapshot(s) to {cfg['ingest_url']}")
    return 0


# --- pruning session-state files ----------------------------------------------


def newest_ts(path: Path) -> tuple["datetime | None", int]:
    """The newest hook line's ``ts``, and how many lines were skipped as malformed.

    A line counts when it is a JSON object with a ``ts`` that parses as an
    ISO 8601 time; anything else (a torn write, a foreign line) is skipped.
    """
    newest: datetime | None = None
    skipped = 0
    for raw in path.read_bytes().splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
            ts = datetime.fromisoformat(obj["ts"].replace("Z", "+00:00"))
            if ts.tzinfo is None:
                raise ValueError("naive ts")
        except (ValueError, TypeError, KeyError, AttributeError):
            skipped += 1
            continue
        if newest is None or ts > newest:
            newest = ts
    return newest, skipped


def prune(
    state: Path, retention_days: int, now: datetime, log: Callable[[str], None]
) -> int:
    """Delete the session files that are fully ingested and old; the count deleted.

    Fully ingested: the saved (server-accepted) cursor's mark for the file is
    its current inode at its current size. Old: its newest line and its mtime
    are both older than ``retention_days``. See PRUNING in the module doc.
    """
    sessions = state / "sessions"
    if not sessions.is_dir():
        return 0
    marks = snapshot.load_cursor(state / snapshot.CURSOR_NAME).get("sessions")
    marks = marks if isinstance(marks, dict) else {}
    cutoff = now.timestamp() - retention_days * 86400
    deleted = skipped = 0
    for path in sorted(sessions.glob("*.jsonl")):
        mark = marks.get(path.name)
        try:
            st = path.stat()
            if not (
                isinstance(mark, dict)
                and mark.get("inode") == st.st_ino
                and mark.get("offset") == st.st_size
            ):
                continue  # unread lines, or not yet accepted by the server
            if st.st_mtime >= cutoff:
                continue
            newest, bad = newest_ts(path)
            skipped += bad
            if newest is not None and newest.timestamp() >= cutoff:
                continue
            # Re-check just before the delete: a hook that appended since the
            # stat changed the size, and the file is kept.
            again = path.stat()
            if (again.st_ino, again.st_size) != (st.st_ino, st.st_size):
                continue
            path.unlink()
        except OSError:
            continue
        deleted += 1
    if skipped:
        log(f"skipped {skipped} malformed session-state line(s) while pruning")
    if deleted:
        log(
            f"pruned {deleted} session-state file(s) older than {retention_days} day(s)"
        )
    return deleted
