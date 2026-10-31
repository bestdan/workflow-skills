#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/plans.py and the CLI's
`plan set|show|clear`.

The fixture mirror is the one test_task_dashboard_agent_query seeds, written
through the real writers into a temp-file DB, so no acli, gh, git or network
runs. Every clock is a pinned `now` and, for the 04:00 rule, a pinned zone.
Needs Python 3.11: run via scripts/test-task-dashboard-plans.sh.
"""

import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task-dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_query  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import plans  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_agent_query as aq  # noqa: E402

NOW = aq.NOW
REPO_SCOPE = f"repo:{aq.REPO}"
A = ("github", aq.issue_url(1))
B = ("github", aq.issue_url(2))
C = ("jira", "PAY-7")
MINUS7 = timezone(timedelta(hours=-7))


def entry(item, reason="because", **extra):
    return {"source": item[0], "key": item[1], "reason": reason, **extra}


class Case(unittest.TestCase):
    def setUp(self):
        base = aq.Case("setUp")
        base.setUp()
        self.addCleanup(base._td.cleanup)
        base.conn.close()
        self.base = base
        self.db_path = base.db_path
        self.cfg = base.cfg
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)

    def set(self, entries=None, **kw):
        kw.setdefault("scope", REPO_SCOPE)
        kw.setdefault("kind", None)
        kw.setdefault("author", "claude-test")
        return plans.set_plan(
            self.conn, self.cfg, NOW,
            entries=entries if entries is not None else [entry(A), entry(B)],
            tz=MINUS7, **kw,
        )  # fmt: skip

    def show(self, **kw):
        return plans.show(self.conn, self.cfg, NOW, **kw)["plans"]


class SessionExpiryTests(unittest.TestCase):
    def at(self, h, m=0, day=24):
        return datetime(2026, 10, day, h, m, tzinfo=MINUS7).astimezone(timezone.utc)

    def test_after_four_it_is_the_next_days_four(self):
        got = plans.session_expiry(self.at(15, 30), MINUS7)
        self.assertEqual(got, self.at(4, day=25))
        self.assertEqual(got.tzinfo, timezone.utc)

    def test_between_midnight_and_four_it_is_that_mornings_four(self):
        self.assertEqual(plans.session_expiry(self.at(1, 5), MINUS7), self.at(4))

    def test_exactly_four_rolls_to_the_next_day(self):
        self.assertEqual(plans.session_expiry(self.at(4), MINUS7), self.at(4, day=25))

    def test_dst_change_day_still_lands_on_four_local(self):
        try:
            from zoneinfo import ZoneInfo

            ny = ZoneInfo("America/New_York")
        except Exception:
            self.skipTest("no tz database")
        # 2026-11-01: clocks fall back at 02:00 local, the day is 25 hours.
        now = datetime(2026, 10, 31, 22, 0, tzinfo=ny)
        got = plans.session_expiry(now, ny).astimezone(ny)
        self.assertEqual((got.day, got.hour, got.minute), (1, 4, 0))
        self.assertEqual(got.utcoffset(), timedelta(hours=-5))

    def test_the_host_zone_default_is_four_local(self):
        got = plans.session_expiry(NOW).astimezone()
        self.assertEqual((got.hour, got.minute), (4, 0))
        self.assertGreater(got, NOW)
        self.assertLessEqual(got - NOW, timedelta(hours=25))


class SetShowClearTests(Case):
    def test_set_then_show_round_trips_in_order(self):
        entries = [
            entry(B, "unblocks the rest", action="finish", est_min=30),
            entry(A, "small", est_min=15),
            entry(C),
        ]
        self.set(entries)
        [p] = self.show()
        self.assertEqual(p["entries"], entries)
        self.assertEqual(
            (p["scope"], p["kind"], p["author"], p["mode"], p["minutes"]),
            (REPO_SCOPE, "standing", "claude-test", None, None),
        )
        self.assertEqual(
            (p["created_at"], p["expires_at"], p["expired"], p["est_min_sum"]),
            ("2026-10-24T12:00:00Z", None, False, 45),
        )

    def test_est_min_sum_is_null_when_no_entry_has_one(self):
        self.set()
        self.assertIsNone(self.show()[0]["est_min_sum"])

    def test_a_second_set_replaces_the_plan(self):
        self.set([entry(A)])
        self.set([entry(B)], author="someone else")
        [p] = self.show()
        self.assertEqual([e["key"] for e in p["entries"]], [B[1]])
        self.assertEqual(p["author"], "someone else")

    def test_minutes_makes_a_session_plan_expiring_at_the_next_four(self):
        p = self.set(minutes=90, mode="little")
        self.assertEqual(p["kind"], "session")
        self.assertEqual((p["minutes"], p["mode"]), (90, "little"))
        # 12:00 UTC is 05:00 at -07:00, so the next 04:00 local is the 25th.
        self.assertEqual(p["expires_at"], "2026-10-25T11:00:00Z")
        self.assertFalse(p["expired"])

    def test_set_before_four_local_expires_that_morning(self):
        early = datetime(2026, 10, 24, 1, 0, tzinfo=MINUS7)
        p = plans.set_plan(
            self.conn, self.cfg, early.astimezone(timezone.utc), scope=None,
            kind="session", entries=[entry(A)], author="x", tz=MINUS7,
        )  # fmt: skip
        self.assertEqual(p["expires_at"], "2026-10-24T11:00:00Z")

    def test_a_session_plan_past_its_expiry_is_listed_as_expired(self):
        self.set(kind="session")
        later = NOW + timedelta(days=2)
        [p] = plans.show(self.conn, self.cfg, later)["plans"]
        self.assertTrue(p["expired"])

    def test_standing_and_session_plans_coexist_for_one_scope(self):
        self.set([entry(A)])
        self.set([entry(B)], kind="session")
        self.assertEqual([p["kind"] for p in self.show()], ["session", "standing"])

    def test_all_is_stored_as_all(self):
        self.set(scope="all")
        self.set(scope=None, kind="session")
        self.assertEqual({p["scope"] for p in self.show(scope="all")}, {"all"})
        self.assertEqual(len(self.show(scope="all")), 2)

    def test_a_word_resolves_to_one_scope_id(self):
        p = self.set(scope="repo:bestdan/finplan")
        self.assertEqual(p["scope"], REPO_SCOPE)
        p = self.set(scope="FinPlan Tax Engine")
        self.assertEqual(p["scope"], f"ws:{aq.WS}")

    def test_a_word_matching_two_scopes_is_refused_naming_both(self):
        with self.assertRaises(agent_query.QueryError) as cm:
            self.set(scope="finplan")
        msg = str(cm.exception)
        self.assertIn(REPO_SCOPE, msg)
        self.assertIn(f"ws:{aq.WS}", msg)
        self.assertEqual(self.show(), [])

    def test_an_unknown_scope_is_refused(self):
        with self.assertRaises(agent_query.QueryError):
            self.set(scope="zzz-nothing")

    def test_an_entry_not_in_the_mirror_is_refused_with_its_key(self):
        ghost = ("github", "https://github.com/bestdan/finplan/issues/999")
        with self.assertRaises(overlay.OverlayError) as cm:
            self.set([entry(A), entry(ghost), entry(("jira", "NOPE-1"))])
        self.assertIn(f"{ghost[0]}:{ghost[1]}", str(cm.exception))
        self.assertIn("jira:NOPE-1", str(cm.exception))
        self.assertEqual(self.show(), [])

    def test_a_done_item_is_in_the_mirror(self):
        self.set([entry(("jira", "PAY-9"), "shipped, drop it", action="drop")])
        self.assertEqual(len(self.show()), 1)

    def test_show_filters_by_scope_and_kind(self):
        self.set(scope="all")
        self.set(kind="session")
        self.assertEqual(len(self.show()), 2)
        self.assertEqual([p["scope"] for p in self.show(scope="all")], ["all"])
        self.assertEqual([p["kind"] for p in self.show(kind="session")], ["session"])
        self.assertEqual(self.show(scope="repo:bestdan/finplan", kind="standing"), [])

    def test_clear_removes_both_kinds_or_only_the_named_one(self):
        self.set()
        self.set(kind="session")
        self.assertEqual(
            plans.clear(self.conn, self.cfg, NOW, REPO_SCOPE, "session"), 1
        )
        self.assertEqual([p["kind"] for p in self.show()], ["standing"])
        self.set(kind="session")
        self.assertEqual(plans.clear(self.conn, self.cfg, NOW, REPO_SCOPE), 2)
        self.assertEqual(self.show(), [])
        self.assertEqual(plans.clear(self.conn, self.cfg, NOW, REPO_SCOPE), 0)

    def test_clear_all_removes_only_the_all_plan(self):
        self.set(scope="all")
        self.set()
        self.assertEqual(plans.clear(self.conn, self.cfg, NOW, "all"), 1)
        self.assertEqual([p["scope"] for p in self.show()], [REPO_SCOPE])

    def test_a_plan_whose_scope_left_the_mirror_can_still_be_shown_and_cleared(self):
        self.set(scope=f"ws:{aq.WS}")
        self.conn.execute("DELETE FROM workstreams WHERE id = ?", (aq.WS,))
        self.conn.execute("DELETE FROM workstream_members WHERE ws_id = ?", (aq.WS,))
        self.conn.commit()
        self.assertEqual(len(self.show(scope=f"ws:{aq.WS}")), 1)
        self.assertEqual(plans.clear(self.conn, self.cfg, NOW, f"ws:{aq.WS}"), 1)

    def test_minutes_with_a_standing_kind_is_refused(self):
        with self.assertRaises(overlay.OverlayError):
            self.set(kind="standing", minutes=30)

    def test_bad_values_are_refused_and_nothing_is_stored(self):
        bad: list[dict[str, Any]] = [
            dict(minutes=0),
            dict(minutes=True),
            dict(minutes=-5),
            dict(mode="nap"),
            dict(kind="forever"),
            dict(author=""),
            dict(author="  "),
            dict(author="a\nb"),
            dict(author="x" * 201),
        ]
        for kw in bad:
            with self.subTest(kw), self.assertRaises(overlay.OverlayError):
                self.set(**kw)
        self.assertEqual(self.show(), [])

    def test_bad_entry_shapes_are_refused_naming_the_entry(self):
        ok = entry(A)
        cases = {
            "not a list": {"a": 1},
            "empty": [],
            "not an object": ["x"],
            "no reason": [{"source": "github", "key": A[1]}],
            "empty reason": [entry(A, "  ")],
            "multi-line reason": [entry(A, "a\nb")],
            "long reason": [entry(A, "x" * 501)],
            "non-string reason": [entry(A, 3)],
            "unknown field": [{**ok, "why": "x"}],
            "bad action": [entry(A, action="launch")],
            "negative est": [entry(A, est_min=-1)],
            "bool est": [entry(A, est_min=True)],
            "float est": [entry(A, est_min=1.5)],
            "no source": [{"key": A[1], "reason": "r"}],
            "empty key": [entry(("github", ""))],
            "duplicate": [ok, entry(B), ok],
            "too many": [entry(("github", f"k{i}")) for i in range(201)],
        }
        for name, entries in cases.items():
            with self.subTest(name), self.assertRaises(overlay.OverlayError):
                self.set(entries)
        with self.assertRaises(overlay.OverlayError) as cm:
            self.set([ok, entry(B, "")])
        self.assertIn("entries[1]", str(cm.exception))
        self.assertEqual(self.show(), [])


class ExportImportTests(Case):
    def test_export_then_import_keeps_plans(self):
        self.set([entry(A, action="do", est_min=10), entry(B)], minutes=45)
        self.set(scope="all", mode="deep")
        before = self.show()
        doc = json.loads(overlay.dumps(overlay.export(self.conn, NOW)))
        self.assertEqual(doc["version"], overlay.EXPORT_VERSION)
        self.assertEqual([p["scope"] for p in doc["plans"]], ["all", REPO_SCOPE])
        self.conn.execute("DELETE FROM plans")
        self.conn.commit()
        counts = overlay.import_(self.conn, doc)
        self.assertEqual(counts["plans"], 2)
        self.assertEqual(self.show(), before)

    def test_import_replaces_plans_and_does_not_check_the_mirror(self):
        self.set([entry(A)])
        doc = overlay.export(self.conn, NOW)
        doc["plans"][0]["entries"] = [entry(("github", "left/the/mirror"))]
        overlay.import_(self.conn, doc)
        self.assertEqual(self.show()[0]["entries"][0]["key"], "left/the/mirror")

    def test_a_malformed_plan_in_a_file_is_refused(self):
        self.set([entry(A)])
        good = overlay.export(self.conn, NOW)
        for name, mutate in {
            "bad kind": lambda p: p.update(kind="weekly"),
            "extra field": lambda p: p.update(x=1),
            "empty entries": lambda p: p.update(entries=[]),
            "bad stamp": lambda p: p.update(created_at="today"),
            "bad mode": lambda p: p.update(mode="nap"),
            "no author": lambda p: p.update(author=""),
        }.items():
            doc = json.loads(json.dumps(good))
            mutate(doc["plans"][0])
            with self.subTest(name), self.assertRaises(overlay.OverlayError):
                overlay.import_(self.conn, doc)
        dup = json.loads(json.dumps(good))
        dup["plans"].append(dup["plans"][0])
        with self.assertRaises(overlay.OverlayError):
            overlay.import_(self.conn, dup)
        self.assertEqual(len(self.show()), 1)


class CliTests(Case):
    def run_cli(self, *argv, stdin=None):
        cfg_path = Path(self.db_path.parent) / "config.toml"
        cfg_path.write_text(aq.CONFIG_TOML)
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = dashboard.main(
                    [*argv, "--db", str(self.db_path), "--config", str(cfg_path)]
                )
        finally:
            sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def file(self, entries):
        path = Path(self.db_path.parent) / "plan.json"
        path.write_text(json.dumps(entries))
        return str(path)

    def test_set_show_clear(self):
        f = self.file([entry(A, "first"), entry(B, "second", est_min=20)])
        code, out, _ = self.run_cli(
            "plan", "set", "--scope", REPO_SCOPE, "--by", "me", "--file", f
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["plans"][0]["kind"], "standing")
        code, out, _ = self.run_cli("plan", "show", "--scope", "repo:bestdan/finplan")
        shown = json.loads(out)["plans"]
        self.assertEqual(
            [e["reason"] for e in shown[0]["entries"]], ["first", "second"]
        )
        self.assertEqual(len(out.strip().splitlines()), 1)  # compact
        code, out, _ = self.run_cli("plan", "clear", "--scope", REPO_SCOPE)
        self.assertEqual((code, json.loads(out)["cleared"]), (0, 1))
        code, out, _ = self.run_cli("plan", "show")
        self.assertEqual(json.loads(out)["plans"], [])

    def test_minutes_sets_a_session_plan_and_stdin_is_read(self):
        code, out, _ = self.run_cli(
            "plan", "set", "--scope", "all", "--minutes", "60", "--by", "me",
            "--file", "-",
            stdin=json.dumps([entry(A)]),
        )  # fmt: skip
        self.assertEqual(code, 0)
        plan = json.loads(out)["plans"][0]
        self.assertEqual((plan["scope"], plan["kind"]), ("all", "session"))
        self.assertIsNotNone(plan["expires_at"])

    def test_a_refused_plan_exits_2_and_names_the_key(self):
        f = self.file([entry(("github", "nope/1"))])
        code, out, err = self.run_cli(
            "plan", "set", "--scope", "all", "--by", "me", "--file", f
        )
        self.assertEqual((code, out), (2, ""))
        self.assertIn("github:nope/1", err)
        code, _, err = self.run_cli(
            "plan", "set", "--scope", "finplan", "--by", "me",
            "--file", self.file([entry(A)]),
        )  # fmt: skip
        self.assertEqual(code, 2)
        self.assertIn("pass one scope id", err)

    def test_an_unreadable_or_invalid_file_exits_1(self):
        code, _, err = self.run_cli(
            "plan", "set", "--scope", "all", "--by", "me", "--file", "/no/such"
        )
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)
        code, _, _ = self.run_cli(
            "plan", "set", "--scope", "all", "--by", "me", "--file", "-",
            stdin="{",
        )  # fmt: skip
        self.assertEqual(code, 1)

    def test_by_file_and_scope_are_required(self):
        for argv in (
            ["plan", "set", "--scope", "all", "--file", "x"],
            ["plan", "set", "--by", "me", "--file", "x"],
        ):
            with self.subTest(argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    dashboard.main(argv)


if __name__ == "__main__":
    unittest.main()
