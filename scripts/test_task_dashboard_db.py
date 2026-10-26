#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/{config,db,dashboard}.py.

The package directory has a hyphen, so the modules are loaded by file path.
Everything lives under tempdirs; the real ~/.config and ~/.local are never read.
Needs Python 3.11 (tomllib): run via scripts/test-task-dashboard-db.sh.
"""

import ast
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
# config.py imports include.py, its sibling, by name.
sys.path.insert(0, str(PKG))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"td_{name}", PKG / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


config = _load("config")
db = _load("db")

MINIMAL = """
[jira]
site = "x.atlassian.net"
board_ids = [1]
[jira.fields]
sprint = "customfield_1"
flagged = "customfield_2"
"""


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)

    def write_config(self, text):
        p = self.tmp / "config.toml"
        p.write_text(text)
        return p

    def tables(self, conn):
        return {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def set_env(self, name, value):
        old = os.environ.get(name)
        os.environ[name] = value

        def restore():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old

        self.addCleanup(restore)


class DbTests(TmpCase):
    def test_fresh_db_has_all_tables_and_wal(self):
        conn = db.connect(self.tmp / "d.db")
        self.assertTrue(
            set(db.MIRROR_TABLES + db.OVERLAY_TABLES + ("meta",)) <= self.tables(conn)
        )
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        ver = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        self.assertEqual(int(ver), db.SCHEMA_VERSION)

    def test_reopen_is_idempotent_and_keeps_overlay(self):
        path = self.tmp / "d.db"
        conn = db.connect(path)
        before = self.tables(conn)
        conn.execute(
            "INSERT INTO notes (source, item_key, body) VALUES ('jira', 'ABC-1', 'keep me')"
        )
        conn.commit()
        conn.close()
        conn = db.connect(path)
        self.assertEqual(self.tables(conn), before)
        self.assertEqual(
            conn.execute("SELECT body FROM notes WHERE item_key='ABC-1'").fetchone()[0],
            "keep me",
        )
        ver = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        self.assertEqual(int(ver), db.SCHEMA_VERSION)

    def test_order_table_is_usable(self):
        conn = db.connect(self.tmp / "d.db")
        conn.execute(
            'INSERT INTO "order" (source, item_key, position) VALUES (?, ?, ?)',
            ("jira", "ABC-1", 1.5),
        )
        row = conn.execute(
            'SELECT position FROM "order" WHERE item_key = ?', ("ABC-1",)
        ).fetchone()
        self.assertEqual(row[0], 1.5)
        conn.close()

    def test_issues_columns(self):
        conn = db.connect(self.tmp / "d.db")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(issues)")}
        want = {
            "key", "summary", "status_name", "status_category", "status_category_changed",
            "assignee", "reporter", "priority", "due_date", "parent_key", "labels", "created",
            "updated", "resolution_date", "sprint_id", "sprint_name", "sprint_state",
            "sprint_end", "flagged", "raw_json", "synced_at",
        }  # fmt: skip
        self.assertEqual(want - cols, set())

    def test_migration_hook_applies_newer_version(self):
        path = self.tmp / "d.db"
        db.connect(path).close()
        original = db.MIGRATIONS
        db.MIGRATIONS = original + [
            (original[-1][0] + 1, "ALTER TABLE pins ADD COLUMN why TEXT;")
        ]
        self.addCleanup(setattr, db, "MIGRATIONS", original)
        conn = db.connect(path)
        self.assertIn("why", {r[1] for r in conn.execute("PRAGMA table_info(pins)")})
        ver = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        self.assertEqual(int(ver), original[-1][0] + 1)
        conn.close()
        db.connect(path).close()  # second open must not re-run the ALTER

    def test_v2_items_are_keyed_by_source_and_key(self):
        conn = db.connect(self.tmp / "d.db")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(issues)")}
        self.assertLessEqual({"source", "kind", "key", "scope"}, cols)
        for source, kind in (("jira", "ticket"), ("claude-remote", "session")):
            conn.execute(
                "INSERT INTO issues (source, kind, key) VALUES (?, ?, 'X-1')",
                (source, kind),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO issues (source, kind, key) VALUES ('jira', 'ticket', 'X-1')"
            )
        self.assertEqual(
            conn.execute("SELECT kind FROM jira_issues").fetchall(), [("ticket",)]
        )
        for table in ("pins", '"order"', "snoozes", "notes"):
            conn.execute(
                f"INSERT INTO {table} (source, item_key) VALUES ('jira', 'X-1')"
            )
            conn.execute(
                f"INSERT INTO {table} (source, item_key) VALUES ('claude-remote', 'X-1')"
            )
        conn.close()

    def test_v1_rows_migrate_to_v2(self):
        path = self.tmp / "d.db"
        original = db.MIGRATIONS
        db.MIGRATIONS = original[:1]
        try:
            conn = db.connect(path)
        finally:
            db.MIGRATIONS = original
        conn.execute("INSERT INTO issues (key, summary) VALUES ('ABC-1', 'old')")
        conn.execute("INSERT INTO notes (item_key, body) VALUES ('ABC-1', 'n1')")
        conn.execute(
            "INSERT INTO pins (item_key, pinned_at) VALUES ('https://github.com/a/b/pull/1', 't')"
        )
        conn.execute("INSERT INTO \"order\" (item_key, position) VALUES ('ABC-1', 2.5)")
        conn.commit()
        conn.close()
        conn = db.connect(path)
        self.assertEqual(
            conn.execute(
                "SELECT source, kind, key, summary, scope FROM issues"
            ).fetchall(),
            [("jira", "ticket", "ABC-1", "old", "search")],
        )
        self.assertEqual(
            conn.execute("SELECT source, item_key, body FROM notes").fetchall(),
            [("jira", "ABC-1", "n1")],
        )
        self.assertEqual(
            conn.execute("SELECT source, pinned_at FROM pins").fetchall(),
            [("github", "t")],
        )
        self.assertEqual(
            conn.execute('SELECT source, position FROM "order"').fetchall(),
            [("jira", 2.5)],
        )
        self.assertIn(
            "outcome", {r[1] for r in conn.execute("PRAGMA table_info(sync_runs)")}
        )
        conn.close()

    def test_v7_focus_table_is_dropped_and_the_rest_kept(self):
        path = self.tmp / "d.db"
        original = db.MIGRATIONS
        db.MIGRATIONS = [m for m in original if m[0] <= 7]
        try:
            conn = db.connect(path)
        finally:
            db.MIGRATIONS = original
        conn.execute(
            "INSERT INTO focus (source, item_key, set_at) VALUES ('jira', 'ABC-1', 't')"
        )
        conn.execute(
            "INSERT INTO notes (source, item_key, body) VALUES ('jira', 'ABC-1', 'n1')"
        )
        conn.commit()
        conn.close()
        conn = db.connect(path)
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'focus%'"
            )
        }
        self.assertEqual(names, set())
        self.assertEqual(conn.execute("SELECT body FROM notes").fetchall(), [("n1",)])
        ver = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        self.assertEqual(int(ver), db.SCHEMA_VERSION)
        conn.close()

    def test_failed_migration_releases_the_write_lock(self):
        path = self.tmp / "d.db"
        db.connect(path).close()
        original = db.MIGRATIONS
        db.MIGRATIONS = original + [
            (original[-1][0] + 1, "CREATE TABLE ok_one (x);\nNOT VALID SQL;")
        ]
        self.addCleanup(setattr, db, "MIGRATIONS", original)
        with self.assertRaises(sqlite3.Error):
            db.connect(path)
        other = sqlite3.connect(path, timeout=0.1)
        other.execute("INSERT INTO pins (source, item_key) VALUES ('jira', 'Z-1')")
        other.commit()
        names = {r[0] for r in other.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("ok_one", names)
        other.close()

    def test_concurrent_migrate_rechecks_version_under_the_lock(self):
        # Two connections open a v1 DB at once. Both read version 1 before
        # either takes the write lock; A migrates first, then B must see v2
        # under the lock and skip, not re-run v2 against the new schema.
        path = self.tmp / "d.db"
        original = db.MIGRATIONS
        db.MIGRATIONS = original[:1]
        try:
            db.connect(path).close()
        finally:
            db.MIGRATIONS = original
        a = sqlite3.connect(path)
        b = sqlite3.connect(path)
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        real = db._stored_version
        reads: list[int] = []

        def stale_unlocked_reads(conn):
            # B's two unlocked reads (the v1 and v2 checks) happened before A
            # committed; only the read under the lock sees A's v2.
            if conn is not b:
                return real(conn)
            value = 1 if len(reads) < 2 else real(conn)
            reads.append(value)
            return value

        db.migrate(a)
        db._stored_version = stale_unlocked_reads
        self.addCleanup(setattr, db, "_stored_version", real)
        db.migrate(b)
        # The third read is under the lock. Each later migration's unlocked
        # check then reads the real, current version and skips.
        self.assertEqual(reads[:3], [1, 1, db.SCHEMA_VERSION])
        self.assertTrue(all(r == db.SCHEMA_VERSION for r in reads[3:]), reads)
        self.assertFalse(b.in_transaction)
        ver = b.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        self.assertEqual(int(ver[0]), db.SCHEMA_VERSION)
        self.assertIn("source", {r[1] for r in b.execute("PRAGMA table_info(issues)")})

    def test_statements_splits_a_script(self):
        stmts = db._statements(
            "CREATE TABLE a (x);\n-- note; here\nCREATE TABLE b (y);\n"
        )
        self.assertEqual(len(stmts), 2)
        with self.assertRaises(ValueError):
            db._statements("CREATE TABLE a (x)")

    def test_env_var_db_path(self):
        target = self.tmp / "sub" / "env.db"
        self.set_env(db.ENV_VAR, str(target))
        db.connect().close()
        self.assertTrue(target.exists())


class ConfigTests(TmpCase):
    def test_missing_board_ids(self):
        p = self.write_config(MINIMAL.replace("board_ids = [1]\n", ""))
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p)
        self.assertIn("jira.board_ids", str(cm.exception))

    def test_missing_sprint_field(self):
        p = self.write_config(MINIMAL.replace('sprint = "customfield_1"\n', ""))
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p)
        self.assertIn("jira.fields.sprint", str(cm.exception))

    def test_each_required_jira_key_is_named_when_enabled_is_unset(self):
        for dotted, line in (
            ("jira.site", 'site = "x.atlassian.net"\n'),
            ("jira.board_ids", "board_ids = [1]\n"),
            ("jira.fields.sprint", 'sprint = "customfield_1"\n'),
            ("jira.fields.flagged", 'flagged = "customfield_2"\n'),
        ):
            p = self.write_config(MINIMAL.replace(line, ""))
            with self.assertRaises(config.ConfigError, msg=dotted) as cm:
                config.load(p)
            self.assertIn(dotted, str(cm.exception))
        self.assertIs(config.load(self.write_config(MINIMAL))["jira"]["enabled"], True)

    def test_jira_disabled_needs_no_other_jira_key(self):
        cfg = config.load(self.write_config("[jira]\nenabled = false\n"))
        self.assertIs(cfg["jira"]["enabled"], False)
        self.assertEqual(cfg["jira"]["site"], "")
        self.assertEqual(cfg["jira"]["board_ids"], [])
        # acli_path is not checked either: nothing will run it.
        cfg = config.load(
            self.write_config('[jira]\nenabled = false\nacli_path = ""\n')
        )
        self.assertIs(cfg["jira"]["enabled"], False)

    def test_jira_enabled_must_be_a_bool(self):
        p = self.write_config(MINIMAL.replace("[jira]\n", '[jira]\nenabled = "no"\n'))
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p)
        self.assertIn("jira.enabled", str(cm.exception))

    def test_missing_file_names_path(self):
        p = self.tmp / "nope.toml"
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p)
        self.assertIn(str(p), str(cm.exception))

    def test_invalid_toml_names_path(self):
        p = self.write_config("[jira\n")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p)
        self.assertIn(str(p), str(cm.exception))

    def test_defaults_and_partial_weights(self):
        p = self.write_config(MINIMAL + "\n[score.weights]\npriority = 9\n")
        cfg = config.load(p)
        self.assertEqual(cfg["sync"]["interval_minutes"], 5)
        self.assertEqual(cfg["server"]["port"], 8765)
        self.assertEqual(cfg["jira"]["acli_path"], "acli")
        self.assertTrue(cfg["github"]["enabled"])
        w = cfg["score"]["weights"]
        self.assertEqual(w["priority"], 9)
        self.assertEqual(w["blocked"], -10)
        self.assertEqual(w["deadline"], 2)
        self.assertEqual(
            config.DEFAULTS["score"]["weights"]["priority"], 3
        )  # not mutated

    def test_env_var_override(self):
        self.set_env(config.ENV_VAR, str(self.write_config(MINIMAL)))
        self.assertEqual(config.load()["jira"]["site"], "x.atlassian.net")


class DashboardTests(unittest.TestCase):
    def test_parses_under_python_39(self):
        # Syntax only: 3.9 parses `X | Y`, so this cannot catch a runtime-only
        # 3.10+ feature placed above the guard.
        src = (PKG / "dashboard.py").read_text()
        ast.parse(src, feature_version=(3, 9))
        self.assertLess(src.index("sys.version_info < (3, 11)"), src.index("import db"))

    def test_help_exits_zero(self):
        r = subprocess.run(
            [sys.executable, str(PKG / "dashboard.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage", r.stdout)

    def test_init_db(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "x.db"
            r = subprocess.run(
                [
                    sys.executable,
                    str(PKG / "dashboard.py"),
                    "init-db",
                    "--db",
                    str(target),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(target.exists())
            sqlite3.connect(target).close()


class AskedColumnTests(unittest.TestCase):
    def test_session_events_has_a_nullable_asked_column(self):
        with tempfile.TemporaryDirectory() as td:
            conn = db.connect(Path(td) / "d.db")
            try:
                cols = {
                    r[1]: r for r in conn.execute("PRAGMA table_info(session_events)")
                }
                self.assertIn("asked", cols)
                self.assertEqual(cols["asked"][2], "INTEGER")
                self.assertEqual(cols["asked"][3], 0)  # nullable
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
