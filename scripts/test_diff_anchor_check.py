#!/usr/bin/env python3
"""Hermetic tests for scripts/diff-anchor-check.py.

Loads the script by file path (it has a hyphenated name, so it isn't a valid
module) and drives its pure functions directly — no subprocess, no network,
no filesystem beyond what the test builds itself.

Five cases, per the card:
  1. a multi-hunk diff — a comment on an added line in the second hunk anchors
  2. a comment on a deleted line does not anchor
  3. a comment on a context line anchors
  4. a renamed file — a comment against the new path anchors
  5. a comment on a file the diff doesn't mention does not anchor
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diff-anchor-check.py"

_spec = importlib.util.spec_from_file_location("diff_anchor_check", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
diff_anchor_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diff_anchor_check)


MULTI_HUNK_DIFF = """\
diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,4 +1,4 @@
 one
-two
+TWO
 three
 four
@@ -10,3 +10,4 @@
 ten
 eleven
 twelve
+thirteen
@@ -20,4 +20,3 @@
 twenty
 twentyone
 twentytwo
-twentythree
"""

RENAME_DIFF = """\
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
index 3333333..4444444 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,3 +1,4 @@
 alpha
 beta
+gamma
 delta
"""


class DiffAnchorCheckTests(unittest.TestCase):
    def test_multi_hunk_added_line_in_second_hunk_anchors(self):
        files = diff_anchor_check._load_parse_diff()(MULTI_HUNK_DIFF)
        anchors = diff_anchor_check.right_side_anchors(files)
        comments = [{"path": "foo.py", "line": 13, "body": "second hunk add"}]
        anchored, unanchored = diff_anchor_check.split_comments(comments, anchors)
        self.assertEqual(anchored, comments)
        self.assertEqual(unanchored, [])

    def test_deleted_line_does_not_anchor(self):
        files = diff_anchor_check._load_parse_diff()(MULTI_HUNK_DIFF)
        anchors = diff_anchor_check.right_side_anchors(files)
        # "twentythree" (old-side line 23) is a pure trailing deletion in the
        # third hunk with no compensating add, so the right side of that hunk
        # tops out at 22 ("twentytwo") — 23 is not a valid anchor anywhere.
        comments = [{"path": "foo.py", "line": 23, "body": "on a deleted line"}]
        anchored, unanchored = diff_anchor_check.split_comments(comments, anchors)
        self.assertEqual(anchored, [])
        self.assertEqual(unanchored, comments)

    def test_context_line_anchors(self):
        files = diff_anchor_check._load_parse_diff()(MULTI_HUNK_DIFF)
        anchors = diff_anchor_check.right_side_anchors(files)
        # "three" is unchanged context right after the replaced line.
        comments = [{"path": "foo.py", "line": 3, "body": "context line"}]
        anchored, unanchored = diff_anchor_check.split_comments(comments, anchors)
        self.assertEqual(anchored, comments)
        self.assertEqual(unanchored, [])

    def test_renamed_file_anchors_against_new_path(self):
        files = diff_anchor_check._load_parse_diff()(RENAME_DIFF)
        anchors = diff_anchor_check.right_side_anchors(files)
        comments = [{"path": "new_name.py", "line": 3, "body": "added in rename"}]
        anchored, unanchored = diff_anchor_check.split_comments(comments, anchors)
        self.assertEqual(anchored, comments)
        self.assertEqual(unanchored, [])

    def test_file_not_in_diff_does_not_anchor(self):
        files = diff_anchor_check._load_parse_diff()(MULTI_HUNK_DIFF)
        anchors = diff_anchor_check.right_side_anchors(files)
        comments = [{"path": "unrelated.py", "line": 1, "body": "wrong file"}]
        anchored, unanchored = diff_anchor_check.split_comments(comments, anchors)
        self.assertEqual(anchored, [])
        self.assertEqual(unanchored, comments)


if __name__ == "__main__":
    unittest.main()
