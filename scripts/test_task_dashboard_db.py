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
        conn.execute("INSERT INTO notes (item_key, body) VALUES ('ABC-1', 'keep me')")
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
            'INSERT INTO "order" (item_key, position) VALUES (?, ?)', ("ABC-1", 1.5)
        )

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

    def test_missing_file_names_path(self):
        p = self.tmp / "nope.toml"
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


if __name__ == "__main__":
    unittest.main()
