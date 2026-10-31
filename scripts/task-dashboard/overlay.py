"""The overlay: the user's pins, pinned order, snoozes, notes, kind tags,
ignores, last visit, each view's remembered include filter, the answers to
Now's "why not now" picker, the fleet Now's mode, and the plans agents set.

Every row is keyed by (source, item_key), the same pair as `issues`, except
`last_visit` and `now_mode`, one row each for the whole overlay, and
`view_includes`, which is keyed by view name (include.py), and `why_not`,
which holds any number of answers per item (whynot.py reads them), and
`plans`, keyed by (scope, kind) (plans.py writes them). The sync
never writes these tables, and nothing here reads `issues`: a row for an item
that has left the mirror is kept as is, and the views simply have no card to
show it on. It is the only state on the machine that cannot be rebuilt from
Jira, which is why `export` and `import` exist.

Writes arrive as POSTs that server.py has already put through its Host, Origin
and token checks; `apply()` is what it calls with the parsed form. Rendering
helpers for the card controls live here too, so a view only has to put
`card_attrs()` on each card's root element, call `card_controls()` inside it,
and include `CSS` and `JS`.
"""

import html
import json
import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import db
import icons
import include
import score

EXPORT_FORMAT = "task-dashboard-overlay"
# 2 added kind_tags, focus and last_visit; 3 added ignores; 4 added
# view_includes; 5 dropped focus, a feature since removed; 6 added why_not; 7
# added now_mode; 8 added plans. An older file still imports, with what it lacks
# empty and its focus rows dropped, so a backup taken before an upgrade can be
# restored.
EXPORT_VERSION = 8
IMPORT_VERSIONS = (1, 2, 3, 4, 5, 6, 7, 8)
MAX_KEY = 512
MAX_BODY = 256 * 1024  # a POST body; room for a long note, percent-encoded
MAX_NOTE = 20_000

# Value columns per table, in export order. (source, item_key) comes first in
# every table and is the primary key.
COLUMNS: dict[str, tuple[str, ...]] = {
    "pins": ("pinned_at",),
    "order": ("position",),
    "snoozes": ("until",),
    "notes": ("body", "updated_at"),
    "kind_tags": ("tag", "tagged_at"),
    "ignores": ("ignored_at",),
}
assert tuple(COLUMNS) == db.OVERLAY_TABLES
V1_TABLES = ("pins", "order", "snoozes", "notes")
# Versions 2 to 4 also carry `focus`, whose rows are accepted and dropped.
V2_TABLES = (*V1_TABLES, "kind_tags", "focus")
V3_TABLES = (*V2_TABLES, "ignores")

KIND_TAGS = ("side-quest", "substantial")
# The Snooze button hides a card for this many days; Unsnooze clears it.
SNOOZE_DAYS = 7
# The lengths Now's snoozed line offers instead, in days; SNOOZE_DAYS among them.
SNOOZE_CHOICES = (1, 3, 7, 14, 30)

# The "why not now" picker's reasons, (code, label), in the order it shows
# them. A code is what is stored, so a label may change and a code may not.
WHY_NOT_REASONS = (
    ("not_urgent", "not urgent"),
    ("blocked", "blocked / waiting on someone"),
    ("wrong_priority", "wrong priority (too high)"),
    ("elsewhere", "doing it elsewhere"),
    ("other", "other"),
)
WHY_NOT_CODES = tuple(code for code, _ in WHY_NOT_REASONS)
MAX_WHY_TEXT = 500
# A `why_not` row as export writes it; `id` is the DB's and is not carried.
WHY_NOT_COLUMNS = (
    "source", "item_key", "kind", "title", "reason", "text", "score", "terms",
    "features", "recorded_at",
)  # fmt: skip

# The fleet Now's modes (fleet_now.py), in the order its button cycles them.
# The first is the default: what Now shows with no saved mode.
NOW_MODES = ("ship", "little", "deep", "tidy", "plan")

# Plans (plans.py). `standing` stays until cleared; `session` expires.
PLAN_KINDS = ("standing", "session")
PLAN_ACTIONS = ("do", "finish", "unblock", "review", "defer", "drop")
MAX_PLAN_ENTRIES = 200
MAX_PLAN_REASON = 500
MAX_PLAN_AUTHOR = 200
PLAN_ENTRY_FIELDS = {"source", "key", "reason", "action", "est_min"}
# A `plans` row as export writes it.
PLAN_COLUMNS = (
    "scope", "kind", "entries", "author", "mode", "minutes", "created_at",
    "expires_at",
)  # fmt: skip

ACTIONS = (
    "pin", "unpin", "order", "snooze", "note", "tag", "visit", "ignore",
    "unignore", "include", "whynot", "mode",
)  # fmt: skip

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

Item = tuple[str, str]


class OverlayError(ValueError):
    """A write or an import file that is refused; the overlay is unchanged."""


def _now(now: "datetime | None") -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ident(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OverlayError(f"{what} must be a non-empty string")
    if len(value) > MAX_KEY or _CONTROL.search(value):
        raise OverlayError(f"{what} is too long or has control characters")
    return value


def check_plan_entries(value: Any, what: str = "entries") -> "list[dict[str, Any]]":
    """A plan's entries, validated and normalised (field order fixed); else
    OverlayError naming the entry. Whether each item is in the mirror is the
    caller's to check: import does not, so a backup restores whole."""
    if not isinstance(value, list) or not value:
        raise OverlayError(f"{what} must be a non-empty JSON list")
    if len(value) > MAX_PLAN_ENTRIES:
        raise OverlayError(f"{what} has more than {MAX_PLAN_ENTRIES} entries")
    out: list[dict[str, Any]] = []
    seen: set[Item] = set()
    for i, e in enumerate(value):
        where = f"{what}[{i}]"
        if not isinstance(e, dict):
            raise OverlayError(f"{where} must be an object")
        extra = sorted(set(e) - PLAN_ENTRY_FIELDS)
        if extra:
            raise OverlayError(f"{where}: unknown field(s): {', '.join(extra)}")
        for need in ("source", "key", "reason"):
            if need not in e:
                raise OverlayError(f"{where}: {need} is required")
        item = (
            _ident(e["source"], f"{where}: source"),
            _ident(e["key"], f"{where}: key"),
        )
        reason = e["reason"]
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > MAX_PLAN_REASON
            or _CONTROL.search(reason)
        ):
            raise OverlayError(
                f"{where}: reason must be one non-empty line of at most"
                f" {MAX_PLAN_REASON} characters"
            )
        if item in seen:
            raise OverlayError(f"{where}: duplicate {item[0]}:{item[1]}")
        seen.add(item)
        entry: dict[str, Any] = {"source": item[0], "key": item[1], "reason": reason}
        if e.get("action") is not None:
            if e["action"] not in PLAN_ACTIONS:
                raise OverlayError(
                    f"{where}: action must be one of {', '.join(PLAN_ACTIONS)},"
                    f" got {e['action']!r}"
                )
            entry["action"] = e["action"]
        if e.get("est_min") is not None:
            est = e["est_min"]
            if type(est) is not int or est < 0:
                raise OverlayError(f"{where}: est_min must be a non-negative integer")
            entry["est_min"] = est
        out.append(entry)
    return out


def check_plan_author(value: Any, what: str = "author") -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_PLAN_AUTHOR
        or _CONTROL.search(value)
    ):
        raise OverlayError(
            f"{what} must be a non-empty line of at most {MAX_PLAN_AUTHOR} characters"
        )
    return value


def check_plan_minutes(value: Any, what: str = "minutes") -> "int | None":
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise OverlayError(f"{what} must be a positive integer, got {value!r}")
    return value


def _iso_date(value: Any) -> str:
    """A strict YYYY-MM-DD that is also a real calendar date."""
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise OverlayError(f"snooze date must be YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise OverlayError(f"snooze date is not a real date: {value!r}") from None
    return value


# --- writes ------------------------------------------------------------------


def pin(conn: sqlite3.Connection, item: Item, now: "datetime | None" = None) -> None:
    """Pin an item, last in the pinned order. Pinning a pinned item is a no-op."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO pins (source, item_key, pinned_at) VALUES (?, ?, ?)",
            (*item, _now(now)),
        )
        if cur.rowcount:
            conn.execute(
                'INSERT OR REPLACE INTO "order" (source, item_key, position)'
                ' SELECT ?, ?, COALESCE(MAX(position), 0) + 1 FROM "order"',
                item,
            )


def unpin(conn: sqlite3.Connection, item: Item) -> None:
    with conn:
        conn.execute("DELETE FROM pins WHERE source = ? AND item_key = ?", item)
        conn.execute('DELETE FROM "order" WHERE source = ? AND item_key = ?', item)


def set_order(conn: sqlite3.Connection, items: "list[Item]") -> None:
    """Put the listed pinned items first, in that order.

    The page can only list the pinned items it shows, and a pinned item whose
    issue left the mirror has no card. Those keep their relative order, after
    the listed ones, rather than being dropped or colliding on a position.
    """
    if len(set(items)) != len(items):
        raise OverlayError("order lists an item twice")
    listed = set(items)
    with conn:
        # IMMEDIATE: the pinned set read below must still hold at the write.
        conn.execute("BEGIN IMMEDIATE")
        pinned = set(conn.execute("SELECT source, item_key FROM pins").fetchall())
        missing = [f"{s}:{k}" for s, k in items if (s, k) not in pinned]
        if missing:
            raise OverlayError(f"order names unpinned items: {', '.join(missing)}")
        rest = [
            (s, k)
            for s, k in conn.execute(
                'SELECT p.source, p.item_key FROM pins p LEFT JOIN "order" o'
                " USING (source, item_key)"
                " ORDER BY o.position IS NULL, o.position, p.pinned_at, p.item_key"
            )
            if (s, k) not in listed
        ]
        conn.execute('DELETE FROM "order"')
        conn.executemany(
            'INSERT INTO "order" (source, item_key, position) VALUES (?, ?, ?)',
            [(s, k, float(i)) for i, (s, k) in enumerate(items + rest, start=1)],
        )


def snooze(conn: sqlite3.Connection, item: Item, until: str) -> None:
    """Snooze until a date (YYYY-MM-DD); an empty date clears the snooze."""
    with conn:
        if not until:
            conn.execute("DELETE FROM snoozes WHERE source = ? AND item_key = ?", item)
            return
        conn.execute(
            "INSERT OR REPLACE INTO snoozes (source, item_key, until) VALUES (?, ?, ?)",
            (*item, _iso_date(until)),
        )


def why_not(
    conn: sqlite3.Connection,
    item: Item,
    reason: str,
    text: str = "",
    snap: "dict[str, Any] | None" = None,
    now: "datetime | None" = None,
) -> None:
    """Record why an item that is already snoozed was snoozed.

    Snooze writes the snooze; this only adds the answer, so it never snoozes
    again and refuses an item with no snooze to explain. `snap` is the item as
    Now ranked it at this moment (whynot.snapshot): its kind, title, fleet
    score, score terms and features. None when Now no longer ranks it, as
    from a page left open: the answer is still kept, with what is known.
    """
    if reason not in WHY_NOT_CODES:
        raise OverlayError(
            f"reason must be one of {', '.join(WHY_NOT_CODES)}, got {reason!r}"
        )
    if len(text) > MAX_WHY_TEXT:
        raise OverlayError(f"the reason text is longer than {MAX_WHY_TEXT} characters")
    snap = snap or {}
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            "SELECT 1 FROM snoozes WHERE source = ? AND item_key = ?", item
        ).fetchone():
            raise OverlayError("whynot needs a snoozed item: snooze it first")
        conn.execute(
            "INSERT INTO why_not (source, item_key, kind, title, reason, text,"
            " score, terms, features, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *item,
                snap.get("kind"),
                snap.get("title"),
                reason,
                text.strip() or None,
                snap.get("score"),
                json.dumps(snap.get("terms") or []),
                json.dumps(snap.get("features") or {}, sort_keys=True),
                _now(now),
            ),
        )


SUBSTANTIAL_NEEDS_NOTE = (
    "an item tagged substantial must name the figure it moves in its note"
)


def _write_note(
    conn: sqlite3.Connection, item: Item, text: str, now: "datetime | None"
) -> None:
    """The note write itself, inside the caller's transaction."""
    if len(text) > MAX_NOTE:
        raise OverlayError(f"note is longer than {MAX_NOTE} characters")
    if not text.strip():
        conn.execute("DELETE FROM notes WHERE source = ? AND item_key = ?", item)
        return
    conn.execute(
        "INSERT OR REPLACE INTO notes (source, item_key, body, updated_at)"
        " VALUES (?, ?, ?, ?)",
        (*item, text, _now(now)),
    )


def note(
    conn: sqlite3.Connection, item: Item, text: str, now: "datetime | None" = None
) -> None:
    """Set the item's note; blank text deletes it.

    A substantial item's note cannot be blanked: untag it first.
    """
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if not text.strip():
            row = conn.execute(
                "SELECT tag FROM kind_tags WHERE source = ? AND item_key = ?", item
            ).fetchone()
            if row and row[0] == "substantial":
                raise OverlayError(SUBSTANTIAL_NEEDS_NOTE + "; untag it first")
        _write_note(conn, item, text, now)


def tag(
    conn: sqlite3.Connection,
    item: Item,
    kind: str,
    text: str = "",
    now: "datetime | None" = None,
) -> None:
    """Tag an item `side-quest` or `substantial`; an empty tag clears it.

    `substantial` needs a note naming the figure the item moves. Non-blank
    `text` is saved as that note in the same transaction; otherwise the item
    must already have one. `text` is ignored for any other tag, so it can
    never overwrite a note. Re-tagging with the same tag keeps `tagged_at`.
    """
    if kind and kind not in KIND_TAGS:
        raise OverlayError(f"tag must be one of {', '.join(KIND_TAGS)}, got {kind!r}")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if not kind:
            conn.execute(
                "DELETE FROM kind_tags WHERE source = ? AND item_key = ?", item
            )
            return
        if kind == "substantial" and text.strip():
            _write_note(conn, item, text, now)
        if kind == "substantial":
            row = conn.execute(
                "SELECT body FROM notes WHERE source = ? AND item_key = ?", item
            ).fetchone()
            if not row or not (row[0] or "").strip():
                raise OverlayError(SUBSTANTIAL_NEEDS_NOTE)
        conn.execute(
            "INSERT INTO kind_tags (source, item_key, tag, tagged_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT (source, item_key) DO UPDATE"
            " SET tag = excluded.tag, tagged_at = excluded.tagged_at"
            " WHERE tag != excluded.tag",
            (*item, kind, _now(now)),
        )


def ignore(conn: sqlite3.Connection, item: Item, now: "datetime | None" = None) -> None:
    """Mark an item as not mine; ignoring an epic hides everything under it."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO ignores (source, item_key, ignored_at)"
            " VALUES (?, ?, ?)",
            (*item, _now(now)),
        )


def under_ignored(
    key: str,
    parent: "str | None",
    parent_of: "Callable[[str], str | None]",
    ignored: "set[str]",
) -> bool:
    """`key` is ignored, or so is an ancestor reachable through `parent_of`.

    The walk stops at an ancestor the mirror does not hold, and at a key it
    has already seen, so a parent cycle in the data cannot loop.
    """
    if key in ignored:
        return True
    seen = {key}
    while parent and parent not in seen:
        if parent in ignored:
            return True
        seen.add(parent)
        parent = parent_of(parent)
    return False


def _jira_parent(conn: sqlite3.Connection, key: str) -> "str | None":
    row = conn.execute(
        "SELECT parent_key FROM issues WHERE source = 'jira' AND key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def hidden(conn: sqlite3.Connection, ignored: "set[Item]", item: Item) -> bool:
    """Ignored, or a Jira item under an ignored ancestor (an epic, a story)."""
    if item in ignored:
        return True
    jira = {k for s, k in ignored if s == "jira"}
    if item[0] != "jira" or not jira:
        return False

    def parent_of(key: str) -> "str | None":
        return _jira_parent(conn, key)

    return under_ignored(item[1], parent_of(item[1]), parent_of, jira)


def unignore(conn: sqlite3.Connection, item: Item) -> None:
    with conn:
        conn.execute("DELETE FROM ignores WHERE source = ? AND item_key = ?", item)


def set_last_visit(conn: sqlite3.Connection, now: "datetime | None" = None) -> None:
    """Record `now` as the last visit, unless a later one is already recorded.

    Needs you's "Mark as read" calls it, through the `visit` action, with the
    time the page was rendered. A tab left open on an older render
    therefore cannot move the visit back and re-show what was already read.
    """
    with conn:
        conn.execute(
            "INSERT INTO last_visit (id, at) VALUES (1, ?) ON CONFLICT (id)"
            " DO UPDATE SET at = excluded.at WHERE excluded.at > at",
            (_now(now),),
        )


def set_view_include(
    conn: sqlite3.Connection,
    view: str,
    groups: "frozenset[str]",
    now: "datetime | None" = None,
) -> None:
    """Remember `groups` as the view's include choice (include.py). A retired
    view's choice is dropped, not refused: a page open from before it went
    can still post one."""
    if view in include.RETIRED:
        return
    if view not in include.VIEWS:
        raise OverlayError(
            f"view must be one of {', '.join(include.VIEWS)}, got {view!r}"
        )
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO view_includes (view, groups, set_at)"
            " VALUES (?, ?, ?)",
            (view, ",".join(include.canonical(groups)), _now(now)),
        )


def now_mode_of(value: Any, what: str = "mode") -> str:
    """`value` if it is one of NOW_MODES; else OverlayError."""
    if value not in NOW_MODES:
        raise OverlayError(
            f"{what} must be one of {', '.join(NOW_MODES)}, got {value!r}"
        )
    return value


def set_now_mode(
    conn: sqlite3.Connection, mode: str, now: "datetime | None" = None
) -> None:
    """Save the fleet Now's mode, as its mode button does."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO now_mode (id, mode, set_at) VALUES (1, ?, ?)",
            (now_mode_of(mode), _now(now)),
        )


def saved_now_mode(conn: sqlite3.Connection) -> "str | None":
    """The saved mode, or None when none is saved or it is no longer a mode."""
    row = conn.execute("SELECT mode FROM now_mode WHERE id = 1").fetchone()
    return row[0] if row and row[0] in NOW_MODES else None


def _visit_at(value: str, now: "datetime | None") -> datetime:
    """The `visit` action's `at`: a page's render time, never past `now`."""
    now = now or datetime.now(timezone.utc)
    if not value:
        return now
    if not _STAMP.fullmatch(value):
        raise OverlayError(f"at must be YYYY-MM-DDTHH:MM:SSZ, got {value!r}")
    try:
        at = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise OverlayError(f"at is not a real time: {value!r}") from None
    return min(at.replace(tzinfo=timezone.utc), now)


def apply(
    conn: sqlite3.Connection,
    action: str,
    form: "dict[str, list[str]]",
    now: "datetime | None" = None,
    snapshot: "Callable[[Item], dict[str, Any] | None] | None" = None,
) -> None:
    """One overlay POST, from its parsed form. Raises OverlayError on bad input.

    Fields: `source` (default `jira`) and `key`; `order` repeats `key`, one per
    pinned item in the new order; `snooze` takes `until`, `note` takes `text`,
    `tag` takes `tag` (empty clears) and an optional `text` for the note.
    `visit` takes no key, only `at`: the render time of the page whose
    "Mark as read" was pressed (absent means now). `include` takes no key,
    only `view` and `include`, the view's comma-joined groups (empty: none).
    `whynot` takes `reason` and an optional `text` for an item that is
    already snoozed, and asks `snapshot` for the item as Now ranks it. `mode`
    takes no key, only `mode`, one of NOW_MODES.
    """
    if action not in ACTIONS:
        raise OverlayError(f"unknown overlay action: {action!r}")
    if action == "mode":
        set_now_mode(conn, (form.get("mode") or [""])[0].strip(), now)
        return
    if action == "include":
        try:
            groups = include.parse(form.get("include") or [""])
        except include.IncludeError as exc:
            raise OverlayError(str(exc)) from None
        view = (form.get("view") or [""])[0].strip()
        set_view_include(conn, view, groups, now)
        return
    if action == "visit":
        set_last_visit(conn, _visit_at((form.get("at") or [""])[0].strip(), now))
        return
    source = _ident((form.get("source") or ["jira"])[0], "source")
    keys = [_ident(k, "key") for k in form.get("key") or []]
    if action == "order":
        set_order(conn, [(source, k) for k in keys])
        return
    if len(keys) != 1:
        raise OverlayError("exactly one key is required")
    item = (source, keys[0])
    if action == "pin":
        pin(conn, item, now)
    elif action == "unpin":
        unpin(conn, item)
    elif action == "snooze":
        snooze(conn, item, (form.get("until") or [""])[0].strip())
    elif action == "whynot":
        why_not(
            conn,
            item,
            (form.get("reason") or [""])[0].strip(),
            (form.get("text") or [""])[0],
            snapshot(item) if snapshot else None,
            now,
        )
    elif action == "tag":
        kind = (form.get("tag") or [""])[0].strip()
        tag(conn, item, kind, (form.get("text") or [""])[0], now)
    elif action == "ignore":
        ignore(conn, item, now)
    elif action == "unignore":
        unignore(conn, item)
    else:
        note(conn, item, (form.get("text") or [""])[0], now)


# --- reads -------------------------------------------------------------------


@dataclass
class Overlay:
    """Every overlay table, keyed by (source, item_key), plus the last visit."""

    pinned_at: "dict[Item, str | None]" = field(default_factory=dict)
    position: "dict[Item, float | None]" = field(default_factory=dict)
    snoozed_until: "dict[Item, str]" = field(default_factory=dict)
    notes: "dict[Item, str]" = field(default_factory=dict)
    kind_tags: "dict[Item, str]" = field(default_factory=dict)
    last_visit: "str | None" = None
    ignored: "set[Item]" = field(default_factory=set)
    view_includes: "dict[str, frozenset[str]]" = field(default_factory=dict)
    now_mode: "str | None" = None  # None: none saved, which is NOW_MODES[0]

    def is_pinned(self, item: Item) -> bool:
        return item in self.pinned_at

    def is_snoozed(self, item: Item, now: datetime) -> bool:
        """True until local midnight starting the snooze date.

        The same rule as the Now view: `score.parse_ts` turns the date-only
        `until` into local midnight. What a snooze does is the view's call:
        the Board only dims a snoozed card, because its lanes must match
        /list-tasks.
        """
        until = score.parse_ts(self.snoozed_until.get(item))
        return until is not None and until > now

    def pin_rank(self, item: Item) -> "tuple[bool, float, str, str]":
        """Sort key for pinned items: manual order, then pin time."""
        pos = self.position.get(item)
        return (pos is None, pos or 0.0, self.pinned_at.get(item) or "", item[1])


def load(conn: sqlite3.Connection) -> Overlay:
    """Every overlay table, for rendering."""
    ov = Overlay()
    for s, k, at in conn.execute("SELECT source, item_key, pinned_at FROM pins"):
        ov.pinned_at[(s, k)] = at
    for s, k, pos in conn.execute('SELECT source, item_key, position FROM "order"'):
        ov.position[(s, k)] = pos
    for s, k, until in conn.execute("SELECT source, item_key, until FROM snoozes"):
        if until:
            ov.snoozed_until[(s, k)] = until
    for s, k, body in conn.execute("SELECT source, item_key, body FROM notes"):
        if body:
            ov.notes[(s, k)] = body
    for s, k, kind in conn.execute("SELECT source, item_key, tag FROM kind_tags"):
        ov.kind_tags[(s, k)] = kind
    ov.ignored = {
        (s, k) for s, k in conn.execute("SELECT source, item_key FROM ignores")
    }
    row = conn.execute("SELECT at FROM last_visit WHERE id = 1").fetchone()
    ov.last_visit = row[0] if row else None
    ov.view_includes = view_includes(conn)
    ov.now_mode = saved_now_mode(conn)
    return ov


def view_includes(conn: sqlite3.Connection) -> "dict[str, frozenset[str]]":
    """Every remembered include choice, by view; a retired view's is left out."""
    return {
        view: frozenset(g for g in str(groups).split(",") if g in include.GROUPS)
        for view, groups in conn.execute("SELECT view, groups FROM view_includes")
        if view not in include.RETIRED
    }


# --- export / import ---------------------------------------------------------


def export(conn: sqlite3.Connection, now: "datetime | None" = None) -> dict[str, Any]:
    """The overlay as a versioned, JSON-ready document, rows sorted by key."""
    tables: dict[str, list[dict[str, Any]]] = {}
    last_visit: "str | None" = None
    views: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    mode: "dict[str, Any] | None" = None
    plans: list[dict[str, Any]] = []
    # One read transaction, so every table comes from the same snapshot: a pin
    # committed between two SELECTs would otherwise give a file import refuses.
    conn.execute("BEGIN")
    try:
        for table, cols in COLUMNS.items():
            names = ("source", "item_key", *cols)
            cur = conn.execute(
                f'SELECT {", ".join(names)} FROM "{table}" ORDER BY source, item_key'
            )
            tables[table] = [dict(zip(names, row)) for row in cur]
        row = conn.execute("SELECT at FROM last_visit WHERE id = 1").fetchone()
        last_visit = row[0] if row else None
        views = [
            {"view": v, "include": [g for g in str(gs).split(",") if g], "set_at": at}
            for v, gs, at in conn.execute(
                "SELECT view, groups, set_at FROM view_includes ORDER BY view"
            )
            if v not in include.RETIRED
        ]
        cur = conn.execute(
            f"SELECT {', '.join(WHY_NOT_COLUMNS)} FROM why_not ORDER BY recorded_at, id"
        )
        for row in cur:
            answer = dict(zip(WHY_NOT_COLUMNS, row))
            answer["terms"] = _json_or(answer["terms"], [])
            answer["features"] = _json_or(answer["features"], {})
            answers.append(answer)
        row = conn.execute("SELECT mode, set_at FROM now_mode WHERE id = 1").fetchone()
        if row and row[0] in NOW_MODES:
            mode = {"mode": row[0], "set_at": row[1]}
        for row in conn.execute(
            f"SELECT {', '.join(PLAN_COLUMNS)} FROM plans ORDER BY scope, kind"
        ):
            plan = dict(zip(PLAN_COLUMNS, row))
            plan["entries"] = _json_or(plan["entries"], [])
            plans.append(plan)
    finally:
        conn.rollback()
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _now(now),
        "last_visit": last_visit,
        "view_includes": views,
        "why_not": answers,
        "now_mode": mode,
        "plans": plans,
        "tables": tables,
    }


def _json_or(text: "str | None", default: Any) -> Any:
    try:
        value = json.loads(text or "")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _finite(value: "int | float") -> bool:
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float, or for SQLite
        return False


def _check_value(table: str, col: str, value: Any, where: str) -> None:
    if col == "position":
        ok = value is None or (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and _finite(value)
        )
    else:
        ok = value is None or isinstance(value, str)
    if not ok:
        raise OverlayError(f"{where}: {col} has the wrong type")
    if table == "snoozes" and value is not None:
        _iso_date(value)
    if col == "tag" and value not in KIND_TAGS:
        raise OverlayError(f"{where}: tag must be one of {', '.join(KIND_TAGS)}")
    # The write path's limit too, or an imported note could never be re-saved.
    if col == "body" and isinstance(value, str) and len(value) > MAX_NOTE:
        raise OverlayError(f"{where}: note is longer than {MAX_NOTE} characters")


def _check_last_visit(value: Any) -> "str | None":
    if value is None:
        return None
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise OverlayError("last_visit must be null or YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise OverlayError(f"last_visit is not a real time: {value!r}") from None
    return value


def _check_view_includes(value: Any) -> list[tuple[Any, ...]]:
    """A version 4 file's `view_includes`, as (view, groups, set_at) rows."""
    if not isinstance(value, list):
        raise OverlayError("view_includes must be a list")
    out: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for i, row in enumerate(value):
        where = f"view_includes[{i}]"
        if not isinstance(row, dict) or set(row) != {"view", "include", "set_at"}:
            raise OverlayError(f"{where} must have exactly: view, include, set_at")
        view, groups, at = row["view"], row["include"], row["set_at"]
        if view in include.RETIRED:
            continue  # a backup from before the view went: dropped, not refused
        if view not in include.VIEWS:
            raise OverlayError(
                f"{where}: view must be one of {', '.join(include.VIEWS)}"
            )
        if view in seen:
            raise OverlayError(f"{where}: duplicate view {view}")
        seen.add(view)
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            raise OverlayError(f"{where}: include must be a list of group names")
        try:
            parsed = include.parse(groups)
        except include.IncludeError as exc:
            raise OverlayError(f"{where}: {exc}") from None
        if at is not None and (not isinstance(at, str) or not _STAMP.fullmatch(at)):
            raise OverlayError(f"{where}: set_at must be null or YYYY-MM-DDTHH:MM:SSZ")
        out.append((view, ",".join(include.canonical(parsed)), at))
    return out


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and _finite(value)
    )


def _check_why_not(value: Any) -> list[tuple[Any, ...]]:
    """A version 6 file's `why_not`, as rows in WHY_NOT_COLUMNS order."""
    if not isinstance(value, list):
        raise OverlayError("why_not must be a list")
    out: list[tuple[Any, ...]] = []
    for i, row in enumerate(value):
        where = f"why_not[{i}]"
        if not isinstance(row, dict) or set(row) != set(WHY_NOT_COLUMNS):
            raise OverlayError(
                f"{where} must have exactly: {', '.join(WHY_NOT_COLUMNS)}"
            )
        _ident(row["source"], "source")
        _ident(row["item_key"], "key")
        for col in ("kind", "title", "text"):
            if row[col] is not None and not isinstance(row[col], str):
                raise OverlayError(f"{where}: {col} has the wrong type")
        if row["reason"] not in WHY_NOT_CODES:
            raise OverlayError(
                f"{where}: reason must be one of {', '.join(WHY_NOT_CODES)}"
            )
        if isinstance(row["text"], str) and len(row["text"]) > MAX_WHY_TEXT:
            raise OverlayError(
                f"{where}: text is longer than {MAX_WHY_TEXT} characters"
            )
        if row["score"] is not None and not _number(row["score"]):
            raise OverlayError(f"{where}: score has the wrong type")
        terms = row["terms"]
        if not isinstance(terms, list) or not all(
            isinstance(t, list)
            and len(t) == 3
            and isinstance(t[0], str)
            and _number(t[1])
            and isinstance(t[2], str)
            for t in terms
        ):
            raise OverlayError(f"{where}: terms must be [name, value, why] triples")
        features = row["features"]
        if not isinstance(features, dict) or not all(
            v is None or isinstance(v, str) or _number(v) for v in features.values()
        ):
            raise OverlayError(
                f"{where}: features must map names to text, numbers or null"
            )
        at = row["recorded_at"]
        if not isinstance(at, str) or not _STAMP.fullmatch(at):
            raise OverlayError(f"{where}: recorded_at must be YYYY-MM-DDTHH:MM:SSZ")
        out.append(
            tuple(
                json.dumps(row[c], sort_keys=c == "features")
                if c in ("terms", "features")
                else row[c]
                for c in WHY_NOT_COLUMNS
            )
        )
    return out


def _check_now_mode(value: Any) -> list[tuple[Any, ...]]:
    """A version 7 file's `now_mode`, as zero or one (mode, set_at) rows."""
    if value is None:
        return []
    if not isinstance(value, dict) or set(value) != {"mode", "set_at"}:
        raise OverlayError("now_mode must be null or have exactly: mode, set_at")
    at = value["set_at"]
    if at is not None and (not isinstance(at, str) or not _STAMP.fullmatch(at)):
        raise OverlayError("now_mode: set_at must be null or YYYY-MM-DDTHH:MM:SSZ")
    return [(now_mode_of(value["mode"], "now_mode: mode"), at)]


def _check_plans(value: Any) -> list[tuple[Any, ...]]:
    """A version 8 file's `plans`, as rows in PLAN_COLUMNS order. The entries
    are checked for shape only, never against the mirror: a backup must
    restore even when an item has since left it."""
    if not isinstance(value, list):
        raise OverlayError("plans is not a list")
    out: list[tuple[Any, ...]] = []
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(value):
        where = f"plans[{i}]"
        if not isinstance(row, dict) or set(row) != set(PLAN_COLUMNS):
            raise OverlayError(f"{where} must have exactly: {', '.join(PLAN_COLUMNS)}")
        scope = _ident(row["scope"], f"{where}: scope")
        if row["kind"] not in PLAN_KINDS:
            raise OverlayError(f"{where}: kind must be one of {', '.join(PLAN_KINDS)}")
        if (scope, row["kind"]) in seen:
            raise OverlayError(f"{where}: duplicate plan {scope} {row['kind']}")
        seen.add((scope, row["kind"]))
        entries = check_plan_entries(row["entries"], f"{where}: entries")
        mode = row["mode"]
        if mode is not None:
            now_mode_of(mode, f"{where}: mode")
        for col in ("created_at", "expires_at"):
            at = row[col]
            if (at is None and col == "created_at") or (
                at is not None and (not isinstance(at, str) or not _STAMP.fullmatch(at))
            ):
                raise OverlayError(f"{where}: {col} must be YYYY-MM-DDTHH:MM:SSZ")
        # What set_plan writes: only a session plan expires, after it was made,
        # and only a session plan carries minutes.
        if (row["kind"] == "standing") != (row["expires_at"] is None):
            raise OverlayError(
                f"{where}: a standing plan has no expires_at and a session plan has one"
            )
        if row["expires_at"] is not None and row["expires_at"] <= row["created_at"]:
            raise OverlayError(f"{where}: expires_at must be after created_at")
        if row["kind"] == "standing" and row["minutes"] is not None:
            raise OverlayError(f"{where}: minutes belongs to a session plan")
        out.append(
            (
                scope,
                row["kind"],
                json.dumps(entries, separators=(",", ":"), ensure_ascii=False),
                check_plan_author(row["author"], f"{where}: author"),
                mode,
                check_plan_minutes(row["minutes"], f"{where}: minutes"),
                row["created_at"],
                row["expires_at"],
            )
        )
    return out


def validate(
    doc: Any,
) -> "tuple[dict[str, list[tuple[Any, ...]]], str | None]":
    """Check a whole import document; return each table's rows as tuples, and
    the last visit. The `view_includes` entry holds the view filters' rows.

    A version 1 file has only the first four tables and no `last_visit`; the
    tables added in version 2 (and `ignores`, added in 3) come back empty. A
    version 1 to 3 file has no `view_includes`, which comes back empty. A
    version 2 to 4 file's `focus` rows are dropped unread. A version 1 to 5
    file has no `why_not`, which comes back empty. A version 1 to 6 file has
    no `now_mode`, which comes back empty: Now reads that as its first mode. A
    version 1 to 7 file has no `plans`, which comes back empty.
    """
    if not isinstance(doc, dict):
        raise OverlayError("import file is not a JSON object")
    if doc.get("format") != EXPORT_FORMAT:
        raise OverlayError(f"import file is not a {EXPORT_FORMAT} export")
    version = doc.get("version")
    if type(version) is not int or version not in IMPORT_VERSIONS:
        raise OverlayError(
            f"import file version {version!r} is not one of"
            f" {', '.join(map(str, IMPORT_VERSIONS))}"
        )
    expected = {1: V1_TABLES, 2: V2_TABLES, 3: V3_TABLES, 4: V3_TABLES}.get(
        version, tuple(COLUMNS)
    )
    tables = doc.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(expected):
        raise OverlayError(f"tables must be exactly: {', '.join(expected)}")
    if version == 1:
        if "last_visit" in doc:
            raise OverlayError("a version 1 file has no last_visit")
        last_visit = None
    else:
        if "last_visit" not in doc:
            raise OverlayError("last_visit is missing (null when never visited)")
        last_visit = _check_last_visit(doc["last_visit"])
    if version < 4:
        if "view_includes" in doc:
            raise OverlayError(f"a version {version} file has no view_includes")
        views: list[tuple[Any, ...]] = []
    else:
        if "view_includes" not in doc:
            raise OverlayError("view_includes is missing (an empty list when none)")
        views = _check_view_includes(doc["view_includes"])
    if version < 6:
        if "why_not" in doc:
            raise OverlayError(f"a version {version} file has no why_not")
        answers: list[tuple[Any, ...]] = []
    else:
        if "why_not" not in doc:
            raise OverlayError("why_not is missing (an empty list when none)")
        answers = _check_why_not(doc["why_not"])
    if version < 7:
        if "now_mode" in doc:
            raise OverlayError(f"a version {version} file has no now_mode")
        mode: list[tuple[Any, ...]] = []
    else:
        if "now_mode" not in doc:
            raise OverlayError("now_mode is missing (null when none is saved)")
        mode = _check_now_mode(doc["now_mode"])
    if version < 8:
        if "plans" in doc:
            raise OverlayError(f"a version {version} file has no plans")
        plans: list[tuple[Any, ...]] = []
    else:
        if "plans" not in doc:
            raise OverlayError("plans is missing (an empty list when none)")
        plans = _check_plans(doc["plans"])
    out: dict[str, list[tuple[Any, ...]]] = {}
    for table, cols in COLUMNS.items():
        rows = tables.get(table, [])
        if not isinstance(rows, list):
            raise OverlayError(f"{table} is not a list")
        names = ("source", "item_key", *cols)
        seen: set[Item] = set()
        out[table] = []
        for i, row in enumerate(rows):
            where = f"{table}[{i}]"
            if not isinstance(row, dict) or set(row) != set(names):
                raise OverlayError(f"{where} must have exactly: {', '.join(names)}")
            item = (_ident(row["source"], "source"), _ident(row["item_key"], "key"))
            if item in seen:
                raise OverlayError(f"{where}: duplicate {item[0]}:{item[1]}")
            seen.add(item)
            for col in cols:
                _check_value(table, col, row[col], where)
            out[table].append(tuple(row[n] for n in names))
    # Only what the write paths can produce: pins and ordered items are the
    # same set, and every position is a number (finiteness is checked above).
    pinned = {(r[0], r[1]) for r in out["pins"]}
    orphans = [f"{r[0]}:{r[1]}" for r in out["order"] if (r[0], r[1]) not in pinned]
    if orphans:
        raise OverlayError(f"order names unpinned items: {', '.join(orphans)}")
    positioned = {(r[0], r[1]) for r in out["order"] if r[2] is not None}
    unplaced = sorted(f"{s}:{k}" for s, k in pinned - positioned)
    if unplaced:
        raise OverlayError(f"pins without a position: {', '.join(unplaced)}")
    noted = {(r[0], r[1]) for r in out["notes"] if (r[2] or "").strip()}
    bare = sorted(
        f"{r[0]}:{r[1]}"
        for r in out["kind_tags"]
        if r[2] == "substantial" and (r[0], r[1]) not in noted
    )
    if bare:
        raise OverlayError(f"{SUBSTANTIAL_NEEDS_NOTE}: {', '.join(bare)}")
    out["view_includes"] = views
    out["why_not"] = answers
    out["now_mode"] = mode
    out["plans"] = plans
    return out, last_visit


def import_(conn: sqlite3.Connection, doc: Any) -> dict[str, int]:
    """Replace the overlay with a validated document, in one transaction."""
    rows, last_visit = validate(doc)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for table in (
            *COLUMNS,
            *db.OVERLAY_SINGLETONS,
            *db.OVERLAY_VIEW_TABLES,
            *db.OVERLAY_LOG_TABLES,
            *db.OVERLAY_PLAN_TABLES,
        ):
            conn.execute(f'DELETE FROM "{table}"')
        conn.executemany(
            f"INSERT INTO plans ({', '.join(PLAN_COLUMNS)})"
            f" VALUES ({', '.join('?' * len(PLAN_COLUMNS))})",
            rows["plans"],
        )
        conn.executemany(
            f"INSERT INTO why_not ({', '.join(WHY_NOT_COLUMNS)})"
            f" VALUES ({', '.join('?' * len(WHY_NOT_COLUMNS))})",
            rows["why_not"],
        )
        conn.executemany(
            "INSERT INTO view_includes (view, groups, set_at) VALUES (?, ?, ?)",
            rows["view_includes"],
        )
        conn.executemany(
            "INSERT INTO now_mode (id, mode, set_at) VALUES (1, ?, ?)",
            rows["now_mode"],
        )
        if last_visit is not None:
            conn.execute("INSERT INTO last_visit (id, at) VALUES (1, ?)", (last_visit,))
        for table, cols in COLUMNS.items():
            names = ("source", "item_key", *cols)
            marks = ", ".join("?" * len(names))
            conn.executemany(
                f'INSERT INTO "{table}" ({", ".join(names)}) VALUES ({marks})',
                rows[table],
            )
    return {table: len(r) for table, r in rows.items()}


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


# --- rendering ---------------------------------------------------------------

_e = html.escape

CSS = """
.card .ov { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:4px;
  font-size:12px; }
.card .ov button, .card .ov input { font:inherit; padding:1px 6px; border-radius:4px;
  border:1px solid var(--line); background:var(--bg); color:var(--fg); }
.card .ov .ov-icon { display:inline-flex; align-items:center; padding:2px 4px; cursor:pointer;
  border:1px solid var(--line); border-radius:4px; background:var(--bg); color:var(--fg); }
.card .ov .ov-icon:hover { border-color:var(--muted); }
.card .ov .ov-icon .glyph { display:block; width:14px; height:14px; }
@media (pointer:coarse) { .card .ov .ov-icon { padding:7px; } }
/* With a mouse, the controls wait at the card's bottom right until it is
   hovered or holds focus, as a GitHub row's actions do, so they cost no line.
   A touch screen cannot hover, so there they stay a line of their own; and a
   snoozed card's controls stay put, since they carry its "snoozed until". */
@media (hover:hover) and (pointer:fine) {
  .card .ov:not(:has(.badge)) { position:absolute; right:8px; bottom:6px; margin:0;
    padding-left:8px; background:inherit; opacity:0; }
  .card:hover .ov, .card:focus-within .ov { opacity:1; }
}
.card.snoozed { opacity:.6; }
.card .badge { color:var(--muted); }
"""

# One delegated listener; every control posts a form and reloads on success,
# except on Now's cards, where Snooze, a reason and Undo change the card in
# place (`done` runs instead of the reload).
JS = """
(function () {
  var token = document.querySelector('meta[name=dashboard-token]').content;
  function post(action, fields, done) {
    var body = 'token=' + encodeURIComponent(token);
    fields.forEach(function (f) {
      body += '&' + f[0] + '=' + encodeURIComponent(f[1]);
    });
    return fetch('/overlay/' + action, { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body
    }).then(function (r) {
      if (r.ok) { if (done) done(); else location.reload(); return; }
      return r.text().then(function (t) { alert('Not saved (' + r.status + '): ' + t); });
    }).catch(function () { alert('Not saved: the server did not answer'); });
  }
  function item(el) {
    var c = el.closest('.card');
    return c && [['source', c.dataset.source], ['key', c.dataset.key]];
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-ov]');
    if (!b) return;
    var act = b.dataset.ov, it = item(b);
    if (act === 'pin' || act === 'unpin' || act === 'ignore' || act === 'unignore') {
      post(act, it);
    }
    else if (act === 'visit') { post('visit', [['at', b.dataset.at]]); }
    else if (act === 'mode') {
      // Saved, then Now again without a one-off ?mode= in the URL; any
      // other parameter (?include=) is kept.
      post('mode', [['mode', b.dataset.mode]], function () {
        var u = new URL(location.href);
        u.searchParams.delete('mode');
        location.assign(u.pathname + u.search);
      });
    }
    else if (act === 'snooze') {
      // On Now, Snooze snoozes at once and the card gives way to a line that
      // offers a reason and Undo; elsewhere it reloads as every control does.
      var card = b.closest('.card');
      var line = b.dataset.why && card.querySelector('.whynot');
      post('snooze', it.concat([['until', b.dataset.until]]), line && function () {
        line.querySelector('.whynot-until').textContent = b.dataset.until;
        line.querySelector('input').value = '';
        line.querySelectorAll('[data-ov="snooze-for"]').forEach(function (x) {
          x.setAttribute('aria-pressed', x.dataset.until === b.dataset.until ? 'true' : 'false');
        });
        card.classList.add('just-snoozed');
        line.hidden = false;
      });
    }
    else if (act === 'snooze-for') {
      // The snoozed line's 1d … 30d: the same snooze, for that long.
      var row = b.closest('.whynot');
      post('snooze', it.concat([['until', b.dataset.until]]), function () {
        row.querySelector('.whynot-until').textContent = b.dataset.until;
        row.querySelectorAll('[data-ov="snooze-for"]').forEach(function (x) {
          x.setAttribute('aria-pressed', x === b ? 'true' : 'false');
        });
      });
    }
    else if (act === 'whynot') {
      var box = b.closest('.whynot');
      post('whynot', it.concat([['reason', b.dataset.reason],
        ['text', box.querySelector('input').value],
        ['mode', box.closest('.card').dataset.mode || '']]),
        function () { box.closest('.card').remove(); });
    }
    else if (act === 'undo') {
      var undone = b.closest('.whynot');
      post('snooze', it.concat([['until', '']]), function () {
        undone.hidden = true;
        undone.closest('.card').classList.remove('just-snoozed');
      });
    }
    else if (act === 'up' || act === 'down') {
      var lane = b.closest('[data-pinned]');
      var cards = Array.prototype.slice.call(lane.querySelectorAll('.card'));
      var i = cards.indexOf(b.closest('.card')), j = act === 'up' ? i - 1 : i + 1;
      if (j < 0 || j >= cards.length) return;
      var t = cards[i]; cards[i] = cards[j]; cards[j] = t;
      post('order', [['source', cards[0].dataset.source]].concat(
        cards.map(function (c) { return ['key', c.dataset.key]; })));
    }
  });
})();
"""


def card_attrs(item: Item, ov: Overlay, now: datetime) -> str:
    """Attributes for a card's root element: identity and snoozed state."""
    cls = "card snoozed" if ov.is_snoozed(item, now) else "card"
    return f'class="{cls}" data-source="{_e(item[0])}" data-key="{_e(item[1])}"'


def snooze_until(now: datetime, days: int = SNOOZE_DAYS) -> str:
    """The date a snooze of `days` local days ends: the Snooze button's, by
    default."""
    return (now.astimezone() + timedelta(days=days)).date().isoformat()


# A GitHub PR or issue, as its card's key: what the Refresh button re-reads.
_REFRESHABLE = re.compile(r"^https://github\.com/[^/]+/[^/]+/(?:pull|issues)/\d+$")


def refreshable(item: Item) -> bool:
    """Whether the card for `item` carries a Refresh button."""
    return item[0] == "github" and bool(_REFRESHABLE.match(item[1]))


PIN_TIP = "Pin: keep it at the top of Now, the Board and Epics"
UNPIN_TIP = "Unpin: rank it like any other item"


def card_controls(
    item: Item,
    ov: Overlay,
    now: datetime,
    *,
    reorder: bool,
    pin: bool = True,
    pin_tip: str = PIN_TIP,
    why_not: bool = False,
) -> str:
    """The pin toggle, snooze, ignore and refresh buttons, and
    (pinned lane) up/down. `pin=False` drops Pin where nothing ranks by it;
    `pin_tip` is Pin's tooltip, for a page whose pin does something else.
    `why_not=True` makes Snooze hide the card in place and show its "why
    not now" line (whynot.picker, which the caller puts in the card) instead
    of reloading the page."""
    pinned = ov.is_pinned(item)
    parts = []
    if pin:
        if pinned:
            act, label, glyph = "unpin", "Unpin", "pin-off"
            tip = UNPIN_TIP
        else:
            act, label, glyph = "pin", "Pin", "pin"
            tip = pin_tip
        parts.append(
            f'<button type="button" class="ov-icon" data-ov="{act}"'
            f' aria-label="{label}" title="{tip}">{icons.svg(glyph)}</button>'
        )
    if reorder:
        parts.append(
            '<button type="button" data-ov="up" title="Move up">↑</button>'
            '<button type="button" data-ov="down" title="Move down">↓</button>'
        )
    if ov.is_snoozed(item, now):
        until = ov.snoozed_until.get(item, "")
        parts.append(
            f'<span class="badge">snoozed until {_e(until)}</span>'
            '<button type="button" class="ov-icon" data-ov="snooze" data-until=""'
            ' aria-label="Unsnooze" title="Unsnooze">'
            f"{icons.svg('alarm-clock-off')}</button>"
        )
    else:
        back = snooze_until(now)
        why = ' data-why="1"' if why_not else ""
        parts.append(
            f'<button type="button" class="ov-icon" data-ov="snooze"'
            f' data-until="{back}"{why} aria-label="Snooze {SNOOZE_DAYS}d"'
            f' title="Snooze {SNOOZE_DAYS}d, until {back}: hidden from Now and'
            f' Needs, dimmed elsewhere">{icons.svg("alarm-clock")}</button>'
        )
    parts.append(
        '<button type="button" class="ov-icon" data-ov="ignore" aria-label="Ignore"'
        f' title="Ignore: not mine, hide it">{icons.svg("eye-off")}</button>'
    )
    if refreshable(item):
        # refresh.JS handles it: POST /refresh, then the card follows the item.
        parts.append(
            '<button type="button" class="ov-icon" data-refresh="" aria-label="Refresh"'
            ' title="Refresh: re-read this item from GitHub, without a full sync">'
            f"{icons.svg('refresh-cw')}</button>"
        )
    return f'<div class="ov">{"".join(parts)}</div>'
