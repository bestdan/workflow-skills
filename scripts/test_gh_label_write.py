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
    """Records every `gh` invocation; an empty log means no network was touched."""

    def __init__(self):
        self.calls = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        return 0, "{}", ""


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
        self.assertEqual(len(self.recorder.calls), 1, "must be exactly one request")

        args, stdin = self.recorder.calls[0]
        self.assertEqual(
            args[:4], ["api", "--method", "PATCH", "repos/owner/name/issues/142"]
        )
        self.assertEqual(
            json.loads(stdin)["labels"],
            ["status:3_started", "auto:eligible", "prio:1", "est:3"],
        )

    def test_no_incremental_label_flag_is_ever_used(self):
        self._write(VALID)
        flat = " ".join(part for args, _ in self.recorder.calls for part in args)
        self.assertNotIn("--add-label", flat)
        self.assertNotIn("--remove-label", flat)
        # Prose in this file names the banned flags to explain the ban, so a
        # source scan would fire on its own documentation. The argv assertion
        # above is the real guard: it fails if a flag ever reaches `gh`.

    def test_done_write_requires_the_status_rung_to_be_absent(self):
        code, _, _ = self._write("auto:eligible,prio:1,est:3", extra=["--done"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.recorder.calls), 1)

        code, _, err = self._write(VALID, extra=["--done"])
        self.assertEqual(code, 2)
        self.assertIn("--done means no status rung", err)

    def test_report_only_run_sends_nothing(self):
        code, out, _ = self._main(
            ["--repo", "owner/name", "--issue", "142", "--labels", VALID]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.recorder.calls, [])
        self.assertIn("Would write", out)


if __name__ == "__main__":
    unittest.main()
