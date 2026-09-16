#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-rank.py — the MCP-floor
entry point for `linear-common.md`'s "Ready-candidate selection".

No network: the script itself never touches the network (it only decides
over JSON handed to it on stdin), so these just drive `main()` with a stubbed
stdin/stdout, per PRE-533.
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
    / "linear-rank.py"
)

_spec = importlib.util.spec_from_file_location("linear_rank", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
linear_rank = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(linear_rank)


def issue(
    id_,
    priority=3,
    estimate=2,
    updated_at="2026-01-01T00:00:00Z",
    labels=None,
    assignee_id=None,
):
    return {
        "id": id_,
        "priority": priority,
        "estimate": estimate,
        "updatedAt": updated_at,
        "labels": labels or [],
        "assigneeId": assignee_id,
    }


def run(argv, issues):
    old_argv = sys.argv
    sys.argv = ["linear-rank.py"] + argv
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(issues))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            linear_rank.main()
        return json.loads(buf.getvalue())
    finally:
        sys.argv = old_argv
        sys.stdin = old_stdin


class GateTests(unittest.TestCase):
    def test_over_estimate_drop(self):
        result = run(
            ["--max-estimate", "3"],
            [issue("A-1", estimate=3), issue("A-2", estimate=2)],
        )
        self.assertEqual(result["dropped"], {"A-1": "estimate 3 >= 3"})
        self.assertEqual([c["id"] for c in result["candidates"]], ["A-2"])

    def test_blocked_label_drop(self):
        result = run(
            ["--max-estimate", "3"],
            [issue("A-1", labels=["blocked"]), issue("A-2")],
        )
        self.assertEqual(result["dropped"], {"A-1": "blocked"})
        self.assertEqual([c["id"] for c in result["candidates"]], ["A-2"])

    def test_no_estimate_drop(self):
        result = run(["--max-estimate", "3"], [issue("A-1", estimate=None)])
        self.assertEqual(result["dropped"], {"A-1": "no estimate set"})
        self.assertEqual(result["candidates"], [])

    def test_assignee_gate_against_viewer_id(self):
        result = run(
            ["--max-estimate", "3", "--viewer-id", "me"],
            [issue("A-1", assignee_id="someone-else"), issue("A-2", assignee_id="me")],
        )
        self.assertEqual(result["dropped"], {"A-1": "assigned to someone-else"})
        self.assertEqual([c["id"] for c in result["candidates"]], ["A-2"])

    def test_no_viewer_id_skips_assignee_gate(self):
        result = run(
            ["--max-estimate", "3"], [issue("A-1", assignee_id="someone-else")]
        )
        self.assertEqual(result["dropped"], {})
        self.assertEqual([c["id"] for c in result["candidates"]], ["A-1"])


class PerProjectMaxEstimateTests(unittest.TestCase):
    def test_project_max_estimate_overrides_the_flag(self):
        a = issue("A-1", estimate=4)
        a["project"] = {"id": "p1", "max_estimate": 5}
        b = issue("B-1", estimate=4)
        b["project"] = {"id": "p2", "max_estimate": 3}
        result = run(["--max-estimate", "3"], [a, b])
        self.assertEqual(result["dropped"], {"B-1": "estimate 4 >= 3"})
        self.assertEqual([c["id"] for c in result["candidates"]], ["A-1"])


class RankTests(unittest.TestCase):
    def test_none_priority_sorts_last(self):
        # priority 0 ("None") must sort after every real priority, not first
        # as a naive numeric ascending sort would put it.
        result = run(
            ["--max-estimate", "3"],
            [
                issue("NONE", priority=0),
                issue("LOW", priority=4),
                issue("URGENT", priority=1),
            ],
        )
        self.assertEqual(
            [c["id"] for c in result["candidates"]], ["URGENT", "LOW", "NONE"]
        )

    def test_updated_at_tie_break_oldest_first(self):
        result = run(
            ["--max-estimate", "3"],
            [
                issue("NEWER", priority=2, updated_at="2026-02-01T00:00:00Z"),
                issue("OLDER", priority=2, updated_at="2026-01-01T00:00:00Z"),
            ],
        )
        self.assertEqual([c["id"] for c in result["candidates"]], ["OLDER", "NEWER"])


if __name__ == "__main__":
    unittest.main()
