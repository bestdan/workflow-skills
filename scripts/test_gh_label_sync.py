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
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
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

    def _bad_vocabulary(self, text):
        """Write `text` as a labels.yml and return the path."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "labels.yml"
        path.write_text(text)
        return path

    def test_a_label_list_at_the_cap_refuses_rather_than_truncating(self):
        """`gh label list` has no --paginate, so the cap is the only bound.

        A truncated read is indistinguishable from a complete one, and
        gh-issue-reconcile.py concludes ABSENCE from this list — it voids a whole
        audit rule on it. Concluding anything from a possibly-partial list is the
        silent wrong answer this refusal exists to prevent.
        """
        repo = FakeRepo(labels=[f"l{n}" for n in range(gh_label_sync.LABEL_LIST_LIMIT)])
        gh_label_sync.run_gh = repo.run_gh

        with self.assertRaises(SystemExit) as caught:
            gh_label_sync.existing_labels("owner/name")

        self.assertIn("cap", str(caught.exception))
        self.assertEqual(repo.created(), [])

    def test_a_label_list_below_the_cap_is_returned_normally(self):
        repo = FakeRepo(
            labels=[f"l{n}" for n in range(gh_label_sync.LABEL_LIST_LIMIT - 1)]
        )
        gh_label_sync.run_gh = repo.run_gh

        names = gh_label_sync.existing_labels("owner/name")

        self.assertEqual(len(names), gh_label_sync.LABEL_LIST_LIMIT - 1)

    def test_malformed_vocabulary_raises_rather_than_dropping_a_group(self):
        # A scalar where an inline list belongs.
        bad = self._bad_vocabulary("status: 0_untriaged\n")
        with self.assertRaises(gh_label_sync.VocabularyError):
            gh_label_sync.load_vocabulary(bad)

        # Every group present, but one has no color.
        bad = self._bad_vocabulary(
            "status: [a]\nauto: [b]\nprio: [c]\nest: [d]\n"
            "colors:\n  status: 1d76db\n  auto: 0e8a16\n  prio: d93f0b\n"
        )
        with self.assertRaises(gh_label_sync.VocabularyError) as ctx:
            gh_label_sync.load_vocabulary(bad)
        self.assertIn("est", str(ctx.exception))

    def test_a_group_with_no_values_is_rejected(self):
        """An empty `status: []` would provision nothing while the writer still
        demands exactly one `status:` label — a state model with a hole in it."""
        bad = self._bad_vocabulary(
            "status: []\nauto: [b]\nprio: [c]\nest: [d]\n"
            "colors:\n  status: 1d76db\n  auto: 0e8a16\n  prio: d93f0b\n  est: 5319e2\n"
        )
        with self.assertRaises(gh_label_sync.VocabularyError) as ctx:
            gh_label_sync.load_vocabulary(bad)
        self.assertIn("no values", str(ctx.exception))

    def test_a_misspelled_group_name_is_rejected(self):
        """labels.yml and the writer's EXACTLY_ONE/AT_MOST_ONE are two sources for
        one fact. Unchecked, `stauts:` provisions a dead namespace and then makes
        every write impossible, because validate() still demands `status:`."""
        bad = self._bad_vocabulary(
            "stauts: [a]\nauto: [b]\nprio: [c]\nest: [d]\n"
            "colors:\n  stauts: 1d76db\n  auto: 0e8a16\n  prio: d93f0b\n  est: 5319e2\n"
        )
        with self.assertRaises(gh_label_sync.VocabularyError) as ctx:
            gh_label_sync.load_vocabulary(bad)
        message = str(ctx.exception)
        self.assertIn("stauts", message)
        self.assertIn("status", message)


if __name__ == "__main__":
    unittest.main()
