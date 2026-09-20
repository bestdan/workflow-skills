#!/usr/bin/env python3
"""Hermetic tests for the jev-description-collision.py beside this file.

The pure half only: frontmatter parsing, manifest parsing, the ranking maths, the
collision threshold, and the key-resolution ladder. Nothing here touches the network
or reads a real key. There is deliberately no live counterpart: nothing in this repo
depends on the Jev API, so a standing test against it would be surface with no
dependency behind it. Check the request and response shapes by running the
instrument when that changes.

Each case reproduces something that actually bit during the section-1 measurement,
rather than restating the implementation.

No gate runs this: it is a frozen artifact of the record in the parent directory, and
leaving the repo's `scripts/test-*.sh` glob is the cost of that. Run it by path —
`python3 dev_docs/research/2026-09-17-jev-applications/references/test_jev_description_collision.py`
— when re-running the measurement. Dependencies: the standard library only.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The bundle sits four levels below the repository root:
# dev_docs/research/<record>/references/<this file>.
ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "jev_collision", HERE / "jev-description-collision.py"
)
assert SPEC and SPEC.loader
jev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jev)


class RankTests(unittest.TestCase):
    def test_margin_is_winner_minus_runner_up(self):
        w, pw, r, pr, margin = jev.rank({"a": 0.63, "b": 0.37, "c": 0.0})
        self.assertEqual((w, r), ("a", "b"))
        self.assertAlmostEqual(pw, 0.63)
        self.assertAlmostEqual(pr, 0.37)
        self.assertAlmostEqual(margin, 0.26)

    def test_ordering_does_not_depend_on_dict_order(self):
        """The real response arrives in arbitrary order; the winner is by mass."""
        _, _, _, _, a = jev.rank({"z": 0.1, "m": 0.9})
        _, _, _, _, b = jev.rank({"m": 0.9, "z": 0.1})
        self.assertEqual(a, b)

    def test_single_option_has_no_runner_up(self):
        w, pw, r, pr, margin = jev.rank({"only": 1.0})
        self.assertEqual((w, r, pr), ("only", "", 0.0))
        self.assertAlmostEqual(margin, pw)

    def test_empty_is_an_error_not_a_silent_zero(self):
        with self.assertRaises(ValueError):
            jev.rank({})


class CollisionTests(unittest.TestCase):
    def test_below_threshold_is_a_collision(self):
        self.assertTrue(jev.is_collision(0.09, 0.10))

    def test_threshold_itself_is_clean(self):
        """A boundary that lands exactly on the threshold is a separation."""
        self.assertFalse(jev.is_collision(0.10, 0.10))

    def test_the_tightest_measured_margin_is_not_a_collision_at_default(self):
        """0.16 is the lowest margin seen in four runs (local-review vs co-review).

        The whole "no collisions" result rests on that minimum clearing the default
        threshold. If a future run drops below it the record's claim needs revisiting,
        so the number is pinned here rather than left in prose.
        """
        self.assertFalse(jev.is_collision(0.16))


class DescriptionTests(unittest.TestCase):
    def test_folded_scalar_is_joined_into_one_line(self):
        text = (
            "---\nname: co-review\ndescription: >\n  Use when the user wants a\n"
            "  collaborative review of a PR.\n---\n\n# body\n"
        )
        got = jev.parse_descriptions({"a": text})
        self.assertEqual(
            got,
            {"co-review": "Use when the user wants a collaborative review of a PR."},
        )

    def test_literal_scalar_is_also_handled(self):
        text = "---\nname: x\ndescription: |\n  One line.\n  Two line.\n---\n\n#\n"
        self.assertEqual(
            jev.parse_descriptions({"a": text}), {"x": "One line. Two line."}
        )

    def test_plain_one_line_description(self):
        text = "---\nname: y\ndescription: Just this.\n---\n\n#\n"
        self.assertEqual(jev.parse_descriptions({"a": text}), {"y": "Just this."})

    def test_a_file_without_frontmatter_is_skipped_not_fatal(self):
        """One malformed skill must not take the whole run down."""
        good = "---\nname: ok\ndescription: Fine.\n---\n\n#\n"
        self.assertEqual(
            jev.parse_descriptions({"bad": "# no frontmatter\n", "good": good}),
            {"ok": "Fine."},
        )

    def test_every_real_skill_yields_a_description(self):
        """The option set IS the interface; a skill missing from it can never win."""
        files = {
            str(p): p.read_text() for p in sorted((ROOT / "skills").glob("*/SKILL.md"))
        }
        got = jev.parse_descriptions(files)
        self.assertEqual(len(got), len(files))
        self.assertTrue(all(v for v in got.values()))


class ManifestTests(unittest.TestCase):
    def test_comments_and_blanks_are_skipped(self):
        text = "# header\n\nco-review\tprompts/co-review.txt\t6\n# trailing\n"
        self.assertEqual(
            jev.parse_manifest(text), [("co-review", "prompts/co-review.txt")]
        )

    def test_a_skill_may_appear_twice(self):
        """`task` has two cases; deduping rows would silently drop one."""
        text = "task\tprompts/task.txt\t6\ntask\tprompts/task-named-issue.txt\t6\n"
        self.assertEqual(len(jev.parse_manifest(text)), 2)

    def test_real_manifest_rows_all_name_an_existing_prompt(self):
        rows = jev.parse_manifest((ROOT / "evals" / "manifest.tsv").read_text())
        self.assertTrue(rows)
        for _, rel in rows:
            self.assertTrue((ROOT / "evals" / rel).is_file(), rel)


class KeyLadderTests(unittest.TestCase):
    def test_raw_beats_ref(self):
        """Rung 0 of auth_key_access.md: a raw secret wins over a pointer."""
        text = 'typesafe:\n  api_key_ref: "op://v/i/f"\n  api_key: "sk-real"\n'
        self.assertEqual(jev.extract_key(text), ("raw", "sk-real"))

    def test_ref_alone_resolves_to_a_pointer(self):
        self.assertEqual(
            jev.extract_key('typesafe:\n  api_key_ref: "op://v/i/f"\n'),
            ("ref", "op://v/i/f"),
        )

    def test_unfilled_placeholder_is_treated_as_absent(self):
        """The template ships REPLACE_ME; sending it to the API would be worse."""
        self.assertIsNone(
            jev.extract_key(f'typesafe:\n  api_key: "{jev.PLACEHOLDER}"\n')
        )

    def test_placeholder_falls_through_to_the_ref(self):
        text = (
            f'typesafe:\n  api_key: "{jev.PLACEHOLDER}"\n  api_key_ref: "op://v/i/f"\n'
        )
        self.assertEqual(jev.extract_key(text), ("ref", "op://v/i/f"))

    def test_commented_out_key_is_not_read(self):
        self.assertIsNone(jev.extract_key('typesafe:\n  # api_key: "sk-nope"\n'))

    def test_empty_config_yields_nothing(self):
        self.assertIsNone(jev.extract_key("typesafe:\n"))

    def test_another_services_key_is_never_returned(self):
        """The real config holds linear.api_key too; returning it would send a
        full-account Linear token to TypeSafe in an Authorization header."""
        text = (
            'linear:\n  api_key: "lin_api_SECRET"\n'
            'typesafe:\n  api_key: "sk-typesafe"\n'
        )
        self.assertEqual(jev.extract_key(text), ("raw", "sk-typesafe"))

    def test_no_typesafe_block_means_no_key_at_all(self):
        """Not 'fall back to whatever key is in the file'."""
        self.assertIsNone(jev.extract_key('linear:\n  api_key: "lin_api_SECRET"\n'))

    def test_a_nested_typesafe_key_does_not_count_as_the_block(self):
        """Only a top-level `typesafe:` mapping is the TypeSafe config."""
        self.assertIsNone(
            jev.extract_key('other:\n  typesafe:\n    api_key: "sk-nested"\n')
        )


class RedactionTests(unittest.TestCase):
    def test_a_pointer_is_reduced_to_its_vault(self):
        """auth_key_access.md: "Never print a full reference. Reduce it."""
        self.assertEqual(jev.redact_ref("op://Private/TypeSafe/key"), "op://Private/…")

    def test_something_that_is_not_a_full_pointer_is_left_alone(self):
        self.assertEqual(jev.redact_ref("op://Private"), "op://Private")


if __name__ == "__main__":
    unittest.main(verbosity=2)
