#!/usr/bin/env python3
"""Hermetic tests for scripts/tier-coverage.py.

The checker exists because two tiering mistakes shipped in one PR — a
consumer-executed file pinned to the contributor floor, and two entrypoints in
no tier at all. Each case below reproduces one of those incidents against the
pure `classify()`, so the checker is proven to fail on the exact drift it was
written for rather than merely to say OK.

`classify()` and `patterns()` take their inputs directly, so nothing here needs
a repository fixture, a subprocess, or a temp checkout.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tier-coverage.py"

_spec = importlib.util.spec_from_file_location("tier_coverage", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
tier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tier)

TIERS = {
    "STRICT_FILES": ["scripts/research-spike.py"],
    "CONSUMER_FILES": [
        "commands/handlers/assets/*.py",
        "scripts/local-review/server.py",
    ],
    "DEV_FILES": ["scripts/validate.py"],
    "TEST_FILES": ["scripts/test_*.py"],
    "EXCLUDED_FROM_TYPECHECK": ["skills/analysis-pipeline/example/*.py"],
}


class TestClassify(unittest.TestCase):
    def test_a_file_in_exactly_one_tier_is_clean(self):
        uncovered, doubled = tier.classify(
            [
                "scripts/research-spike.py",
                "commands/handlers/assets/gh-issue-rollups.py",
                "scripts/local-review/server.py",
                "scripts/validate.py",
                "scripts/test_shape.py",
                "skills/analysis-pipeline/example/model.py",
            ],
            TIERS,
        )
        self.assertEqual(uncovered, [])
        self.assertEqual(doubled, [])

    def test_the_A4_incident_an_entrypoint_in_no_tier(self):
        """`scripts/bump-version.py` matched none of the arrays, so mypy never
        read it and the gate stayed green."""
        uncovered, doubled = tier.classify(["scripts/bump-version.py"], TIERS)
        self.assertEqual(uncovered, ["scripts/bump-version.py"])
        self.assertEqual(doubled, [])

    def test_the_A3_incident_a_file_in_two_tiers(self):
        """The tiers pin different --python-version floors, so a double-listed
        file is checked twice and one verdict is silently discarded."""
        tiers = dict(TIERS)
        tiers["DEV_FILES"] = TIERS["DEV_FILES"] + ["scripts/local-review/server.py"]
        uncovered, doubled = tier.classify(["scripts/local-review/server.py"], tiers)
        self.assertEqual(uncovered, [])
        self.assertEqual(len(doubled), 1)
        path, hits = doubled[0]
        self.assertEqual(path, "scripts/local-review/server.py")
        self.assertCountEqual(hits, ["CONSUMER_FILES", "DEV_FILES"])

    def test_an_excluded_file_counts_as_covered_not_uncovered(self):
        """Exclusion is a declaration, not a silence — but it must still satisfy
        the partition, or the array would be pointless."""
        uncovered, _ = tier.classify(
            ["skills/analysis-pipeline/example/fill_templates.py"], TIERS
        )
        self.assertEqual(uncovered, [])

    def test_removing_the_exclusion_makes_those_files_uncovered(self):
        tiers = dict(TIERS)
        tiers["EXCLUDED_FROM_TYPECHECK"] = []
        uncovered, _ = tier.classify(
            ["skills/analysis-pipeline/example/model.py"], tiers
        )
        self.assertEqual(uncovered, ["skills/analysis-pipeline/example/model.py"])

    def test_a_glob_does_not_match_a_deeper_path(self):
        """`commands/handlers/assets/*.py` must not swallow a file in a
        subdirectory — that would hide it in a tier it was never checked by.

        Note this direction was never the risk: `*` does not cross `/` under
        either matcher. The sibling case below is the one that caught a bug."""
        uncovered, _ = tier.classify(
            ["commands/handlers/assets/nested/thing.py"], TIERS
        )
        self.assertEqual(uncovered, ["commands/handlers/assets/nested/thing.py"])

    def test_a_tier_pattern_is_anchored_at_the_repository_root(self):
        """The direction that actually broke. `Path.match()` right-matches a
        relative pattern, so `fixtures/scripts/test_new.py` matched
        `scripts/test_*.py` and was reported covered while mypy never read it —
        a false negative in the checker written to prevent false coverage.

        The test above named itself as if it covered anchoring and did not,
        which is how the bug survived being written and reviewed."""
        for path in (
            "fixtures/scripts/test_new.py",
            "vendor/commands/handlers/assets/x.py",
            "test/vendor/scripts/test_helper.py",
        ):
            with self.subTest(path=path):
                uncovered, _ = tier.classify([path], TIERS)
                self.assertEqual(
                    uncovered, [path], f"{path} must not match a root-anchored tier"
                )


class TestMatches(unittest.TestCase):
    """`matches()` is the anchoring rule itself, tested directly."""

    def test_exact_path(self):
        self.assertTrue(tier.matches("scripts/validate.py", "scripts/validate.py"))

    def test_glob_in_the_last_segment(self):
        self.assertTrue(tier.matches("scripts/test_shape.py", "scripts/test_*.py"))

    def test_leading_directory_is_not_ignored(self):
        self.assertFalse(tier.matches("x/scripts/test_shape.py", "scripts/test_*.py"))

    def test_star_does_not_cross_a_separator(self):
        self.assertFalse(tier.matches("a/b/c.py", "a/*.py"))

    def test_a_shorter_path_does_not_match_a_longer_pattern(self):
        self.assertFalse(tier.matches("test_x.py", "scripts/test_*.py"))


class TestPatterns(unittest.TestCase):
    """`patterns()` parses the real bash arrays, so a formatting change in
    typecheck.sh that it cannot read would make every file look uncovered."""

    def test_reads_a_multiline_array(self):
        source = "DEV_FILES=(\n  scripts/a.py\n  scripts/b.py\n)\n"
        self.assertEqual(
            tier.patterns(source, "DEV_FILES"), ["scripts/a.py", "scripts/b.py"]
        )

    def test_reads_a_single_line_array(self):
        source = "TEST_FILES=(scripts/test_*.py)\n"
        self.assertEqual(tier.patterns(source, "TEST_FILES"), ["scripts/test_*.py"])

    def test_strips_trailing_comments(self):
        source = "DEV_FILES=(\n  scripts/a.py  # why this one\n)\n"
        self.assertEqual(tier.patterns(source, "DEV_FILES"), ["scripts/a.py"])

    def test_an_absent_array_is_empty_not_an_error(self):
        self.assertEqual(tier.patterns("NOTHING=(x)\n", "DEV_FILES"), [])

    def test_the_real_typecheck_script_still_parses(self):
        """The end-to-end guard: if typecheck.sh is reformatted such that these
        arrays stop parsing, every array reads empty and the checker's own
        empty-array guard is the only thing standing between that and a
        vacuous OK."""
        source = (ROOT / "scripts" / "typecheck.sh").read_text()
        for name in tier.ARRAYS:
            with self.subTest(array=name):
                self.assertTrue(tier.patterns(source, name), f"{name} parsed as empty")


if __name__ == "__main__":
    unittest.main(verbosity=2)
