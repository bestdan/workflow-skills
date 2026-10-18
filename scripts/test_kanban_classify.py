#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/kanban-classify.py — the shared
kanban classification + ordering for the linear, gh-issue and jira `/list-tasks`
list sections.

No network: the script itself never touches the network.
"""

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

ASSET = (
    Path(__file__).resolve().parents[1]
    / "commands"
    / "handlers"
    / "assets"
    / "kanban-classify.py"
)

_spec = importlib.util.spec_from_file_location("kanban_classify", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
kanban_classify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kanban_classify)


def row(id_, category, labels=None, has_open_pr=False, priority=None, sort_date=""):
    return {
        "id": id_,
        "category": category,
        "labels": labels or [],
        "has_open_pr": has_open_pr,
        "priority": priority,
        "sort_date": sort_date,
    }


def run(tracker, rows):
    old_argv = sys.argv
    sys.argv = ["kanban-classify.py", "--tracker", tracker]
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(rows))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kanban_classify.main()
        return json.loads(buf.getvalue())
    finally:
        sys.argv = old_argv
        sys.stdin = old_stdin


class LinearClassifyTests(unittest.TestCase):
    def test_map(self):
        rows = [
            row("A-1", "backlog"),
            row("A-2", "backlog", labels=["human-approval-requested"]),
            row("A-3", "unstarted"),
            row("A-4", "started"),
            row("A-5", "started", labels=["blocked"]),
            row("A-6", "started", has_open_pr=True),
            row("A-7", "completed"),
        ]
        result = run("linear", rows)
        sections = result["sections"]
        self.assertEqual([r["id"] for r in sections["new"]], ["A-1"])
        self.assertEqual([r["id"] for r in sections["needs_refinement"]], ["A-2"])
        self.assertEqual([r["id"] for r in sections["ready"]], ["A-3"])
        self.assertEqual([r["id"] for r in sections["in_progress"]], ["A-4"])
        self.assertEqual([r["id"] for r in sections["blocked"]], ["A-5"])
        self.assertEqual([r["id"] for r in sections["needs_review"]], ["A-6"])
        self.assertEqual([r["id"] for r in sections["done"]], ["A-7"])
        self.assertEqual(result["unmatched"], [])

    def test_blocked_over_needs_review(self):
        rows = [row("A-1", "started", labels=["blocked"], has_open_pr=True)]
        result = run("linear", rows)
        self.assertEqual([r["id"] for r in result["sections"]["blocked"]], ["A-1"])
        self.assertEqual(result["sections"]["needs_review"], [])

    def test_none_priority_sorts_last(self):
        rows = [
            row("NONE", "unstarted", priority=0),
            row("LOW", "unstarted", priority=4),
            row("URGENT", "unstarted", priority=1),
        ]
        result = run("linear", rows)
        self.assertEqual(
            [r["id"] for r in result["sections"]["ready"]], ["URGENT", "LOW", "NONE"]
        )

    def test_no_match(self):
        result = run("linear", [row("A-1", "canceled")])
        self.assertEqual(result["unmatched"], ["A-1"])


class GhIssueClassifyTests(unittest.TestCase):
    def test_map(self):
        rows = [
            row("1", "0_untriaged"),
            row("2", ""),
            row("3", "1_needs_refinement"),
            row("4", "2_ready"),
            row("5", "3_started"),
            row("6", "4_needs_review"),
            row("7", "closed"),
            row("8", "3_started", labels=["blocked"]),
        ]
        result = run("gh-issue", rows)
        sections = result["sections"]
        self.assertEqual([r["id"] for r in sections["new"]], ["1", "2"])
        self.assertEqual([r["id"] for r in sections["needs_refinement"]], ["3"])
        self.assertEqual([r["id"] for r in sections["ready"]], ["4"])
        self.assertEqual([r["id"] for r in sections["in_progress"]], ["5"])
        self.assertEqual([r["id"] for r in sections["needs_review"]], ["6"])
        self.assertEqual([r["id"] for r in sections["done"]], ["7"])
        self.assertEqual([r["id"] for r in sections["blocked"]], ["8"])
        self.assertEqual(result["unmatched"], [])

    def test_blocked_over_needs_review(self):
        rows = [row("1", "4_needs_review", labels=["blocked"])]
        result = run("gh-issue", rows)
        self.assertEqual([r["id"] for r in result["sections"]["blocked"]], ["1"])
        self.assertEqual(result["sections"]["needs_review"], [])

    def test_closed_wins_over_a_stale_blocked_label(self):
        # A closed issue's labels aren't cleared on close, so a `blocked`
        # label surviving from before the merge must not pull it back out
        # of `done`.
        rows = [row("1", "closed", labels=["blocked"])]
        result = run("gh-issue", rows)
        self.assertEqual([r["id"] for r in result["sections"]["done"]], ["1"])
        self.assertEqual(result["sections"]["blocked"], [])

    def test_priority_none_sorts_last(self):
        rows = [
            row("NONE", "2_ready", priority=None),
            row("P3", "2_ready", priority=3),
            row("P0", "2_ready", priority=0),
        ]
        result = run("gh-issue", rows)
        self.assertEqual(
            [r["id"] for r in result["sections"]["ready"]], ["P0", "P3", "NONE"]
        )

    def test_no_match(self):
        result = run("gh-issue", [row("1", "9_bogus")])
        self.assertEqual(result["unmatched"], ["1"])


class JiraClassifyTests(unittest.TestCase):
    def test_map(self):
        rows = [
            row("A-1", "new"),
            row("A-2", "new", labels=["human-approval-requested"]),
            row("A-3", "new", labels=["auto-eligible"]),
            row("A-4", "indeterminate"),
            row("A-5", "indeterminate", labels=["blocked"]),
            row("A-6", "indeterminate", labels=["needs-review"]),
            row("A-7", "done"),
        ]
        result = run("jira", rows)
        sections = result["sections"]
        self.assertEqual([r["id"] for r in sections["new"]], ["A-1"])
        self.assertEqual([r["id"] for r in sections["needs_refinement"]], ["A-2"])
        self.assertEqual([r["id"] for r in sections["ready"]], ["A-3"])
        self.assertEqual([r["id"] for r in sections["in_progress"]], ["A-4"])
        self.assertEqual([r["id"] for r in sections["blocked"]], ["A-5"])
        self.assertEqual([r["id"] for r in sections["needs_review"]], ["A-6"])
        self.assertEqual([r["id"] for r in sections["done"]], ["A-7"])
        self.assertEqual(result["unmatched"], [])

    def test_blocked_over_needs_review(self):
        rows = [row("A-1", "indeterminate", labels=["blocked", "needs-review"])]
        result = run("jira", rows)
        self.assertEqual([r["id"] for r in result["sections"]["blocked"]], ["A-1"])
        self.assertEqual(result["sections"]["needs_review"], [])

    def test_done_wins_over_stale_blocked_and_needs_review_labels(self):
        # statusCategory done isn't recomputed on close, so labels set
        # earlier in the issue's life must not pull it back into blocked
        # or needs_review.
        rows = [row("A-1", "done", labels=["blocked", "needs-review"])]
        result = run("jira", rows)
        self.assertEqual([r["id"] for r in result["sections"]["done"]], ["A-1"])
        self.assertEqual(result["sections"]["blocked"], [])
        self.assertEqual(result["sections"]["needs_review"], [])

    def test_ready_over_needs_refinement(self):
        rows = [
            row(
                "A-1",
                "new",
                labels=["auto-eligible", "human-approval-requested"],
            )
        ]
        result = run("jira", rows)
        self.assertEqual([r["id"] for r in result["sections"]["ready"]], ["A-1"])
        self.assertEqual(result["sections"]["needs_refinement"], [])

    def test_priority_none_sorts_last(self):
        rows = [
            row("NONE", "new", labels=["auto-eligible"], priority=None),
            row("LOW", "new", labels=["auto-eligible"], priority="low"),
            row("URGENT", "new", labels=["auto-eligible"], priority="urgent"),
        ]
        result = run("jira", rows)
        self.assertEqual(
            [r["id"] for r in result["sections"]["ready"]], ["URGENT", "LOW", "NONE"]
        )

    def test_no_match(self):
        result = run("jira", [row("A-1", "cancelled")])
        self.assertEqual(result["unmatched"], ["A-1"])


if __name__ == "__main__":
    unittest.main()
