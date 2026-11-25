"""Hermetic tests for garden-checks.py, the /garden-backlog core's checks.

Loaded via importlib since the asset's filename is hyphenated. `gh` is stubbed
through the one `run` seam; the git search runs against a throwaway fixture
repository with GIT_CONFIG_GLOBAL pinned, so no test touches a network.
"""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "garden-checks.py"

_spec = importlib.util.spec_from_file_location("garden_checks", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)

BUG = """Steps to reproduce
1. Open the goal page.
2. Change the target date.
Expected
The chart redraws.
Actual
The chart stays stale until a refresh.
"""


def row(key: str = "AB-1", description: Any = BUG, **over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "key": key,
        "title": "Chart stays stale",
        "description": description,
        "status": "initial",
        "blocked_by": [],
    }
    base.update(over)
    return checks.prejudge([base])[0]


def record(key: str = "AB-1", **over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "key": key,
        "early_exit": None,
        "validity": {"verdict": "valid", "confidence": "high", "evidence": []},
        "kind": {"value": "fix", "confidence": "high"},
        "quality": "pass",
        "size": {"fibonacci": 2, "confidence": "high"},
        "importance": {"level": "minor", "confidence": "high", "reason": "x"},
        "status": {"target": "ready", "reason": "quality pass"},
        "proposed_writes": [],
        "human_queue": [],
    }
    base.update(over)
    return base


def groups(rec: Dict[str, Any]) -> List[str]:
    return [w["group"] for w in rec["proposed_writes"]]


def final(r: Dict[str, Any], rec: Dict[str, Any]) -> Dict[str, Any]:
    return checks.finalize([r], [rec])[0]


class AcceptanceCriteria(unittest.TestCase):
    def test_expected_actual_with_repro_passes(self) -> None:
        self.assertEqual(checks.ac_forms(BUG), ["expected-actual"])
        self.assertEqual(row()["checks"]["has_acceptance_criteria"], "pass")

    def test_inline_and_markdown_headings(self) -> None:
        text = "## Repro steps\n- open it\n**Expected:** it loads\nActual: it spins\n"
        self.assertEqual(checks.ac_forms(text), ["expected-actual"])

    def test_requirements_and_ac_sections(self) -> None:
        self.assertEqual(
            checks.ac_forms("Requirements\n- the link always shows\n"), ["section"]
        )
        self.assertEqual(
            checks.ac_forms("# Acceptance Criteria\n- [ ] done\n"), ["section"]
        )

    def test_deeper_heading_stays_in_the_section(self) -> None:
        self.assertEqual(
            checks.ac_forms("## Acceptance Criteria\n### Scenario\n- done\n"),
            ["section"],
        )

    def test_expected_actual_without_repro_fails(self) -> None:
        self.assertEqual(checks.ac_forms("Expected: loads\nActual: spins\n"), [])

    def test_empty_heading_and_prose_fail(self) -> None:
        self.assertEqual(checks.ac_forms("Acceptance Criteria\n## Notes\nlater\n"), [])
        self.assertEqual(
            checks.ac_forms("## Acceptance Criteria\n## Notes\nlater\n"), []
        )
        self.assertEqual(
            checks.ac_forms("### Acceptance Criteria\n# Notes\nlater\n"), []
        )
        self.assertEqual(checks.ac_forms("We should look at the expected value."), [])
        self.assertEqual(checks.ac_forms(None), [])

    def test_a_judge_that_passed_without_criteria_is_overruled(self) -> None:
        out = final(row(description="Make the chart better."), record())
        self.assertEqual(out["quality"], "has_acceptance_criteria")
        self.assertEqual(out["status"]["target"], "refinement")
        self.assertIn("status", groups(out))

    def test_a_judge_that_failed_present_criteria_is_queued(self) -> None:
        out = final(row(), record(quality="has_acceptance_criteria"))
        self.assertEqual(groups(out), ["human-queue"])
        self.assertTrue(any("later conditions" in q for q in out["human_queue"]))
        self.assertEqual(out["status"]["target"], "unchanged")


class Status(unittest.TestCase):
    def test_quality_pass_moves_to_ready(self) -> None:
        out = final(row(), record(status={"target": "refinement", "reason": "x"}))
        self.assertEqual(out["status"]["target"], "ready")
        self.assertEqual(
            out["proposed_writes"],
            [{"group": "status", "field": "status", "value": "ready"}],
        )

    def test_unresolved_blocker_never_gets_ready(self) -> None:
        out = final(row(blocked_by=["AB-7"]), record())
        self.assertEqual(
            out["status"], {"target": "unchanged", "reason": "blocked by AB-7"}
        )
        self.assertEqual(groups(out), [])

    def test_unread_links_hold_ready(self) -> None:
        out = final(row(blocked_by=None), record())
        self.assertEqual(out["status"]["target"], "unchanged")
        self.assertNotIn("status", groups(out))

    def test_blocked_ticket_that_fails_quality_still_goes_to_refinement(self) -> None:
        out = final(
            row(description="vague", blocked_by=["AB-7"]),
            record(quality="has_acceptance_criteria"),
        )
        self.assertEqual(out["status"]["target"], "refinement")
        self.assertIn("status", groups(out))

    def test_started_is_never_moved(self) -> None:
        out = final(row(status="started"), record())
        self.assertEqual(out["status"]["target"], "unchanged")
        self.assertEqual(groups(out), ["gardened"])

    def test_started_with_a_blocker_is_still_gardened(self) -> None:
        for blocked_by in (["AB-7"], None):
            out = final(row(status="started", blocked_by=blocked_by), record())
            self.assertEqual(out["status"]["target"], "unchanged")
            self.assertEqual(groups(out), ["gardened"])

    def test_target_equal_to_current_writes_nothing(self) -> None:
        self.assertEqual(groups(final(row(status="ready"), record())), ["gardened"])


IMPORTANCE_WRITE = {"group": "importance", "field": "importance", "value": "critical"}


class ValidityHolds(unittest.TestCase):
    def unsure(self) -> Dict[str, Any]:
        return record(
            validity={"verdict": "unsure", "confidence": "medium", "evidence": []},
            importance={"level": "critical", "confidence": "high", "reason": "x"},
            proposed_writes=[{"group": "kind", "field": "kind", "value": "spike"}],
            human_queue=["validity unsure: no repo"],
        )

    def test_unsure_holds_status_kind_and_importance(self) -> None:
        rec = self.unsure()
        rec["proposed_writes"].append(IMPORTANCE_WRITE)
        out = final(row(), rec)
        self.assertEqual(out["status"]["target"], "ready")
        self.assertEqual(out["importance"]["level"], "critical")
        self.assertEqual(groups(out), ["human-queue"])

    def test_settled_validity_keeps_the_kind_and_importance_writes(self) -> None:
        rec = record(
            proposed_writes=[
                {"group": "kind", "field": "kind", "value": "spike"},
                IMPORTANCE_WRITE,
            ]
        )
        self.assertEqual(groups(final(row(), rec)), ["kind", "importance", "status"])

    def test_closure_below_high_holds_too(self) -> None:
        rec = record(
            validity={"verdict": "fixed", "confidence": "medium", "evidence": []}
        )
        rec["proposed_writes"] = [
            {"group": "kind", "field": "kind", "value": "spike"},
            IMPORTANCE_WRITE,
        ]
        self.assertEqual(groups(final(row(), rec)), ["gardened"])

    def test_high_closure_and_early_exit_pass_through(self) -> None:
        closure = record(
            validity={"verdict": "fixed", "confidence": "high", "evidence": ["pr"]},
            proposed_writes=[{"group": "closure", "field": "labels", "value": "x"}],
        )
        early = {"key": "AB-2", "early_exit": "gardened, unchanged since"}
        out = checks.finalize([row(), row("AB-2")], [closure, early])
        self.assertEqual(out, [closure, early])

    def test_record_without_a_row_is_queued(self) -> None:
        out = checks.finalize([], [record()])[0]
        self.assertEqual(groups(out), ["human-queue"])


class KeySearch(unittest.TestCase):
    def test_key_in_text(self) -> None:
        self.assertTrue(checks.key_in_text("AB-12", "feat: thing [AB-12] (#99)"))
        self.assertTrue(checks.key_in_text("AB-12", "ab-12: lower case"))
        self.assertFalse(checks.key_in_text("AB-12", "fix AB-120 and XAB-12"))

    def test_pr_search_is_a_quoted_phrase_post_filtered(self) -> None:
        hits = [
            {
                "number": 1,
                "url": "u1",
                "title": "unrelated AB work",
                "body": "",
                "state": "OPEN",
                "mergedAt": None,
            },
            {
                "number": 2,
                "url": "u2",
                "title": "fix AB-120",
                "body": "",
                "state": "OPEN",
                "mergedAt": None,
            },
            {
                "number": 3,
                "url": "u3",
                "title": "feat: link [AB-12]",
                "body": "",
                "state": "CLOSED",
                "mergedAt": "2026-10-26T14:00:52Z",
            },
            {
                "number": 4,
                "url": "u4",
                "title": "chore",
                "body": "Refs AB-12",
                "state": "OPEN",
                "mergedAt": None,
            },
        ]
        seen: List[List[str]] = []

        def fake(argv: List[str]) -> Any:
            seen.append(argv)
            return checks.Proc(0, json.dumps(hits), "")

        with mock.patch.object(checks, "run", fake):
            out = checks.key_search("AB-12", ["acme/widgets"], [], "origin/HEAD")
        self.assertIn('"AB-12"', seen[0])
        self.assertEqual(
            [(p["number"], p["match"], p["state"]) for p in out["prs"]],
            [(3, "title", "MERGED"), (4, "body", "OPEN")],
        )
        self.assertEqual(out["errors"], [])

    def test_failed_search_is_an_error_not_an_empty_result(self) -> None:
        with mock.patch.object(
            checks, "run", lambda _a: checks.Proc(1, "", "HTTP 502")
        ):
            out = checks.key_search("AB-12", ["acme/widgets"], [], "origin/HEAD")
        self.assertEqual(out["prs"], [])
        self.assertTrue(out["errors"])

    def test_commit_search_finds_the_tagged_merge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = {
                **os.environ,
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
            }

            def git(*a: str) -> None:
                subprocess.run(
                    ["git", "-C", d, *a], check=True, capture_output=True, env=env
                )

            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            for msg in (
                "feat: thing [AB-12] (#99)",
                "fix: AB-120 unrelated",
                "chore: tidy",
            ):
                git("commit", "-q", "--allow-empty", "-m", msg)
            with mock.patch.dict(os.environ, env):
                out = checks.key_search("AB-12", [], [d], "HEAD")
        self.assertEqual(
            [c["subject"] for c in out["commits"]], ["feat: thing [AB-12] (#99)"]
        )
        self.assertEqual(out["errors"], [])


if __name__ == "__main__":
    unittest.main()
