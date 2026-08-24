#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-label-write.py.

Stubs the module's run_gh() seam so nothing shells out to `gh` or touches the
network, and records every call so a test can assert that a rejected write made
no call at all. Covers the properties that make the helper safe: an
out-of-vocabulary label is rejected BEFORE the network (a raw REST write would
otherwise create it), the invariants hold, and an accepted write is exactly one
PATCH carrying the complete set — never --add-label/--remove-label.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-label-write.py"

_spec = importlib.util.spec_from_file_location("gh_label_write", ASSET)
gh_label_write = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gh_label_write)

VALID = "status:3_started,auto:eligible,prio:1,est:3"


class Recorder:
    """Records every `gh` invocation; an empty log means no network was touched.

    `existing` is what the issue already carries, so tests can prove the helper
    carries forward labels outside the four managed namespaces instead of
    deleting them with the full-set write.
    """

    def __init__(self, existing=()):
        self.calls = []
        self.existing = list(existing)

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ["issue", "view"]:
            return 0, json.dumps({"labels": [{"name": n} for n in self.existing]}), ""
        return 0, "{}", ""

    def writes(self):
        """Only the mutating calls — reads are not what atomicity is about."""
        return [(a, s) for a, s in self.calls if "--method" in a]

    def written_labels(self):
        return json.loads(self.writes()[0][1])["labels"]


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        self._orig = gh_label_write.run_gh
        gh_label_write.run_gh = self.recorder.run_gh
        self.addCleanup(self._restore)

    def _restore(self):
        gh_label_write.run_gh = self._orig

    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gh_label_write.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _write(self, labels, extra=()):
        return self._main(
            [
                "--repo",
                "owner/name",
                "--issue",
                "142",
                "--labels",
                labels,
                "--apply",
                *extra,
            ]
        )

    def test_out_of_vocabulary_label_is_rejected_before_any_network_call(self):
        code, _, err = self._write("status:3_started,auto:eligible,est:7")

        self.assertEqual(code, 2)
        self.assertEqual(
            self.recorder.calls, [], "a rejected write must not reach the network"
        )
        self.assertIn("est:7", err)
        self.assertIn("labels.yml", err)

    def test_two_status_labels_are_rejected(self):
        code, _, err = self._write("status:2_ready,status:3_started,auto:eligible")

        self.assertEqual(code, 2)
        self.assertEqual(self.recorder.calls, [])
        self.assertIn("exactly one `status:`", err)

    def test_missing_auto_label_is_rejected(self):
        code, _, err = self._write("status:3_started")

        self.assertEqual(code, 2)
        self.assertEqual(self.recorder.calls, [])
        self.assertIn("exactly one `auto:`", err)

    def test_two_estimates_are_rejected(self):
        code, _, err = self._write("status:3_started,auto:eligible,est:3,est:5")

        self.assertEqual(code, 2)
        self.assertEqual(self.recorder.calls, [])
        self.assertIn("at most one `est:`", err)

    def test_accepted_write_is_a_single_patch_carrying_the_complete_set(self):
        code, _, _ = self._write(VALID)

        self.assertEqual(code, 0)
        # The read is a second request, but atomicity is about the WRITE.
        self.assertEqual(len(self.recorder.writes()), 1, "must be exactly one write")

        args, stdin = self.recorder.writes()[0]
        self.assertEqual(
            args[:4], ["api", "--method", "PATCH", "repos/owner/name/issues/142"]
        )
        self.assertEqual(
            json.loads(stdin)["labels"],
            ["status:3_started", "auto:eligible", "prio:1", "est:3"],
        )

    def test_labels_outside_the_managed_namespaces_are_carried_forward(self):
        """The full-set write must not delete what this helper does not own.

        `follow-up` is the handler's own marker — gh-issue.md applies it and
        /archive-tasks refuses to sweep without it — and it can never live in
        labels.yml, so omitting it from the write would silently destroy it.
        """
        self.recorder.existing = ["follow-up", "status:2_ready", "bug"]
        code, out, _ = self._write(VALID)

        self.assertEqual(code, 0)
        written = self.recorder.written_labels()
        # Unmanaged labels survive...
        self.assertIn("follow-up", written)
        self.assertIn("bug", written)
        # ...while the stale managed rung is replaced, not duplicated.
        self.assertNotIn("status:2_ready", written)
        self.assertIn("status:3_started", written)
        self.assertIn("Carried forward", out)

    def test_the_read_happens_after_validation_not_before(self):
        """A bad set must cost zero requests — including the read."""
        self.recorder.existing = ["follow-up"]
        code, _, _ = self._write("status:3_started,auto:eligible,est:7")

        self.assertEqual(code, 2)
        self.assertEqual(self.recorder.calls, [])

    def test_no_incremental_label_flag_is_ever_used(self):
        self._write(VALID)
        flat = " ".join(part for args, _ in self.recorder.calls for part in args)
        self.assertNotIn("--add-label", flat)
        self.assertNotIn("--remove-label", flat)
        # Prose in this file names the banned flags to explain the ban, so a
        # source scan would fire on its own documentation. The argv assertion
        # above is the real guard: it fails if a flag ever reaches `gh`.

    def test_done_write_requires_both_rungs_to_be_absent(self):
        """A closed issue keeps prio/est but carries no status and no auto rung."""
        code, _, _ = self._write("prio:1,est:3", extra=["--done"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.recorder.writes()), 1)

        # Clear first: otherwise the accepted write above is still in the log and
        # the no-network-on-refusal assertion below would pass vacuously.
        self.recorder.calls.clear()

        code, _, err = self._write(VALID, extra=["--done"])
        self.assertEqual(code, 2)
        self.assertEqual(self.recorder.calls, [])
        self.assertIn("--done means no `status:` rung", err)

        self.recorder.calls.clear()
        code, _, err = self._write("auto:eligible,prio:1", extra=["--done"])
        self.assertEqual(code, 2)
        self.assertEqual(self.recorder.calls, [])
        self.assertIn("--done means no `auto:` rung", err)

    def test_report_only_run_mutates_nothing(self):
        """It reads, so the preview is accurate — but it never writes."""
        self.recorder.existing = ["follow-up"]
        code, out, _ = self._main(
            ["--repo", "owner/name", "--issue", "142", "--labels", VALID]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.recorder.writes(), [])
        self.assertIn("Would write", out)
        self.assertIn("follow-up", out)


if __name__ == "__main__":
    unittest.main()
