"""`dashboard.py plan set|show|clear`: the ordered answer to "what next".

dev_docs/designs/2026-10-30-dashboard-as-agent-data-source.md, section 5. A plan
is an agent's (or the owner's) chosen ordered list of items for one scope, a
reason each. It is overlay state (the `plans` table; export and import carry
it), so a sync never touches it.

- A plan belongs to exactly one scope id (as `items --scope` resolves it) or to
  `all`. There is at most one plan per (scope, kind).
- `standing` stays until cleared. `session` expires at the next 04:00 host
  local time after it was set, and is still listed afterwards with `expired`
  true, so a reader can say the plan has lapsed rather than that none exists.
- `set` refuses an entry that is not in the mirror, so a plan never names an
  item the dashboard cannot show. Import does not (overlay.import_).

The scope words and the mirror's items come from agent_query: there is one
resolver, so `plan set --scope x` and `items --scope x` cannot disagree.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

import agent_query
import overlay

SCHEMA_VERSION = 1
ALL = agent_query.ALL
EXPIRY_HOUR = 4
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def session_expiry(now: datetime, tz: "tzinfo | None" = None) -> datetime:
    """The next 04:00 local time after `now`, as an aware UTC datetime: that
    day's when `now` is before 04:00 local, else the next day's. `tz` is the
    host's local zone when None; tests pass one so they hold on any host."""
    local = now.astimezone(tz)
    wall = datetime(local.year, local.month, local.day, EXPIRY_HOUR)
    if local.hour >= EXPIRY_HOUR:
        wall += timedelta(days=1)
    # A naive wall time is read as the host's local time, DST included.
    aware = wall.astimezone() if tz is None else wall.replace(tzinfo=tz)
    return aware.astimezone(timezone.utc)


def resolve_scope(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    word: "str | None",
    items: "list[agent_query.Item] | None" = None,
) -> str:
    """The one scope id `word` names, or `all` (also for no word). A word that
    matches several scopes is refused, naming each, since a plan has one."""
    if word is None or word == ALL:
        return ALL
    if items is None:
        items = agent_query.collect(conn, cfg, now)
    found = agent_query.resolve(agent_query._scopes(conn, items), [word]) or []
    if len(found) != 1:
        ids = ", ".join(s.id for s in found)
        raise agent_query.QueryError(
            f"{word!r} matches {len(found)} scopes ({ids}); a plan belongs to one,"
            " so pass one scope id"
        )
    return found[0].id


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_STAMP)


def set_plan(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    *,
    scope: "str | None",
    kind: "str | None",
    entries: Any,
    author: Any,
    mode: "str | None" = None,
    minutes: "int | None" = None,
    tz: "tzinfo | None" = None,
) -> dict[str, Any]:
    """Store the plan (replacing the one for that scope and kind) and return it
    as `show` lists it. Raises OverlayError for a bad entry or value, and
    QueryError for a scope that is not exactly one."""
    clean = overlay.check_plan_entries(entries)
    who = overlay.check_plan_author(author)
    mins = overlay.check_plan_minutes(minutes)
    _check_kind(kind)
    if mins is not None and kind == "standing":
        raise overlay.OverlayError(
            "--minutes sets a session plan; drop --kind standing"
        )
    kind = kind or ("session" if mins is not None else "standing")
    if mode is not None:
        overlay.now_mode_of(mode)
    items = agent_query.collect(conn, cfg, now)
    known = {(i["source"], i["key"]) for i in items}
    missing = [
        f"{e['source']}:{e['key']}"
        for e in clean
        if (e["source"], e["key"]) not in known
    ]
    if missing:
        raise overlay.OverlayError(
            f"not in the mirror ({len(missing)}): {', '.join(missing)}"
        )
    sid = resolve_scope(conn, cfg, now, scope, items)
    expires = _stamp(session_expiry(now, tz)) if kind == "session" else None
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO plans"
            " (scope, kind, entries, author, mode, minutes, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid, kind,
                json.dumps(clean, separators=(",", ":"), ensure_ascii=False),
                who, mode, mins, _stamp(now), expires,
            ),
        )  # fmt: skip
    return _plans(conn, now, sid, kind)[0]


def _stored_scope(
    conn: sqlite3.Connection, cfg: dict[str, Any], now: datetime, word: str
) -> str:
    """A stored plan's scope id as typed, else the word resolved as `items
    --scope` does. So a plan whose scope has left the mirror can still be shown
    and cleared by its id."""
    if conn.execute("SELECT 1 FROM plans WHERE scope = ?", (word,)).fetchone():
        return word
    return resolve_scope(conn, cfg, now, word)


def _check_kind(kind: "str | None") -> None:
    if kind is not None and kind not in overlay.PLAN_KINDS:
        raise overlay.OverlayError(
            f"kind must be one of {', '.join(overlay.PLAN_KINDS)}, got {kind!r}"
        )


def _plans(
    conn: sqlite3.Connection,
    now: datetime,
    scope: "str | None",
    kind: "str | None",
) -> list[dict[str, Any]]:
    where, args = [], []
    if scope is not None:
        where.append("scope = ?")
        args.append(scope)
    if kind is not None:
        where.append("kind = ?")
        args.append(kind)
    sql = (
        "SELECT scope, kind, entries, author, mode, minutes, created_at, expires_at"
        " FROM plans"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY scope, kind"
    )
    at = _stamp(now)
    out = []
    for sc, kd, raw, author, mode, minutes, created, expires in conn.execute(sql, args):
        entries = json.loads(raw)
        ests = [e["est_min"] for e in entries if "est_min" in e]
        out.append(
            {
                "scope": sc, "kind": kd, "author": author, "mode": mode,
                "minutes": minutes, "created_at": created, "expires_at": expires,
                "expired": expires is not None and expires <= at,
                "est_min_sum": sum(ests) if ests else None,
                "entries": entries,
            }
        )  # fmt: skip
    return out


def show(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    scope: "str | None" = None,
    kind: "str | None" = None,
) -> dict[str, Any]:
    """The stored plans (all, or one scope's, or one kind), by scope then kind."""
    _check_kind(kind)
    sid = None if scope is None else _stored_scope(conn, cfg, now, scope)
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": _stamp(now),
        "plans": _plans(conn, now, sid, kind),
    }


def current(
    conn: sqlite3.Connection, now: datetime
) -> "tuple[dict[str, Any] | None, dict[str, Any] | None]":
    """(the plan Now's Plan mode shows, the newest lapsed session plan). The
    first is the newest unexpired plan of any scope, a session plan winning
    over a standing one while it lasts; None when no plan is live. The second
    is None unless the first is, so the page can say a plan lapsed."""
    every = _plans(conn, now, None, None)
    live = [p for p in every if not p["expired"]]
    best = max(
        live,
        key=lambda p: (p["kind"] == "session", p["created_at"], p["scope"]),
        default=None,
    )
    if best is not None:
        return best, None
    lapsed = [p for p in every if p["expired"]]
    return None, max(lapsed, key=lambda p: (p["expires_at"], p["scope"]), default=None)


def clear(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    now: datetime,
    scope: str,
    kind: "str | None" = None,
) -> int:
    """Delete a scope's plan of `kind`, or both kinds; the number of plans gone.
    `all` names the all-scope plan."""
    _check_kind(kind)
    sid = _stored_scope(conn, cfg, now, scope)
    with conn:
        if kind is None:
            cur = conn.execute("DELETE FROM plans WHERE scope = ?", (sid,))
        else:
            cur = conn.execute(
                "DELETE FROM plans WHERE scope = ? AND kind = ?", (sid, kind)
            )
    return cur.rowcount


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
