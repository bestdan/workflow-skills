#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-graph-analyze.py.

No network, no subprocess — the module is pure JSON-in/JSON-out, so these
tests call `analyze()` directly against small `{issues: [...]}` fixtures
shaped like `linear-relations.py`'s output.

Each case pins one of the four behaviors the card asks for:
  - a two-node cycle is reported and excluded from `order`;
  - priority ordering is `1 -> 2 -> 3 -> 4 -> 0`, None sorting last;
  - a `0`-priority (None) blocker on an urgent dependent is an inversion;
  - tie-break within a rank is by estimate, then `createdAt`.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "linear-graph-analyze.py"

_spec = importlib.util.spec_from_file_location("linear_graph_analyze", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
graph_analyze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(graph_analyze)


def issue(
    identifier,
    priority=0,
    estimate=None,
    created_at=None,
    state_type="started",
    blocked_by=None,
):
    return {
        "identifier": identifier,
        "priority": priority,
        "estimate": estimate,
        "createdAt": created_at,
        "state": {"type": state_type},
        "blockedBy": blocked_by or [],
    }


class TwoNodeCycleTest(unittest.TestCase):
    def test_cycle_reported_and_excluded_from_order(self):
        payload = {
            "issues": [
                issue("A", blocked_by=["B"]),
                issue("B", blocked_by=["A"]),
            ]
        }
        result = graph_analyze.analyze(payload)
        self.assertEqual(result["cycles"], [["A", "B"]])
        self.assertEqual(result["order"], [])

    def test_acyclic_remainder_still_ordered(self):
        payload = {
            "issues": [
                issue("A", blocked_by=["B"]),
                issue("B", blocked_by=["A"]),
                issue("C"),
            ]
        }
        result = graph_analyze.analyze(payload)
        self.assertEqual(result["cycles"], [["A", "B"]])
        self.assertEqual(result["order"], ["C"])


class NoneLastOrderingTest(unittest.TestCase):
    def test_priority_rank_order_is_1_2_3_4_0(self):
        self.assertEqual(
            [graph_analyze.priority_rank(p) for p in (1, 2, 3, 4, 0)],
            sorted(graph_analyze.priority_rank(p) for p in (1, 2, 3, 4, 0)),
        )

    def test_none_priority_sorts_last_in_order(self):
        payload = {
            "issues": [
                issue("URGENT", priority=1),
                issue("LOW", priority=4),
                issue("NONE", priority=0),
            ]
        }
        result = graph_analyze.analyze(payload)
        self.assertEqual(result["order"], ["URGENT", "LOW", "NONE"])


class InversionWithZeroBlockerTest(unittest.TestCase):
    def test_none_priority_blocker_on_urgent_dependent_is_an_inversion(self):
        payload = {
            "issues": [
                issue("DEPENDENT", priority=1, blocked_by=["BLOCKER"]),
                issue("BLOCKER", priority=0),
            ]
        }
        result = graph_analyze.analyze(payload)
        self.assertEqual(
            result["inversions"],
            [
                {
                    "blocker": "BLOCKER",
                    "dependent": "DEPENDENT",
                    "blocker_priority": 0,
                    "dependent_priority": 1,
                }
            ],
        )

    def test_no_inversion_when_blocker_at_least_as_urgent(self):
        payload = {
            "issues": [
                issue("DEPENDENT", priority=3, blocked_by=["BLOCKER"]),
                issue("BLOCKER", priority=1),
            ]
        }
        result = graph_analyze.analyze(payload)
        self.assertEqual(result["inversions"], [])

    def test_terminal_blocker_excluded_from_inversion_sweep(self):
        payload = {
            "issues": [
                issue("DEPENDENT", priority=1, blocked_by=["BLOCKER"]),
                issue("BLOCKER", priority=0, state_type="completed"),
            ]
        }
        result = graph_analyze.analyze(payload)
        self.assertEqual(result["inversions"], [])


class TieBreakTest(unittest.TestCase):
    def test_tie_break_by_estimate_then_created_at(self):
        # Same priority rank throughout, so ordering falls entirely to the
        # estimate/createdAt tie-break.
        payload = {
            "issues": [
                issue("BIG_OLD", priority=2, estimate=5, created_at="2026-01-01"),
                issue("SMALL_NEW", priority=2, estimate=1, created_at="2026-06-01"),
                issue("SMALL_OLD", priority=2, estimate=1, created_at="2026-01-01"),
                issue(
                    "NO_ESTIMATE", priority=2, estimate=None, created_at="2025-01-01"
                ),
            ]
        }
        result = graph_analyze.analyze(payload)
        # Smaller estimate first; within equal (smallest) estimate, older
        # createdAt first; a missing estimate sorts after every known one
        # regardless of age.
        self.assertEqual(
            result["order"],
            ["SMALL_OLD", "SMALL_NEW", "BIG_OLD", "NO_ESTIMATE"],
        )


class MalformedInputTest(unittest.TestCase):
    def test_missing_issues_key_raises(self):
        with self.assertRaises(graph_analyze.ShapeError):
            graph_analyze.analyze({})

    def test_issue_without_identifier_or_id_raises(self):
        with self.assertRaises(graph_analyze.MalformedInput):
            graph_analyze.analyze({"issues": [{"priority": 1}]})

    def test_non_list_blocked_by_raises_even_when_falsy(self):
        # `0`/`""` are falsy but not None/missing — a bare `or []` fallback
        # would silently swallow this instead of rejecting it. Built by hand
        # (not the `issue()` helper, which has the same `or []` shape) so the
        # falsy `0` actually reaches the module under test.
        with self.assertRaises(graph_analyze.MalformedInput):
            graph_analyze.analyze(
                {"issues": [{"identifier": "A", "priority": 1, "blockedBy": 0}]}
            )

    def test_non_numeric_estimate_raises(self):
        with self.assertRaises(graph_analyze.MalformedInput):
            graph_analyze.analyze({"issues": [issue("A", estimate="big")]})

    def test_boolean_estimate_raises(self):
        with self.assertRaises(graph_analyze.MalformedInput):
            graph_analyze.analyze({"issues": [issue("A", estimate=True)]})

    def test_main_exits_nonzero_on_invalid_json(self):
        import io
        import sys
        from contextlib import redirect_stderr

        stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("not json")
            with redirect_stderr(io.StringIO()):
                rc = graph_analyze.main([])
        finally:
            sys.stdin = stdin
        self.assertEqual(rc, 1)


class OutOfScopeBlockerTest(unittest.TestCase):
    def test_blocker_outside_input_set_is_dropped_not_backfilled(self):
        payload = {
            "issues": [
                issue("A", priority=1, blocked_by=["NOT-IN-SCOPE"]),
            ]
        }
        result = graph_analyze.analyze(payload)
        self.assertEqual(result["cycles"], [])
        self.assertEqual(result["order"], ["A"])
        self.assertEqual(result["inversions"], [])


if __name__ == "__main__":
    unittest.main()
