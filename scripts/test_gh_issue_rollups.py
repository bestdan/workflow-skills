#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-rollups.py.

Stubs the module's run_gh() seam, so nothing shells out to `gh` and nothing
touches the network. The stub is installed on the module object itself rather
than on `PATH`, which sidesteps the two false passes that bit the shell harness
this file replaces: a stub that never landed on `PATH` made `gh` resolve to
nothing, and `gh: not found` exits 127 — indistinguishable from the permission
denial under test. Every case here asserts on the calls the stub actually
received, so a stub that is never reached fails rather than passes.

Each case names the defect it pins. All seven come from three review rounds on
PR #432; every one produced a wrong or empty result that read as a clean run.
"""

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-rollups.py"

_spec = importlib.util.spec_from_file_location("gh_issue_rollups", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
rollups = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rollups)


def page(nodes, has_next=False, end_cursor=None):
    """A well-formed GraphQL page body, as `gh api graphql` prints it."""
    return json.dumps(
        {
            "data": {
                "repository": {
                    "issues": {
                        "nodes": nodes,
                        "pageInfo": {
                            "hasNextPage": has_next,
                            "endCursor": end_cursor,
                        },
                    }
                }
            }
        }
    )


def node(number, total):
    return {"number": number, "subIssues": {"totalCount": total}}


class FakeGh:
    """Serves a canned list of (returncode, stdout, stderr) responses in order.

    Records every argv it was handed, so a test can assert what the second
    request actually carried — the property the shell harness could not check.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if not self.responses:
            raise AssertionError(f"unexpected extra gh call: {args}")
        return self.responses.pop(0)

    def cursor_of(self, call_index):
        """The `cursor=` value the given call carried, or None if it carried none."""
        for arg in self.calls[call_index]:
            if arg.startswith("cursor="):
                return arg[len("cursor=") :]
        return None


class RollupTestCase(unittest.TestCase):
    def install(self, *responses):
        fake = FakeGh(responses)
        self.addCleanup(setattr, rollups, "run_gh", rollups.run_gh)
        rollups.run_gh = fake
        return fake

    def run_main(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rollups.main(list(argv))
        return code, buf.getvalue()


class TestSuccess(RollupTestCase):
    def test_parents_are_the_issues_with_sub_issues(self):
        self.install((0, page([node(1, 0), node(2, 3), node(3, 1)]), ""))
        self.assertEqual(rollups.collect_parents("o/r"), [2, 3])

    def test_no_rollups_is_a_successful_empty_result(self):
        """The single most important property: a repo with no rollups must stay
        distinguishable from a lookup that failed."""
        fake = self.install((0, page([node(1, 0), node(2, 0)]), ""))
        code, out = self.run_main("--repo", "o/r")
        self.assertEqual(code, 0)
        self.assertEqual(out, "ROLLUP_OK=1\n")
        self.assertEqual(len(fake.calls), 1)

    def test_numbers_are_deduplicated_and_sorted(self):
        self.install(
            (0, page([node(9, 1), node(2, 2)], has_next=True, end_cursor="c1"), ""),
            (0, page([node(2, 2), node(5, 1)]), ""),
        )
        self.assertEqual(rollups.collect_parents("o/r"), [2, 5, 9])

    def test_stdout_is_the_contract(self):
        """Defect (3): the block computed a result and printed nothing, so a
        successful run handed the next tool call no parent numbers at all."""
        self.install((0, page([node(7, 2), node(4, 1)]), ""))
        code, out = self.run_main("--repo", "o/r")
        self.assertEqual(code, 0)
        self.assertEqual(out, "ROLLUP_OK=1\n4\n7\n")

    def test_json_mode(self):
        self.install((0, page([node(4, 1)]), ""))
        code, out = self.run_main("--repo", "o/r", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True, "reason": None, "parents": [4]})


class TestPagination(RollupTestCase):
    def test_paginates_to_exhaustion(self):
        """Defect (1): with no pagination, candidates past the first 100-node
        page went unchecked, and an unchecked parent rollup is auto-promoted."""
        fake = self.install(
            (0, page([node(1, 1)], has_next=True, end_cursor="c1"), ""),
            (0, page([node(2, 1)], has_next=True, end_cursor="c2"), ""),
            (0, page([node(3, 1)]), ""),
        )
        self.assertEqual(rollups.collect_parents("o/r"), [1, 2, 3])
        self.assertEqual(len(fake.calls), 3)

    def test_second_request_carries_the_first_response_end_cursor(self):
        """Pins cursor PROPAGATION, not just that a second call happens.

        The shell harness this replaces advanced its stub by call count and never
        inspected arguments, so deleting the `CURSOR=` assignment left the whole
        suite green (verified by mutation). Deleting `cursor = end_cursor` from
        collect_parents must turn this case red.
        """
        fake = self.install(
            (0, page([node(1, 1)], has_next=True, end_cursor="CURSOR-ONE"), ""),
            (0, page([node(2, 1)], has_next=True, end_cursor="CURSOR-TWO"), ""),
            (0, page([node(3, 1)]), ""),
        )
        rollups.collect_parents("o/r")
        self.assertIsNone(fake.cursor_of(0), "first page must be requested uncursored")
        self.assertEqual(fake.cursor_of(1), "CURSOR-ONE")
        self.assertEqual(fake.cursor_of(2), "CURSOR-TWO")

    def test_repeated_cursor_fails_instead_of_looping_forever(self):
        """Defect (7): a valid but unchanging cursor loops forever. The guard
        added for defect (6) does not cover it — the cursor is a non-empty
        string, so it passes every shape check."""
        fake = self.install(
            (0, page([node(1, 1)], has_next=True, end_cursor="same"), ""),
            (0, page([node(2, 1)], has_next=True, end_cursor="same"), ""),
        )
        with self.assertRaises(rollups.LookupFailed) as ctx:
            rollups.collect_parents("o/r")
        self.assertIn("repeated cursor", str(ctx.exception))
        self.assertEqual(len(fake.calls), 2)


class TestFailuresDiscardPartialResults(RollupTestCase):
    def assert_fails(self, *responses, contains=None, repo="o/r"):
        self.install(*responses)
        with self.assertRaises(rollups.LookupFailed) as ctx:
            rollups.collect_parents(repo)
        if contains:
            self.assertIn(contains, str(ctx.exception))
        return str(ctx.exception)

    def test_non_zero_exit(self):
        """Defect (2): no failure check, so a denied page yielded an empty
        parent set indistinguishable from a repo with no rollups."""
        self.assert_fails((1, "", "HTTP 403: Resource not accessible"), contains="403")

    def test_empty_body_with_zero_exit(self):
        self.assert_fails((0, "", ""), contains="empty body")

    def test_non_json_body(self):
        """Defect (4): unchecked `jq`, so a well-formed HTTP 200 with an
        unexpected body read as a genuine last page."""
        self.assert_fails((0, "<html>gateway timeout</html>", ""), contains="not JSON")

    def test_graphql_errors_envelope_arrives_with_exit_zero(self):
        body = json.dumps({"data": None, "errors": [{"message": "rate limited"}]})
        self.assert_fails((0, body, ""), contains="rate limited")

    def test_nodes_is_not_an_array(self):
        body = json.dumps(
            {"data": {"repository": {"issues": {"nodes": {}, "pageInfo": {}}}}}
        )
        self.assert_fails(
            (0, body, ""), contains="issues.nodes: expected list, got dict"
        )

    def test_a_non_object_inside_nodes_is_rejected(self):
        """`nodes` is a list of objects, and nothing guarantees the API sends
        one. A scalar here must fail closed rather than reach `.get()` on an
        int. Found by mutation: removing expect()'s container check left the
        whole suite green, because no case covered this shape."""
        body = json.dumps(
            {
                "data": {
                    "repository": {
                        "issues": {
                            "nodes": [42],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        )
        self.assert_fails(
            (0, body, ""), contains="issue node: expected an object, got int"
        )

    def test_missing_repository_object(self):
        self.assert_fails(
            (0, json.dumps({"data": {}}), ""),
            contains="response.data.repository: missing",
        )

    def test_truthy_non_dict_data_is_rejected(self):
        """A truthy non-dict `data` has no `.get`, so an unguarded dereference
        raises AttributeError — which escapes LookupFailed entirely and prints
        no verdict at all. The falsy `{"data": {}}` case above does not cover
        this: `{} or {}` still yields a dict."""
        self.assert_fails(
            (0, json.dumps({"data": "unexpected"}), ""),
            contains="response.data: expected dict, got str",
        )

    def test_string_total_count_is_rejected(self):
        """Defect (5): a string `totalCount` satisfied `> 0` because jq orders
        every number before every string, so a promotable issue was silently
        skipped as a rollup."""
        self.assert_fails(
            (0, page([node(1, "0")]), ""),
            contains="subIssues.totalCount: expected int, got str",
        )

    def test_boolean_total_count_is_rejected(self):
        """`bool` is an `int` subclass, so this must be rejected by its own
        carve-out, not by the str check above — assert `got bool`."""
        self.assert_fails(
            (0, page([node(1, True)]), ""),
            contains="subIssues.totalCount: expected int, got bool",
        )

    def test_non_numeric_issue_number_is_rejected(self):
        self.assert_fails(
            (0, page([{"number": "12", "subIssues": {"totalCount": 1}}]), ""),
            contains="number: expected int, got str",
        )

    def test_has_next_page_must_be_boolean_typed(self):
        body = json.dumps(
            {
                "data": {
                    "repository": {
                        "issues": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": "true", "endCursor": "c"},
                        }
                    }
                }
            }
        )
        self.assert_fails((0, body, ""), contains="hasNextPage: expected bool, got str")

    def test_null_end_cursor_with_has_next_page_fails_rather_than_hanging(self):
        """Defect (6): `hasNextPage: true` with a missing/null/empty `endCursor`
        set CURSOR to the literal string "null" and re-fetched the same page
        forever — a hang, not a failure."""
        self.assert_fails(
            (0, page([node(1, 1)], has_next=True, end_cursor=None), ""),
            contains="endCursor is None",
        )

    def test_empty_end_cursor_with_has_next_page_fails(self):
        self.assert_fails(
            (0, page([node(1, 1)], has_next=True, end_cursor=""), ""),
            contains="endCursor is ''",
        )

    def test_a_later_page_failure_discards_the_earlier_pages(self):
        """A partial set silently under-reports rollups, which is the whole
        failure mode this file exists to prevent."""
        fake = self.install(
            (0, page([node(1, 1)], has_next=True, end_cursor="c1"), ""),
            (1, "", "HTTP 502"),
        )
        with self.assertRaises(rollups.LookupFailed):
            rollups.collect_parents("o/r")
        self.assertEqual(len(fake.calls), 2)

    def test_malformed_repo_argument(self):
        self.install()
        with self.assertRaises(rollups.LookupFailed) as ctx:
            rollups.collect_parents("no-slash")
        self.assertIn("owner/name", str(ctx.exception))


class TestFailureOutputChannel(RollupTestCase):
    def test_failure_prints_ok_zero_with_a_reason_and_exits_non_zero(self):
        self.install((1, "", "HTTP 403: Resource not accessible"))
        code, out = self.run_main("--repo", "o/r")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertEqual(lines[0], "ROLLUP_OK=0")
        self.assertTrue(lines[1].startswith("ROLLUP_REASON="))
        self.assertIn("403", lines[1])
        # No issue number may appear on a failed run.
        self.assertEqual(len(lines), 2)

    def test_malformed_data_still_prints_the_failure_contract(self):
        """The regression guard that matters: an unguarded dereference dies with
        a traceback, so stdout carries no verdict and the handler — which parses
        stdout — cannot tell a failure from a rollup-free repo."""
        self.install((0, json.dumps({"data": "unexpected"}), ""))
        code, out = self.run_main("--repo", "o/r")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertEqual(lines[0], "ROLLUP_OK=0")
        self.assertTrue(lines[1].startswith("ROLLUP_REASON="))

    def test_failure_json_mode(self):
        self.install((0, "", ""))
        code, out = self.run_main("--repo", "o/r", "--json")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["parents"], [])
        self.assertIn("empty body", payload["reason"])

    def test_sub_issues_unavailable_gets_the_reason_the_handler_prose_names(self):
        """The handler's Fallback names this reason specifically; every other
        failure reports its own text."""
        stderr = "GraphQL: Field 'subIssues' doesn't exist on type 'Issue'"
        self.install((1, "", stderr))
        code, out = self.run_main("--repo", "o/r")
        self.assertEqual(code, 1)
        self.assertIn(
            f"ROLLUP_REASON={rollups.SUBISSUES_UNAVAILABLE}", out.splitlines()[1]
        )


class TestQueryShape(RollupTestCase):
    def test_the_query_asks_for_page_info_and_accepts_a_cursor(self):
        """Pins what the pagination depends on. Dropping `after: $cursor` or
        `pageInfo` from QUERY makes every cursor test meaningless."""
        self.assertIn("after: $cursor", rollups.QUERY)
        self.assertIn("hasNextPage", rollups.QUERY)
        self.assertIn("endCursor", rollups.QUERY)
        self.assertIn("subIssues(first: 0)", rollups.QUERY)
        self.assertIn("states: [OPEN]", rollups.QUERY)

    def test_owner_and_name_are_split_and_passed_separately(self):
        fake = self.install((0, page([]), ""))
        rollups.collect_parents("bestdan/workflow-skills")
        self.assertIn("owner=bestdan", fake.calls[0])
        self.assertIn("repo=workflow-skills", fake.calls[0])

    def test_the_call_is_read_only(self):
        """No --method, no mutation: this script must never write."""
        fake = self.install((0, page([]), ""))
        rollups.collect_parents("o/r")
        for arg in fake.calls[0]:
            self.assertNotIn("--method", arg)
            self.assertNotIn("mutation", arg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
