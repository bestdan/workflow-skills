#!/usr/bin/env python3
"""Hermetic tests for scripts/co-review-stamp.py.

Replaces the module's run_gh() seam with a fake `gh` that keeps a PR's issue
comments in memory, so create, update-in-place, duplicate collapse and the
failure path are all exercised without a network. The marker regex below is
the shape the fleet view's parser (scripts/task-dashboard/github_fleet.py)
reads; a change here that breaks it breaks the gauge.
"""

import contextlib
import importlib.util
import io
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "co-review-stamp.py"

_spec = importlib.util.spec_from_file_location("co_review_stamp", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
stamp_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stamp_mod)

REPO = "owner/name"
PR = 42
HEAD_A = "a" * 40
HEAD_B = "b" * 40
MARKER_RE = re.compile(r"<!-- co-review-stamp v1 head=([0-9a-f]{40}) -->")


def stamped_head(body):
    match = MARKER_RE.search(body)
    assert match is not None, f"no stamp marker in {body!r}"
    return match.group(1)


class FakeGh:
    """An in-memory PR comment thread behind the `gh api` calls the script makes."""

    def __init__(self, login="me", comments=(), fail_on=None, fail_path=None):
        self.login = login
        self.comments = [dict(c) for c in comments]
        self.next_id = 1000
        self.fail_on = fail_on  # a method ("GET", "POST", ...) that should fail
        # When set, fail_on applies only to calls naming this path fragment, so a
        # test can fail the comment listing while `gh api user` still succeeds.
        self.fail_path = fail_path
        self.calls = []

    def _url(self, cid):
        return f"https://github.com/{REPO}/pull/{PR}#issuecomment-{cid}"

    def run_gh(self, args, stdin=None):
        self.calls.append(args)
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        on_path = self.fail_path is None or any(self.fail_path in a for a in args)
        if method == self.fail_on and on_path:
            return 1, "", "HTTP 403: Resource not accessible by integration"
        if args[:2] == ["api", "user"]:
            return 0, self.login + "\n", ""
        if method == "GET":
            lines = [
                json.dumps(
                    {
                        "id": c["id"],
                        "login": c["login"],
                        "body": c["body"],
                        "url": self._url(c["id"]),
                    }
                )
                for c in self.comments
            ]
            return 0, "\n".join(lines) + "\n", ""
        path = args[args.index("--method") + 2]
        if method == "POST":
            cid = self.next_id
            self.next_id += 1
            body = json.loads(stdin)["body"]
            self.comments.append({"id": cid, "login": self.login, "body": body})
            return 0, json.dumps({"html_url": self._url(cid)}), ""
        cid = int(path.rsplit("/", 1)[1])
        if method == "PATCH":
            for c in self.comments:
                if c["id"] == cid:
                    c["body"] = json.loads(stdin)["body"]
            return 0, json.dumps({"html_url": self._url(cid)}), ""
        if method == "DELETE":
            self.comments = [c for c in self.comments if c["id"] != cid]
            return 0, "", ""
        raise AssertionError(f"unexpected gh call {args}")

    def writes(self):
        return [a for a in self.calls if "--method" in a]

    def stamps(self):
        return [c for c in self.comments if MARKER_RE.search(c["body"])]


class StampTests(unittest.TestCase):
    def use(self, fake):
        orig = stamp_mod.run_gh
        stamp_mod.run_gh = fake.run_gh
        self.addCleanup(setattr, stamp_mod, "run_gh", orig)
        return fake

    def run_main(self, head=HEAD_A, summary="claude ✅ ran · 2 findings"):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--repo", REPO, "--pr", str(PR), "--head", head, "--summary", summary]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stamp_mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_creates_one_stamp_naming_the_head(self):
        fake = self.use(FakeGh())
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("STAMPED: created https://"), out)
        self.assertEqual(len(fake.stamps()), 1)
        body = fake.stamps()[0]["body"]
        self.assertEqual(stamped_head(body), HEAD_A)
        self.assertTrue(body.startswith(f"<!-- co-review-stamp v1 head={HEAD_A} -->\n"))
        # The fake answers any GET, so pin the listing call itself: without
        # --paginate, gh returns one page and a stamp past it would be missed.
        listing = [a for a in fake.calls if f"repos/{REPO}/issues/{PR}/comments" in a]
        self.assertTrue(any("--paginate" in a for a in listing), fake.calls)

    def test_second_run_updates_in_place(self):
        fake = self.use(FakeGh())
        self.run_main(head=HEAD_A)
        first_id = fake.stamps()[0]["id"]
        code, out, _ = self.run_main(head=HEAD_B)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("STAMPED: updated "), out)
        self.assertEqual(len(fake.stamps()), 1)
        self.assertEqual(fake.stamps()[0]["id"], first_id)
        self.assertEqual(stamped_head(fake.stamps()[0]["body"]), HEAD_B)

    def test_identical_rerun_writes_nothing(self):
        fake = self.use(FakeGh())
        self.run_main()
        before = len(fake.writes())
        code, out, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("STAMPED: unchanged "), out)
        self.assertEqual(len(fake.writes()), before)

    def test_duplicates_collapse_to_the_oldest(self):
        old = stamp_mod.stamp_body(HEAD_A, "x")
        fake = self.use(
            FakeGh(
                comments=[
                    {"id": 5, "login": "me", "body": old},
                    {"id": 9, "login": "me", "body": old},
                ]
            )
        )
        code, _, _ = self.run_main(head=HEAD_B)
        self.assertEqual(code, 0)
        self.assertEqual([c["id"] for c in fake.stamps()], [5])
        self.assertIn(HEAD_B, fake.stamps()[0]["body"])

    def test_other_accounts_and_unrelated_comments_are_left_alone(self):
        theirs = {
            "id": 3,
            "login": "someone",
            "body": stamp_mod.stamp_body(HEAD_A, "x"),
        }
        plain = {"id": 4, "login": "me", "body": "LGTM, mentions co-review-stamp"}
        fake = self.use(FakeGh(comments=[theirs, plain]))
        code, out, _ = self.run_main(head=HEAD_B)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("STAMPED: created "), out)
        self.assertIn(theirs, fake.comments)
        self.assertIn(plain, fake.comments)

    def test_a_failed_write_exits_nonzero_with_one_line_and_no_traceback(self):
        fake = self.use(FakeGh(fail_on="POST"))
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertTrue(err.startswith("co-review-stamp: "), err)
        self.assertIn("403", err)
        self.assertEqual(fake.stamps(), [])

    def test_a_failed_read_writes_nothing(self):
        fake = self.use(FakeGh(fail_on="GET"))
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("co-review-stamp: "), err)
        self.assertEqual(fake.writes(), [])

    def test_a_failed_comment_listing_writes_nothing(self):
        fake = self.use(FakeGh(fail_on="GET", fail_path="/comments"))
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("co-review-stamp: "), err)
        self.assertIn("403", err)
        # Login resolution ran and succeeded; the listing is what failed.
        self.assertIn(["api", "user", "--jq", ".login"], fake.calls)
        self.assertEqual(fake.writes(), [])

    def test_a_failed_patch_exits_nonzero_and_leaves_the_stamp(self):
        old = stamp_mod.stamp_body(HEAD_A, "x")
        fake = self.use(
            FakeGh(comments=[{"id": 5, "login": "me", "body": old}], fail_on="PATCH")
        )
        code, out, err = self.run_main(head=HEAD_B)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertTrue(err.startswith("co-review-stamp: "), err)
        self.assertEqual(fake.stamps()[0]["body"], old)

    def test_a_failed_duplicate_delete_is_a_warning_not_a_failure(self):
        old = stamp_mod.stamp_body(HEAD_A, "x")
        fake = self.use(
            FakeGh(
                comments=[
                    {"id": 5, "login": "me", "body": old},
                    {"id": 9, "login": "me", "body": old},
                ],
                fail_on="DELETE",
            )
        )
        code, out, err = self.run_main(head=HEAD_B)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("STAMPED: updated "), out)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertTrue(err.startswith("co-review-stamp: warning: "), err)
        self.assertIn("9", err)
        kept = [c for c in fake.stamps() if c["id"] == 5][0]
        self.assertEqual(stamped_head(kept["body"]), HEAD_B)

    def test_missing_gh_is_a_failure_not_a_crash(self):
        orig = stamp_mod.run_gh
        self.addCleanup(setattr, stamp_mod, "run_gh", orig)
        stamp_mod.run_gh = lambda args, stdin=None: (127, "", "gh: not found")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("rc 127", err)

    def test_summary_is_folded_to_one_line(self):
        fake = self.use(FakeGh())
        self.run_main(summary="two\nlines")
        body = fake.stamps()[0]["body"]
        self.assertEqual(len(body.splitlines()), 2)
        self.assertTrue(body.endswith("two lines"))

    def test_short_or_uppercase_sha_is_a_usage_error(self):
        self.use(FakeGh())
        for bad in ("abc1234", "A" * 40):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    stamp_mod.main(
                        ["--repo", REPO, "--pr", "1", "--head", bad, "--summary", "s"]
                    )
                self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
