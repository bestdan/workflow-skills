"""Linear issues as a Now source: the sync step that mirrors them.

ON with `[linear] enabled = true` and a key (linear_api.resolve_key), on every
instance: unlike the Workstreams tab's Linear projects, it does not wait for a
fleet read. Its own `sync_runs` row (kind RUN_KIND) records what happened; no
key, or a key Linear refuses, is `auth_failed` with the reason, and any other
failure is `failed`. Either way the last good rows stay.

WHAT ONE READ IS. The issues assigned to `linear.me` (an email), or with it
empty to the key's own user, whose state type is in
linear_api.OPEN_STATE_TYPES (`unstarted`, `started`), every page of them. So
each read is a full snapshot, and write() replaces the source's rows with it:
an issue that was finished, canceled, moved to the backlog or reassigned
leaves the mirror on the next good run. Set `me` when the key was made on a
different account from the one the issues are assigned to: without it the
read returns the key's own account's issues instead, with no error. A `me`
that names no Linear user is `failed` (linear_api.assigned_issues checks).

THE ROW, under `source = 'linear'`, `kind = 'ticket'` (include's `tasks`
group), keyed by the identifier (`PRE-123`). It fills the Jira columns so
score.score() reads it as it reads a ticket:

    status_category          `indeterminate` for started, `new` for unstarted
    status_category_changed  startedAt, else createdAt (Linear has no "state
                             changed at"): the stuck term's clock
    priority                 Linear's priorityLabel; "No priority" is NULL
    due_date, labels         dueDate, the label names
    sprint_*                 the issue's cycle, `sprint_state = 'active'`
                             when it is the active one: the deadline term
                             reads its end as Jira's sprint end

`raw_json` is linear_api.assigned_issues's dict, which adds `url`, `team`,
`project`, `blocked_by` and `blocks` (the open issues on each side of a
`blocks` relation). fleet_now.linear_tickets turns the rows into candidates.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

import linear_api

SOURCE = "linear"
KIND = "ticket"
RUN_KIND = "linear_issues"

_CATEGORY = {"started": "indeterminate", "unstarted": "new"}


def enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg["linear"]["enabled"])


def to_row(issue: dict[str, Any]) -> dict[str, Any]:
    """An assigned_issues dict as `issues` columns."""
    label = issue.get("priority_label")
    return {
        "key": issue["key"],
        "summary": issue.get("title") or "",
        "status_name": issue.get("state"),
        "status_category": _CATEGORY.get(issue.get("state_type") or "", "new"),
        "status_category_changed": issue.get("started") or issue.get("created"),
        "priority": label if issue.get("priority") else None,
        "due_date": issue.get("due"),
        "labels": json.dumps(issue.get("labels") or []),
        "created": issue.get("created"),
        "updated": issue.get("updated"),
        "sprint_name": issue.get("cycle"),
        "sprint_state": "active" if issue.get("cycle_active") else None,
        "sprint_end": issue.get("cycle_end"),
        "raw_json": json.dumps(issue, sort_keys=True),
    }


def write(conn: sqlite3.Connection, issues: list[dict], now: datetime) -> dict:
    """Replace the source's rows with `issues`, in one transaction. Returns
    {issues, started, blocked, truncated, dropped}."""
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [to_row(i) for i in issues]
    cols = list(rows[0]) if rows else []
    conn.execute("BEGIN IMMEDIATE")
    try:
        keys = [r["key"] for r in rows]
        marks = ", ".join("?" for _ in keys)
        dropped = conn.execute(
            "DELETE FROM issues WHERE source = ?"
            + (f" AND key NOT IN ({marks})" if keys else ""),
            (SOURCE, *keys),
        ).rowcount
        if rows:
            names = ", ".join(cols)
            updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "key")
            conn.executemany(
                f"INSERT INTO issues (source, kind, {names}, scope, synced_at)"
                f" VALUES (?, ?, {', '.join('?' for _ in cols)}, 'assigned', ?)"
                f" ON CONFLICT(source, key) DO UPDATE SET kind = excluded.kind,"
                f" {updates}, synced_at = excluded.synced_at",
                [(SOURCE, KIND, *r.values(), stamp) for r in rows],
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {
        "issues": len(issues),
        "started": sum(1 for i in issues if i.get("state_type") == "started"),
        "blocked": sum(1 for i in issues if i.get("blocked_by")),
        # Labels or relations cut short at linear_api's cap of 10: a blocker
        # past the cap is missed.
        "truncated": sum(1 for i in issues if i.get("truncated")),
        "dropped": dropped,
    }


def step_body(
    cfg: dict[str, Any],
    conn: sqlite3.Connection,
    now: datetime,
    runner: linear_api.Runner = linear_api.post,
    resolve_key: Callable[[], str] = linear_api.resolve_key,
) -> "tuple[dict[str, Any], str | None]":
    """The step for sync._run_step: (counts, None), or (counts, reason) when
    there is no usable key or Linear refuses it. Other failures raise."""
    try:
        key = resolve_key()
    except linear_api.NoKey as exc:
        return {"linear": "no key"}, f"no Linear key ({exc.category}): {exc}"
    timeout = float(cfg["linear"]["timeout_seconds"])
    try:
        issues = linear_api.assigned_issues(
            key, runner, timeout, str(cfg["linear"].get("me") or "").strip()
        )
    except linear_api.AuthError as exc:
        return {"linear": "auth failed"}, str(exc)
    return write(conn, issues, now), None
