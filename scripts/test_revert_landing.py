#!/usr/bin/env python3
"""Hermetic tests for scripts/revert-landing.py.

`gh` is replaced by FakeGh, which answers from recorded JSON shaped like the
real `gh pr view`, `gh pr list`, commit, `revertPullRequest` and PR-files
responses, and records the mutation it was sent.
"""

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "revert_landing", ROOT / "scripts" / "revert-landing.py"
)
assert _spec is not None and _spec.loader is not None
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)
land = rl.land

REPO = "acme/widgets"
SHA = "c" * 40
LANDED = "fix(scripts): tidy (#7)\n\nLanded-by: land-pr.sh\nCo-review-head: abc\n"
ORIGINAL_FILES = [
    {"filename": "scripts/a.py", "additions": 10, "deletions": 2, "status": "modified"},
    {
        "filename": "scripts/new.py",
        "previous_filename": "scripts/old.py",
        "additions": 1,
        "deletions": 0,
        "status": "renamed",
    },
]
REVERT_FILES = [
    {"filename": "scripts/a.py", "additions": 2, "deletions": 10, "status": "modified"},
    {
        "filename": "scripts/old.py",
        "previous_filename": "scripts/new.py",
        "additions": 0,
        "deletions": 1,
        "status": "renamed",
    },
]


class FakeGh:
    def __init__(self):
        self.pr = {
            "id": "PR_node7",
            "number": 7,
            "title": "fix(scripts): tidy",
            "state": "MERGED",
            "mergeCommit": {"oid": SHA},
            "url": f"https://github.com/{REPO}/pull/7",
        }
        self.message = LANDED
        self.open_prs = []
        self.files = {7: ORIGINAL_FILES, 8: REVERT_FILES}
        self.empty_reads = 0  # revert-file reads that return no files first
        self.fail = set()
        self.mutations = []

    def __call__(self, args, stdin=None):
        joined = " ".join(args)
        for bad in self.fail:
            if bad in joined:
                return 1, "", "HTTP 500"
        if args[:2] == ["pr", "view"]:
            return 0, json.dumps(self.pr), ""
        if args[:2] == ["pr", "list"]:
            return 0, json.dumps(self.open_prs), ""
        if args[:2] == ["api", "graphql"]:
            fields = dict(
                a.split("=", 1) for a in args if "=" in a and not a.startswith("query=")
            )
            self.mutations.append(fields)
            made = {"number": 8, "url": f"https://github.com/{REPO}/pull/8"}
            return (
                0,
                json.dumps(
                    {"data": {"revertPullRequest": {"revertPullRequest": made}}}
                ),
                "",
            )
        endpoint = next(a for a in args if a.startswith("repos/"))
        if "/commits/" in endpoint:
            return 0, self.message, ""
        if endpoint.endswith("/files"):
            number = int(endpoint.split("/")[4])
            if number == 8 and self.empty_reads:
                self.empty_reads -= 1
                return 0, json.dumps([[]]), ""
            return 0, json.dumps([self.files.get(number, [])]), ""
        raise AssertionError(f"unexpected gh call: {joined}")


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGh()
        self.slept: list = []
        for name, value in (("run_gh", self.fake), ("sleep", self.slept.append)):
            self.addCleanup(setattr, land, name, getattr(land, name))
            setattr(land, name, value)

    def run_script(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rl.main(["7", "--repo", REPO, *extra])
        return code, out.getvalue().splitlines()


class RevertTests(Base):
    def test_opens_a_fix_titled_revert_and_checks_it_reverses_the_commit(self):
        code, lines = self.run_script("--reason", "broke the gate")
        self.assertEqual(code, 0)
        self.assertEqual(
            lines,
            [
                f"REVERT: opened https://github.com/{REPO}/pull/8",
                f"CHECK: reverses {SHA[:7]}",
            ],
        )
        (sent,) = self.fake.mutations
        self.assertEqual(sent["title"], "fix: revert #7 (fix(scripts): tidy)")
        self.assertEqual(sent["id"], "PR_node7")
        self.assertIn(SHA, sent["body"])
        self.assertIn("Why: broke the gate", sent["body"])

    def test_the_title_matches_the_audit_follow_up_rule(self):
        audit_spec = importlib.util.spec_from_file_location(
            "merge_audit", ROOT / "scripts" / "merge-audit.py"
        )
        assert audit_spec is not None and audit_spec.loader is not None
        audit = importlib.util.module_from_spec(audit_spec)
        audit_spec.loader.exec_module(audit)
        original = {"number": 7, "mergedAt": "2026-11-02T10:00:00Z"}
        revert = {
            "number": 8,
            "mergedAt": "2026-11-03T10:00:00Z",
            "title": rl.revert_title(7, "feat(x): thing"),
            "body": "",
        }
        self.assertEqual(audit.follow_ups(original, [original, revert]), [revert])

    def test_files_not_yet_computed_are_read_again(self):
        self.fake.empty_reads = 2
        code, lines = self.run_script()
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], f"CHECK: reverses {SHA[:7]}")
        self.assertEqual(self.slept, [2, 4])

    def test_a_revert_that_does_not_mirror_the_original_fails_the_check(self):
        self.fake.files[8] = [dict(REVERT_FILES[0], additions=3), REVERT_FILES[1]]
        code, lines = self.run_script()
        self.assertEqual(code, 3)
        self.assertTrue(lines[0].startswith("REVERT: opened"))
        self.assertEqual(
            lines[-1], "CHECK: mismatch scripts/a.py: revert +3/-10, expected +2/-10"
        )

    def test_unreadable_original_files_are_unknown(self):
        self.fake.fail = {"pulls/7/files"}
        code, lines = self.run_script()
        self.assertEqual(code, 3)
        self.assertTrue(
            lines[-1].startswith("CHECK: unknown reason=cannot read #7's files")
        )

    def test_files_still_missing_after_every_retry_are_a_mismatch(self):
        self.fake.empty_reads = 4
        code, lines = self.run_script()
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1], "CHECK: mismatch the revert PR shows no files yet")
        self.assertEqual(self.slept, [2, 4, 8])

    def test_revert_files_unreadable_on_every_retry_are_unknown(self):
        self.fake.fail = {"pulls/8/files"}
        code, lines = self.run_script()
        self.assertEqual(code, 3)
        self.assertTrue(
            lines[-1].startswith("CHECK: unknown reason=cannot read #8's files")
        )

    def test_a_merge_without_the_trailer_is_reverted_with_a_note(self):
        self.fake.message = "fix: by hand\n"
        code, lines = self.run_script()
        self.assertEqual(code, 0)
        self.assertTrue(lines[0].startswith(f"NOTE: {SHA[:7]} "))
        self.assertIn("not an agent landing", lines[0])

    def test_an_open_revert_is_reported_not_duplicated(self):
        self.fake.open_prs = [
            {"number": 8, "title": "fix: revert #7 (fix(scripts): tidy)", "url": "u8"},
            {"number": 9, "title": "fix: revert #70 (other)", "url": "u9"},
        ]
        code, lines = self.run_script()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "REVERT: exists u8")
        self.assertEqual(self.fake.mutations, [])

    def test_an_unmerged_pr_is_refused(self):
        self.fake.pr.update(state="OPEN", mergeCommit=None)
        code, lines = self.run_script()
        self.assertEqual(code, 1)
        self.assertEqual(
            lines, ["REVERT: refused reason=#7 is open, not merged with a merge commit"]
        )
        self.assertEqual(self.fake.mutations, [])

    def test_a_failed_mutation_is_refused_not_a_crash(self):
        self.fake.fail = {"graphql"}
        code, lines = self.run_script()
        self.assertEqual(code, 1)
        self.assertTrue(lines[-1].startswith("REVERT: refused reason="))

    def test_an_unlistable_open_pr_set_refuses_rather_than_risk_a_duplicate(self):
        self.fake.fail = {"pr list"}
        code, lines = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("rule out a duplicate", lines[-1])
        self.assertEqual(self.fake.mutations, [])


class UsageTests(unittest.TestCase):
    def test_bad_repo_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                rl.main(["7", "--repo", "widgets"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
