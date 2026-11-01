#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/brief.py and the CLI's `brief`.

The fixture mirror is the one test_task_dashboard_agent_query seeds, written
through the real writers into a temp-file DB, so no acli, gh, git or network
runs and the live DB is never opened. Needs Python 3.11: run via
scripts/test-task-dashboard-brief.sh.
"""

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "task-dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_query  # type: ignore[import-not-found]  # noqa: E402
import brief  # type: ignore[import-not-found]  # noqa: E402
import dashboard  # type: ignore[import-not-found]  # noqa: E402
import db  # type: ignore[import-not-found]  # noqa: E402
import overlay  # type: ignore[import-not-found]  # noqa: E402
import test_task_dashboard_agent_query as aq  # noqa: E402

NOW = aq.NOW
ISSUE2 = ("github", aq.issue_url(2))
ISSUE3 = ("github", aq.issue_url(3))


def keys(lst):
    return [i["key"] for i in lst["items"]]


class Case(unittest.TestCase):
    def setUp(self):
        base = aq.Case("setUp")
        base.setUp()
        self.addCleanup(base._td.cleanup)
        self.base = base
        self.db_path = base.db_path
        self.cfg = base.cfg
        self.conn = base.conn

    def write(self, fn):
        """Run fn(conn) against a writable connection to the fixture DB."""
        conn = db.connect(self.db_path)
        try:
            fn(conn)
            conn.commit()
        finally:
            conn.close()

    def brief(self, **kw):
        return brief.brief_doc(self.conn, self.cfg, NOW, **kw)


class ShapeTests(Case):
    def test_scope_finplan_prints_every_section_within_the_budget(self):
        doc = self.brief(scope=["finplan"])
        self.assertEqual(doc["schema"], brief.SCHEMA_VERSION)
        self.assertEqual(doc["sections"], list(brief.SECTIONS))
        for name in brief.SECTIONS:
            self.assertIn(name, doc)
        self.assertEqual(
            [s["id"] for s in doc["scope"]], ["repo:bestdan/finplan", "ws:linear:p-1"]
        )
        printed = len(agent_query.dumps(doc).encode())
        self.assertLessEqual(printed, brief.BUDGET_TOKENS * brief.BYTES_PER_TOKEN)
        self.assertEqual(doc["budget"]["used"], -(-(printed - 1) // 4))
        self.assertFalse(doc["budget"]["truncated"])

    def test_sections_narrows_the_output(self):
        doc = self.brief(sections="gaps,freshness")
        self.assertEqual(doc["sections"], ["freshness", "gaps"])
        self.assertNotIn("in_flight", doc)
        self.assertNotIn("candidates", doc)

    def test_refuses_a_bad_mode_section_or_time_box(self):
        for kw in ({"mode": "fast"}, {"sections": "everything"}, {"minutes": 0},
                   {"scope": ["zzz"]}):  # fmt: skip
            with self.subTest(kw=kw), self.assertRaises(agent_query.QueryError):
                self.brief(**kw)

    def test_freshness_names_each_step_and_host(self):
        ids = [s["id"] for s in self.brief()["freshness"]["sources"]]
        self.assertIn("github_fleet", ids)
        self.assertIn("machine:mini", ids)


class InFlightTests(Case):
    def test_prs_go_by_readiness(self):
        flight = self.brief(scope=["repo:bestdan/finplan"])["in_flight"]
        self.assertEqual(keys(flight["ready_to_merge"]), [aq.pr_url(13)])  # bot, green
        self.assertIn(aq.pr_url(10), keys(flight["other"]))  # no co-review
        self.assertEqual(flight["ci_failing"]["total"], 0)

    def test_the_readiness_rule(self):
        cases = [
            ({"is_draft": True, "ci_state": "failure"}, "draft"),
            ({"ci_state": "failure", "review_decision": "APPROVED"}, "ci_failing"),
            ({"ci_state": "success", "review_decision": "APPROVED"}, "ready_to_merge"),
            ({"ci_state": "success", "awaiting": {"author_is_bot": True}},
             "ready_to_merge"),
            ({"ci_state": "pending", "review_decision": "APPROVED"}, "other"),
            ({"ci_state": "success", "awaiting": {"in_queue": True}}, "review_queue"),
            ({"ci_state": "success", "awaiting": {"queue_misses": ["co_review"]}},
             "other"),
        ]  # fmt: skip
        for row, want in cases:
            with self.subTest(row=row):
                self.assertEqual(brief.pr_readiness(row), want)

    def test_waiting_sessions_and_worktrees_with_no_pr(self):
        flight = self.brief(scope=["repo:finplan"])["in_flight"]
        self.assertIn("s-10", keys(flight["waiting"]))
        # The fix-10 worktree has PR 10; the clean `main` checkouts are no work.
        self.assertEqual(flight["worktrees"]["total"], 0)


class BlockedTests(Case):
    def test_blocked_and_blocking(self):
        blocked = self.brief()["blocked"]
        got = keys(blocked["blocked"])
        self.assertIn(aq.issue_url(4), got)  # natively blocked
        self.assertIn(aq.issue_url(5), got)  # the `blocked` label
        self.assertIn("PRE-1", got)
        self.assertIn("PRE-1", keys(blocked["blocking"]))


class CandidateTests(Case):
    def test_ship_order_with_in_flight_items_as_stubs(self):
        cands = self.brief(scope=["repo:bestdan/finplan"])["candidates"]
        items = cands["items"]["items"]
        self.assertTrue(items)
        self.assertTrue(any(r.get("in_flight") for r in items))
        for rec in items:
            self.assertIn("tier", rec["now"])
            if rec.get("in_flight"):
                self.assertNotIn("labels", rec)
        self.assertIn("argue", cands["order"])

    def test_minutes_keeps_the_sum_within_twice_the_box(self):
        cands = self.brief(mode="little", minutes=30)["candidates"]
        sized = cands["items"]["items"]
        total = sum(r["size"]["est_min"] for r in sized)
        self.assertLessEqual(total, 60)
        self.assertEqual(cands["minutes"]["sum"], total)
        self.assertEqual(cands["minutes"]["cap"], 60)
        self.assertIn("unsized", cands)
        self.assertEqual(
            [r["now"]["tier"] for r in sized][:2], ["bot PR", "docs-only PR"]
        )

    def test_unsized_candidates_are_listed_apart(self):
        cands = self.brief(minutes=30)["candidates"]
        unsized = cands["unsized"]["items"]
        self.assertTrue(unsized)
        for r in unsized:
            self.assertIsNone((r.get("size") or {}).get("est_min"))
        for r in cands["items"]["items"]:
            self.assertIsNotNone(r["size"]["est_min"])

    def test_deep_inlines_the_top_dossier_and_ignores_the_sum(self):
        cands = self.brief(mode="deep", minutes=60)["candidates"]
        first = cands["items"]["items"][0]
        self.assertEqual(cands["top"]["key"], first["key"])
        self.assertEqual(cands["top"]["sections"], list(agent_query.SECTIONS))
        self.assertIn("ignored", cands["minutes"])
        self.assertNotIn("unsized", cands)

    def test_a_pin_leads_and_a_snooze_leaves_the_list(self):
        def mark(conn):
            overlay.pin(conn, ISSUE3, NOW)
            overlay.snooze(conn, ISSUE2, "2026-11-30")

        self.write(mark)
        doc = self.brief()
        got = keys(doc["candidates"]["items"])
        self.assertEqual(got[0], ISSUE3[1])
        self.assertNotIn(ISSUE2[1], got)
        signals = {r["key"]: r for r in doc["signals"]["items"]["items"]}
        self.assertEqual(next(iter(signals)), ISSUE3[1])  # pins first
        self.assertTrue(signals[ISSUE3[1]]["pinned"])
        self.assertEqual(signals[ISSUE2[1]]["snoozed_until"], "2026-11-30")


class GapTests(unittest.TestCase):
    def test_names_each_source_not_mirrored(self):
        cfg = aq.cfg()
        cfg["jira"]["enabled"] = False
        cfg["linear"]["enabled"] = False
        got = {g["source"] for g in brief.gaps(cfg)}
        self.assertEqual(got, {"jira", "linear", "repo-pr"})

    def test_repo_pr_is_always_a_gap(self):
        self.assertEqual([g["source"] for g in brief.gaps(aq.cfg())], ["repo-pr"])


class BudgetTests(Case):
    def test_past_the_budget_lists_say_what_they_left_out(self):
        saved = brief.BUDGET_TOKENS
        brief.BUDGET_TOKENS = 1200
        self.addCleanup(setattr, brief, "BUDGET_TOKENS", saved)
        doc = self.brief(minutes=30)
        self.assertTrue(doc["budget"]["truncated"])
        cut = [
            lst for s, k in brief.FILL
            if (lst := doc[s][k]).get("omitted")
        ]  # fmt: skip
        self.assertTrue(cut)
        for lst in cut:
            self.assertEqual(lst["omitted"] + len(lst["items"]), lst["total"])
            self.assertTrue(lst["more"].startswith("dashboard.py "))
        # The first list in FILL keeps its items; the last gives way first.
        self.assertFalse(doc["in_flight"]["ready_to_merge"].get("omitted"))
        self.assertTrue(doc["candidates"]["unsized"].get("omitted"))
        # The cut lists' `omitted` and `more` and the budget block fit too,
        # whatever the budget.
        for tokens in range(900, 3001, 25):
            brief.BUDGET_TOKENS = tokens
            b = self.brief(minutes=30)["budget"]
            self.assertLessEqual(b["used"], tokens, tokens)
            self.assertFalse(b["over"])

    def test_candidates_over_the_box_say_how_to_list_them(self):
        cands = self.brief(mode="little", minutes=5)["candidates"]
        over = cands["minutes"]["over_cap"]
        self.assertGreater(over, 0)
        more = cands["minutes"]["over_cap_more"]
        self.assertNotIn("--minutes", more)
        self.assertIn("--mode little", more)


class CliTests(Case):
    def run_cli(self, *argv):
        cfg_path = Path(self.base._td.name) / "config.toml"
        cfg_path.write_text(aq.CONFIG_TOML)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dashboard.main(
                [*argv, "--db", str(self.db_path), "--config", str(cfg_path)]
            )
        return code, out.getvalue(), err.getvalue()

    def test_prints_json_and_never_writes_or_prints_the_token(self):
        before = self.db_path.read_bytes()
        for argv in (("brief",), ("brief", "--scope", "finplan"),
                     ("brief", "--mode", "deep", "--minutes", "60"),
                     ("brief", "--mode", "little", "--minutes", "30")):  # fmt: skip
            with self.subTest(argv=argv):
                code, out, _ = self.run_cli(*argv)
                self.assertEqual(code, 0)
                self.assertNotIn(aq.TOKEN, out)
                self.assertEqual(json.loads(out)["schema"], brief.SCHEMA_VERSION)
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_a_bad_value_exits_2(self):
        for argv in (("brief", "--mode", "x"), ("brief", "--sections", "x"),
                     ("brief", "--minutes", "-5")):  # fmt: skip
            with self.subTest(argv=argv):
                code, out, _ = self.run_cli(*argv)
                self.assertEqual((code, out), (2, ""))


if __name__ == "__main__":
    unittest.main()
