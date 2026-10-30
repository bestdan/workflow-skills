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
import github_fleet  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_server as server_harness  # noqa: E402
import test_task_dashboard_sync as sync_harness  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def dump(conn):
    """Every overlay row, for an exact before/after comparison."""
    out = {
        t: conn.execute(f'SELECT * FROM "{t}" ORDER BY source, item_key').fetchall()
        for t in db.OVERLAY_TABLES
    }
    for t in db.OVERLAY_SINGLETONS:
        out[t] = conn.execute(f'SELECT * FROM "{t}"').fetchall()
    for t in db.OVERLAY_VIEW_TABLES:
        out[t] = conn.execute(f'SELECT * FROM "{t}" ORDER BY view').fetchall()
    # The answers' own ids are the DB's and are not exported.
    cols = ", ".join(overlay.WHY_NOT_COLUMNS)
    out["why_not"] = conn.execute(
        f"SELECT {cols} FROM why_not ORDER BY recorded_at, id"
    ).fetchall()
    return out


# A pre-version-5 file's `focus` table: still accepted, and its rows dropped.
FOCUS_ROWS = [{"source": "jira", "item_key": "ABC-1", "set_at": "2026-10-23T12:00:00Z"}]


def legacy(doc, version):
    """`doc` (a current export) as a version 2 to 6 file, with a focus row
    up to version 4."""
    old = {
        k: v
        for k, v in copy.deepcopy(doc).items()
        if (version >= 4 or k != "view_includes")
        and (version >= 6 or k != "why_not")
        and k != "now_mode"
    }
    old["version"] = version
    if version >= 5:
        return old
    tables = overlay.V2_TABLES if version == 2 else overlay.V3_TABLES
    old["tables"] = {t: old["tables"].get(t, []) for t in tables}
    old["tables"]["focus"] = copy.deepcopy(FOCUS_ROWS)
    return old


def empty():
    return {
        t: []
        for t in db.OVERLAY_TABLES
        + db.OVERLAY_SINGLETONS
        + db.OVERLAY_VIEW_TABLES
        + db.OVERLAY_LOG_TABLES
    }


WHY_SNAP = {
    "kind": "pr",
    "title": "a bot PR",
    "score": 10.3,
    "terms": [["quick_win", 10.0, "bot PR"], ["age", 0.3, "waiting 2d"]],
    "features": {"repo": "acme/app", "kind": "pr", "age_days": 2.1, "author": None},
}


def populate(conn):
    overlay.pin(conn, ("jira", "ABC-1"), NOW)
    overlay.pin(conn, ("jira", "ABC-2"), NOW + timedelta(minutes=1))
    overlay.pin(conn, ("github", "https://github.com/acme/app/pull/7"), NOW)
    overlay.set_order(conn, [("jira", "ABC-2"), ("jira", "ABC-1")])
    overlay.snooze(conn, ("jira", "ABC-3"), "2026-10-24")
    overlay.note(conn, ("jira", "ABC-1"), "call Sam\nthen ship", NOW)
    overlay.note(conn, ("jira", "GONE-1"), "kept while absent", NOW)
    overlay.tag(conn, ("jira", "ABC-2"), "side-quest", now=NOW)
    overlay.tag(conn, ("jira", "ABC-1"), "substantial", now=NOW)  # has a note
    overlay.ignore(conn, ("jira", "NOTME-1"), NOW)
    overlay.set_last_visit(conn, NOW)
    overlay.set_view_include(conn, "needs", frozenset({"sessions", "prs"}), NOW)
    overlay.set_view_include(conn, "board", frozenset(), NOW)
    url = "https://github.com/acme/app/pull/9"
    overlay.snooze(conn, ("github", url), "2026-10-30")
    overlay.why_not(conn, ("github", url), "wrong_priority", "later", WHY_SNAP, NOW)
    overlay.why_not(conn, ("github", url), "other", "", None, NOW)
    overlay.set_now_mode(conn, "little", NOW)


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

    def test_a_reason_needs_a_snooze_and_never_snoozes_again(self):
        item = ("github", "https://github.com/acme/app/pull/9")
        before = dump(self.conn)
        with self.assertRaisesRegex(overlay.OverlayError, "snooze it first"):
            overlay.why_not(self.conn, item, "other", "", WHY_SNAP, NOW)
        with self.assertRaisesRegex(overlay.OverlayError, "snooze it first"):
            overlay.apply(
                self.conn, "whynot", {"source": [item[0]], "key": [item[1]],
                                      "reason": ["other"]}, NOW
            )  # fmt: skip
        self.assertEqual(dump(self.conn), before)
        overlay.snooze(self.conn, item, "2026-10-30")
        # A stray date on the form is not a second snooze.
        overlay.apply(
            self.conn,
            "whynot",
            {"source": [item[0]], "key": [item[1]], "reason": ["not_urgent"],
             "until": ["2026-12-01"]},
            NOW,
            lambda _: WHY_SNAP,
        )  # fmt: skip
        after = dump(self.conn)
        self.assertEqual(after["snoozes"], [(*item, "2026-10-30")])
        [row] = after["why_not"]
        self.assertEqual(row[4], "not_urgent")
        self.assertEqual(json.loads(row[7]), WHY_SNAP["terms"])
        self.assertEqual(json.loads(row[8]), WHY_SNAP["features"])

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


class FleetWriteTests(OverlayCase):
    """kind_tag and last_visit."""

    def tags(self):
        return dict(
            ((s, k), t)
            for s, k, t in self.conn.execute(
                "SELECT source, item_key, tag FROM kind_tags"
            )
        )

    def test_substantial_without_a_note_is_rejected(self):
        for text in ("", "   \n"):
            with self.assertRaises(overlay.OverlayError, msg=repr(text)):
                overlay.tag(self.conn, ("jira", "A-1"), "substantial", text, NOW)
        self.assertEqual(dump(self.conn), empty())

    def test_substantial_with_a_note_is_accepted_either_way(self):
        overlay.tag(self.conn, ("jira", "A-1"), "substantial", "moves: yes-rate", NOW)
        self.assertEqual(
            overlay.load(self.conn).notes[("jira", "A-1")], "moves: yes-rate"
        )
        overlay.note(self.conn, ("jira", "A-2"), "moves: queue length", NOW)
        overlay.tag(self.conn, ("jira", "A-2"), "substantial", now=NOW)
        self.assertEqual(
            self.tags(),
            {("jira", "A-1"): "substantial", ("jira", "A-2"): "substantial"},
        )

    def test_a_substantial_items_note_cannot_be_blanked(self):
        overlay.tag(self.conn, ("jira", "A-1"), "substantial", "figure", NOW)
        before = dump(self.conn)
        with self.assertRaises(overlay.OverlayError):
            overlay.note(self.conn, ("jira", "A-1"), " ", NOW)
        self.assertEqual(dump(self.conn), before)
        overlay.tag(self.conn, ("jira", "A-1"), "", now=NOW)  # untag, then it can
        overlay.note(self.conn, ("jira", "A-1"), "", NOW)
        self.assertEqual(dump(self.conn), empty())

    def test_side_quest_needs_no_note_and_bad_tags_are_refused(self):
        overlay.tag(self.conn, ("jira", "A-1"), "side-quest", now=NOW)
        self.assertEqual(self.tags(), {("jira", "A-1"): "side-quest"})
        for bad in ("side_quest", "Substantial", "other"):
            with self.assertRaises(overlay.OverlayError, msg=bad):
                overlay.tag(self.conn, ("jira", "A-1"), bad, now=NOW)
        self.assertEqual(self.tags(), {("jira", "A-1"): "side-quest"})

    def test_retagging_the_same_tag_keeps_tagged_at(self):
        overlay.tag(self.conn, ("jira", "A-1"), "side-quest", now=NOW)
        overlay.tag(self.conn, ("jira", "A-1"), "side-quest", now=NOW + timedelta(1))
        at = self.conn.execute("SELECT tagged_at FROM kind_tags").fetchone()
        self.assertEqual(at, ("2026-10-23T12:00:00Z",))

    def test_last_visit_is_one_row(self):
        overlay.set_last_visit(self.conn, NOW)
        overlay.set_last_visit(self.conn, NOW + timedelta(hours=2))
        self.assertEqual(overlay.load(self.conn).last_visit, "2026-10-23T14:00:00Z")
        self.assertEqual(len(dump(self.conn)["last_visit"]), 1)

    def test_last_visit_never_moves_back(self):
        overlay.set_last_visit(self.conn, NOW)
        overlay.set_last_visit(self.conn, NOW - timedelta(hours=2))
        self.assertEqual(overlay.load(self.conn).last_visit, "2026-10-23T12:00:00Z")

    def test_visit_records_the_render_time_capped_at_now(self):
        overlay.apply(self.conn, "visit", {"at": ["2026-10-23T11:30:00Z"]}, NOW)
        self.assertEqual(overlay.load(self.conn).last_visit, "2026-10-23T11:30:00Z")
        overlay.apply(self.conn, "visit", {"at": ["2026-10-24T00:00:00Z"]}, NOW)
        self.assertEqual(overlay.load(self.conn).last_visit, "2026-10-23T12:00:00Z")

    def test_visit_without_at_is_now_and_a_bad_at_is_refused(self):
        overlay.apply(self.conn, "visit", {}, NOW - timedelta(hours=1))
        self.assertEqual(overlay.load(self.conn).last_visit, "2026-10-23T11:00:00Z")
        for bad in ("yesterday", "2026-02-30T00:00:00Z", "2026-10-23"):
            with self.assertRaises(overlay.OverlayError, msg=bad):
                overlay.apply(self.conn, "visit", {"at": [bad]}, NOW)
        self.assertEqual(overlay.load(self.conn).last_visit, "2026-10-23T11:00:00Z")

    def test_apply_routes_the_new_actions(self):
        overlay.apply(self.conn, "tag", {"key": ["A-1"], "tag": ["side-quest"]}, NOW)
        overlay.apply(
            self.conn,
            "tag",
            {"key": ["A-2"], "tag": ["substantial"], "text": ["figure"]},
            NOW,
        )
        overlay.apply(self.conn, "tag", {"key": ["A-1"], "tag": [""]}, NOW)
        self.assertEqual(self.tags(), {("jira", "A-2"): "substantial"})
        for gone in ("focus", "unfocus"):
            with self.assertRaises(overlay.OverlayError, msg=gone):
                overlay.apply(self.conn, gone, {"key": ["A-1"]}, NOW)
        with self.assertRaises(overlay.OverlayError):
            overlay.apply(
                self.conn, "tag", {"key": ["A-3"], "tag": ["substantial"]}, NOW
            )


class ExportImportTests(OverlayCase):
    def wipe(self):
        with self.conn:
            for t in empty():
                self.conn.execute(f'DELETE FROM "{t}"')

    def test_a_retired_views_include_is_left_out_everywhere(self):
        # Since you were away folded into Needs you. A choice remembered for
        # it before, or carried by an older backup, loads as nothing.
        populate(self.conn)
        with self.conn:
            self.conn.execute(
                "INSERT INTO view_includes (view, groups, set_at)"
                " VALUES ('away', 'prs', '2026-10-23T12:00:00Z')"
            )
        self.assertNotIn("away", overlay.load(self.conn).view_includes)
        doc = json.loads(overlay.dumps(overlay.export(self.conn, NOW)))
        self.assertNotIn("away", [r["view"] for r in doc["view_includes"]])
        doc["view_includes"].append(
            {"view": "away", "include": ["prs"], "set_at": "2026-10-23T12:00:00Z"}
        )
        self.wipe()
        counts = overlay.import_(self.conn, doc)
        self.assertEqual(counts["view_includes"], 2)
        self.assertNotIn("away", overlay.load(self.conn).view_includes)

    def test_round_trip_leaves_tables_identical(self):
        populate(self.conn)
        before = dump(self.conn)
        doc = json.loads(overlay.dumps(overlay.export(self.conn, NOW)))
        self.assertFalse(self.conn.in_transaction)  # the read snapshot is closed
        self.assertEqual((doc["format"], doc["version"]), ("task-dashboard-overlay", 7))
        self.assertEqual(
            doc["now_mode"], {"mode": "little", "set_at": "2026-10-23T12:00:00Z"}
        )
        self.assertEqual(
            [(a["reason"], a["text"], a["score"]) for a in doc["why_not"]],
            [("wrong_priority", "later", 10.3), ("other", None, None)],
        )
        self.assertEqual(doc["why_not"][0]["terms"], WHY_SNAP["terms"])
        self.assertEqual(doc["why_not"][0]["features"], WHY_SNAP["features"])
        self.assertNotIn("focus", doc["tables"])
        self.assertEqual(doc["last_visit"], "2026-10-23T12:00:00Z")
        self.assertEqual(
            doc["view_includes"],
            [
                {"view": "board", "include": [], "set_at": "2026-10-23T12:00:00Z"},
                {
                    "view": "needs",
                    "include": ["prs", "sessions"],
                    "set_at": "2026-10-23T12:00:00Z",
                },
            ],
        )
        self.wipe()
        self.assertEqual(dump(self.conn), empty())
        counts = overlay.import_(self.conn, doc)
        self.assertEqual(dump(self.conn), before)
        self.assertEqual(
            counts,
            {
                "pins": 3,
                "order": 3,
                "snoozes": 2,  # one is the snooze the why_not answers explain
                "notes": 2,
                "kind_tags": 2,
                "ignores": 1,
                "view_includes": 2,
                "why_not": 2,
                "now_mode": 1,
            },
        )
        self.assertEqual(
            overlay.load(self.conn).view_includes,
            {"needs": frozenset({"prs", "sessions"}), "board": frozenset()},
        )
        ov = overlay.load(self.conn)
        self.assertEqual(
            ov.kind_tags,
            {("jira", "ABC-1"): "substantial", ("jira", "ABC-2"): "side-quest"},
        )

    def test_a_version_1_file_still_imports_with_the_new_fields_empty(self):
        populate(self.conn)
        doc = overlay.export(self.conn)
        v1 = {
            "format": doc["format"],
            "version": 1,
            "exported_at": doc["exported_at"],
            "tables": {t: doc["tables"][t] for t in overlay.V1_TABLES},
        }
        before = dump(self.conn)
        counts = overlay.import_(self.conn, v1)
        after = dump(self.conn)
        for t in overlay.V1_TABLES:
            self.assertEqual(after[t], before[t], t)
        self.assertEqual((after["kind_tags"], after["last_visit"]), ([], []))
        self.assertEqual(after["view_includes"], [])
        self.assertEqual(counts["kind_tags"], 0)
        self.assertNotIn("focus", counts)

    def test_a_version_2_file_still_imports_with_no_ignores(self):
        populate(self.conn)
        overlay.ignore(self.conn, ("jira", "GONE-1"), NOW)
        doc = overlay.export(self.conn)
        before = dump(self.conn)
        counts = overlay.import_(self.conn, legacy(doc, 2))
        after = dump(self.conn)
        self.assertEqual(after["ignores"], [])
        self.assertEqual(after["kind_tags"], before["kind_tags"])
        self.assertNotIn("focus", counts)

    def test_ignore_is_idempotent_and_unignore_clears_it(self):
        item = ("jira", "ABC-1")
        overlay.ignore(self.conn, item, NOW)
        overlay.ignore(self.conn, item, NOW)
        self.assertEqual(overlay.load(self.conn).ignored, {item})
        overlay.unignore(self.conn, item)
        self.assertEqual(overlay.load(self.conn).ignored, set())

    def test_a_version_3_file_still_imports_with_no_view_filters(self):
        populate(self.conn)
        doc = overlay.export(self.conn)
        before = dump(self.conn)
        overlay.import_(self.conn, legacy(doc, 3))
        after = dump(self.conn)
        self.assertEqual(after["view_includes"], [])
        self.assertEqual(after["pins"], before["pins"])
        self.assertEqual(after["ignores"], before["ignores"])

    def test_a_version_6_file_imports_with_no_mode_saved(self):
        populate(self.conn)
        doc = overlay.export(self.conn)
        before = dump(self.conn)
        counts = overlay.import_(self.conn, legacy(doc, 6))
        after = dump(self.conn)
        self.assertEqual(after["now_mode"], [])
        self.assertEqual(counts["now_mode"], 0)
        self.assertIsNone(overlay.load(self.conn).now_mode)  # Now reads Ship
        for t in db.OVERLAY_TABLES + db.OVERLAY_VIEW_TABLES + db.OVERLAY_LOG_TABLES:
            self.assertEqual(after[t], before[t], t)

    def test_a_null_now_mode_imports_as_none_saved(self):
        populate(self.conn)
        doc = overlay.export(self.conn)
        doc["now_mode"] = None
        overlay.import_(self.conn, doc)
        self.assertIsNone(overlay.saved_now_mode(self.conn))

    def test_a_version_5_file_still_imports_with_no_why_not_answers(self):
        populate(self.conn)
        doc = overlay.export(self.conn)
        before = dump(self.conn)
        counts = overlay.import_(self.conn, legacy(doc, 5))
        after = dump(self.conn)
        self.assertEqual(after["why_not"], [])
        self.assertEqual(counts["why_not"], 0)
        for t in db.OVERLAY_TABLES + db.OVERLAY_VIEW_TABLES:
            self.assertEqual(after[t], before[t], t)

    def test_a_version_4_files_focus_rows_are_accepted_and_dropped(self):
        populate(self.conn)
        doc = overlay.export(self.conn)
        before = dump(self.conn)
        self.wipe()
        counts = overlay.import_(self.conn, legacy(doc, 4))
        before["why_not"] = []  # a version 4 file has none
        before["now_mode"] = []
        self.assertEqual(dump(self.conn), before)
        self.assertNotIn("focus", counts)
        # Even rows the old schema refused (two focus items) are not read.
        two = legacy(doc, 4)
        two["tables"]["focus"].append(dict(FOCUS_ROWS[0], item_key="ABC-2"))
        overlay.import_(self.conn, two)
        self.assertEqual(dump(self.conn), before)
        # A version 4 file must still carry the table, as it always did.
        no_focus = legacy(doc, 4)
        del no_focus["tables"]["focus"]
        with self.assertRaises(overlay.OverlayError):
            overlay.import_(self.conn, no_focus)

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
            "wrong version": bad(lambda d: d.update(version=8)),
            "v6 with now_mode": bad(lambda d: d.update(version=6)),
            "missing now_mode": bad(lambda d: d.pop("now_mode")),
            "now_mode unknown mode": bad(lambda d: d["now_mode"].update(mode="nap")),
            "now_mode extra field": bad(lambda d: d["now_mode"].update(x=1)),
            "now_mode bad stamp": bad(lambda d: d["now_mode"].update(set_at="today")),
            "v5 with why_not": bad(lambda d: (d.update(version=5), d.pop("now_mode"))),
            "missing why_not": bad(lambda d: d.pop("why_not")),
            "why_not not a list": bad(lambda d: d.update(why_not={})),
            "why_not unknown reason": bad(
                lambda d: d["why_not"][0].update(reason="bored")
            ),
            "why_not extra field": bad(lambda d: d["why_not"][0].update(x=1)),
            "why_not bad terms": bad(
                lambda d: d["why_not"][0].update(terms=[["age", "high", "x"]])
            ),
            "why_not bad features": bad(
                lambda d: d["why_not"][0].update(features={"repo": ["a"]})
            ),
            "why_not bool score": bad(lambda d: d["why_not"][0].update(score=True)),
            "why_not bad stamp": bad(
                lambda d: d["why_not"][0].update(recorded_at="today")
            ),
            "why_not text too long": bad(
                lambda d: d["why_not"][0].update(text="x" * (overlay.MAX_WHY_TEXT + 1))
            ),
            "v3 with view_includes": bad(
                lambda d: (d.update(version=3), d["tables"].update(focus=[]))
            ),
            "v5 with a focus table": bad(lambda d: d["tables"].update(focus=[])),
            "missing view_includes": bad(lambda d: d.pop("view_includes")),
            "view_includes not a list": bad(lambda d: d.update(view_includes={})),
            "unknown view": bad(lambda d: d["view_includes"][0].update(view="nope")),
            "unknown group": bad(
                lambda d: d["view_includes"][0].update(include=["bugs"])
            ),
            "group not text": bad(lambda d: d["view_includes"][0].update(include=[1])),
            "duplicate view": bad(
                lambda d: d["view_includes"].append(dict(d["view_includes"][0]))
            ),
            "view row extra field": bad(lambda d: d["view_includes"][0].update(x=1)),
            "bad set_at": bad(lambda d: d["view_includes"][0].update(set_at="now")),
            "bool version": bad(lambda d: d.update(version=True)),
            "v1 with the v2 tables": bad(lambda d: d.update(version=1)),
            "v1 with a last_visit": bad(
                lambda d: (
                    d.update(version=1),
                    d["tables"].pop("kind_tags"),
                    d["tables"].pop("ignores"),
                )
            ),
            "missing last_visit": bad(lambda d: d.pop("last_visit")),
            "bad last_visit": bad(lambda d: d.update(last_visit="yesterday")),
            "unreal last_visit": bad(
                lambda d: d.update(last_visit="2026-02-30T00:00:00Z")
            ),
            "missing kind_tags": bad(lambda d: d["tables"].pop("kind_tags")),
            "bad tag": bad(lambda d: d["tables"]["kind_tags"][0].update(tag="epic")),
            "null tag": bad(lambda d: d["tables"]["kind_tags"][0].update(tag=None)),
            "substantial without a note": bad(
                lambda d: d["tables"]["kind_tags"].append(
                    {
                        "source": "jira",
                        "item_key": "ABC-3",
                        "tag": "substantial",
                        "tagged_at": None,
                    }
                )
            ),
            "substantial whose note is dropped": bad(
                lambda d: d["tables"].update(
                    notes=[n for n in d["tables"]["notes"] if n["item_key"] != "ABC-1"]
                )
            ),
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
            "position too big for a float": bad(
                lambda d: d["tables"]["order"][0].update(position=10**400)
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
            "[" * 100_000,  # json.loads raises RecursionError, not ValueError
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

    def test_snooze_is_one_button_a_local_week_out(self):
        self.in_zone("Asia/Tokyo")  # the instant is already the 24th there
        html = overlay.card_controls(
            ("jira", "A-1"), overlay.Overlay(), self.INSTANT, reorder=False
        )
        self.assertIn('data-ov="snooze" data-until="2026-10-31"', html)
        self.assertIn('aria-label="Snooze 7d"', html)
        self.assertNotIn('type="date"', html)

    def test_a_snoozed_card_offers_unsnooze(self):
        self.in_zone("UTC")
        ov = overlay.Overlay(snoozed_until={("jira", "A-1"): "2026-10-24"})
        html = overlay.card_controls(("jira", "A-1"), ov, self.INSTANT, reorder=False)
        self.assertIn("snoozed until 2026-10-24", html)
        self.assertIn('data-ov="snooze" data-until="" aria-label="Unsnooze"', html)

    def test_pin_and_snooze_are_labelled_icons_and_there_is_no_tag_picker(self):
        self.in_zone("UTC")
        item = ("jira", "A-1")
        html = overlay.card_controls(
            item, overlay.Overlay(), self.INSTANT, reorder=False
        )
        self.assertIn('data-ov="pin" aria-label="Pin"', html)
        self.assertNotIn(">Pin<", html)
        self.assertNotIn("Snooze 7d<", html)
        self.assertNotIn('data-ov="tag"', html)
        self.assertIn('data-ov="ignore" aria-label="Ignore"', html)
        self.assertIn('<summary class="ov-icon" aria-label="Add note"', html)
        pinned = overlay.Overlay(pinned_at={item: "2026-10-24"})
        html = overlay.card_controls(item, pinned, self.INSTANT, reorder=False)
        self.assertIn('data-ov="unpin" aria-label="Unpin"', html)


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
    fleet = True  # the review-queue gauge reads "—" unless github.scope is "owner"

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
        "tag": [("key", "ABC-1"), ("tag", "side-quest")],
        "visit": [("at", "2026-10-23T11:00:00Z")],
        "ignore": [("key", "ABC-1")],
        "unignore": [("key", "ABC-1")],
        "include": [("view", "needs"), ("include", "prs,sessions")],
        "whynot": [("key", "ABC-1"), ("reason", "other")],
        "mode": [("mode", "little")],
    }

    def test_routes_cover_every_action(self):
        self.assertEqual(set(self.ROUTES), set(overlay.ACTIONS))

    def gauge(self, page, name):
        m = re.search(rf'data-gauge="{name}"[^>]*>(.*?)</span><input', page, re.S)
        return m and re.sub(r"<[^>]+>", "", m.group(1))

    def test_the_header_has_no_side_quest_gauge_even_with_a_tag(self):
        conn = self.conn()
        server_harness.seed(conn)
        conn.close()
        # Tagging still works; it only no longer reaches the header.
        fields = [("key", "ABC-1"), ("tag", "side-quest")]
        self.assertEqual(self.post("tag", fields)[0], 200)
        for path in ("/board", "/needs", "/now", "/stale"):
            page = self.request("GET", path)[1]
            self.assertNotIn("side-quest", page.split("</header>", 1)[0], path)
            self.assertEqual(
                self.gauge(page, "review-queue"), "Review: — ready · — waiting", path
            )
            self.assertIsNone(self.gauge(page, "focus"), path)
            self.assertNotIn('data-ov="focus"', page, path)

    def test_the_removed_focus_actions_are_not_found(self):
        for action in ("focus", "unfocus"):
            self.assertEqual(self.post(action, [("key", "ABC-1")])[0], 404, action)

    def test_review_queue_reads_the_github_rows_once_the_fleet_step_ran(self):
        conn = self.conn()
        server_harness.add_run(conn, "ok", NOW, kind="github_fleet")
        rows = [
            {
                "kind": "pr",
                "key": f"https://github.com/o/r/pull/{n}",
                "url": f"https://github.com/o/r/pull/{n}",
                "title": f"pr {n}",
                "in_queue": n <= 6,
            }
            for n in range(1, 9)
        ] + [{"kind": "issue", "key": "https://github.com/o/r/issues/1", "title": "i"}]
        github_fleet.write(conn, rows, NOW)
        conn.close()
        page = self.request("GET", "/now")[1]
        self.assertEqual(
            self.gauge(page, "review-queue"), "Review: 6 ready · 2 waiting"
        )
        self.assertIn('class="gauge over" data-gauge="review-queue"', page)
        conn = self.conn()
        github_fleet.write(conn, rows[-1:], NOW)  # GitHub synced, no PR waiting
        conn.close()
        page = self.request("GET", "/now")[1]
        self.assertEqual(
            self.gauge(page, "review-queue"), "Review: 0 ready · 0 waiting"
        )

    def test_a_side_quest_post_carrying_text_leaves_the_note_alone(self):
        self.assertEqual(
            self.post("note", [("key", "ABC-1"), ("text", "keep me")])[0], 200
        )
        fields = [("key", "ABC-1"), ("tag", "side-quest"), ("text", "clobber")]
        self.assertEqual(self.post("tag", fields)[0], 200)
        rows = self.overlay_rows()
        self.assertEqual(rows["notes"][0][2], "keep me")
        self.assertEqual(rows["kind_tags"][0][2], "side-quest")

    def test_tagging_substantial_without_a_note_is_400(self):
        status, body = self.post("tag", [("key", "ABC-1"), ("tag", "substantial")])
        self.assertEqual(status, 400)
        self.assertIn("figure", body)
        self.assertEqual(self.overlay_rows(), empty())
        fields = [("key", "ABC-1"), ("tag", "substantial"), ("text", "moves: yes-rate")]
        self.assertEqual(self.post("tag", fields)[0], 200)
        self.assertEqual(self.overlay_rows()["notes"][0][2], "moves: yes-rate")

    def test_every_route_without_the_token_is_403(self):
        for action, fields in self.ROUTES.items():
            for token in ("", "nope"):
                status, _ = self.post(action, fields, token=token)
                self.assertEqual(status, 403, (action, token))
        self.assertEqual(self.overlay_rows(), empty())

    def test_every_route_needs_host_and_origin(self):
        for action, fields in self.ROUTES.items():
            status, _ = self.post(action, fields, origin="http://evil.example")
            self.assertEqual(status, 403, action)
            status, _ = self.post(action, fields, host=f"evil.example:{self.port}")
            self.assertEqual(status, 403, action)
        self.assertEqual(self.overlay_rows(), empty())

    def test_an_include_post_is_remembered_and_validated(self):
        fields = [("view", "now"), ("include", "tasks,prs")]
        self.assertEqual(self.post("include", fields)[0], 200)
        rows = self.overlay_rows()["view_includes"]
        self.assertEqual([r[:2] for r in rows], [("now", "tasks,prs")])
        status, body = self.post("include", [("view", "now"), ("include", "bugs")])
        self.assertEqual(status, 400)
        self.assertIn("valid groups are tasks, prs, sessions, other", body)
        status, body = self.post("include", [("view", "nope"), ("include", "prs")])
        self.assertEqual(status, 400)
        self.assertIn("view must be one of", body)
        # Every switch off is a choice too: the empty set is stored.
        self.assertEqual(
            self.post("include", [("view", "now"), ("include", "")])[0], 200
        )
        self.assertEqual(self.overlay_rows()["view_includes"][0][:2], ("now", ""))

    def test_a_retired_views_include_is_dropped_not_refused(self):
        # Since you were away folded into Needs you: a tab open from before
        # can still post its switches, and that is a no-op.
        status, _ = self.post("include", [("view", "away"), ("include", "prs")])
        self.assertEqual(status, 200)
        self.assertEqual(self.overlay_rows()["view_includes"], [])

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
        page = self.request("GET", "/board")[1]
        pinned = re.search(r'data-section="pinned".*?</section>', page, re.S)
        assert pinned is not None
        self.assertEqual(
            re.findall(r'data-key="([^"]+)"', pinned.group(0)), ["ABC-1", "ABC-4"]
        )
        self.assertIn("&lt;b&gt;me&lt;/b&gt;", page)
        self.assertNotIn("<b>me</b>", page)
        self.assertIn("snoozed until 2999-01-01", page)
        self.assertIn('class="card snoozed" data-source="jira" data-key="ABC-6"', page)

    def test_unsnooze_clears_through_the_route(self):
        # Unsnooze posts an empty `until`, which parse_qs drops from the form.
        self.assertEqual(
            self.post("snooze", [("key", "ABC-6"), ("until", "2999-01-01")])[0], 200
        )
        self.assertEqual(self.post("snooze", [("key", "ABC-6"), ("until", "")])[0], 200)
        page = self.request("GET", "/board")[1]
        self.assertNotIn("snoozed until 2999-01-01", page)
        self.assertEqual(self.overlay_rows()["snoozes"], [])

    def test_bad_input_is_400_and_writes_nothing(self):
        for action, fields in (
            ("snooze", [("key", "ABC-1"), ("until", "tomorrow")]),
            ("order", [("key", "NOT-PINNED")]),
            ("pin", []),
        ):
            self.assertEqual(self.post(action, fields)[0], 400, action)
        self.assertEqual(self.overlay_rows(), empty())

    def test_note_for_an_absent_issue_is_hidden_and_comes_back(self):
        self.assertEqual(
            self.post("note", [("key", "ABC-2"), ("text", "for later")])[0], 200
        )
        self.assertNotIn("for later", self.request("GET", "/board")[1])
        conn = self.conn()
        server_harness.seed(conn)
        conn.close()
        self.assertIn("for later", self.request("GET", "/board")[1])

    def test_a_database_error_is_503_with_a_json_body(self):
        real = overlay.apply

        def locked(conn, action, form, now=None, snapshot=None):
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
        self.assertIn(badge, self.request("GET", "/board")[1])
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        self.assertNotIn(badge, self.request("GET", "/board")[1])

    def test_a_long_note_fits_the_overlay_body_limit(self):
        text = "é" * overlay.MAX_NOTE
        self.assertEqual(self.post("note", [("key", "ABC-1"), ("text", text)])[0], 200)
        self.assertEqual(self.overlay_rows()["notes"][0][2], text)


if __name__ == "__main__":
    unittest.main()
