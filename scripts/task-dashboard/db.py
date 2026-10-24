"""SQLite schema and migrations for the task dashboard.

Two kinds of table share one file and must stay apart. MIRROR tables are the
sync's: any row may be overwritten or deleted on the next run. OVERLAY tables
(pins, order, snoozes, notes, kind tags, focus, last visit, view filters) are
the user's, keyed by (source, item key) apart from the one-row last visit and
the per-view filters, and a sync never touches them; they are the only state
here that cannot be rebuilt.

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

MIRROR_TABLES = (
    "issues",
    "links",
    "sprints",
    "prs",
    "sync_runs",
    "machines",
    "session_events",
    "history_entries",
)
# Keyed by (source, item_key). `last_visit` is overlay state too, but one row
# with no item key, so it is named apart.
OVERLAY_TABLES = (
    "pins",
    "order",
    "snoozes",
    "notes",
    "kind_tags",
    "focus",
    "ignores",
)
OVERLAY_SINGLETONS = ("last_visit",)
# Keyed by view name rather than by item: the remembered include filters.
OVERLAY_VIEW_TABLES = ("view_includes",)

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

# Version 2: the main mirror table holds any kind of work item, not only Jira
# issues, so a second instance (the fleet view: Claude sessions, PRs, worktrees,
# handoffs) can reuse the tables. `source` names where a row came from (`jira`,
# `github`, `claude-remote`, `machine:<host>`, ...) and `kind` what it is
# (`ticket`, `pr`, `session`, `worktree`, `handoff`). A key is unique per
# (source, key), and the Jira-specific columns stay nullable. SQLite cannot
# change a primary key in place, so each table is rebuilt and its rows copied.
#
# The overlay tables gain `source` the same way, so a pin or note can attach to
# any item. A v1 overlay key was an issue key or a PR URL; the URL is GitHub's.
#
# `scope` is the Jira sync's own bookkeeping: `search` for a key a scope query
# returned, `linked` for one fetched only as a blocker/blocked neighbour. The
# incremental linked pass needs it to walk one hop from the in-scope set and no
# further. NULL for non-Jira rows.
#
# `jira_issues` is what Jira-only readers (the Board view, the PR join) select
# from, so a non-Jira row never reaches them.
#
# sync_runs keeps `kind` as the run's mode (incremental | reconcile | full) and
# gains the outcome (ok | failed | auth_failed; NULL while running), the counts
# and the keys whose `view` failed twice, the last two as JSON.
_ISSUE_COLUMNS = """
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
    synced_at TEXT"""

_ISSUE_COPY = (
    "summary, status_name, status_category, status_category_changed, assignee,"
    " reporter, priority, due_date, parent_key, labels, created, updated,"
    " resolution_date, sprint_id, sprint_name, sprint_state, sprint_end, flagged,"
    " raw_json, synced_at"
)

_OVERLAY_V2 = {
    "pins": "pinned_at TEXT",
    "order": "position REAL",
    "snoozes": "until TEXT",
    "notes": "body TEXT,\n    updated_at TEXT",
}


def _overlay_v2(table: str, columns: str) -> str:
    cols = ", ".join(c.split()[0] for c in columns.split(","))
    return f"""
CREATE TABLE "{table}_v2" (
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    {columns},
    PRIMARY KEY (source, item_key)
);
INSERT INTO "{table}_v2" (source, item_key, {cols})
    SELECT CASE WHEN item_key LIKE 'https://%' THEN 'github' ELSE 'jira' END,
           item_key, {cols}
    FROM "{table}";
DROP TABLE "{table}";
ALTER TABLE "{table}_v2" RENAME TO "{table}";
"""


_V2 = f"""
CREATE TABLE issues_v2 (
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,{_ISSUE_COLUMNS},
    scope TEXT,
    PRIMARY KEY (source, key)
);
INSERT INTO issues_v2 (source, kind, key, {_ISSUE_COPY})
    SELECT 'jira', 'ticket', key, {_ISSUE_COPY} FROM issues;
UPDATE issues_v2 SET scope = 'search';
DROP TABLE issues;
ALTER TABLE issues_v2 RENAME TO issues;
CREATE VIEW jira_issues AS SELECT * FROM issues WHERE source = 'jira';

ALTER TABLE sync_runs ADD COLUMN outcome TEXT;
ALTER TABLE sync_runs ADD COLUMN counts TEXT;
ALTER TABLE sync_runs ADD COLUMN failed_keys TEXT;
""" + "".join(_overlay_v2(t, c) for t, c in _OVERLAY_V2.items())

# Version 3: the fleet view's ingest (snapshot_adapter.py). A machine's
# worktrees, handoffs and tmux sessions go into `issues` under
# `source = 'machine:<host>'`, so they need no table of their own. What does:
#
# - `machines`: one row per host, with the `taken_at` of the last snapshot
#   ingested from it (the header's per-machine age) and when it arrived.
# - `session_events` and `history_entries`: the snapshot's two deltas, appended
#   rather than replaced, since each snapshot carries only what is new.
_V3 = """
CREATE TABLE machines (
    host TEXT PRIMARY KEY,
    last_snapshot_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    counts TEXT
);

CREATE TABLE session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    ts TEXT NOT NULL,
    event TEXT,
    session_id TEXT,
    cwd TEXT,
    branch TEXT,
    notification TEXT,
    received_at TEXT NOT NULL
);
CREATE INDEX session_events_host_ts ON session_events (host, ts);

CREATE TABLE history_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    ts TEXT NOT NULL,
    session_id TEXT,
    project TEXT,
    approval_shaped INTEGER NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX history_entries_host_ts ON history_entries (host, ts);
"""

# Version 4: the fleet view's overlay additions (design, "Overlay additions").
#
# - `kind_tags`: an item's `side-quest` or `substantial` tag. A substantial item
#   must name the figure it moves in its note; overlay.py enforces that, since
#   the note lives in another table.
# - `focus`: the one deep-work item. The constant-expression unique index makes
#   a second row a constraint error, so "at most one" holds even against a
#   writer that skips overlay.set_focus.
# - `last_visit`: one timestamp for the whole overlay, not one per item, so a
#   single row (id 1) rather than a (source, item_key) key.
_V4 = """
CREATE TABLE kind_tags (
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    tag TEXT NOT NULL CHECK (tag IN ('side-quest', 'substantial')),
    tagged_at TEXT,
    PRIMARY KEY (source, item_key)
);

CREATE TABLE focus (
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    set_at TEXT,
    PRIMARY KEY (source, item_key)
);
CREATE UNIQUE INDEX focus_one ON focus ((0));

CREATE TABLE last_visit (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    at TEXT NOT NULL
);
"""

# Version 5: `ignores`, items the user has said are not theirs. Ignoring an epic
# hides its children too; views.py applies that, since parentage lives in
# `issues`.
_V5 = """
CREATE TABLE ignores (
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    ignored_at TEXT,
    PRIMARY KEY (source, item_key)
);
"""

# Version 6: `view_includes`, each view's last include-filter choice
# (include.py). Overlay state keyed by view name, one row per view. `groups` is
# the chosen group names, comma-joined in include.GROUPS order; empty means
# every switch was turned off, which is a choice too.
_V6 = """
CREATE TABLE view_includes (
    view TEXT PRIMARY KEY,
    groups TEXT NOT NULL,
    set_at TEXT
);
"""

# Version 7: `asked` on `session_events`, from the session-state hook's format
# 2 (a Stop whose last assistant text ended in "?"): 1, 0, or NULL when the hook
# could not tell or the line is format 1. sessions.py reads it.
_V7 = """
ALTER TABLE session_events ADD COLUMN asked INTEGER;
"""

# Ordered, ascending, append-only: never edit a shipped entry.
#
# migrate() runs a script one statement at a time through _statements, which
# cuts it at line ends. So a script must follow three rules that executescript
# did not impose:
# - every statement ends in `;`;
# - no two statements share a line (both land in one chunk, and execute
#   raises "You can only execute one statement at a time");
# - nothing follows the last statement's `;`, not even a comment.
# A missing final `;` or a trailing comment makes _statements raise ValueError.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _V1),
    (2, _V2),
    (3, _V3),
    (4, _V4),
    (5, _V5),
    (6, _V6),
    (7, _V7),
]
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


def _statements(script: str) -> list[str]:
    """`script` split into single statements, for `execute` one at a time."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf)
            buf = ""
    if buf.strip():
        raise ValueError(f"incomplete SQL statement in migration: {buf.strip()!r}")
    return out


def migrate(conn: sqlite3.Connection) -> None:
    """Apply every migration above the stored version, each in one transaction.

    Each step takes the write lock first (BEGIN IMMEDIATE) and re-reads the
    stored version under it. Two connections opening an old DB at once would
    otherwise both decide to migrate, and the second would re-run a step the
    first had already applied: re-running v1 resets the stored version to 1 on
    a v2 schema, which every later open then fails to migrate.
    """
    for version, script in sorted(MIGRATIONS):
        if version <= _stored_version(conn):
            continue
        # The statements run one by one rather than through executescript,
        # which commits any open transaction first and so cannot run under the
        # lock taken here. A failing statement leaves the transaction open,
        # holding the write lock, so roll it back before re-raising.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _stored_version(conn) >= version:
                conn.rollback()
                continue
            for stmt in _statements(script):
                conn.execute(stmt)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(int(version)),),
            )
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise


def connect(path: "str | os.PathLike[str] | None" = None) -> sqlite3.Connection:
    """Open (creating) the DB, enable WAL, migrate, and return the connection."""
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn
