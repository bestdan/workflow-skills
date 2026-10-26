"""Write one machine snapshot (snapshot.py, ``schema: 1``, ``2`` or ``3``) into
the mirror.

The fleet view's ingest endpoint (ingest.py) calls ``ingest()`` once it has
authenticated the caller and validated the document. Everything is written in
one transaction, so a snapshot lands whole or not at all:

- worktrees, handoffs, tmux sessions and live registry entries (schema 3)
  become ``issues`` rows under ``source = 'machine:<host>'`` with ``kind``
  ``worktree`` | ``handoff`` | ``tmux`` | ``registry``; sessions.py joins a
  registry row to its session's row by host and session id. A snapshot is that host's full current state, so the host's rows
  are deleted and rewritten; other hosts' rows, and the Jira sync's
  (``source = 'jira'``), are never touched.
- The one exception is a repo named in ``scan_errors`` (schema 2): its scan
  failed, so the snapshot says nothing about its worktrees. Its previous
  worktree rows are kept, matched by ``json_extract(raw_json, '$.repo')``, and
  marked with ``kept`` = ``{step, taken_at}`` naming the failed scan. Their
  ``synced_at`` stays the ``taken_at`` of the snapshot that last saw them, so
  a reader can show how old they are and never shows them as current. The
  next snapshot that scans the repo replaces them as usual.
- ``sessions`` and ``history`` are deltas, appended to ``session_events`` and
  ``history_entries``. A hook line's ``asked`` (format 2 Stop lines) is stored
  as 1 / 0, and NULL when it is null or absent.
- ``machines.last_snapshot_at`` records the snapshot's own ``taken_at``.

A snapshot no newer than the last one ingested from its host is refused with
``StaleSnapshot`` and nothing is written. That is what makes a resend harmless:
replacing the rows with older state would lose newer worktrees, and the deltas
would be appended twice. A snapshot taken more than ``MAX_CLOCK_AHEAD`` after
the server's clock is refused too (BadTakenAt): stored, it would make every
later, correctly-clocked snapshot from that host look stale.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_CLOCK_AHEAD = timedelta(minutes=5)
HOOK_FIELDS = ("event", "session_id", "cwd", "branch", "notification", "asked")
REGISTRY_KIND = "registry"

# kind, key, summary, created, raw_json
Row = tuple[str, str, "str | None", "str | None", str]


class StaleSnapshot(ValueError):
    """The host already has a snapshot taken at or after this one."""


class BadTakenAt(ValueError):
    """``taken_at`` is not ISO 8601 with a zone, or is ahead of the server."""


def source_for(host: str) -> str:
    return f"machine:{host}"


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_taken_at(value: str) -> datetime:
    """``taken_at`` as an aware datetime; BadTakenAt unless ISO 8601 with a zone."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BadTakenAt(f"taken_at is not ISO 8601: {value!r}") from exc
    if dt.utcoffset() is None:
        raise BadTakenAt(f"taken_at has no timezone: {value!r}")
    return dt


def _raw(item: dict[str, Any]) -> str:
    return json.dumps(item, sort_keys=True)


def item_rows(doc: dict[str, Any]) -> list[Row]:
    """One row per worktree, handoff and tmux session. Keys carry the kind, so a
    tmux session name and a path can never collide inside one host's source."""
    rows: list[Row] = []
    for wt in doc["worktrees"]:
        summary = f"{wt['repo']}: {wt['branch'] or '(detached)'}"
        rows.append(("worktree", f"worktree:{wt['path']}", summary, None, _raw(wt)))
    for ho in doc["handoffs"]:
        key = f"handoff:{ho['path']}"
        rows.append(("handoff", key, ho["aim"], ho["created"], _raw(ho)))
    for tm in doc["tmux"]:
        key = f"tmux:{tm['name']}"
        rows.append(("tmux", key, tm["name"], tm["created"], _raw(tm)))
    for reg in doc.get("registry", []):
        key = f"registry:{reg['session_id']}"
        rows.append((REGISTRY_KIND, key, reg["name"], None, _raw(reg)))
    return rows


def _str_or_json(value: Any) -> "str | int | None":
    if isinstance(value, bool):
        return int(value)
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def ingest(
    conn: sqlite3.Connection, doc: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Write a validated snapshot; returns what was written.

    ``doc`` must already pass ``snapshot.validate()``. Raises StaleSnapshot, or
    BadTakenAt for an unparseable or future ``taken_at``, with nothing written.
    Any other ValueError (a string SQLite cannot encode, say) also rolls back.
    """
    host = doc["host"]
    source = source_for(host)
    taken = parse_taken_at(doc["taken_at"])
    if taken > now + MAX_CLOCK_AHEAD:
        raise BadTakenAt(
            f"taken_at {doc['taken_at']} is more than"
            f" {int(MAX_CLOCK_AHEAD.total_seconds() // 60)} minutes ahead of"
            f" server time {_ts(now)}"
        )
    received = _ts(now)
    rows = item_rows(doc)
    counts = {
        "worktrees": len(doc["worktrees"]),
        "handoffs": len(doc["handoffs"]),
        "tmux": len(doc["tmux"]),
        "registry": len(doc.get("registry", [])),
        "sessions": len(doc["sessions"]),
        "history": len(doc["history"]),
        "scan_errors": len(doc.get("scan_errors", [])),
    }
    failed = sorted({err["repo"] for err in doc.get("scan_errors", [])})
    failed_json = json.dumps(failed)
    # IMMEDIATE takes the write lock before the staleness read, so two posts
    # from one host cannot both pass it.
    conn.execute("BEGIN IMMEDIATE")
    try:
        prev = conn.execute(
            "SELECT last_snapshot_at FROM machines WHERE host = ?", (host,)
        ).fetchone()
        if prev is not None and parse_taken_at(prev[0]) >= taken:
            raise StaleSnapshot(
                f"{host}: snapshot taken {doc['taken_at']} is not newer than"
                f" the last one ingested ({prev[0]})"
            )
        conn.execute(
            "DELETE FROM issues WHERE source = ? AND NOT (kind = 'worktree'"
            " AND json_extract(raw_json, '$.repo') IN (SELECT value FROM json_each(?)))",
            (source, failed_json),
        )
        counts["kept_worktrees"] = 0
        if failed:
            step = {err["repo"]: err["step"] for err in doc["scan_errors"]}
            counts["kept_worktrees"] = conn.executemany(
                "UPDATE issues SET raw_json = json_set(raw_json, '$.kept', json(?))"
                " WHERE source = ? AND kind = 'worktree'"
                " AND json_extract(raw_json, '$.repo') = ?",
                [
                    (
                        json.dumps({"step": step[repo], "taken_at": doc["taken_at"]}),
                        source,
                        repo,
                    )
                    for repo in failed
                ],
            ).rowcount
        conn.executemany(
            "INSERT OR REPLACE INTO issues"
            " (source, kind, key, summary, created, raw_json, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(source, *row, doc["taken_at"]) for row in rows],
        )
        conn.executemany(
            "INSERT INTO session_events (host, ts, event, session_id, cwd, branch,"
            " notification, asked, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    host,
                    ev["ts"],
                    *(_str_or_json(ev.get(k)) for k in HOOK_FIELDS),
                    received,
                )
                for ev in doc["sessions"]
            ],
        )
        conn.executemany(
            "INSERT INTO history_entries (host, ts, session_id, project,"
            " approval_shaped, received_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    host,
                    h["ts"],
                    h["session_id"],
                    h["project"],
                    int(h["approval_shaped"]),
                    received,
                )
                for h in doc["history"]
            ],  # fmt: skip
        )
        conn.execute(
            "INSERT INTO machines (host, last_snapshot_at, received_at, counts)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(host) DO UPDATE SET"
            " last_snapshot_at = excluded.last_snapshot_at,"
            " received_at = excluded.received_at, counts = excluded.counts",
            (host, doc["taken_at"], received, json.dumps(counts, sort_keys=True)),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"host": host, "taken_at": doc["taken_at"], **counts}
