#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-pr-sync.py.

Stubs the run_gh() seam of the gh-issue-state module that pr-sync loads, so
nothing shells out to `gh` or touches the network. Covers the two transitions
(ready-for-review forward, closed-unmerged back), every no-op gate, and the
fact that a write is one PATCH carrying the complete set — never
`--add-label`, which is not atomic.

Also asserts the workflow that drives it triggers on `ready_for_review` and
`closed` but NOT `opened`, and declares the `issues: write` permission the
PATCH needs. Those live in YAML rather than Python, but they are load-bearing
behavior: with `opened` in the list a draft PR would be moved to needs_review,
and without the permission every write would 403.
"""

import importlib.util
import io
import contextlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-pr-sync.py"
WORKFLOW = ROOT / ".github" / "workflows" / "gh-issue-pr-sync.yml"

_spec = importlib.util.spec_from_file_location("gh_issue_pr_sync", ASSET)
pr_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_sync)


class FakeRemote:
    """One issue, plus a log of every `gh` call made against it."""

    def __init__(self, labels=(), state="OPEN"):
        self.labels = list(labels)
        self.state = state
        self.calls = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ["issue", "view"]:
            payload = {
                "labels": [{"name": name} for name in self.labels],
                "state": self.state,
            }
            return 0, json.dumps(payload), ""
        if args[:3] == ["api", "--method", "PATCH"]:
            self.labels = json.loads(stdin)["labels"]
            return 0, "{}", ""
        raise AssertionError(f"unexpected gh call: {args}")

    def patches(self):
        return [
            json.loads(stdin)
            for args, stdin in self.calls
            if args[:3] == ["api", "--method", "PATCH"]
        ]


def run(remote, argv):
    """Invoke the CLI against `remote`, returning (exit code, parsed JSON)."""
    original = pr_sync.gh_issue_state.run_gh
    pr_sync.gh_issue_state.run_gh = remote.run_gh
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = pr_sync.main([*argv, "--json"])
    finally:
        pr_sync.gh_issue_state.run_gh = original
    return code, json.loads(out.getvalue())


READY = ["status:3_started", "auto:eligible", "prio:1", "est:3"]
IN_REVIEW = ["status:4_needs_review", "auto:eligible", "prio:1", "est:3"]


class ForwardTransitionTests(unittest.TestCase):
    def test_ready_for_review_moves_started_to_needs_review(self):
        remote = FakeRemote(READY)
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "bestdan/task-142",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIsNone(result["skipped"])
        self.assertEqual(result["issue"], 142)
        self.assertIn("status:4_needs_review", remote.labels)
        self.assertNotIn("status:3_started", remote.labels)

    def test_both_branch_prefixes_reach_the_write(self):
        for branch in ("bestdan/task-142", "claude/task-142", "task-142"):
            remote = FakeRemote(READY)
            code, result = run(
                remote,
                [
                    "--repo",
                    "o/n",
                    "--branch",
                    branch,
                    "--event",
                    "ready_for_review",
                    "--apply",
                ],
            )
            self.assertEqual(code, 0, branch)
            self.assertEqual(result["issue"], 142, branch)
            self.assertIn("status:4_needs_review", remote.labels, branch)

    def test_the_other_rungs_and_unmanaged_labels_survive(self):
        # The write replaces the issue's ENTIRE label set, so anything not
        # echoed back is deleted. `follow-up` is the handler's own marker and
        # /archive-tasks refuses to sweep without it.
        remote = FakeRemote([*READY, "follow-up", "bug"])
        run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        for label in ("auto:eligible", "prio:1", "est:3", "follow-up", "bug"):
            self.assertIn(label, remote.labels)

    def test_labels_the_write_purges_are_named_not_silently_deleted(self):
        # The full-set write is right to purge a `prio:urgent` a human invented,
        # but unattended an Actions log is the only place anyone could see it go.
        remote = FakeRemote([*READY, "prio:urgent", "est:99"])
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertEqual(sorted(result["dropped"]), ["est:99", "prio:urgent"])
        for label in ("prio:urgent", "est:99"):
            self.assertNotIn(label, remote.labels)

    def test_write_is_one_patch_carrying_the_complete_set(self):
        remote = FakeRemote(READY)
        run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        patches = remote.patches()
        self.assertEqual(len(patches), 1)
        self.assertEqual(
            sorted(patches[0]["labels"]),
            sorted(["status:4_needs_review", "auto:eligible", "prio:1", "est:3"]),
        )
        for args, _stdin in remote.calls:
            self.assertNotIn("--add-label", args)
            self.assertNotIn("--remove-label", args)

    def test_without_apply_it_reads_but_never_writes(self):
        remote = FakeRemote(READY)
        code, result = run(
            remote,
            ["--repo", "o/n", "--branch", "task-9", "--event", "ready_for_review"],
        )
        self.assertEqual(code, 0)
        self.assertFalse(result["applied"])
        self.assertEqual(remote.patches(), [])
        self.assertEqual(remote.labels, READY)


class ReverseTransitionTests(unittest.TestCase):
    def test_closed_unmerged_pr_returns_the_issue_to_started(self):
        remote = FakeRemote(IN_REVIEW)
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "bestdan/task-142",
                "--event",
                "closed",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIsNone(result["skipped"])
        self.assertIn("status:3_started", remote.labels)
        self.assertNotIn("status:4_needs_review", remote.labels)

    def test_merged_pr_writes_nothing_and_makes_no_request_at_all(self):
        # `Closes #<n>` closes the issue, and closure IS completion here. A
        # write would put a live rung back on a done issue.
        remote = FakeRemote(IN_REVIEW)
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "bestdan/task-142",
                "--event",
                "closed",
                "--merged",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIn("merged", result["skipped"])
        self.assertEqual(remote.calls, [])
        self.assertEqual(remote.labels, IN_REVIEW)


class NoOpGateTests(unittest.TestCase):
    def test_non_task_branch_makes_no_request_at_all(self):
        for branch in ("main", "feature/other", "task-142-fixup", "mytask-142"):
            remote = FakeRemote(READY)
            code, result = run(
                remote,
                [
                    "--repo",
                    "o/n",
                    "--branch",
                    branch,
                    "--event",
                    "ready_for_review",
                    "--apply",
                ],
            )
            self.assertEqual(code, 0, branch)
            self.assertIn("not a task branch", result["skipped"], branch)
            self.assertEqual(remote.calls, [], branch)

    def test_unexpected_current_rung_is_a_no_op_not_a_correction(self):
        # This runs unattended on every PR in the repo, so it must never be the
        # thing that invents a state.
        for rung in ("status:0_untriaged", "status:2_ready", "status:4_needs_review"):
            remote = FakeRemote([rung, "auto:eligible"])
            code, result = run(
                remote,
                [
                    "--repo",
                    "o/n",
                    "--branch",
                    "task-9",
                    "--event",
                    "ready_for_review",
                    "--apply",
                ],
            )
            self.assertEqual(code, 0, rung)
            self.assertIsNotNone(result["skipped"], rung)
            self.assertEqual(remote.patches(), [], rung)

    def test_issue_with_no_status_rung_is_a_no_op(self):
        remote = FakeRemote(["auto:eligible", "prio:1"])
        code, result = run(
            remote,
            [
                "--repo",
                "o/n",
                "--branch",
                "task-9",
                "--event",
                "ready_for_review",
                "--apply",
            ],
        )
        self.assertEqual(code, 0)
        self.assertIn("no single status: rung", result["skipped"])
        self.assertEqual(remote.patches(), [])

    def test_closed_issue_is_a_no_op(self):
        # gh-issue-state.py refuses an ordinary write against a closed issue,
        # so acting here would paint the Action red on an issue someone closed
        # by hand.
        remote = FakeRemote(IN_REVIEW, state="CLOSED")
        code, result = run(
            remote,
            ["--repo", "o/n", "--branch", "task-9", "--event", "closed", "--apply"],
        )
        self.assertEqual(code, 0)
        self.assertIn("is closed", result["skipped"])
        self.assertEqual(remote.patches(), [])

    def test_missing_auto_rung_refuses_rather_than_writing_a_broken_set(self):
        remote = FakeRemote(["status:3_started", "prio:1"])
        original = pr_sync.gh_issue_state.run_gh
        pr_sync.gh_issue_state.run_gh = remote.run_gh
        err = io.StringIO()
        try:
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = pr_sync.main(
                    [
                        "--repo",
                        "o/n",
                        "--branch",
                        "task-9",
                        "--event",
                        "ready_for_review",
                        "--apply",
                    ]
                )
        finally:
            pr_sync.gh_issue_state.run_gh = original
        self.assertEqual(code, 2)
        self.assertIn("auto", err.getvalue())
        self.assertEqual(remote.patches(), [])


class WorkflowTriggerTests(unittest.TestCase):
    """The workflow's trigger list and permissions are behavior, not config."""

    def setUp(self):
        self.text = WORKFLOW.read_text()

    def trigger_types(self):
        match = re.search(r"^\s*types:\s*\[([^\]]*)\]", self.text, re.MULTILINE)
        self.assertIsNotNone(match, "no `types: [...]` list in the workflow")
        return [t.strip() for t in match.group(1).split(",") if t.strip()]

    def test_triggers_on_ready_for_review_and_closed_but_not_opened(self):
        types = self.trigger_types()
        self.assertIn("ready_for_review", types)
        self.assertIn("closed", types)
        # A draft PR is not needs_review, and the house convention makes some
        # repos' PRs always draft.
        self.assertNotIn("opened", types)

    def test_declares_the_issues_write_permission_the_patch_needs(self):
        self.assertRegex(self.text, r"(?m)^\s+issues:\s*write\s*$")

    def test_runs_the_sync_asset(self):
        self.assertIn("commands/handlers/assets/gh-issue-pr-sync.py", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
