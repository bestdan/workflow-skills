#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/overlay.py and its routes.

Covers the overlay writes (pin, order, snooze, note), the export/import round
trip and its validation, overlay rows surviving a reconcile that drops their
issue, and the overlay POSTs behind the server's Host/Origin/token guard. The
server cases reuse the server suite's harness, and the reconcile case the sync
suite's fake acli. Needs Python 3.11: run via scripts/test-task-dashboard-overlay.sh.
"""

import contextlib
import copy
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
DASHBOARD = PKG / "dashboard.py"
sys.path.insert(0, str(PKG))

# mypy checks this file without scripts/task-dashboard on its path; the
# modules themselves are type-checked in their own group (scripts/typecheck.sh).
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402
import test_task_dashboard_sync as sync_harness  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def dump(conn):
    """Every overlay row, for an exact before/after comparison."""
    return {
        t: conn.execute(f'SELECT * FROM "{t}" ORDER BY source, item_key').fetchall()
        for t in db.OVERLAY_TABLES
    }


def populate(conn):
    overlay.pin(conn, ("jira", "ABC-1"), NOW)
    overlay.pin(conn, ("jira", "ABC-2"), NOW + timedelta(minutes=1))
    overlay.pin(conn, ("github", "https://github.com/acme/app/pull/7"), NOW)
    overlay.set_order(conn, [("jira", "ABC-2"), ("jira", "ABC-1")])
    overlay.snooze(conn, ("jira", "ABC-3"), "2026-10-24")
    overlay.note(conn, ("jira", "ABC-1"), "call Sam\nthen ship", NOW)
    overlay.note(conn, ("jira", "GONE-1"), "kept while absent", NOW)


class OverlayCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.db_path = self.tmp / "d.db"
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)


class WriteTests(OverlayCase):
    def order(self):
        return [
            r[0]
            for r in self.conn.execute('SELECT item_key FROM "order" ORDER BY position')
        ]

    def test_pin_appends_to_the_order_and_is_idempotent(self):
        overlay.pin(self.conn, ("jira", "A-1"), NOW)
        overlay.pin(self.conn, ("jira", "A-2"), NOW)
        overlay.pin(self.conn, ("jira", "A-1"), NOW + timedelta(hours=1))
        self.assertEqual(self.order(), ["A-1", "A-2"])
        at = self.conn.execute(
            "SELECT pinned_at FROM pins WHERE item_key = 'A-1'"
        ).fetchone()
        self.assertEqual(at, ("2026-10-23T12:00:00Z",))

    def test_unpin_removes_pin_and_order(self):
        overlay.pin(self.conn, ("jira", "A-1"), NOW)
        overlay.unpin(self.conn, ("jira", "A-1"))
        self.assertEqual(dump(self.conn)["pins"], [])
        self.assertEqual(self.order(), [])

    def test_order_puts_listed_first_and_keeps_hidden_pins_after(self):
        for k in ("A-1", "A-2", "HIDDEN-1", "A-3"):
            overlay.pin(self.conn, ("jira", k), NOW)
        overlay.set_order(self.conn, [("jira", "A-3"), ("jira", "A-1")])
        self.assertEqual(self.order(), ["A-3", "A-1", "A-2", "HIDDEN-1"])

    def test_order_refuses_unpinned_and_repeated_items(self):
        overlay.pin(self.conn, ("jira", "A-1"), NOW)
        before = dump(self.conn)
        for items in ([("jira", "B-1")], [("jira", "A-1"), ("jira", "A-1")]):
            with self.assertRaises(overlay.OverlayError):
                overlay.set_order(self.conn, items)
        self.assertEqual(dump(self.conn), before)

    def test_snooze_sets_validates_and_clears(self):
        overlay.snooze(self.conn, ("jira", "A-1"), "2026-10-24")
        for bad in ("tomorrow", "2026-13-01", "2026-02-30", "2026-10-24T00:00"):
            with self.assertRaises(overlay.OverlayError, msg=bad):
                overlay.snooze(self.conn, ("jira", "A-1"), bad)
        ov = overlay.load(self.conn)
        # Local instants: noon the day before, and the local midnight it ends.
        noon = datetime(2026, 10, 23, 12).astimezone()
        midnight = datetime(2026, 10, 24).astimezone()
        self.assertTrue(ov.is_snoozed(("jira", "A-1"), noon))
        self.assertFalse(ov.is_snoozed(("jira", "A-1"), midnight))
        overlay.snooze(self.conn, ("jira", "A-1"), "")
        self.assertEqual(dump(self.conn)["snoozes"], [])

    def test_blank_note_deletes(self):
        overlay.note(self.conn, ("jira", "A-1"), "hello", NOW)
        overlay.note(self.conn, ("jira", "A-1"), "  \n", NOW)
        self.assertEqual(dump(self.conn)["notes"], [])

    def test_apply_parses_the_form(self):
        overlay.apply(self.conn, "pin", {"key": ["A-1"]}, NOW)
        overlay.apply(self.conn, "pin", {"source": ["github"], "key": ["u"]}, NOW)
        overlay.apply(self.conn, "note", {"key": ["A-1"], "text": ["x"]}, NOW)
        self.assertEqual(
            [r[:2] for r in dump(self.conn)["pins"]], [("github", "u"), ("jira", "A-1")]
        )
        for action, form in (
            ("pin", {}),
            ("pin", {"key": ["A-1", "A-2"]}),
            ("pin", {"key": ["bad\x00key"]}),
            ("nope", {"key": ["A-1"]}),
        ):
            with self.assertRaises(overlay.OverlayError, msg=(action, form)):
                overlay.apply(self.conn, action, form, NOW)


class ExportImportTests(OverlayCase):
    def wipe(self):
        with self.conn:
            for t in db.OVERLAY_TABLES:
                self.conn.execute(f'DELETE FROM "{t}"')

    def test_round_trip_leaves_tables_identical(self):
        populate(self.conn)
        before = dump(self.conn)
        doc = json.loads(overlay.dumps(overlay.export(self.conn, NOW)))
        self.assertEqual((doc["format"], doc["version"]), ("task-dashboard-overlay", 1))
        self.wipe()
        self.assertEqual(dump(self.conn)["notes"], [])
        counts = overlay.import_(self.conn, doc)
        self.assertEqual(dump(self.conn), before)
        self.assertEqual(counts, {"pins": 3, "order": 3, "snoozes": 1, "notes": 2})

    def test_import_replaces_rather_than_merges(self):
        populate(self.conn)
        doc = overlay.export(self.conn)
        overlay.note(self.conn, ("jira", "EXTRA-1"), "not in the file", NOW)
        overlay.import_(self.conn, doc)
        keys = [r[1] for r in dump(self.conn)["notes"]]
        self.assertNotIn("EXTRA-1", keys)

    def test_malformed_documents_are_refused_with_the_overlay_unchanged(self):
        populate(self.conn)
        good = overlay.export(self.conn)
        before = dump(self.conn)

        def bad(mutate):
            doc = copy.deepcopy(good)
            mutate(doc)
            return doc

        cases = {
            "not an object": [],
            "wrong format": bad(lambda d: d.update(format="other")),
            "wrong version": bad(lambda d: d.update(version=2)),
            "missing table": bad(lambda d: d["tables"].pop("notes")),
            "extra table": bad(lambda d: d["tables"].update(issues=[])),
            "table not a list": bad(lambda d: d["tables"].update(pins={})),
            "row missing a column": bad(lambda d: d["tables"]["notes"][0].pop("body")),
            "row extra column": bad(lambda d: d["tables"]["pins"][0].update(x=1)),
            "empty key": bad(lambda d: d["tables"]["pins"][0].update(item_key="")),
            "bad position": bad(lambda d: d["tables"]["order"][0].update(position="1")),
            "bool position": bad(
                lambda d: d["tables"]["order"][0].update(position=True)
            ),
            "NaN position": bad(
                lambda d: d["tables"]["order"][0].update(position=float("nan"))
            ),
            "infinite position": bad(
                lambda d: d["tables"]["order"][0].update(position=float("inf"))
            ),
            "order row with no pin": bad(
                lambda d: d["tables"]["order"].append(
                    {"source": "jira", "item_key": "NOPIN-1", "position": 9.0}
                )
            ),
            "pin with no order row": bad(lambda d: d["tables"]["order"].pop()),
            "pin with a null position": bad(
                lambda d: d["tables"]["order"][0].update(position=None)
            ),
            "bad snooze date": bad(
                lambda d: d["tables"]["snoozes"][0].update(until="soon")
            ),
            "note not text": bad(lambda d: d["tables"]["notes"][0].update(body=5)),
            "note too long": bad(
                lambda d: d["tables"]["notes"][0].update(
                    body="x" * (overlay.MAX_NOTE + 1)
                )
            ),
            # The last table is the bad one, so a partial write would show.
            "duplicate row": bad(
                lambda d: d["tables"]["notes"].append(dict(d["tables"]["notes"][0]))
            ),
        }
        for name, doc in cases.items():
            with self.assertRaises(overlay.OverlayError, msg=name):
                overlay.import_(self.conn, doc)
            self.assertEqual(dump(self.conn), before, name)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(DASHBOARD), *args, "--db", str(self.db_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_cli_export_import_round_trip(self):
        populate(self.conn)
        before = dump(self.conn)
        out = self.cli("export")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("call Sam", out.stdout)
        path = self.tmp / "backup.json"
        self.assertEqual(self.cli("export", str(path)).returncode, 0)
        self.assertEqual(
            json.loads(path.read_text())["tables"], json.loads(out.stdout)["tables"]
        )
        self.wipe()
        res = self.cli("import", str(path))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(dump(self.conn), before)

    def test_cli_refuses_a_malformed_file(self):
        populate(self.conn)
        before = dump(self.conn)
        path = self.tmp / "bad.json"
        doc = overlay.export(self.conn)
        doc["tables"]["order"][0]["position"] = float("inf")
        unplaced = overlay.export(self.conn)
        unplaced["tables"]["order"].pop()  # a pin with no order row
        for text in (
            "{not json",
            json.dumps({"format": "task-dashboard-overlay"}),
            json.dumps(doc),  # writes the literal Infinity, which json.loads accepts
            json.dumps(unplaced),
        ):
            path.write_text(text)
            res = self.cli("import", str(path))
            self.assertEqual(res.returncode, 1, text)
            self.assertIn("task-dashboard:", res.stderr)
            self.assertEqual(dump(self.conn), before)
        self.assertEqual(
            self.cli("import", str(self.tmp / "missing.json")).returncode, 1
        )

    def test_cli_export_failure_keeps_the_old_file_and_no_temp(self):
        populate(self.conn)
        out_dir = self.tmp / "ro"
        out_dir.mkdir()
        dest = out_dir / "backup.json"
        dest.write_text("old backup")
        out_dir.chmod(0o500)  # the temp file cannot be created beside dest
        self.addCleanup(out_dir.chmod, 0o700)
        res = self.cli("export", str(dest))
        self.assertEqual(res.returncode, 1)
        self.assertRegex(res.stderr, r"^task-dashboard: cannot write .*\n$")
        self.assertEqual(dest.read_text(), "old backup")
        self.assertEqual(sorted(p.name for p in out_dir.iterdir()), ["backup.json"])
        a_file = self.tmp / "a-file"
        a_file.write_text("")
        # A missing parent, and a parent that is a file (where the temp-file
        # cleanup itself fails with NotADirectoryError).
        for bad in (self.tmp / "no-such-dir" / "b.json", a_file / "b.json"):
            res = self.cli("export", str(bad))
            self.assertEqual(res.returncode, 1, bad)
            self.assertRegex(res.stderr, r"^task-dashboard: [^\n]*\n$", bad)

    def test_cli_db_path_os_errors_are_one_line(self):
        a_file = self.tmp / "a-file"
        a_file.write_text("")
        path = self.tmp / "backup.json"
        path.write_text(overlay.dumps(overlay.export(self.conn)))
        for args in (("export",), ("import", str(path))):
            res = subprocess.run(
                [sys.executable, str(DASHBOARD), *args, "--db", str(a_file / "x.db")],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(res.returncode, 1, args)
            self.assertRegex(res.stderr, r"^task-dashboard: [^\n]*\n$", args)

    def test_cli_sqlite_errors_are_one_line(self):
        path = self.tmp / "backup.json"
        path.write_text(overlay.dumps(overlay.export(self.conn)))
        bad_db = self.tmp / "not-a-db"
        bad_db.write_text("this is not a sqlite file\n" * 100)
        for args in (("export",), ("import", str(path))):
            res = subprocess.run(
                [sys.executable, str(DASHBOARD), *args, "--db", str(bad_db)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(res.returncode, 1, args)
            self.assertRegex(res.stderr, r"^task-dashboard: [^\n]*\n$", args)

    def test_import_sqlite_error_is_reported_and_rolled_back(self):
        populate(self.conn)
        before = dump(self.conn)
        path = self.tmp / "backup.json"
        path.write_text(overlay.dumps(overlay.export(self.conn)))
        real = overlay.import_

        def locked(conn, doc):
            raise sqlite3.OperationalError("database is locked")

        overlay.import_ = locked
        self.addCleanup(setattr, overlay, "import_", real)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = dashboard.main(["import", str(path), "--db", str(self.db_path)])
        self.assertEqual(rc, 1)
        self.assertEqual(
            err.getvalue(),
            "task-dashboard: import refused, overlay unchanged: database is locked\n",
        )
        self.assertEqual(dump(self.conn), before)


class LocalDateTests(unittest.TestCase):
    """A snooze date is compared with the machine's local date, not UTC's."""

    # 23:30 UTC on the 23rd is still the 23rd in Los Angeles, the 24th in Tokyo.
    INSTANT = datetime(2026, 10, 23, 23, 30, tzinfo=timezone.utc)

    def in_zone(self, name):
        old = os.environ.get("TZ")
        os.environ["TZ"] = name
        time.tzset()

        def restore():
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

        self.addCleanup(restore)

    def test_snooze_ends_at_local_midnight(self):
        ov = overlay.Overlay(snoozed_until={("jira", "A-1"): "2026-10-24"})
        for zone, snoozed in (
            ("America/Los_Angeles", True),  # still the 23rd there
            ("Asia/Tokyo", False),  # already the 24th
            ("UTC", True),
        ):
            self.in_zone(zone)
            self.assertEqual(
                ov.is_snoozed(("jira", "A-1"), self.INSTANT), snoozed, zone
            )


class ReconcileTests(sync_harness.SyncCase):
    def test_note_survives_a_reconcile_that_drops_its_issue(self):
        acli = sync_harness.FakeAcli(["OLD-1", "KEEP-1"])
        self.run_sync("full", acli)
        overlay.note(self.conn, ("jira", "OLD-1"), "mine", NOW)
        overlay.pin(self.conn, ("jira", "OLD-1"), NOW)
        before = dump(self.conn)
        row = self.run_sync(
            "reconcile", sync_harness.FakeAcli(["KEEP-1"]), now=NOW + timedelta(hours=1)
        )
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(self.keys(), ["KEEP-1"])
        self.assertEqual(dump(self.conn), before)


class RouteTests(server_harness.ServerCase):
    def post(self, action, fields, **kw):
        origin = kw.pop("origin", f"http://127.0.0.1:{self.port}")
        body = [("token", kw.pop("token", self.token)), *fields]
        return self.request(
            "POST", f"/overlay/{action}", origin=origin, body=body, **kw
        )

    def overlay_rows(self):
        conn = self.conn()
        try:
            return dump(conn)
        finally:
            conn.close()

    ROUTES = {
        "pin": [("key", "ABC-1")],
        "unpin": [("key", "ABC-1")],
        "order": [("key", "ABC-1")],
        "snooze": [("key", "ABC-1"), ("until", "2026-10-24")],
        "note": [("key", "ABC-1"), ("text", "hi")],
    }

    def test_every_route_without_the_token_is_403(self):
        for action, fields in self.ROUTES.items():
            for token in ("", "nope"):
                status, _ = self.post(action, fields, token=token)
                self.assertEqual(status, 403, (action, token))
        self.assertEqual(self.overlay_rows(), {t: [] for t in db.OVERLAY_TABLES})

    def test_every_route_needs_host_and_origin(self):
        for action, fields in self.ROUTES.items():
            status, _ = self.post(action, fields, origin="http://evil.example")
            self.assertEqual(status, 403, action)
            status, _ = self.post(action, fields, host=f"evil.example:{self.port}")
            self.assertEqual(status, 403, action)
        self.assertEqual(self.overlay_rows(), {t: [] for t in db.OVERLAY_TABLES})

    def test_unknown_overlay_route_is_404(self):
        self.assertEqual(self.post("drop", [("key", "ABC-1")])[0], 404)

    def test_writes_land_and_the_board_shows_them(self):
        conn = self.conn()
        server_harness.seed(conn)
        conn.close()
        self.assertEqual(self.post("pin", [("key", "ABC-4")])[0], 200)
        self.assertEqual(self.post("pin", [("key", "ABC-1")])[0], 200)
        self.assertEqual(
            self.post(
                "order", [("source", "jira"), ("key", "ABC-1"), ("key", "ABC-4")]
            )[0],
            200,
        )
        self.assertEqual(
            self.post("note", [("key", "ABC-4"), ("text", "<b>me</b>")])[0], 200
        )
        self.assertEqual(
            self.post("snooze", [("key", "ABC-6"), ("until", "2999-01-01")])[0], 200
        )
        page = self.request("GET", "/")[1]
        pinned = re.search(r'data-section="pinned".*?</section>', page, re.S)
        assert pinned is not None
        self.assertEqual(
            re.findall(r'data-key="([^"]+)"', pinned.group(0)), ["ABC-1", "ABC-4"]
        )
        self.assertIn("&lt;b&gt;me&lt;/b&gt;", page)
        self.assertNotIn("<b>me</b>", page)
        self.assertIn("snoozed until 2999-01-01", page)
        self.assertIn('class="card snoozed" data-source="jira" data-key="ABC-6"', page)

    def test_bad_input_is_400_and_writes_nothing(self):
        for action, fields in (
            ("snooze", [("key", "ABC-1"), ("until", "tomorrow")]),
            ("order", [("key", "NOT-PINNED")]),
            ("pin", []),
        ):
            self.assertEqual(self.post(action, fields)[0], 400, action)
        self.assertEqual(self.overlay_rows(), {t: [] for t in db.OVERLAY_TABLES})

    def test_note_for_an_absent_issue_is_hidden_and_comes_back(self):
        self.assertEqual(
            self.post("note", [("key", "ABC-2"), ("text", "for later")])[0], 200
        )
        self.assertNotIn("for later", self.request("GET", "/")[1])
        conn = self.conn()
        server_harness.seed(conn)
        conn.close()
        self.assertIn("for later", self.request("GET", "/")[1])

    def test_a_database_error_is_503_with_a_json_body(self):
        real = overlay.apply

        def locked(conn, action, form, now=None):
            raise sqlite3.OperationalError("database is locked")

        overlay.apply = locked
        self.addCleanup(setattr, overlay, "apply", real)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, body = self.post("pin", [("key", "ABC-1")])
        self.assertEqual(status, 503)
        self.assertEqual(
            json.loads(body), {"ok": False, "error": "database busy, try again"}
        )
        self.assertNotIn("Traceback", body)
        self.assertIn("database is locked", err.getvalue())

    def test_board_snooze_uses_the_local_date(self):
        conn = self.conn()
        server_harness.seed(conn)
        conn.close()
        self.assertEqual(
            self.post("snooze", [("key", "ABC-4"), ("until", "2026-10-24")])[0], 200
        )
        self.httpd.app.now = lambda: LocalDateTests.INSTANT
        old = os.environ.get("TZ")
        self.addCleanup(time.tzset)
        self.addCleanup(
            lambda: (
                os.environ.pop("TZ", None)
                if old is None
                else os.environ.__setitem__("TZ", old)
            )
        )
        badge = "snoozed until 2026-10-24"
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        self.assertIn(badge, self.request("GET", "/")[1])
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        self.assertNotIn(badge, self.request("GET", "/")[1])

    def test_a_long_note_fits_the_overlay_body_limit(self):
        text = "é" * overlay.MAX_NOTE
        self.assertEqual(self.post("note", [("key", "ABC-1"), ("text", text)])[0], 200)
        self.assertEqual(self.overlay_rows()["notes"][0][2], text)


if __name__ == "__main__":
    unittest.main()
