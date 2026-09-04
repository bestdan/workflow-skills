#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-graph.py.

Stubs the module's run_gh() seam, so nothing shells out to `gh` or touches the
network.

The two facts these tests exist to pin are the ones the old report-only flow got
wrong, and both fail silently:

- **Cycle detection reads the NATIVE graph.** A cycle drawn in real `blocked_by`
  edges is found even when no body mentions it, and a "cycle" that exists only in
  `Blocked by:` footer prose is NOT reported — the footer is an echo, never a
  source.
- **This script writes nothing.** Every call it makes is a GET; a finding is
  handed to `gh-issue-deps.py` as an edge, never applied as a body edit.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-graph.py"

_spec = importlib.util.spec_from_file_location("gh_issue_graph", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
gh_issue_graph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gh_issue_graph)

REPO = "bestdan/scratch"


def issue(
    number,
    title="t",
    body="",
    state="OPEN",
    state_reason=None,
    labels=(),
    milestone=None,
):
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "stateReason": state_reason,
        "labels": [{"name": name} for name in labels],
        "milestone": {"title": milestone} if milestone else None,
        "createdAt": f"2026-01-{number:02d}T00:00:00Z",
    }


class FakeRepo:
    """Issues plus the native `blocked_by` list each one carries.

    `scope` is what `gh issue list` returns; `extra` holds issues reachable only
    by number, which is how an out-of-scope blocker behaves. `page_size` splits a
    `blocked_by` list into pages so a test can put an edge beyond page one — the
    shape `gh api --paginate --slurp` returns.
    """

    def __init__(self, scope, edges=None, extra=(), page_size=100):
        self.scope = list(scope)
        self.extra = {i["number"]: i for i in extra}
        self.by_number = {i["number"]: i for i in self.scope}
        self.by_number.update(self.extra)
        self.edges = dict(edges or {})
        self.page_size = page_size
        self.calls = []

    def run_gh(self, args):
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            limit = int(args[args.index("--limit") + 1])
            rows = self.scope
            if "--milestone" in args:
                want = args[args.index("--milestone") + 1]
                rows = [r for r in rows if (r["milestone"] or {}).get("title") == want]
            return 0, json.dumps(rows[:limit]), ""
        if args[:2] == ["issue", "view"]:
            number = int(args[2])
            if number not in self.by_number:
                return 1, "", "gh: Not Found (HTTP 404)"
            return 0, json.dumps(self.by_number[number]), ""
        if args[:3] == ["api", "--paginate", "--slurp"]:
            number = int(args[3].split("/issues/")[1].split("/")[0])
            entries = [
                {"number": n, "state": self._state(n)}
                for n in self.edges.get(number, [])
            ]
            pages = [
                entries[i : i + self.page_size]
                for i in range(0, max(len(entries), 1), self.page_size)
            ]
            return 0, json.dumps(pages), ""
        raise AssertionError(f"unexpected gh call: {args}")

    def _state(self, number):
        node = self.by_number.get(number)
        return (node or {}).get("state", "OPEN").lower()

    def mutating_calls(self):
        return [
            args
            for args in self.calls
            if args[:2] == ["issue", "edit"]
            or ("--method" in args and args[args.index("--method") + 1] != "GET")
        ]


class GraphTests(unittest.TestCase):
    def setUp(self):
        self._orig = gh_issue_graph.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_issue_graph.run_gh = self._orig

    def _run(self, repo, argv=()):
        gh_issue_graph.run_gh = repo.run_gh
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gh_issue_graph.main(["--repo", REPO, "--json", *argv])
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    # --- the native graph is the source ---------------------------------

    def test_a_cycle_in_native_edges_is_found_with_no_footer_prose(self):
        """The bodies say nothing. The edges are the whole evidence."""
        repo = FakeRepo(
            scope=[issue(1), issue(2), issue(3)],
            edges={1: [2], 2: [3], 3: [1]},
        )
        result = self._run(repo)

        self.assertEqual(result["cycles"], [[1, 2, 3]])

    def test_a_cycle_that_exists_only_in_footer_prose_is_not_a_cycle(self):
        """A footer is an echo. Prose alone deadlocks nothing, so it is no cycle."""
        repo = FakeRepo(
            scope=[
                issue(1, body="---\nBlocked by: #2"),
                issue(2, body="---\nBlocked by: #1"),
            ],
            edges={},
        )
        result = self._run(repo)

        self.assertEqual(result["cycles"], [])
        # It is still surfaced — as the migration input, not as a dependency.
        self.assertEqual(
            result["footer_only"],
            [{"blocked": 1, "blocker": 2}, {"blocked": 2, "blocker": 1}],
        )

    def test_a_self_edge_is_reported_as_a_cycle(self):
        repo = FakeRepo(scope=[issue(1)], edges={1: [1]})
        self.assertEqual(self._run(repo)["cycles"], [[1]])

    def test_a_cycle_closed_by_an_edge_past_the_first_page_is_still_found(self):
        """`--paginate --slurp` is why. A bare read stops at 30 entries."""
        blockers = [*range(20, 50), 2]  # 31 entries -> two pages at page_size 30
        scope = [issue(1), issue(2), *[issue(n) for n in range(20, 50)]]
        repo = FakeRepo(scope=scope, edges={1: blockers, 2: [1]}, page_size=30)

        self.assertEqual(self._run(repo)["cycles"], [[1, 2]])

    def test_the_script_never_writes(self):
        """Findings become edges via gh-issue-deps.py; nothing here edits a body."""
        repo = FakeRepo(
            scope=[issue(1, body="---\nBlocked by: #2"), issue(2)], edges={1: [2]}
        )
        self._run(repo)

        self.assertEqual(repo.mutating_calls(), [])

    # --- stale vs satisfied ---------------------------------------------

    def test_a_blocker_closed_not_planned_is_stale(self):
        repo = FakeRepo(
            scope=[issue(1), issue(2, state="CLOSED", state_reason="NOT_PLANNED")],
            edges={1: [2]},
        )
        result = self._run(repo)

        self.assertEqual(result["stale_edges"], [{"blocked": 1, "blocker": 2}])
        self.assertEqual(result["satisfied_edges"], [])

    def test_a_blocker_closed_completed_is_satisfied_not_stale(self):
        repo = FakeRepo(
            scope=[issue(1), issue(2, state="CLOSED", state_reason="COMPLETED")],
            edges={1: [2]},
        )
        result = self._run(repo)

        self.assertEqual(result["satisfied_edges"], [{"blocked": 1, "blocker": 2}])
        self.assertEqual(result["stale_edges"], [])

    # --- priority inversion ---------------------------------------------

    def test_a_less_urgent_blocker_is_an_inversion(self):
        repo = FakeRepo(
            scope=[issue(1, labels=["prio:0"]), issue(2, labels=["prio:3"])],
            edges={1: [2]},
        )
        result = self._run(repo)

        self.assertEqual(len(result["inversions"]), 1)
        self.assertEqual(result["inversions"][0]["blocker"], 2)
        self.assertEqual(result["inversions"][0]["raise_blocker_to"], "0")

    def test_a_blocker_with_no_priority_is_least_urgent(self):
        repo = FakeRepo(scope=[issue(1, labels=["prio:1"]), issue(2)], edges={1: [2]})
        result = self._run(repo)

        self.assertEqual(result["inversions"][0]["blocker_prio"], None)

    def test_an_invented_prio_label_is_not_a_priority(self):
        """`prio:urgent` starts with `prio:` and is outside labels.yml.

        Reading the prefix instead of the vocabulary is the trap task 7's rule 2
        fell into — it would rank this issue by a value nothing defines.
        """
        repo = FakeRepo(
            scope=[issue(1, labels=["prio:1"]), issue(2, labels=["prio:urgent"])],
            edges={1: [2]},
        )
        result = self._run(repo)

        self.assertEqual(result["inversions"][0]["blocker_prio"], None)

    def test_a_more_urgent_blocker_is_not_an_inversion(self):
        repo = FakeRepo(
            scope=[issue(1, labels=["prio:2"]), issue(2, labels=["prio:0"])],
            edges={1: [2]},
        )
        self.assertEqual(self._run(repo)["inversions"], [])

    def test_a_closed_blocker_is_never_an_inversion(self):
        """It is stale or satisfied; ranking a finished issue's urgency says nothing."""
        repo = FakeRepo(
            scope=[
                issue(1, labels=["prio:0"]),
                issue(2, labels=["prio:3"], state="CLOSED", state_reason="COMPLETED"),
            ],
            edges={1: [2]},
        )
        self.assertEqual(self._run(repo)["inversions"], [])

    # --- concurrency ------------------------------------------------------

    def test_a_blocker_and_dependent_both_started_are_flagged(self):
        repo = FakeRepo(
            scope=[
                issue(1, labels=["status:3_started"]),
                issue(2, labels=["status:3_started"]),
            ],
            edges={1: [2]},
        )
        self.assertEqual(self._run(repo)["concurrent"], [{"blocked": 1, "blocker": 2}])

    # --- footer reconciliation -------------------------------------------

    def test_an_edge_whose_footer_echo_is_missing_is_reported(self):
        repo = FakeRepo(scope=[issue(1, body="no footer"), issue(2)], edges={1: [2]})
        self.assertEqual(self._run(repo)["edge_only"], [{"blocked": 1, "blocker": 2}])

    def test_a_footer_matching_its_edge_is_reported_neither_way(self):
        repo = FakeRepo(
            scope=[issue(1, body="---\nBlocked by: #2"), issue(2)], edges={1: [2]}
        )
        result = self._run(repo)

        self.assertEqual(result["footer_only"], [])
        self.assertEqual(result["edge_only"], [])

    def test_a_slug_footer_yields_no_edge_to_migrate(self):
        """`Blocked by task: <slug>` names a plan task that never became an issue."""
        repo = FakeRepo(scope=[issue(1, body="---\nBlocked by task: some_slug")])
        self.assertEqual(self._run(repo)["footer_only"], [])

    def test_a_body_restating_its_own_number_is_not_a_self_block(self):
        repo = FakeRepo(scope=[issue(7, body="---\nBlocked by: #7, #8"), issue(8)])
        self.assertEqual(self._run(repo)["footer_only"], [{"blocked": 7, "blocker": 8}])

    # --- scope ------------------------------------------------------------

    def test_an_out_of_scope_blocker_is_backfilled_as_analysis_only(self):
        """Without it a cross-milestone edge has no state, so it cannot be classified."""
        repo = FakeRepo(
            scope=[issue(1, milestone="A")],
            extra=[issue(9, milestone="B", state="CLOSED", state_reason="NOT_PLANNED")],
            edges={1: [9]},
        )
        result = self._run(repo, ["--milestone", "A"])

        self.assertEqual(result["backfilled"], [9])
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["stale_edges"], [{"blocked": 1, "blocker": 9}])
        node = next(n for n in result["nodes"] if n["number"] == 9)
        self.assertFalse(node["in_scope"])

    def test_a_backfilled_node_is_never_a_footer_finding(self):
        """§Apply must not edit an out-of-scope issue, so it must not be told to."""
        repo = FakeRepo(
            scope=[issue(1, milestone="A")],
            extra=[issue(9, milestone="B", body="no footer")],
            edges={1: [9], 9: [1]},
        )
        result = self._run(repo, ["--milestone", "A"])

        self.assertEqual(result["edge_only"], [{"blocked": 1, "blocker": 9}])

    def test_a_full_page_reports_possible_truncation(self):
        repo = FakeRepo(scope=[issue(1), issue(2)])
        self.assertTrue(self._run(repo, ["--limit", "2"])["truncated"])
        self.assertFalse(self._run(repo, ["--limit", "3"])["truncated"])

    def test_issue_scope_skips_the_list_query_entirely(self):
        repo = FakeRepo(scope=[issue(1), issue(2), issue(3)], edges={1: [2]})
        result = self._run(repo, ["--issue", "1", "--issue", "2"])

        self.assertEqual([a for a in repo.calls if a[:2] == ["issue", "list"]], [])
        self.assertEqual(result["checked"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
