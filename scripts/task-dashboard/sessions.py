"""Session rows for the fleet view, derived from the session-state hook.

The hook (scripts/session-state-hook.py) appends a line per prompt, turn end
and notification on every opted-in machine. The collector ships the lines and
ingest appends them to `session_events` (snapshot_adapter.py). This module
folds those events into one `issues` row per session, under
`source = 'claude-hook'`, `kind = 'session'`, keyed by the hook's session id.

It is the primary source of session state: the cloud session list
(claude_remote.py) is optional, off by default, and carries no post-turn
state. The two sources key sessions differently (a hook id is the local CLI's,
a cloud id starts with `session_`) and nothing in either joins them, so a
Remote Control session can appear once under each source.

THE ROW. `summary` is the registry's `name` when the session's process is live
on its host, else the cwd's last component and branch (the hook records no
title). `status_name` holds the status bucket and `status_category` the
category; `created` and `updated` are the first and last event's times. The
rest is in `raw_json`:

    session_id, host, cwd, branch
    status_bucket     "working" (last event a prompt) | "waiting" (a turn end
                      or a notification since the last prompt)
    status_category   "need_input" (a permission prompt since the last prompt
                      or turn end, or `asked`) | "turn_ended" (any other wait)
                      | null (working)
    needs_action      true only for need_input
    asked             the last Stop since the last prompt has `asked = 1`: its
                      turn ended in a question, by the hook's "?" heuristic. An
                      idle_prompt after it does not clear it; a permission
                      prompt or a prompt does. NULL (hook format 1, or untellable) and 0
                      are both false here, so such a turn end is turn_ended.
    waiting_since     the first Stop or Notification after the last prompt
    has_prompt        whether any UserPromptSubmit was recorded
    empty_spawn       not has_prompt: a session that never got a prompt
    prompts, permission_prompts   counts over the session's recorded events
    brief             always null: the hook has no post-turn summary
    bridge_session_id, live_name, live_status, pid, live_updated_at, tmux
                      from the host's live registry entry for this session
                      (snapshot.py `registry`, schema 3), all null when the
                      process is gone or the collector predates schema 3.
                      `bridge_session_id` is the Remote Control session, so
                      `https://claude.ai/code/<it>` opens this session.
    first_prompt, last_ask, transcript_bridge_session_id
                      from the host's transcript summary for this session
                      (snapshot.py `transcripts`, schema 5): what it was first
                      asked, the end of its last assistant text, and the
                      transcript's Remote Control id, which may have the
                      unverified `cse_` form and so is never linked as is. All
                      null without a summary. The summary's `branch`, when it
                      has one, replaces the hook's.
    ended            the host's latest snapshot carries a registry (schema 3
                      or later, recorded in `machines.counts`) and this
                      session is not in it, and no live entry on that host
                      runs in the session's cwd: its process is gone, so it
                      can only be resumed. A live process can keep its
                      registry id after `/clear` or a resume moves it to a
                      new session id, so a live entry in the same cwd under
                      another id may be this session, and it is not ended.
                      An entry's cwd is its own `cwd` (schema 4); without
                      one (schema 3, or none in the file), the one its own
                      hook session last recorded. An entry with neither
                      protects nothing.
                      False on a host whose latest snapshot has no registry
                      (an older collector, or a registry scan that failed),
                      where a missing entry proves nothing. Recomputed on
                      every derive, so a session that shows up in the
                      registry again is no longer ended.
    cwd_held         a live entry on the session's host runs in its cwd, as
                      `ended` reads it: true keeps the session from ending.

A permission prompt answered mid-turn fires no hook event, so the session
reads as need_input until that turn's Stop. The hook has no archive signal, so
nothing here is archived: a session leaves the rows when its last event is
older than WINDOW_DAYS, and its events stay in `session_events`.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

SOURCE = "claude-hook"
KIND = "session"
RUN_KIND = "sessions"
WINDOW_DAYS = 30


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enabled(config: dict[str, Any]) -> bool:
    """Derive rows only on an instance that ingests snapshots."""
    return bool(config["ingest"]["machines"])


def _events(conn: sqlite3.Connection, cutoff: str) -> list[tuple]:
    """Every event of each session with an event at or after `cutoff`."""
    return conn.execute(
        "SELECT session_id, host, ts, event, cwd, branch, notification, asked"
        " FROM session_events WHERE session_id IN"
        " (SELECT session_id FROM session_events WHERE ts >= ?)"
        " ORDER BY session_id, ts, id",
        (cutoff,),
    ).fetchall()


def fold(session_id: str, events: list[tuple]) -> dict[str, Any]:
    """One session's row from its events, oldest first.

    Each event is (host, ts, event, cwd, branch, notification, asked).
    """
    has_prompt = False
    prompts = permission_prompts = 0
    waiting_since: "str | None" = None
    need_input = asked = False
    host = cwd = branch = None
    last_event = None
    for ev_host, ts, event, ev_cwd, ev_branch, notification, ev_asked in events:
        host = ev_host or host
        cwd = ev_cwd or cwd
        if ev_cwd:
            branch = ev_branch
        if event == "UserPromptSubmit":
            has_prompt = True
            prompts += 1
            waiting_since = None
            need_input = asked = False
        elif event in ("Stop", "Notification"):
            if waiting_since is None:
                waiting_since = ts
            if event == "Stop":
                # A turn cannot end while a permission prompt still blocks it.
                need_input = False
                asked = ev_asked == 1
            if event == "Notification" and notification == "permission_prompt":
                permission_prompts += 1
                need_input = True
                # What blocks the session now is the prompt, not the question.
                asked = False
        else:
            continue
        last_event = event
    working = last_event == "UserPromptSubmit" or last_event is None
    asked = asked and not working
    waits = need_input or asked
    category = None if working else ("need_input" if waits else "turn_ended")
    return {
        "kind": KIND,
        "key": session_id,
        "session_id": session_id,
        "host": host,
        "cwd": cwd,
        "branch": branch,
        "title": _label(cwd, branch),
        "status_bucket": "working" if working else "waiting",
        "status_category": category,
        "needs_action": category == "need_input",
        "asked": asked,
        "waiting_since": None if working else waiting_since,
        "has_prompt": has_prompt,
        "empty_spawn": not has_prompt,
        "prompts": prompts,
        "permission_prompts": permission_prompts,
        "brief": None,
        "created": events[0][1] if events else None,
        "updated": events[-1][1] if events else None,
    }


def _label(cwd: "str | None", branch: "str | None") -> "str | None":
    name = os.path.basename(cwd.rstrip("/")) if cwd else None
    if name and branch:
        return f"{name} @ {branch}"
    return name or branch


def registry(
    conn: sqlite3.Connection, kind: str = "registry"
) -> dict[tuple[str, str], dict[str, Any]]:
    """Each host's live registry entries (snapshot.py, schema 3), or with
    `kind = 'transcript'` its transcript summaries (schema 5), by
    (host, session id)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for source, raw in conn.execute(
        "SELECT source, raw_json FROM issues WHERE source LIKE 'machine:%'"
        " AND kind = ?",
        (kind,),
    ):
        try:
            entry = json.loads(raw or "{}")
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("session_id"):
            out[(source.split(":", 1)[1], str(entry["session_id"]))] = entry
    return out


def registry_hosts(conn: sqlite3.Connection) -> set[str]:
    """The hosts whose latest snapshot carried a registry (schema 3 or later).

    snapshot_adapter records each snapshot's `schema` in `machines.counts`; a
    row written before it did has none, and counts as no registry.
    """
    out: set[str] = set()
    for host, counts in conn.execute("SELECT host, counts FROM machines"):
        try:
            got = json.loads(counts or "{}")
        except ValueError:
            continue
        schema = got.get("schema") if isinstance(got, dict) else None
        if type(schema) is int and schema >= 3:
            out.add(host)
    return out


def live_cwds(
    conn: sqlite3.Connection, live: dict[tuple[str, str], dict[str, Any]]
) -> set[tuple[str, str]]:
    """(host, cwd) for each live registry entry: its own `cwd` (schema 4),
    else the last one its own hook session recorded, at any age."""
    out: set[tuple[str, str]] = set()
    for (host, sid), entry in live.items():
        own = entry.get("cwd")
        if isinstance(own, str) and own:
            out.add((host, own))
            continue
        found = conn.execute(
            "SELECT cwd FROM session_events WHERE host = ? AND session_id = ?"
            " AND cwd IS NOT NULL AND cwd != '' ORDER BY ts DESC, id DESC LIMIT 1",
            (host, sid),
        ).fetchone()
        if found:
            out.add((host, found[0]))
    return out


def with_registry(
    row: dict[str, Any], entry: "dict[str, Any] | None"
) -> dict[str, Any]:
    """`row` with its live registry entry's fields; all null without one.

    The registry's `name` becomes the title, since the hook records none.
    """
    entry = entry or {}
    row.update(
        bridge_session_id=entry.get("bridge_session_id"),
        live_name=entry.get("name"),
        live_status=entry.get("status"),
        pid=entry.get("pid"),
        live_updated_at=entry.get("updated_at"),
        tmux=entry.get("tmux"),
    )
    if entry.get("name"):
        row["title"] = entry["name"]
    return row


def _text(value: Any) -> "str | None":
    return value if isinstance(value, str) and value else None


def with_transcript(
    row: dict[str, Any], summary: "dict[str, Any] | None"
) -> dict[str, Any]:
    """`row` with its transcript summary's fields; all null without one, and
    the hook's branch kept unless the summary names one."""
    summary = summary or {}
    row.update(
        first_prompt=_text(summary.get("first_prompt")),
        last_ask=_text(summary.get("last_ask")),
        transcript_bridge_session_id=_text(summary.get("bridge_session_id")),
    )
    if _text(summary.get("branch")):
        row["branch"] = summary["branch"]
    return row


def derive(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """A row per session active in the last WINDOW_DAYS. Reads; writes nothing."""
    cutoff = _ts(now - timedelta(days=WINDOW_DAYS))
    by_session: dict[str, list[tuple]] = {}
    for session_id, *rest in _events(conn, cutoff):
        if session_id:
            by_session.setdefault(session_id, []).append(tuple(rest))
    live = registry(conn)
    summaries = registry(conn, "transcript")
    hosts = registry_hosts(conn)
    occupied = live_cwds(conn, live)
    rows = []
    for sid, evs in by_session.items():
        row = fold(sid, evs)
        entry = live.get((row["host"] or "", sid))
        with_registry(row, entry)
        with_transcript(row, summaries.get((row["host"] or "", sid)))
        row["cwd_held"] = (row["host"], row["cwd"]) in occupied
        row["ended"] = entry is None and row["host"] in hosts and not row["cwd_held"]
        rows.append(row)
    return rows


def write(conn: sqlite3.Connection, rows: list[dict[str, Any]], now: datetime) -> None:
    """Replace every `source = 'claude-hook'` row with `rows`, in one transaction."""
    stamp = _ts(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM issues WHERE source = ?", (SOURCE,))
        conn.executemany(
            "INSERT INTO issues (source, kind, key, summary, status_name,"
            " status_category, created, updated, raw_json, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    SOURCE, KIND, row["key"], row["title"], row["status_bucket"],
                    row["status_category"], row["created"], row["updated"],
                    json.dumps(row, sort_keys=True), stamp,
                )
                for row in rows
            ],
        )  # fmt: skip
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
