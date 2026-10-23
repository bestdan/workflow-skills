"""The overlay: the user's pins, pinned order, snoozes and notes.

Every row is keyed by (source, item_key), the same pair as `issues`. The sync
never writes these tables, and nothing here reads `issues`: a row for an item
that has left the mirror is kept as is, and the views simply have no card to
show it on. It is the only state on the machine that cannot be rebuilt from
Jira, which is why `export` and `import` exist.

Writes arrive as POSTs that server.py has already put through its Host, Origin
and token checks; `apply()` is what it calls with the parsed form. Rendering
helpers for the card controls live here too, so the views only have to call
`card_controls()` and include `CSS` and `JS`.
"""

import html
import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import db
import score

EXPORT_FORMAT = "task-dashboard-overlay"
EXPORT_VERSION = 1
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
}
assert tuple(COLUMNS) == db.OVERLAY_TABLES

ACTIONS = ("pin", "unpin", "order", "snooze", "note")

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
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


def note(
    conn: sqlite3.Connection, item: Item, text: str, now: "datetime | None" = None
) -> None:
    """Set the item's note; blank text deletes it."""
    if len(text) > MAX_NOTE:
        raise OverlayError(f"note is longer than {MAX_NOTE} characters")
    with conn:
        if not text.strip():
            conn.execute("DELETE FROM notes WHERE source = ? AND item_key = ?", item)
            return
        conn.execute(
            "INSERT OR REPLACE INTO notes (source, item_key, body, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (*item, text, _now(now)),
        )


def apply(
    conn: sqlite3.Connection,
    action: str,
    form: "dict[str, list[str]]",
    now: "datetime | None" = None,
) -> None:
    """One overlay POST, from its parsed form. Raises OverlayError on bad input.

    Fields: `source` (default `jira`) and `key`; `order` repeats `key`, one per
    pinned item in the new order; `snooze` takes `until`, `note` takes `text`.
    """
    if action not in ACTIONS:
        raise OverlayError(f"unknown overlay action: {action!r}")
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
    else:
        note(conn, item, (form.get("text") or [""])[0], now)


# --- reads -------------------------------------------------------------------


@dataclass
class Overlay:
    """All four tables, keyed by (source, item_key)."""

    pinned_at: "dict[Item, str | None]" = field(default_factory=dict)
    position: "dict[Item, float | None]" = field(default_factory=dict)
    snoozed_until: "dict[Item, str]" = field(default_factory=dict)
    notes: "dict[Item, str]" = field(default_factory=dict)

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
    return ov


# --- export / import ---------------------------------------------------------


def export(conn: sqlite3.Connection, now: "datetime | None" = None) -> dict[str, Any]:
    """The four tables as a versioned, JSON-ready document, rows sorted by key."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for table, cols in COLUMNS.items():
        names = ("source", "item_key", *cols)
        cur = conn.execute(
            f'SELECT {", ".join(names)} FROM "{table}" ORDER BY source, item_key'
        )
        tables[table] = [dict(zip(names, row)) for row in cur]
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _now(now),
        "tables": tables,
    }


def _check_value(table: str, col: str, value: Any, where: str) -> None:
    if col == "position":
        ok = value is None or (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    else:
        ok = value is None or isinstance(value, str)
    if not ok:
        raise OverlayError(f"{where}: {col} has the wrong type")
    if table == "snoozes" and value is not None:
        _iso_date(value)
    # The write path's limit too, or an imported note could never be re-saved.
    if col == "body" and isinstance(value, str) and len(value) > MAX_NOTE:
        raise OverlayError(f"{where}: note is longer than {MAX_NOTE} characters")


def validate(doc: Any) -> "dict[str, list[tuple[Any, ...]]]":
    """Check a whole import document; return each table's rows as tuples."""
    if not isinstance(doc, dict):
        raise OverlayError("import file is not a JSON object")
    if doc.get("format") != EXPORT_FORMAT:
        raise OverlayError(f"import file is not a {EXPORT_FORMAT} export")
    if doc.get("version") != EXPORT_VERSION:
        raise OverlayError(
            f"import file version {doc.get('version')!r} is not {EXPORT_VERSION}"
        )
    tables = doc.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(COLUMNS):
        raise OverlayError(f"tables must be exactly: {', '.join(COLUMNS)}")
    out: dict[str, list[tuple[Any, ...]]] = {}
    for table, cols in COLUMNS.items():
        rows = tables[table]
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
    return out


def import_(conn: sqlite3.Connection, doc: Any) -> dict[str, int]:
    """Replace the overlay with a validated document, in one transaction."""
    rows = validate(doc)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for table in COLUMNS:
            conn.execute(f'DELETE FROM "{table}"')
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
.card .ov details { width:100%; }
.card .ov textarea { width:100%; min-height:4em; font:inherit; color:var(--fg);
  background:var(--bg); border:1px solid var(--line); border-radius:4px; }
.card .note { white-space:pre-wrap; color:var(--muted); font-size:12px; margin-top:4px; }
.card.snoozed { opacity:.6; }
.card .badge { color:var(--muted); }
"""

# One delegated listener; every control posts a form and reloads on success.
JS = """
(function () {
  var token = document.querySelector('meta[name=dashboard-token]').content;
  function post(action, fields) {
    var body = 'token=' + encodeURIComponent(token);
    fields.forEach(function (f) {
      body += '&' + f[0] + '=' + encodeURIComponent(f[1]);
    });
    return fetch('/overlay/' + action, { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body
    }).then(function (r) {
      if (r.ok) { location.reload(); return; }
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
    if (act === 'pin' || act === 'unpin') { post(act, it); }
    else if (act === 'note') {
      post('note', it.concat([['text', b.closest('details').querySelector('textarea').value]]));
    } else if (act === 'up' || act === 'down') {
      var lane = b.closest('[data-pinned]');
      var cards = Array.prototype.slice.call(lane.querySelectorAll('.card'));
      var i = cards.indexOf(b.closest('.card')), j = act === 'up' ? i - 1 : i + 1;
      if (j < 0 || j >= cards.length) return;
      var t = cards[i]; cards[i] = cards[j]; cards[j] = t;
      post('order', [['source', cards[0].dataset.source]].concat(
        cards.map(function (c) { return ['key', c.dataset.key]; })));
    }
  });
  document.addEventListener('change', function (ev) {
    var d = ev.target.closest('input[data-ov=snooze]');
    if (d) post('snooze', item(d).concat([['until', d.value]]));
  });
})();
"""


def card_attrs(item: Item, ov: Overlay, now: datetime) -> str:
    """Attributes for a card's root element: identity and snoozed state."""
    cls = "card snoozed" if ov.is_snoozed(item, now) else "card"
    return f'class="{cls}" data-source="{_e(item[0])}" data-key="{_e(item[1])}"'


def card_controls(item: Item, ov: Overlay, now: datetime, *, reorder: bool) -> str:
    """The pin toggle, snooze picker, note editor and (pinned lane) up/down."""
    pinned = ov.is_pinned(item)
    parts = [
        f'<button type="button" data-ov="{"unpin" if pinned else "pin"}">'
        f"{'Unpin' if pinned else 'Pin'}</button>"
    ]
    if reorder:
        parts.append(
            '<button type="button" data-ov="up" title="Move up">↑</button>'
            '<button type="button" data-ov="down" title="Move down">↓</button>'
        )
    until = ov.snoozed_until.get(item, "")
    label = (
        f'<span class="badge">snoozed until {_e(until)}</span>'
        if ov.is_snoozed(item, now)
        else "snooze"
    )
    parts.append(
        f'<label>{label} <input type="date" data-ov="snooze" value="{_e(until)}"></label>'
    )
    body = ov.notes.get(item, "")
    parts.append(
        f"<details><summary>{'Edit note' if body else 'Add note'}</summary>"
        f'<textarea maxlength="{MAX_NOTE}">{_e(body)}</textarea>'
        '<button type="button" data-ov="note">Save note</button></details>'
    )
    shown = f'<div class="note">{_e(body)}</div>' if body else ""
    return f'{shown}<div class="ov">{"".join(parts)}</div>'
