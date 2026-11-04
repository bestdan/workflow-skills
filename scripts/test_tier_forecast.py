#!/usr/bin/env python3
"""Hermetic tests for scripts/tier-forecast.py.

Each case builds a throwaway git repo, commits a policy (or none) on `main`, and
runs main() against it, so the policy is read the way the promoter reads it: from
a ref with `git show`, never from the working tree.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tier-forecast.py"

_spec = importlib.util.spec_from_file_location("tier_forecast", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

POLICY = """\
auto:
  - "scripts/**"
always_human:
  - skills/**
"""

INTENT = """\
## Context

Something.

## Intent

- **Change:** add the forecast step.
- **Premise:** the promoter can list the paths.
- **Finish:** the fixtures pass.

## Acceptance Criteria

- it works
"""

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


class ForecastTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, env=GIT_ENV)

    def commit(self, policy=None):
        if policy is not None:
            path = self.repo / ".github" / "merge-policy.yml"
            path.parent.mkdir(exist_ok=True)
            path.write_text(policy)
        else:
            (self.repo / "README").write_text("x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")

    def run_forecast(self, task, ref="main"):
        task_file = self.tmp / "task.json"
        task_file.write_text(task if isinstance(task, str) else json.dumps(task))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fc.main(
                ["--task", str(task_file), "--ref", ref, "--repo-dir", str(self.repo)]
            )
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        result = json.loads(lines[0])
        self.assertEqual(lines[-1], f"FORECAST: {result['forecast']}")
        return result

    # --- the policy -----------------------------------------------------------

    def test_no_policy_is_skipped(self):
        self.commit()
        result = self.run_forecast({"paths": ["skills/x/SKILL.md"]})
        self.assertEqual(result["forecast"], "skipped")
        self.assertEqual(result["reasons"], ["no merge policy"])

    def test_malformed_policy_is_human(self):
        self.commit("not a policy\n")
        result = self.run_forecast({"paths": ["scripts/a.py"]})
        self.assertEqual(result["forecast"], "human")
        self.assertTrue(result["reasons"][0].startswith("forecast: policy invalid"))

    def test_an_unresolvable_ref_is_human(self):
        self.commit(POLICY)
        result = self.run_forecast({"paths": ["scripts/a.py"]}, ref="origin/HEAD")
        self.assertEqual(result["forecast"], "human")
        self.assertIn("does not resolve", result["reasons"][0])

    def test_the_policy_is_read_from_the_ref_not_the_working_tree(self):
        self.commit(POLICY)
        # A working-tree edit that would make skills/ auto must not count.
        (self.repo / ".github" / "merge-policy.yml").write_text('auto:\n  - "**"\n')
        self.assertEqual(
            self.run_forecast({"paths": ["skills/x/SKILL.md"]})["forecast"], "human"
        )

    # --- the forecast ---------------------------------------------------------

    def test_an_auto_forecast(self):
        self.commit(POLICY)
        result = self.run_forecast({"paths": ["scripts/a.py"]})
        self.assertEqual(result["forecast"], "auto")
        self.assertEqual(result["reasons"], ["forecast: all 1 paths match auto"])

    def test_consult_bar_comes_from_the_policy_or_defaults_to_85(self):
        self.commit(POLICY + "consult_bar: 90\n")
        self.assertEqual(
            self.run_forecast({"paths": ["scripts/a.py"]})["consult_bar"], 90
        )
        self.assertEqual(
            self.run_forecast({"paths": ["scripts/a.py"]}, ref="origin/HEAD")[
                "consult_bar"
            ],
            85,
        )

    def test_a_loaded_policy_without_consult_bar_reports_85(self):
        self.commit(POLICY)
        self.assertEqual(
            self.run_forecast({"paths": ["scripts/a.py"]})["consult_bar"], 85
        )

    def test_a_human_forecast(self):
        self.commit(POLICY)
        result = self.run_forecast({"paths": ["scripts/a.py", "skills/x/SKILL.md"]})
        self.assertEqual(result["forecast"], "human")
        self.assertEqual(
            result["reasons"], ["forecast: skills/x/SKILL.md matches always_human"]
        )

    def test_an_empty_path_list_is_human(self):
        self.commit(POLICY)
        result = self.run_forecast({"paths": [], "body": ""})
        self.assertEqual(result["forecast"], "human")
        self.assertEqual(result["reasons"], ["forecast: no paths identified"])

    def test_a_policy_that_is_not_utf8_is_human(self):
        self.commit("auto: [scripts/**]\n")
        (self.repo / ".github" / "merge-policy.yml").write_bytes(b"auto: [\xff]\n")
        self.git("commit", "-q", "-am", "binary policy")
        result = self.run_forecast({"paths": ["scripts/a.py"]})
        self.assertEqual(result["forecast"], "human")
        self.assertIn("cannot decode", result["reasons"][0])

    def test_an_unreadable_policy_is_human_not_skipped(self):
        self.commit(POLICY)
        blob = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "main:.github/merge-policy.yml"],
            capture_output=True,
            text=True,
            check=True,
            env=GIT_ENV,
        ).stdout.strip()
        (self.repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
        result = self.run_forecast({"paths": ["scripts/a.py"]})
        self.assertEqual(result["forecast"], "human")
        self.assertIn("cannot read", result["reasons"][0])

    def test_bad_input_is_human(self):
        self.commit(POLICY)
        task: object
        for task in (
            "not json",
            {"paths": "scripts/a.py"},
            {"paths": [], "body": 3},
            [],
        ):
            with self.subTest(task=task):
                result = self.run_forecast(task)
                self.assertEqual(result["forecast"], "human")
                self.assertTrue(
                    result["reasons"][0].startswith("forecast: input invalid")
                )

    def test_task_from_stdin(self):
        self.commit(POLICY)
        out = io.StringIO()
        stdin = io.StringIO(json.dumps({"paths": ["scripts/a.py"]}))
        with mock.patch("sys.stdin", stdin), contextlib.redirect_stdout(out):
            fc.main(["--task", "-", "--ref", "main", "--repo-dir", str(self.repo)])
        self.assertEqual(out.getvalue().splitlines()[-1], "FORECAST: auto")

    # --- the Intent block -----------------------------------------------------

    def test_intent_present(self):
        self.commit(POLICY)
        result = self.run_forecast({"paths": ["skills/x/SKILL.md"], "body": INTENT})
        self.assertEqual(result["intent"], {"state": "present", "missing": []})

    def test_intent_partial(self):
        body = INTENT.replace(
            "- **Premise:** the promoter can list the paths.\n", ""
        ).replace("the fixtures pass.", "")
        self.assertEqual(
            fc.parse_intent(body),
            {"state": "partial", "missing": ["Premise", "Finish"]},
        )

    def test_intent_absent(self):
        self.assertEqual(
            fc.parse_intent("## Context\n\nChange: not in an Intent block\n"),
            {"state": "absent", "missing": ["Change", "Premise", "Finish"]},
        )

    def test_intent_ends_at_the_next_heading(self):
        body = "## Intent\n\nChange: a\nPremise: b\n\n## Acceptance Criteria\n\nFinish: c\n"
        self.assertEqual(
            fc.parse_intent(body), {"state": "partial", "missing": ["Finish"]}
        )

    def test_intent_accepts_plain_labels(self):
        body = "## Intent\nChange: a\nPremise: b\nFinish: c\n"
        self.assertEqual(fc.parse_intent(body)["state"], "present")

    def test_heading_variants(self):
        rest = "\nChange: a\nPremise: b\nFinish: c\n"
        for heading, state in (
            ("## Intent", "present"),
            ("## Intent:", "present"),
            ("## intent", "present"),
            ("### Intent", "absent"),
            ("# Intent", "absent"),
            ("## Intent block", "absent"),
        ):
            with self.subTest(heading=heading):
                self.assertEqual(fc.parse_intent(heading + rest)["state"], state)

    def test_a_fenced_example_is_not_an_intent_block(self):
        body = "Body\n\n```markdown\n## Intent\nChange: x\nPremise: y\nFinish: z\n```\n"
        self.assertEqual(fc.parse_intent(body)["state"], "absent")

    def test_a_fence_closes_only_on_its_own_marker(self):
        example = "## Intent\nChange: x\nPremise: y\nFinish: z\n"
        for inner in ("~~~", "```"):
            with self.subTest(inner=inner):
                body = f"````\n{inner}\n{example}{inner}\n````\n"
                self.assertEqual(fc.parse_intent(body)["state"], "absent")
        # A real block after a nested fence still counts.
        body = "````\n```\n````\n" + example
        self.assertEqual(fc.parse_intent(body)["state"], "present")
        # An opener-like line with an info string does not close the fence.
        body = "```\n```sh\n" + example + "```\n"
        self.assertEqual(fc.parse_intent(body)["state"], "absent")

    def test_a_fenced_comment_does_not_end_the_block(self):
        body = "## Intent\nChange: x\n```sh\n# run this\n```\nPremise: y\nFinish: z\n"
        self.assertEqual(fc.parse_intent(body)["state"], "present")

    def test_intent_accepts_the_colon_outside_the_bold(self):
        body = "## Intent\n- **Change**: a\n- **Premise**: b\n- **Finish**: c\n"
        self.assertEqual(fc.parse_intent(body)["state"], "present")

    def test_intent_is_reported_when_the_policy_is_missing(self):
        self.commit()
        result = self.run_forecast({"paths": ["skills/x"], "body": INTENT})
        self.assertEqual(result["intent"]["state"], "present")

    def test_missing_task_flag_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                fc.main([])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
