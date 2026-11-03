#!/usr/bin/env python3
"""Hermetic tests for scripts/merge-tier.py: one fixture per tier rule.

Each case writes a policy file and a PR-facts file to a temp directory, runs
main(), and asserts both the tier and the reason text, so a rule that fires for
the wrong reason fails as surely as one that does not fire.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge-tier.py"

_spec = importlib.util.spec_from_file_location("merge_tier", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
tier_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tier_mod)

POLICY = """\
# fixture policy
auto:
  - "scripts/**"
  - tests/*.py
always_human:
  - scripts/deploy.sh
size_cap: 50
allowed_types: [chore, test, fix]
repo_rules:
  - prompt-paths-need-releasing-type
"""


def pr(files=None, title="chore(scripts): tidy the helper", body="", labels=()):
    if files is None:
        files = [("scripts/helper.py", 5, 2)]
    return {
        "files": [
            {"path": p, "additions": a, "deletions": d, "status": "modified"}
            for p, a, d in files
        ],
        "title": title,
        "body": body,
        "labels": list(labels),
    }


class TierTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def run_tier(self, facts, policy=POLICY, extra=()):
        pr_file = self.tmp / "pr.json"
        pr_file.write_text(json.dumps(facts))
        argv = ["--pr-json", str(pr_file)]
        if policy is not None:
            policy_file = self.tmp / "merge-policy.yml"
            policy_file.write_text(policy)
            argv += ["--policy", str(policy_file)]
        else:
            argv += ["--policy", str(self.tmp / "absent.yml")]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tier_mod.main([*argv, *extra])
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        result = json.loads(lines[0])
        self.assertEqual(lines[-1], f"TIER: {result['tier']}")
        return result

    def assert_human(self, result, reason):
        self.assertEqual(result["tier"], "human")
        self.assertTrue(
            any(reason in r for r in result["reasons"]),
            f"no reason containing {reason!r} in {result['reasons']}",
        )

    # --- the auto path ------------------------------------------------------

    def test_a_clean_pr_inside_auto_globs_is_auto(self):
        result = self.run_tier(pr())
        self.assertEqual(result["tier"], "auto")
        self.assertEqual(len(result["reasons"]), 1)
        self.assertIn("all 1 paths match auto", result["reasons"][0])

    def test_single_star_stays_in_one_directory(self):
        self.assertEqual(
            self.run_tier(pr(files=[("tests/a.py", 1, 0)]))["tier"], "auto"
        )
        self.assert_human(
            self.run_tier(pr(files=[("tests/sub/a.py", 1, 0)])),
            "tests/sub/a.py matches no auto glob",
        )

    # --- path rules ---------------------------------------------------------

    def test_a_path_matching_no_auto_glob_is_human(self):
        result = self.run_tier(pr(files=[("scripts/x.py", 1, 0), ("README.md", 1, 0)]))
        self.assert_human(result, "README.md matches no auto glob")

    def test_always_human_beats_auto(self):
        result = self.run_tier(pr(files=[("scripts/deploy.sh", 1, 0)]))
        self.assert_human(result, "scripts/deploy.sh matches always_human")

    def test_the_policy_file_is_always_human(self):
        policy = POLICY.replace(
            '  - "scripts/**"', '  - "scripts/**"\n  - ".github/**"'
        )
        result = self.run_tier(
            pr(files=[(".github/merge-policy.yml", 1, 0)]), policy=policy
        )
        self.assert_human(
            result,
            ".github/merge-policy.yml is the merge policy, tier script or lander",
        )

    def test_the_tier_script_is_always_human(self):
        result = self.run_tier(pr(files=[("scripts/merge-tier.py", 1, 0)]))
        self.assert_human(
            result, "scripts/merge-tier.py is the merge policy, tier script or lander"
        )

    def test_no_changed_files_is_human(self):
        self.assert_human(self.run_tier(pr(files=[])), "no changed files")

    def test_an_empty_auto_list_makes_everything_human(self):
        policy = "allowed_types: [chore]\n"
        self.assert_human(self.run_tier(pr(), policy=policy), "matches no auto glob")

    # --- demotions ----------------------------------------------------------

    def test_bang_title_is_human(self):
        result = self.run_tier(pr(title="chore(scripts)!: drop the old flag"))
        self.assert_human(result, "breaking change: title ends its type with `!`")

    def test_breaking_change_footer_is_human(self):
        result = self.run_tier(pr(body="Tidy.\n\nBREAKING CHANGE: --old is gone"))
        self.assert_human(result, "breaking change: BREAKING CHANGE footer")

    def test_breaking_change_mid_line_is_not_a_footer(self):
        result = self.run_tier(pr(body="This is not a BREAKING CHANGE: no"))
        self.assertEqual(result["tier"], "auto")

    def test_size_over_cap_is_human(self):
        result = self.run_tier(pr(files=[("scripts/a.py", 40, 11)]))
        self.assert_human(result, "size 51 changed lines over size_cap 50")

    def test_size_at_cap_is_auto(self):
        self.assertEqual(
            self.run_tier(pr(files=[("scripts/a.py", 40, 10)]))["tier"], "auto"
        )

    def test_default_size_cap_is_300(self):
        policy = 'auto:\n  - "scripts/**"\nallowed_types: [chore]\n'
        self.assertEqual(
            self.run_tier(pr(files=[("scripts/a.py", 300, 0)]), policy=policy)["tier"],
            "auto",
        )
        self.assert_human(
            self.run_tier(pr(files=[("scripts/a.py", 301, 0)]), policy=policy),
            "over size_cap 300",
        )

    def test_hold_label_is_human(self):
        self.assert_human(self.run_tier(pr(labels=["merge:hold"])), "label merge:hold")

    def test_agent_land_off_is_human(self):
        result = self.run_tier(pr(), extra=["--agent-land", "off"])
        self.assert_human(result, "AGENT_LAND=off")

    def test_type_outside_allowed_types_is_human(self):
        result = self.run_tier(pr(title="feat(scripts): add a flag"))
        self.assert_human(result, "title type `feat` not in allowed_types")

    def test_unconventional_title_is_human(self):
        self.assert_human(
            self.run_tier(pr(title="Tidy the helper")),
            "title is not a Conventional Commit",
        )

    def test_prompt_path_with_non_releasing_type_trips_the_repo_rule(self):
        policy = POLICY.replace('  - "scripts/**"', '  - "scripts/**"\n  - "skills/**"')
        result = self.run_tier(pr(files=[("skills/x/SKILL.md", 1, 0)]), policy=policy)
        self.assert_human(
            result,
            "repo rule prompt-paths-need-releasing-type: skills/x/SKILL.md changes with non-releasing type `chore`",
        )

    def test_prompt_path_with_releasing_type_passes_the_repo_rule(self):
        policy = POLICY.replace('  - "scripts/**"', '  - "commands/**"')
        result = self.run_tier(
            pr(files=[("commands/x.md", 1, 0)], title="fix(x): word"), policy=policy
        )
        self.assertEqual(result["tier"], "auto")

    def test_every_firing_rule_is_reported(self):
        result = self.run_tier(
            pr(files=[("README.md", 400, 0)], title="feat!: x", labels=["merge:hold"]),
            extra=["--agent-land", "off"],
        )
        self.assertEqual(result["tier"], "human")
        self.assertEqual(len(result["reasons"]), 6, result["reasons"])

    # --- fail closed --------------------------------------------------------

    def test_missing_policy_is_human(self):
        self.assert_human(
            self.run_tier(pr(), policy=None), "policy invalid: cannot read"
        )

    def test_malformed_policy_is_human(self):
        self.assert_human(
            self.run_tier(pr(), policy="auto: scripts/**\n"),
            "policy invalid: line 1: `auto` must be a list",
        )
        self.assert_human(
            self.run_tier(pr(), policy="auto:\n  scripts/**\n"),
            "policy invalid: line 2: unexpected indented line",
        )
        self.assert_human(
            self.run_tier(pr(), policy="size_cap: big\n"),
            "policy invalid: `size_cap` must be a positive integer",
        )

    def test_the_lander_is_always_human(self):
        for path in ("scripts/land-pr.sh", "scripts/land-pr.py"):
            self.assert_human(
                self.run_tier(pr(files=[(path, 1, 0)])),
                f"{path} is the merge policy, tier script or lander",
            )

    def test_consult_bar_is_validated(self):
        result = self.run_tier(pr(), policy=POLICY + "consult_bar: 90\n")
        self.assertEqual(result["tier"], "auto")
        for bad in ("101", "-1", "high", "85.5"):
            self.assert_human(
                self.run_tier(pr(), policy=POLICY + f"consult_bar: {bad}\n"),
                "policy invalid: `consult_bar` must be an integer 0-100",
            )

    def test_evaluate_returns_the_consult_bar_default_85(self):
        pr_file = self.tmp / "pr.json"
        pr_file.write_text(json.dumps(pr()))
        policy_file = self.tmp / "p.yml"
        for text, bar in ((POLICY, 85), (POLICY + "consult_bar: 70\n", 70)):
            policy_file.write_text(text)
            tier, _, policy = tier_mod.evaluate(str(pr_file), str(policy_file), "on")
            self.assertEqual((tier, policy["consult_bar"]), ("auto", bar))

    def test_unknown_key_is_human(self):
        result = self.run_tier(pr(), policy=POLICY + "auto_merge: yes\n")
        self.assert_human(result, "policy invalid: line 11: unknown key `auto_merge`")

    def test_unknown_repo_rule_is_human(self):
        result = self.run_tier(pr(), policy=POLICY + "  - no-such-rule\n")
        self.assert_human(result, "policy invalid: unknown repo rule `no-such-rule`")

    def test_duplicate_key_is_human(self):
        result = self.run_tier(pr(), policy=POLICY + "size_cap: 10\n")
        self.assert_human(result, "policy invalid: line 11: duplicate key `size_cap`")

    def test_malformed_pr_input_is_human(self):
        facts = pr()
        facts["files"][0]["additions"] = "5"
        self.assert_human(self.run_tier(facts), "input invalid")

    def test_a_rename_out_of_always_human_is_human(self):
        facts = pr(files=[("scripts/land.sh", 0, 0)])
        facts["files"][0].update(status="renamed", previous_path="scripts/deploy.sh")
        self.assert_human(
            self.run_tier(facts), "scripts/deploy.sh matches always_human"
        )

    def test_a_rename_of_the_tier_script_is_human(self):
        facts = pr(files=[("scripts/tier.py", 0, 0)])
        facts["files"][0].update(
            status="renamed", previous_path="scripts/merge-tier.py"
        )
        self.assert_human(
            self.run_tier(facts),
            "scripts/merge-tier.py is the merge policy, tier script or lander",
        )

    def test_a_rename_without_previous_path_is_human(self):
        facts = pr(files=[("scripts/land.sh", 0, 0)])
        facts["files"][0]["status"] = "renamed"
        self.assert_human(
            self.run_tier(facts), "scripts/land.sh is renamed with no previous_path"
        )

    def test_a_file_without_a_known_status_is_human(self):
        for status in (None, "moved"):
            with self.subTest(status=status):
                facts = pr()
                if status is None:
                    del facts["files"][0]["status"]
                else:
                    facts["files"][0]["status"] = status
                self.assert_human(
                    self.run_tier(facts),
                    "input invalid: scripts/helper.py: `status` must be one of",
                )

    def test_graphql_upper_case_status_is_understood(self):
        facts = pr(files=[("scripts/ok.py", 0, 0)])
        facts["files"][0]["status"] = "RENAMED"
        self.assert_human(
            self.run_tier(facts), "scripts/ok.py is renamed with no previous_path"
        )

    def test_a_rename_within_auto_globs_stays_auto(self):
        facts = pr(files=[("scripts/new.py", 0, 0)])
        facts["files"][0].update(status="renamed", previous_path="scripts/old.py")
        self.assertEqual(self.run_tier(facts)["tier"], "auto")

    def test_glob_forms(self):
        table = [
            ("scripts/*.py", "scripts/a.py", True),
            ("scripts/*.py", "scripts/x/a.py", False),
            ("a?.md", "ab.md", True),
            ("a?.md", "a/.md", False),
            ("docs/**", "docs/x/y/z.md", True),
            ("**/README.md", "README.md", True),
            ("**/README.md", "x/y/README.md", True),
            ("**/README.md", "xREADME.md", False),
        ]
        for glob, path, expected in table:
            with self.subTest(glob=glob, path=path):
                self.assertEqual(bool(tier_mod.glob_regex(glob).match(path)), expected)

    def test_inline_comment_is_stripped(self):
        policy = POLICY.replace("  - tests/*.py", "  - tests/*.py  # unit tests")
        self.assertEqual(
            self.run_tier(pr(files=[("tests/a.py", 1, 0)]), policy=policy)["tier"],
            "auto",
        )

    def test_a_copy_of_an_always_human_path_is_human(self):
        facts = pr(files=[("scripts/land-copy.sh", 1, 0)])
        facts["files"][0].update(status="copied", previous_path="scripts/deploy.sh")
        self.assert_human(
            self.run_tier(facts), "scripts/deploy.sh matches always_human"
        )

    def test_unconventional_title_on_a_prompt_path_gives_one_title_reason(self):
        policy = POLICY.replace('  - "scripts/**"', '  - "skills/**"')
        result = self.run_tier(
            pr(files=[("skills/x.md", 1, 0)], title="bad"), policy=policy
        )
        self.assert_human(result, "title is not a Conventional Commit")
        self.assertFalse(
            any("`None`" in r for r in result["reasons"]), result["reasons"]
        )

    def test_pr_json_from_stdin(self):
        policy_file = self.tmp / "p.yml"
        policy_file.write_text(POLICY)
        out = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(json.dumps(pr()))),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(
                tier_mod.main(["--pr-json", "-", "--policy", str(policy_file)]), 0
            )
        self.assertEqual(out.getvalue().splitlines()[-1], "TIER: auto")

    def test_negative_or_bool_line_counts_are_human(self):
        for bad in (-900, True):
            with self.subTest(bad=bad):
                facts = pr(files=[("scripts/a.py", 1000, 0)])
                facts["files"][0]["deletions"] = bad
                self.assert_human(self.run_tier(facts), "input invalid")

    def test_non_utf8_policy_or_input_is_human(self):
        bad = self.tmp / "bad.bin"
        bad.write_bytes(b"auto: [\xff]\n")
        good_pr = self.tmp / "good.json"
        good_pr.write_text(json.dumps(pr()))
        good_policy = self.tmp / "good.yml"
        good_policy.write_text(POLICY)
        for argv, reason in (
            (
                ["--pr-json", str(good_pr), "--policy", str(bad)],
                "policy invalid: cannot decode",
            ),
            (
                ["--pr-json", str(bad), "--policy", str(good_policy)],
                "input invalid: cannot read PR input",
            ),
        ):
            with self.subTest(reason=reason):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(tier_mod.main(argv), 0)
                self.assert_human(json.loads(out.getvalue().splitlines()[0]), reason)

    def test_missing_required_flag_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                tier_mod.main(["--pr-json", "-"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
