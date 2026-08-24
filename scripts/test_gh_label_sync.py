#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-label-sync.py.

Stubs the module's run_gh() seam so nothing shells out to `gh` or touches the
network. Covers the three properties the sync has to hold for label provisioning
to be safe: it is idempotent (a second run creates nothing and exits 0), it
reports but never deletes a label it does not recognise, and it takes every label
name from labels.yml rather than from a literal in its own source.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-label-sync.py"
LABELS_FILE = ROOT / "commands" / "handlers" / "assets" / "labels.yml"

_spec = importlib.util.spec_from_file_location("gh_label_sync", ASSET)
gh_label_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gh_label_sync)


class FakeRepo:
    """A repo's label set, driven through the same `gh` argv the script emits."""

    def __init__(self, labels=()):
        self.labels = list(labels)
        self.calls = []

    def run_gh(self, args):
        self.calls.append(args)
        if args[:2] == ["label", "list"]:
            return 0, json.dumps([{"name": n} for n in self.labels]), ""
        if args[:2] == ["label", "create"]:
            name = args[2]
            if name in self.labels:
                return 1, "", f"label {name} already exists"
            self.labels.append(name)
            return 0, "", ""
        raise AssertionError(f"unexpected gh call: {args}")

    def created(self):
        return [c[2] for c in self.calls if c[:2] == ["label", "create"]]

    def deleted(self):
        return [c for c in self.calls if "delete" in c]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._orig_run_gh = gh_label_sync.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_label_sync.run_gh = self._orig_run_gh

    def _main(self, repo, argv):
        gh_label_sync.run_gh = repo.run_gh
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gh_label_sync.main(argv)
        return code, out.getvalue()

    def test_second_run_creates_nothing_and_exits_zero(self):
        repo = FakeRepo()
        code, _ = self._main(repo, ["--repo", "owner/name", "--apply"])
        self.assertEqual(code, 0)
        first_pass = repo.created()
        self.assertEqual(len(first_pass), 17)

        repo.calls.clear()
        code, text = self._main(repo, ["--repo", "owner/name", "--apply"])
        self.assertEqual(code, 0)
        self.assertEqual(repo.created(), [])
        self.assertIn("No changes", text)

    def test_unknown_label_is_reported_and_not_deleted(self):
        repo = FakeRepo(["bug", "good first issue"])
        code, text = self._main(repo, ["--repo", "owner/name", "--apply"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.deleted(), [])
        self.assertIn("bug", text)
        self.assertIn("good first issue", text)
        self.assertIn("left untouched", text)
        # Still present afterwards, and still not part of the vocabulary.
        self.assertIn("bug", repo.labels)

    def test_report_only_run_mutates_nothing(self):
        repo = FakeRepo()
        code, text = self._main(repo, ["--repo", "owner/name"])

        self.assertEqual(code, 0)
        self.assertEqual(repo.created(), [])
        self.assertEqual(repo.labels, [])
        self.assertIn("Would create 17", text)

    def test_vocabulary_is_the_schema_from_the_requirements(self):
        groups, colors = gh_label_sync.load_vocabulary(LABELS_FILE)
        self.assertEqual(set(groups), {"status", "auto", "prio", "est"})
        self.assertEqual(
            groups["status"],
            [
                "0_untriaged",
                "1_needs_refinement",
                "2_ready",
                "3_started",
                "4_needs_review",
            ],
        )
        self.assertEqual(groups["auto"], ["eligible", "human-review-needed"])
        self.assertEqual(groups["prio"], ["0", "1", "2", "3"])
        self.assertEqual(groups["est"], ["1", "2", "3", "5", "8", "13"])
        self.assertEqual(set(colors), set(groups))

        names = gh_label_sync.expected_labels(groups, colors)
        self.assertEqual(len(names), 17)
        self.assertIn("status:2_ready", names)
        self.assertIn("auto:human-review-needed", names)

    def test_no_label_name_is_hardcoded_in_the_script(self):
        """labels.yml is the single source — the script must not restate it."""
        source = ASSET.read_text()
        groups, colors = gh_label_sync.load_vocabulary(LABELS_FILE)
        for name in gh_label_sync.expected_labels(groups, colors):
            self.assertNotIn(name, source, f"{name} is hardcoded in {ASSET.name}")
        for color in set(colors.values()):
            self.assertNotIn(
                color, source, f"color {color} is hardcoded in {ASSET.name}"
            )

    def test_malformed_vocabulary_raises_rather_than_dropping_a_group(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bad = Path(tmp.name) / "labels.yml"
        bad.write_text("status: 0_untriaged\n")
        with self.assertRaises(gh_label_sync.VocabularyError):
            gh_label_sync.load_vocabulary(bad)

        bad.write_text("status: [a, b]\n")
        with self.assertRaises(gh_label_sync.VocabularyError):
            gh_label_sync.load_vocabulary(bad)


if __name__ == "__main__":
    unittest.main()
