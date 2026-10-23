"""SQLite schema and migrations for the task dashboard.

Two kinds of table share one file and must stay apart. MIRROR tables are the
sync's: any row may be overwritten or deleted on the next run. OVERLAY tables
(pins, order, snoozes, notes) are the user's, keyed by issue key or PR URL, and
a sync never touches them; they are the only state here that cannot be rebuilt.

The DB lives under ~/.local/state, outside every checkout and plugin directory,
so a plugin update never wipes the overlay. WAL mode lets the HTTP thread read a
committed snapshot while the sync thread writes.

Schema changes are appended to MIGRATIONS as (version, script). connect() applies
each one above the stored version in a transaction and records the new version,
so opening an up-to-date DB is a no-op.
"""

import os
import sqlite3
from pathlib import Path

ENV_VAR = "TASK_DASHBOARD_DB"
DEFAULT_PATH = Path("~/.local/state/task-dashboard/dashboard.db")

MIRROR_TABLES = ("issues", "links", "sprints", "prs", "sync_runs")
OVERLAY_TABLES = ("pins", "order", "snoozes", "notes")

_V1 = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS issues (
    key TEXT PRIMARY KEY,
    summary TEXT,
    status_name TEXT,
    status_category TEXT,
    status_category_changed TEXT,
    assignee TEXT,
    reporter TEXT,
    priority TEXT,
    due_date TEXT,
    parent_key TEXT,
    labels TEXT,
    created TEXT,
    updated TEXT,
    resolution_date TEXT,
    sprint_id INTEGER,
    sprint_name TEXT,
    sprint_state TEXT,
    sprint_end TEXT,
    flagged INTEGER,
    raw_json TEXT,
    synced_at TEXT
);

CREATE TABLE IF NOT EXISTS links (
    issue_key TEXT NOT NULL,
    linked_key TEXT NOT NULL,
    link_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    linked_status_category TEXT,
    PRIMARY KEY (issue_key, linked_key, link_type, direction)
);

CREATE TABLE IF NOT EXISTS sprints (
    id INTEGER PRIMARY KEY,
    board_id INTEGER,
    name TEXT,
    state TEXT,
    start_date TEXT,
    end_date TEXT
);

CREATE TABLE IF NOT EXISTS prs (
    url TEXT PRIMARY KEY,
    repo TEXT,
    number INTEGER,
    title TEXT,
    head_ref TEXT,
    is_draft INTEGER,
    review_decision TEXT,
    ci_state TEXT,
    role TEXT CHECK (role IN ('author', 'reviewer')),
    issue_key TEXT,
    updated TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT,
    started_at TEXT,
    finished_at TEXT,
    ok INTEGER,
    error TEXT,
    error_kind TEXT,
    issues_seen INTEGER
);

CREATE TABLE IF NOT EXISTS pins (
    item_key TEXT PRIMARY KEY,
    pinned_at TEXT
);

-- "order" is a reserved word, hence the quotes everywhere it is named.
CREATE TABLE IF NOT EXISTS "order" (
    item_key TEXT PRIMARY KEY,
    position REAL
);

CREATE TABLE IF NOT EXISTS snoozes (
    item_key TEXT PRIMARY KEY,
    until TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    item_key TEXT PRIMARY KEY,
    body TEXT,
    updated_at TEXT
);
"""

# Ordered, ascending, append-only: never edit a shipped entry.
MIGRATIONS: list[tuple[int, str]] = [(1, _V1)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def db_path(path: "str | os.PathLike[str] | None" = None) -> Path:
    """Argument, else $TASK_DASHBOARD_DB, else the default location."""
    chosen = path if path is not None else os.environ.get(ENV_VAR) or DEFAULT_PATH
    return Path(chosen).expanduser()


def _stored_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> None:
    """Apply every migration above the stored version, each in one transaction."""
    stored = _stored_version(conn)
    for version, script in sorted(MIGRATIONS):
        if version <= stored:
            continue
        # executescript runs in autocommit, so the BEGIN/COMMIT lives inside the
        # script to keep the DDL and the version bump atomic.
        conn.executescript(
            "BEGIN;\n"
            + script
            + "\nCREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
            + f"\nINSERT INTO meta (key, value) VALUES ('schema_version', '{int(version)}')"
            + " ON CONFLICT(key) DO UPDATE SET value = excluded.value;"
            + "\nCOMMIT;"
        )


def connect(path: "str | os.PathLike[str] | None" = None) -> sqlite3.Connection:
    """Open (creating) the DB, enable WAL, migrate, and return the connection."""
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn
