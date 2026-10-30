#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/github_gh.py.

Every gh call goes through a fake runner fed from fixtures/gh/; nothing reaches
the network. Needs Python 3.11: run via scripts/test-task-dashboard-gh.sh.
"""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
FIXTURES = PKG / "fixtures" / "gh"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"td_{name}", PKG / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gh = _load("github_gh")


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeRunner:
    """Returns queued (rc, stdout, stderr) responses and records each argv."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if not self.responses:
            raise AssertionError(f"unexpected gh call: {argv}")
        return self.responses.pop(0)


def ok(obj):
    return (0, json.dumps(obj), "")


def arg(argv, name):
    """Value of a `-f name=value` field in a gh api argv, or None."""
    for a in argv:
        if a.startswith(f"{name}="):
            return a.split("=", 1)[1]
    return None


def two_pages():
    first = fixture("search_page.json")
    second = copy.deepcopy(first)
    nodes = first["data"]["search"]["nodes"]
    first["data"]["search"]["nodes"] = nodes[:2]
    second["data"]["search"]["nodes"] = nodes[2:]
    second["data"]["search"]["pageInfo"] = {
        "hasNextPage": False,
        "endCursor": "Y3Vyc29yOjk5",
    }
    return first, second


class JiraKeysTests(unittest.TestCase):
    def test_title_key(self):
        self.assertEqual(gh.jira_keys("feat(x): thing [AB-12]", ["AB"]), ["AB-12"])

    def test_branch_key_is_upper_cased(self):
        self.assertEqual(gh.jira_keys("bestdan/ab-12-thing", ["AB"]), ["AB-12"])

    def test_version_string_needs_allowlist(self):
        self.assertEqual(gh.jira_keys("bump to v2-1", ["AB"]), [])
        self.assertEqual(gh.jira_keys("bump to v2-1", ["V2"]), ["V2-1"])

    def test_unlisted_project_dropped(self):
        self.assertEqual(gh.jira_keys("CD-7 and AB-3", ["AB"]), ["AB-3"])

    def test_title_and_branch_deduplicate(self):
        text = "feat: thing [AB-12] bestdan/ab-12-thing"
        self.assertEqual(gh.jira_keys(text, ["ab"]), ["AB-12"])

    def test_no_mid_token_match(self):
        self.assertEqual(gh.jira_keys("xab-12", ["AB"]), [])


class CiStateTests(unittest.TestCase):
    def run_check(self, **kw):
        return dict(
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
            **kw,
        )

    def status(self, state):
        return {"__typename": "StatusContext", "context": "ci/x", "state": state}

    def test_fixture_checkrun_only_with_skipped_is_success(self):
        runner = FakeRunner([ok(fixture("rollup_checkrun_only.json"))])
        self.assertEqual(
            gh.ci_state("https://github.com/o/r/pull/1", runner), "success"
        )
        self.assertEqual(runner.calls[0][:3], ["gh", "pr", "view"])

    def test_fixture_mixed_is_success(self):
        runner = FakeRunner([ok(fixture("rollup_mixed.json"))])
        self.assertEqual(
            gh.ci_state("https://github.com/o/r/pull/1", runner), "success"
        )

    def test_empty_is_none(self):
        self.assertEqual(gh.reduce_rollup([]), "none")
        runner = FakeRunner([ok({"statusCheckRollup": []})])
        self.assertEqual(gh.ci_state("u", runner), "none")

    def test_any_failure_wins(self):
        entries = [
            self.run_check(),
            self.run_check(status="IN_PROGRESS", conclusion=""),
            self.run_check(conclusion="FAILURE"),
        ]
        self.assertEqual(gh.reduce_rollup(entries), "failure")

    def test_status_context_failure_and_error(self):
        self.assertEqual(
            gh.reduce_rollup([self.run_check(), self.status("FAILURE")]), "failure"
        )
        self.assertEqual(
            gh.reduce_rollup([self.run_check(), self.status("ERROR")]), "failure"
        )

    def test_pending_when_running_and_none_failed(self):
        running = self.run_check(status="IN_PROGRESS", conclusion=None)
        self.assertEqual(gh.reduce_rollup([self.run_check(), running]), "pending")
        self.assertEqual(
            gh.reduce_rollup([self.run_check(), self.status("PENDING")]), "pending"
        )

    def test_success_only_when_every_check_passed(self):
        entries = [
            self.run_check(),
            self.run_check(conclusion="NEUTRAL"),
            self.status("SUCCESS"),
        ]
        self.assertEqual(gh.reduce_rollup(entries), "success")
        # An unrecognised conclusion is not reported green.
        self.assertEqual(
            gh.reduce_rollup([self.run_check(conclusion="STALE")]), "pending"
        )

    def test_cancelled_is_failure(self):
        self.assertEqual(
            gh.reduce_rollup([self.run_check(conclusion="CANCELLED")]), "failure"
        )


class SearchTests(unittest.TestCase):
    def test_pagination_follows_end_cursor(self):
        first, second = two_pages()
        runner = FakeRunner([ok(first), ok(second)])
        nodes = gh.search_prs("is:pr is:open author:@me org:bestdan", runner)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(len(runner.calls), 2)
        self.assertIsNone(arg(runner.calls[0], "after"))
        self.assertEqual(
            arg(runner.calls[1], "after"),
            first["data"]["search"]["pageInfo"]["endCursor"],
        )

    def test_my_prs_one_search_per_owner(self):
        first, second = two_pages()
        runner = FakeRunner([ok(second), ok(first), ok(second)])
        rows = gh.my_prs(["bestdan", "cli"], runner)
        queries = [arg(c, "q") for c in runner.calls]
        self.assertEqual(
            queries[0], "is:pr is:open archived:false author:@me org:bestdan"
        )
        self.assertEqual(queries[1], "is:pr is:open archived:false author:@me org:cli")
        # second page's nodes appear under both owners; rows dedupe by url.
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["head_ref"] for r in rows))
        self.assertTrue(all(r["role"] == "author" for r in rows))

    def test_row_shape(self):
        page = fixture("search_page.json")
        page["data"]["search"]["pageInfo"]["hasNextPage"] = False
        rows = gh.my_prs(["bestdan"], FakeRunner([ok(page)]))
        row = next(r for r in rows if r["url"].endswith("workflow-skills/pull/1077"))
        self.assertEqual(row["repo"], "bestdan/workflow-skills")
        self.assertEqual(row["number"], 1077)
        self.assertEqual(row["head_ref"], "bestdan/fast-mode-advisor")
        self.assertTrue(row["is_draft"])
        self.assertEqual(json.loads(row["raw_json"])["url"], row["url"])

    def test_review_requested_query_and_role(self):
        _, second = two_pages()
        runner = FakeRunner([ok(second)])
        rows = gh.review_requested(["bestdan"], runner)
        self.assertEqual(
            arg(runner.calls[0], "q"),
            "is:pr is:open archived:false review-requested:@me org:bestdan",
        )
        self.assertTrue(all(r["role"] == "reviewer" for r in rows))

    def test_owner_name_is_validated(self):
        with self.assertRaises(ValueError):
            gh.my_prs(["bestdan is:closed"], FakeRunner([]))

    def test_search_asks_for_ci_state_and_the_budget(self):
        _, second = two_pages()
        runner = FakeRunner([ok(second)])
        gh.my_prs(["bestdan"], runner)
        query = arg(runner.calls[0], "query")
        self.assertIn(gh.ROLLUP_STATE, query)
        self.assertIn("rateLimit { remaining resetAt }", query)
        # The state alone: the full rollup's contexts timed the search out.
        self.assertNotIn("contexts", query)

    def test_row_ci_state_comes_from_the_node(self):
        _, second = two_pages()
        second["data"]["search"]["nodes"][0]["commits"] = rollup("FAILURE")
        rows = gh.my_prs(["bestdan"], FakeRunner([ok(second)]))
        self.assertEqual(rows[0]["ci_state"], "failure")
        # A node captured before the field was asked for has no state.
        self.assertIsNone(rows[1]["ci_state"])

    def test_budget_keeps_the_lowest_remaining(self):
        first, second = two_pages()
        first["data"]["rateLimit"] = {"remaining": 900, "resetAt": "T1"}
        second["data"]["rateLimit"] = {"remaining": 4000, "resetAt": "T2"}
        budget = gh.Budget()
        gh.search_prs("q", FakeRunner([ok(first), ok(second)]), budget=budget)
        self.assertEqual((budget.remaining, budget.reset_at), (900, "T1"))
        self.assertTrue(budget.below(1000))
        self.assertFalse(budget.below(900))

    def test_budget_unknown_never_reads_as_low(self):
        budget = gh.Budget()
        budget.note({"search": {}})
        budget.note(None)
        self.assertIsNone(budget.remaining)
        self.assertFalse(budget.below(10**9))


def rollup(state):
    """A node's `commits` as ROLLUP_STATE selects it; None: no checks."""
    found = {"state": state} if state else None
    return {"nodes": [{"commit": {"statusCheckRollup": found}}]}


class RollupStateTests(unittest.TestCase):
    def test_states(self):
        for state, want in (
            ("SUCCESS", "success"),
            ("FAILURE", "failure"),
            ("ERROR", "failure"),
            ("PENDING", "pending"),
            ("EXPECTED", "pending"),
            ("SOMETHING_NEW", "pending"),
            (None, "none"),
        ):
            with self.subTest(state=state):
                self.assertEqual(gh.rollup_state({"commits": rollup(state)}), want)

    def test_no_commit_is_none_and_no_field_is_unknown(self):
        self.assertEqual(gh.rollup_state({"commits": {"nodes": []}}), "none")
        self.assertIsNone(gh.rollup_state({}))


class ErrorTests(unittest.TestCase):
    def test_auth_failure(self):
        runner = FakeRunner(
            [(1, "", "To get started with GitHub CLI, please run:  gh auth login")]
        )
        with self.assertRaises(gh.AuthError):
            gh.ci_state("u", runner)

    def test_other_failure_is_not_auth(self):
        runner = FakeRunner([(1, "", "HTTP 502: Bad Gateway")])
        with self.assertRaises(gh.GhError) as cm:
            gh.search_prs("q", runner)
        self.assertNotIsInstance(cm.exception, gh.AuthError)

    def test_non_json(self):
        with self.assertRaises(gh.GhError):
            gh.ci_state("u", FakeRunner([(0, "<html>oops</html>", "")]))

    def test_runner_timeout_is_rc_124(self):
        rc, _, err = gh.run(["sleep", "5"], timeout=0.1)
        self.assertEqual(rc, 124)
        self.assertIn("timed out", err)


if __name__ == "__main__":
    unittest.main()
