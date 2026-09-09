#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/_body_refs.py.

Pins the fixed phrase -> direction/strength table `gh-issue-reoptimize.md`
and `linear-reoptimize.md` Dimensions 1-2 both used to hand-walk, plus the
three behaviors the card that extracted this module names explicitly:
`unblocks` reverses direction, a self-mention is excluded, and a mention
inside a code span is excluded (decided and pinned here, not left to a
future re-derivation).
"""

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "_body_refs.py"

_spec = importlib.util.spec_from_file_location("body_refs", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
body_refs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(body_refs)

# The two real id_pattern shapes this module is called with.
GH_ID_PATTERN = re.compile(r"(?<![\w./-])#(?P<num>\d+)\b")
LINEAR_ID_PATTERN = re.compile(
    r'<issue\b[^>]*\bid="(?P<tag_id>[A-Z]+-\d+)"[^>]*>'
    r"|(?<![\w-])(?P<bare_id>[A-Z]+-\d+)(?![\w-])"
)


class StrongPhrasesTests(unittest.TestCase):
    """Every strong phrase (except `unblocks`, its own test) -> blocked_by."""

    def _one(self, body):
        refs = body_refs.parse(body, "1", GH_ID_PATTERN)
        self.assertEqual(len(refs), 1, refs)
        return refs[0]

    def test_blocked_on(self):
        ref = self._one("This is blocked on #2 finishing first.")
        self.assertEqual(ref["target"], "2")
        self.assertEqual(ref["direction"], "blocked_by")
        self.assertEqual(ref["strength"], "strong")

    def test_blocked_by(self):
        ref = self._one("Work here is blocked by #2.")
        self.assertEqual(ref["direction"], "blocked_by")
        self.assertEqual(ref["strength"], "strong")

    def test_relies_on(self):
        ref = self._one("This relies on #2 landing first.")
        self.assertEqual(ref["direction"], "blocked_by")
        self.assertEqual(ref["strength"], "strong")

    def test_depends_on(self):
        ref = self._one("Depends on #2 for the shared schema.")
        self.assertEqual(ref["direction"], "blocked_by")
        self.assertEqual(ref["strength"], "strong")

    def test_requires(self):
        ref = self._one("This requires #2 to be merged first.")
        self.assertEqual(ref["direction"], "blocked_by")
        self.assertEqual(ref["strength"], "strong")

    def test_with_x_in_place(self):
        ref = self._one("This only works with #2 in place.")
        self.assertEqual(ref["target"], "2")
        self.assertEqual(ref["direction"], "blocked_by")
        self.assertEqual(ref["strength"], "strong")
        self.assertEqual(ref["phrase"], "with X in place")


class WeakPhrasesTests(unittest.TestCase):
    def _one(self, body):
        refs = body_refs.parse(body, "1", GH_ID_PATTERN)
        self.assertEqual(len(refs), 1, refs)
        return refs[0]

    def test_re_scoped_per(self):
        ref = self._one("This was re-scoped per #2's discussion.")
        self.assertEqual(ref["direction"], "related")
        self.assertEqual(ref["strength"], "weak")
        self.assertEqual(ref["phrase"], "re-scoped per")

    def test_part_of_plan(self):
        ref = self._one("This is part of #2's rollout plan.")
        self.assertEqual(ref["direction"], "related")
        self.assertEqual(ref["strength"], "weak")
        self.assertEqual(ref["phrase"], "part of … plan")

    def test_bare_mention(self):
        ref = self._one("See #2 for context on the schema.")
        self.assertEqual(ref["direction"], "related")
        self.assertEqual(ref["strength"], "weak")
        self.assertEqual(ref["phrase"], "mention")


class UnblocksReversalTests(unittest.TestCase):
    """`unblocks` is the one phrase that points the other way."""

    def test_unblocks_reverses_direction(self):
        refs = body_refs.parse("Landing this unblocks #2.", "1", GH_ID_PATTERN)
        self.assertEqual(len(refs), 1, refs)
        ref = refs[0]
        self.assertEqual(ref["target"], "2")
        self.assertEqual(ref["direction"], "blocks")
        self.assertEqual(ref["strength"], "strong")
        self.assertEqual(ref["phrase"], "unblocks")

    def test_unblocks_differs_from_blocked_by(self):
        """The same target, phrased both ways, must not collapse to one direction."""
        unblocks = body_refs.parse("Landing this unblocks #2.", "1", GH_ID_PATTERN)
        blocked_by = body_refs.parse("This is blocked by #2.", "1", GH_ID_PATTERN)
        self.assertNotEqual(unblocks[0]["direction"], blocked_by[0]["direction"])


class SelfExclusionTests(unittest.TestCase):
    def test_self_mention_excluded(self):
        refs = body_refs.parse("This is #1, and #1 depends on #2.", "1", GH_ID_PATTERN)
        self.assertEqual([r["target"] for r in refs], ["2"])

    def test_self_exclusion_is_case_insensitive_for_linear_ids(self):
        refs = body_refs.parse(
            "This is pre-142, part of PRE-142's own plan.",
            "PRE-142",
            LINEAR_ID_PATTERN,
        )
        self.assertEqual(refs, [])


class CodeSpanTests(unittest.TestCase):
    """A mention inside a code span is excluded — decided and pinned here."""

    def test_inline_code_span_excluded(self):
        refs = body_refs.parse(
            "Example: `blocked by #2` is how you'd write it.", "1", GH_ID_PATTERN
        )
        self.assertEqual(refs, [])

    def test_fenced_code_block_excluded(self):
        body = "See below:\n```\nblocked by #2\n```\nNo real reference here."
        refs = body_refs.parse(body, "1", GH_ID_PATTERN)
        self.assertEqual(refs, [])

    def test_mention_outside_code_span_still_found(self):
        body = "`#3` is just an example. This one actually depends on #2."
        refs = body_refs.parse(body, "1", GH_ID_PATTERN)
        self.assertEqual([r["target"] for r in refs], ["2"])


class LinearIdPatternTests(unittest.TestCase):
    def test_tag_mention(self):
        body = 'Blocked by <issue id="PRE-189" href="https://x/PRE-189/y">this</issue>.'
        refs = body_refs.parse(body, "PRE-210", LINEAR_ID_PATTERN)
        self.assertEqual(len(refs), 1, refs)
        self.assertEqual(refs[0]["target"], "PRE-189")
        self.assertEqual(refs[0]["direction"], "blocked_by")

    def test_bare_identifier_mention(self):
        refs = body_refs.parse(
            "This unblocks PRE-189 once it lands.", "PRE-210", LINEAR_ID_PATTERN
        )
        self.assertEqual(len(refs), 1, refs)
        self.assertEqual(refs[0]["target"], "PRE-189")
        self.assertEqual(refs[0]["direction"], "blocks")

    def test_no_false_match_inside_longer_token(self):
        refs = body_refs.parse(
            "See subPRE-189x for unrelated context.", "PRE-210", LINEAR_ID_PATTERN
        )
        self.assertEqual(refs, [])


class CrossRepoExclusionTests(unittest.TestCase):
    """gh-issue's pattern refuses a repo-qualified mention entirely."""

    def test_qualified_mention_not_matched(self):
        refs = body_refs.parse("See other/repo#2 for prior art.", "1", GH_ID_PATTERN)
        self.assertEqual(refs, [])


if __name__ == "__main__":
    unittest.main()
