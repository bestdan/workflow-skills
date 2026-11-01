"""Keeping plans current: ticks at each sync, a session plan's outcome when it
ends, and a planned item's reason on a "why not now" answer.

dev_docs/designs/2026-10-30-dashboard-as-agent-data-source.md, section 5.

TICKS AT SYNC. Plan mode (plan_mode.py) ticks from mirror rows only, and the
GitHub issue mirror holds only open issues, so an issue closed by hand leaves
the mirror with nothing saying it closed: `gone`, not ticked. The sync's
`plans` step (sync.run_plans, run last, after every mirror step) re-reads each
such entry with refresh.py and keeps GitHub's answer in `plan_checks`, which
Plan mode reads as a closed signal for an entry not in the mirror. Only an
entry that is a GitHub issue or PR URL, not in the mirror, with no closed_pr
row and no settled check yet is re-read; an earlier `updated` answer (open on
GitHub, but no mirror row) is re-read each sync. At most MAX_REFRESH re-reads
per sync, the expired plans' entries first; the rest wait for the next sync.
With `github.scope` not "owner" nothing is re-read, since there is no issue
mirror to have left. The step runs only while some plan needs it (pending()),
so an instance with no plans records no `plans` run.

OUTCOMES. A session plan that ends leaves one `plan_outcomes` row: the plan as
set, how it ended, and each entry's state and evidence at that moment, which
is what a later calibration of `[size.minutes]` compares with `est_min`. The
first sync after its 04:00 expiry re-reads its gone entries, then records it
(`expired`); a plan whose re-read failed waits for the next sync. `plan set`
replacing a session plan and `plan clear` removing one record it first, from
the mirror as it stands (`replaced` or `cleared`, or `expired` when it already
had), so no plan ends unrecorded. A standing plan never ends, so it leaves
none. `plan outcomes` lists them.

WHY NOT NOW. A "why not now" answer on an item that a live plan lists carries
that plan's reason, action, scope and kind among its features (annotate()),
so `tune` can set the owner's verdict beside the plan's. Capture only: tune
does not read them yet.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import github_gh
import plan_mode
import plans
import refresh

RUN_KIND = "plans"
MAX_REFRESH = 10
ENDINGS = ("expired", "replaced", "cleared")
# A `plan_outcomes` row, as `plan outcomes` lists it.
OUTCOME_COLUMNS = (
    "scope", "kind", "created_at", "expires_at", "author", "mode", "minutes",
    "ended", "recorded_at", "entries",
)  # fmt: skip
# refresh outcomes that settle an entry: it is not re-read again.
_SETTLED = frozenset(refresh.REMOVED)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _has_outcome(conn: sqlite3.Connection, plan: dict[str, Any]) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM plan_outcomes WHERE scope = ? AND kind = ? AND created_at = ?",
            (plan["scope"], plan["kind"], plan["created_at"]),
        ).fetchone()
        is not None
    )


def pending(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """The plans the sync step works on: every live plan, and every expired
    session plan with no outcome yet."""
    return [
        p
        for p in plans._plans(conn, now, None, None)
        if not p["expired"] or not _has_outcome(conn, p)
    ]


def record_outcome(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    plan: dict[str, Any],
    ended: str,
) -> bool:
    """Write a session plan's outcome from the mirror as it stands; False when
    it has one already (the first record stands) or is not a session plan."""
    if plan["kind"] != "session" or ended not in ENDINGS:
        return False
    entries = [
        {**e, "state": state, "evidence": evidence}
        for e, _item, _pr, state, evidence in plan_mode.entry_states(
            conn, cfg, now, plan
        )
    ]
    with conn:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO plan_outcomes ({', '.join(OUTCOME_COLUMNS)})"
            f" VALUES ({', '.join('?' * len(OUTCOME_COLUMNS))})",
            (
                plan["scope"], plan["kind"], plan["created_at"], plan["expires_at"],
                plan["author"], plan["mode"], plan["minutes"], ended, _stamp(now),
                json.dumps(entries, separators=(",", ":"), ensure_ascii=False),
            ),
        )  # fmt: skip
    return cur.rowcount == 1


def end_session(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    scope: str,
    ended: str,
) -> None:
    """Record the scope's session plan, if any, before `plan set` replaces it
    or `plan clear` removes it; `expired` when it already had."""
    for p in plans._plans(conn, now, scope, "session"):
        record_outcome(conn, cfg, now, p, "expired" if p["expired"] else ended)


def outcomes(
    conn: sqlite3.Connection, scope: "str | None" = None
) -> list[dict[str, Any]]:
    """Every recorded outcome (or one scope's), oldest first, each with how many
    entries ended in each state."""
    sql = f"SELECT {', '.join(OUTCOME_COLUMNS)} FROM plan_outcomes"
    args: tuple[str, ...] = ()
    if scope is not None:
        sql += " WHERE scope = ?"
        args = (scope,)
    out = []
    for row in conn.execute(sql + " ORDER BY created_at, scope", args):
        o = dict(zip(OUTCOME_COLUMNS, row))
        o["entries"] = json.loads(o["entries"])
        states = [e.get("state") for e in o["entries"]]
        o["counts"] = {s: states.count(s) for s in plan_mode.STATES}
        out.append(o)
    return out


def _wants_read(
    e: dict[str, Any],
    item: Any,
    pr: "dict[str, Any] | None",
    check: "dict[str, Any] | None",
) -> bool:
    if item is not None or pr is not None or e["source"] != "github":
        return False
    if check is not None and check["outcome"] in _SETTLED:
        return False
    return bool(plan_mode._GITHUB.match(e["key"]))


def _write_check(
    conn: sqlite3.Connection, key: str, res: refresh.Result, now: datetime
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO plan_checks"
            " (source, item_key, outcome, kind, closed_at, checked_at)"
            " VALUES ('github', ?, ?, ?, ?, ?)",
            (key, res.outcome, res.kind, res.closed_at, _stamp(now)),
        )


def _prune(conn: sqlite3.Connection) -> None:
    """Drop the checks of items no stored plan lists any more."""
    planned = {
        (e["source"], e["key"])
        for (raw,) in conn.execute("SELECT entries FROM plans")
        for e in json.loads(raw)
    }
    stale = [
        (s, k)
        for s, k in conn.execute("SELECT source, item_key FROM plan_checks")
        if (s, k) not in planned
    ]
    if stale:
        with conn:
            conn.executemany(
                "DELETE FROM plan_checks WHERE source = ? AND item_key = ?", stale
            )


def step_body(
    cfg: dict[str, Any],
    conn: sqlite3.Connection,
    now: datetime,
    runner: github_gh.Runner = github_gh.run,
    limit: int = MAX_REFRESH,
    can_read: bool = True,
) -> "tuple[dict[str, int], str | None]":
    """The `plans` sync step: re-read gone GitHub entries (at most `limit`),
    then record each expired session plan's outcome. (counts, auth error)."""
    todo = pending(conn, now)
    expired = [p for p in todo if p["expired"]]
    counts = {
        "live": len(todo) - len(expired), "expired": len(expired), "read": 0,
        "ticked": 0, "read_failed": 0, "deferred": 0, "outcomes": 0,
    }  # fmt: skip
    auth_error: "str | None" = None
    checks = plan_mode._checks(conn)
    done: set[str] = set()
    waiting: set[int] = set()  # indexes in `todo` whose gone entries were not all read
    for idx, plan in sorted(enumerate(todo), key=lambda ip: not ip[1]["expired"]):
        for e, item, pr, _s, _ev in plan_mode.entry_states(conn, cfg, now, plan):
            key = e["key"]
            if key in done or not _wants_read(e, item, pr, checks.get(("github", key))):
                continue
            if not can_read:
                continue
            if auth_error is not None or counts["read"] >= limit:
                counts["deferred"] += 1
                waiting.add(idx)
                continue
            try:
                res = refresh.refresh(conn, cfg, key, now, runner)
            except refresh.RefreshError:
                done.add(key)  # not this instance's to read: nothing to wait for
                continue
            except github_gh.AuthError as exc:
                auth_error = f"{exc} (fix: run `gh auth login`)"
                counts["deferred"] += 1
                waiting.add(idx)
                continue
            except github_gh.GhError:
                counts["read_failed"] += 1
                waiting.add(idx)
                continue
            done.add(key)
            counts["read"] += 1
            _write_check(conn, key, res, now)
            if res.outcome == refresh.MERGED or (
                res.outcome == refresh.CLOSED and res.kind == "issue"
            ):
                counts["ticked"] += 1
    for idx, plan in enumerate(todo):
        if plan["expired"] and idx not in waiting:
            counts["outcomes"] += record_outcome(conn, cfg, now, plan, "expired")
    _prune(conn)
    return counts, auth_error


def annotate(
    conn: sqlite3.Connection,
    item: "tuple[str, str]",
    now: datetime,
    snap: "dict[str, Any] | None",
) -> "dict[str, Any] | None":
    """`snap` (whynot.snapshot's shape, or None) with the planning live plan's
    reason, action, scope and kind added to its features, when a live plan
    lists `item`; else `snap` as given. Of several, the plan Plan mode would
    show first: a session plan, then the newest."""
    live = [
        p
        for p in plans._plans(conn, now, None, None)
        if not p["expired"]
        and any((e["source"], e["key"]) == tuple(item) for e in p["entries"])
    ]
    if not live:
        return snap
    plan = max(
        live, key=lambda p: (p["kind"] == "session", p["created_at"], p["scope"])
    )
    entry = next(e for e in plan["entries"] if (e["source"], e["key"]) == tuple(item))
    out = dict(snap or {})
    out["features"] = {
        **(out.get("features") or {}),
        "plan_reason": entry["reason"],
        "plan_action": entry.get("action"),
        "plan_scope": plan["scope"],
        "plan_kind": plan["kind"],
    }
    return out
