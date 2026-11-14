#!/usr/bin/env python3
"""Hermetic tests for linear-scan.py's `--with-unassigned` partition.

The partition decides which in-flight issues a scheduled `--apply` sweep may
complete, so its one rule is tested directly: keep configured-project and
project-less issues, and only COUNT issues in any other project. Keeping those
would widen the sweep to every unconfigured project's work.
"""

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ASSET = (
    Path(__file__).resolve().parents[1]
    / "commands"
    / "handlers"
    / "assets"
    / "linear-scan.py"
)
_spec = importlib.util.spec_from_file_location("linear_scan", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


def issue(ident, project):
    return {"identifier": ident, "project": project}


A = {"id": "p-a", "name": "Alpha"}
B = {"id": "p-b", "name": "Beta"}
C = {"id": "p-c", "name": "Gamma"}


class PartitionTests(unittest.TestCase):
    def test_keeps_configured_and_project_less_and_counts_the_rest(self):
        issues = [
            issue("X-1", A),
            issue("X-2", None),
            issue("X-3", B),
            issue("X-4", C),
            issue("X-5", C),
        ]
        kept, outside = scan.partition(issues, ["p-a"])
        self.assertEqual([i["identifier"] for i in kept], ["X-1", "X-2"])
        self.assertEqual([i["scope"] for i in kept], ["p-a", "Unassigned"])
        self.assertEqual(
            outside,
            [{"project": C, "count": 2}, {"project": B, "count": 1}],
        )

    def test_nothing_outside_gives_an_empty_list(self):
        kept, outside = scan.partition([issue("X-1", A)], ["p-a"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(outside, [])

    def test_with_unassigned_refuses_without_a_project(self):
        argv = ["linear-scan.py", "--team", "Core", "--with-unassigned"]
        prelude = ({"id": "v"}, {"id": "t", "name": "Core", "states": {"nodes": []}})
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", argv))
            stack.enter_context(mock.patch.object(scan, "get_key", return_value="k"))
            stack.enter_context(
                mock.patch.object(scan, "resolve_team", return_value=prelude)
            )
            fetch = stack.enter_context(mock.patch.object(scan, "fetch_issues"))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            cm = stack.enter_context(self.assertRaises(SystemExit))
            scan.main()
        self.assertIn("needs at least one --project", str(cm.exception.code))
        fetch.assert_not_called()

    def test_with_unassigned_prints_the_partitioned_whole_team_result(self):
        argv = [
            "linear-scan.py",
            "--team",
            "Core",
            "--project",
            "p-a",
            "--with-unassigned",
        ]
        prelude = ({"id": "v"}, {"id": "t", "name": "Core", "states": {"nodes": []}})

        def node(ident, project):
            return {
                "identifier": ident,
                "project": project,
                "attachments": {"nodes": [{"url": f"https://x/{ident}"}]},
            }

        fetched = [node("X-1", A), node("X-2", None), node("X-3", B)]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", argv))
            stack.enter_context(mock.patch.object(scan, "get_key", return_value="k"))
            stack.enter_context(
                mock.patch.object(scan, "resolve_team", return_value=prelude)
            )
            fetch = stack.enter_context(
                mock.patch.object(scan, "fetch_issues", return_value=fetched)
            )
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            scan.main()
        self.assertIsNone(fetch.call_args.args[3], "expected one whole-team query")
        result = json.loads(out.getvalue())
        self.assertEqual(
            [(i["identifier"], i["scope"]) for i in result["issues"]],
            [("X-1", "p-a"), ("X-2", "Unassigned")],
        )
        self.assertEqual(result["issues"][0]["attachments"], ["https://x/X-1"])
        self.assertEqual(result["meta"]["out_of_scope"], [{"project": B, "count": 1}])


if __name__ == "__main__":
    unittest.main()
