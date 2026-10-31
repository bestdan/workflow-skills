"""Linear issues as a Now source and an agent source: the sync step that
mirrors them.

ON with `[linear] enabled = true` and a key (linear_api.resolve_key), on every
instance: unlike the Workstreams tab's Linear projects, it does not wait for a
fleet read. Its own `sync_runs` row (kind RUN_KIND) records what happened; no
key, or a key Linear refuses, is `auth_failed` with the reason, and any other
failure is `failed`. Either way the last good rows stay.

WHAT ONE READ IS. Two reads, merged by key, every page of each:

- MINE: the issues assigned to `linear.me` (an email), or with it empty to the
  key's own user, whose state type is in linear_api.OPEN_STATE_TYPES
  (`unstarted`, `started`). Set `me` when the key was made on a different
  account from the one the issues are assigned to: without it the read
  returns the key's own account's issues instead, with no error. A `me` that
  names no Linear user is `failed` (linear_api.assigned_issues checks).
- THE BACKLOG: every issue of the teams the open Linear projects belong to
  (the team set workstreams.py derives), assigned to anyone or no one, whose
  state type is in linear_api.BACKLOG_STATE_TYPES (`backlog`, `unstarted`,
  `started`; not `triage`, which linear_api says why). It costs one projects
  read and about one page per 50 open issues. No open project, no team, and
  no backlog read.

Mine is read on its own rather than picked out of the backlog so that my
issues in a team with no open project stay on Now. Each read is a full
snapshot, and write() replaces the source's rows with it: an issue that was
finished, canceled, moved to another team or (if it was only mine) moved to
the backlog or reassigned leaves the mirror on the next good run.

THE ROW, under `source = 'linear'`, `kind = 'ticket'` (include's `tasks`
group), keyed by the identifier (`PRE-123`). `scope` is `assigned` for an
issue the MINE read returned and `team` for the rest: it is what Now reads
(fleet_now.linear_tickets ranks only `assigned` rows), so the wider backlog
never counts as mine there. It fills the Jira columns so score.score() reads
it as it reads a ticket:

    status_category          `indeterminate` for started, `new` for unstarted
                             and backlog
    status_category_changed  startedAt, else createdAt (Linear has no "state
                             changed at"): the stuck term's clock
    priority                 Linear's priorityLabel; "No priority" is NULL
    due_date, labels         dueDate, the label names
    sprint_*                 the issue's cycle, `sprint_state = 'active'`
                             when it is the active one: the deadline term
                             reads its end as Jira's sprint end

`raw_json` is linear_api's issue dict, which adds `url`, `team`, `state_type`
(so a reader tells backlog from queued), `project` and `project_id`,
`estimate`, `assignee`, `assignee_email` and `assignee_is_me` (Linear's
`isMe`, relative to the key's own user, not to `linear.me`: with `me` set, a
reader tells mine by `assignee_email`, as agent_query does, or by the row's
`scope`), `blocked_by` and `blocks` (the
open issues on each side of a `blocks` relation). fleet_now.linear_tickets
turns the `assigned` rows into candidates; agent_query lists every row.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import linear_api

SOURCE = "linear"
KIND = "ticket"
RUN_KIND = "linear_issues"

# `scope` of an issue the MINE read returned, and of the rest of the backlog.
MINE = "assigned"
TEAM = "team"

_CATEGORY = {"started": "indeterminate", "unstarted": "new"}


def enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg["linear"]["enabled"])


def to_row(issue: dict[str, Any]) -> dict[str, Any]:
    """A linear_api issue dict as `issues` columns."""
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


def write(
    conn: sqlite3.Connection,
    issues: list[dict],
    now: datetime,
    mine: "Iterable[str] | None" = None,
) -> dict:
    """Replace the source's rows with `issues`, in one transaction. `mine` is
    the keys the MINE read returned, scoped `assigned`; the rest are `team`.
    None means every issue is mine (the MINE read alone). Returns {issues,
    mine, unassigned, backlog, started, blocked, truncated, dropped}."""
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    own = None if mine is None else set(mine)
    rows = [to_row(i) for i in issues]
    scopes = [MINE if own is None or r["key"] in own else TEAM for r in rows]
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
                f" VALUES (?, ?, {', '.join('?' for _ in cols)}, ?, ?)"
                f" ON CONFLICT(source, key) DO UPDATE SET kind = excluded.kind,"
                f" {updates}, scope = excluded.scope, synced_at = excluded.synced_at",
                [(SOURCE, KIND, *r.values(), sc, stamp) for r, sc in zip(rows, scopes)],
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {
        "issues": len(issues),
        "mine": scopes.count(MINE),
        "unassigned": sum(1 for i in issues if not i.get("assignee")),
        "backlog": sum(1 for i in issues if i.get("state_type") == "backlog"),
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
    there is no usable key or Linear refuses it. Other failures raise. The
    counts add `teams` (the backlog read's, comma-joined) and `calls` (Linear
    requests made) to write()'s."""
    try:
        key = resolve_key()
    except linear_api.NoKey as exc:
        return {"linear": "no key"}, f"no Linear key ({exc.category}): {exc}"
    timeout = float(cfg["linear"]["timeout_seconds"])
    calls = 0

    def counted(body: dict, k: str, t: float) -> Any:
        nonlocal calls
        calls += 1
        return runner(body, k, t)

    try:
        mine = linear_api.assigned_issues(
            key, counted, timeout, str(cfg["linear"].get("me") or "").strip()
        )
        projects = linear_api.projects(key, counted, timeout)
        teams = sorted({t for p in projects for t in p.get("teams") or []})
        backlog = linear_api.team_issues(key, teams, counted, timeout)
    except linear_api.AuthError as exc:
        return {"linear": "auth failed", "calls": calls}, str(exc)
    merged = {i["key"]: i for i in backlog}
    merged.update({i["key"]: i for i in mine})
    issues = [merged[k] for k in sorted(merged)]
    counts = write(conn, issues, now, mine=[i["key"] for i in mine])
    return {**counts, "teams": ",".join(teams), "calls": calls}, None
