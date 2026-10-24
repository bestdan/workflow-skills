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

THE ROW. `summary` is the cwd's last component and branch (the hook records no
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


def derive(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """A row per session active in the last WINDOW_DAYS. Reads; writes nothing."""
    cutoff = _ts(now - timedelta(days=WINDOW_DAYS))
    by_session: dict[str, list[tuple]] = {}
    for session_id, *rest in _events(conn, cutoff):
        if session_id:
            by_session.setdefault(session_id, []).append(tuple(rest))
    return [fold(sid, evs) for sid, evs in by_session.items()]


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
