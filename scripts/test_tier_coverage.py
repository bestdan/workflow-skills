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
import subprocess
import tempfile
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


class TestMatchesAgreesWithBash(unittest.TestCase):
    """Differential test against ground truth.

    Every case above asserts what the author BELIEVES bash does. That belief was
    wrong once already — `Path.match()` right-matches, and the suite happily
    agreed with it. So this class stops asserting beliefs and asks bash.

    A fixture tree is built containing the shapes that broke the original
    matcher, each tier pattern is expanded by a real `bash -c 'shopt -s
    nullglob; printf ...'`, and the result is compared with `matches()`. Any
    divergence means the checker's model of typecheck.sh has drifted from what
    typecheck.sh actually passes to mypy.

    Hermetic: a temp directory, no network, nothing outside it is read.
    """

    # Ordinary layouts plus the adversarial ones. The `fixtures/`, `vendor/` and
    # `a/b/` entries are the prefix shapes that `Path.match()` wrongly accepted.
    FIXTURE = [
        "scripts/validate.py",
        "scripts/test_shape.py",
        "scripts/test_tier_coverage.py",
        "scripts/local-review/server.py",
        "commands/handlers/assets/x.py",
        "commands/handlers/assets/nested/deep.py",
        "fixtures/scripts/test_new.py",
        "vendor/commands/handlers/assets/y.py",
        "a/b/scripts/test_z.py",
    ]

    PATTERNS = [
        "scripts/test_*.py",
        "commands/handlers/assets/*.py",
        "scripts/*.py",
        "scripts/validate.py",
        "scripts/local-review/server.py",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for rel in self.FIXTURE:
            f = self.root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("# fixture\n")

    def bash_expand(self, pattern):
        """Which FIXTURE files bash selects for this pattern.

        Intersected with the fixture on purpose. `nullglob` applies only to
        words containing a wildcard: a literal like `scripts/research-spike.py`
        is not subject to pathname expansion at all, so bash echoes it back even
        when no such file exists (verified). typecheck.sh relies on exactly that
        — it passes a literal to mypy whether or not it resolves — but the
        question here is which EXISTING files each method selects, so a literal
        naming an absent file must count for neither.
        """
        proc = subprocess.run(
            ["bash", "-c", f'shopt -s nullglob; printf "%s\\n" {pattern}'],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        expanded = {p for p in proc.stdout.split() if p}
        return sorted(expanded & set(self.FIXTURE))

    def test_the_fixture_still_contains_the_adversarial_shapes(self):
        """Without these, every comparison below passes under either matcher and
        the class becomes decoration. Trimming the fixture must fail here."""
        for shape in (
            "fixtures/scripts/test_new.py",
            "vendor/commands/handlers/assets/y.py",
            "commands/handlers/assets/nested/deep.py",
        ):
            self.assertIn(shape, self.FIXTURE)

    def test_every_tier_pattern_expands_the_same_way(self):
        for pattern in self.PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertEqual(
                    sorted(f for f in self.FIXTURE if tier.matches(f, pattern)),
                    self.bash_expand(pattern),
                    f"matches() and bash disagree on {pattern!r}",
                )

    def test_the_real_tier_patterns_expand_the_same_way(self):
        """The fixture proves the rule; this proves it for the patterns actually
        in typecheck.sh, so a newly added pattern of an unanticipated shape is
        covered without anyone remembering to extend PATTERNS."""
        source = (ROOT / "scripts" / "typecheck.sh").read_text()
        real = [p for name in tier.ARRAYS for p in tier.patterns(source, name)]
        self.assertTrue(real, "no patterns parsed — the comparison would be vacuous")
        for pattern in real:
            with self.subTest(pattern=pattern):
                self.assertEqual(
                    sorted(f for f in self.FIXTURE if tier.matches(f, pattern)),
                    self.bash_expand(pattern),
                    f"matches() and bash disagree on {pattern!r}",
                )


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
