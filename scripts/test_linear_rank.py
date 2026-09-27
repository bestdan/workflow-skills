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


class BlockerGateTests(unittest.TestCase):
    def blocked(self, id_, *blockers, labels=None):
        i = issue(id_, labels=labels)
        i["blockedBy"] = [
            {"id": b, "statusType": s} if s is not None else {"id": b}
            for b, s in blockers
        ]
        return i

    def test_open_blocker_drops(self):
        result = run(["--max-estimate", "3"], [self.blocked("A-1", ("B-1", "started"))])
        self.assertEqual(result["dropped"], {"A-1": "waiting on B-1"})

    def test_completed_blocker_passes(self):
        result = run(
            ["--max-estimate", "3"], [self.blocked("A-1", ("B-1", "completed"))]
        )
        self.assertEqual([c["id"] for c in result["candidates"]], ["A-1"])

    def test_canceled_blocker_still_blocks(self):
        result = run(
            ["--max-estimate", "3"], [self.blocked("A-1", ("B-1", "canceled"))]
        )
        self.assertEqual(result["dropped"], {"A-1": "waiting on B-1"})

    def test_blocker_with_unknown_state_blocks(self):
        result = run(["--max-estimate", "3"], [self.blocked("A-1", ("B-1", None))])
        self.assertEqual(result["dropped"], {"A-1": "waiting on B-1"})

    def test_names_every_unmet_blocker(self):
        result = run(
            ["--max-estimate", "3"],
            [
                self.blocked(
                    "A-1",
                    ("B-1", "unstarted"),
                    ("B-2", "completed"),
                    ("B-3", "backlog"),
                )
            ],
        )
        self.assertEqual(result["dropped"], {"A-1": "waiting on B-1, B-3"})

    def test_blocker_reported_ahead_of_the_overridable_hold(self):
        # A direct pick can override `human-approval-requested`; if that reason
        # came first, the override would claim past an open blocker.
        result = run(
            ["--max-estimate", "3"],
            [
                self.blocked(
                    "A-1", ("B-1", "started"), labels=["human-approval-requested"]
                )
            ],
        )
        self.assertEqual(result["dropped"], {"A-1": "waiting on B-1"})

    def test_blocker_reported_ahead_of_a_missing_estimate(self):
        # /deliver-task never ran the estimate gates, and Pre-flight step 6
        # acts only on `waiting on` — an estimate reason must not mask it.
        i = self.blocked("A-1", ("B-1", "started"))
        i["estimate"] = None
        result = run(["--max-estimate", "3"], [i])
        self.assertEqual(result["dropped"], {"A-1": "waiting on B-1"})

    def test_no_blocked_by_key_skips_the_gate(self):
        result = run(["--max-estimate", "3"], [issue("A-1")])
        self.assertEqual([c["id"] for c in result["candidates"]], ["A-1"])


class StackInSetTests(unittest.TestCase):
    """The MCP floor's `--stack-in-set`, for /auto-pilot's list_ready."""

    def chain(self, id_, *blockers):
        i = issue(id_)
        i["blockedBy"] = [{"id": b, "statusType": s} for b, s in blockers]
        return i

    def test_in_set_blocker_is_a_stack_edge(self):
        result = run(
            ["--max-estimate", "3", "--stack-in-set"],
            [self.chain("P-1"), self.chain("C-1", ("P-1", "unstarted"))],
        )
        self.assertEqual(result["dropped"], {})
        self.assertEqual(sorted(c["id"] for c in result["candidates"]), ["C-1", "P-1"])

    def test_out_of_set_open_blocker_drops(self):
        result = run(
            ["--max-estimate", "3", "--stack-in-set"],
            [self.chain("C-1", ("OUT-1", "started"))],
        )
        self.assertEqual(result["dropped"], {"C-1": "waiting on OUT-1"})

    def test_chain_rooted_outside_the_set_drops_whole(self):
        result = run(
            ["--max-estimate", "3", "--stack-in-set"],
            [
                self.chain("P-1", ("OUT-1", "started")),
                self.chain("C-1", ("P-1", "unstarted")),
            ],
        )
        self.assertEqual(
            result["dropped"], {"P-1": "waiting on OUT-1", "C-1": "waiting on P-1"}
        )


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
