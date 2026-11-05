#!/usr/bin/env python3
"""Hermetic tests for scripts/co-review-carry.py.

One fake `gh` answers the three things the script reads or writes: the PR's
comment thread (the FakeGh from test_co_review_stamp.py), each commit's
`co-review` statuses, and GitHub's compare of the reviewed head against the
fix commit. The carried record is read back with co-review-evidence.py, so the
round trip covers the body the lander will actually parse.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stamp_tests = _load(
    "test_co_review_stamp", ROOT / "scripts" / "test_co_review_stamp.py"
)
carry = _load("co_review_carry", ROOT / "scripts" / "co-review-carry.py")
reader = _load("co_review_evidence", ROOT / "scripts" / "co-review-evidence.py")
REPO, PR = stamp_tests.REPO, stamp_tests.PR
REVIEWED, FIX = stamp_tests.HEAD_A, stamp_tests.HEAD_B


def record(**overrides):
    rec = {
        "head": REVIEWED,
        "unresolved_findings": 0,
        "verification_open": 1,
        "consult_calls": [],
        "next_round": "no further round",
    }
    rec.update(overrides)
    return rec


# Loaded at runtime, so mypy cannot see the class; Any lets it be a base.
ThreadFake: Any = stamp_tests.FakeGh


class FakeGh(ThreadFake):
    """The comment thread, plus per-commit statuses and one compare answer."""

    def __init__(self):
        super().__init__()
        self.statuses = {REVIEWED: ["success"]}  # newest first
        self.ahead_behind = (1, 0)
        self.posted = []  # (sha, payload)
        self.fail_status_post = False
        # When set, the statuses read returns this text as-is: one line per
        # page, as `--paginate` with a per-page `--jq` prints it.
        self.status_pages = None

    def run_gh(self, args, stdin=None):
        path = next((a for a in args if a.startswith("repos/")), "")
        if "/statuses/" in path and "--method" in args:
            self.calls.append(args)
            if self.fail_status_post:
                return 1, "", "HTTP 422: Validation Failed"
            payload = json.loads(stdin)
            self.posted.append((path.rsplit("/", 1)[1], payload))
            return 0, json.dumps({"target_url": payload.get("target_url")}), ""
        if path.endswith("/statuses"):
            self.calls.append(args)
            if self.status_pages is not None:
                return 0, self.status_pages, ""
            sha = path.split("/")[-2]
            states = self.statuses.get(sha, [])
            return 0, (states[0] + "\n") if states else "", ""
        if "/compare/" in path:
            self.calls.append(args)
            ahead, behind = self.ahead_behind
            body = {"status": "ahead", "ahead_by": ahead, "behind_by": behind}
            return 0, json.dumps(body), ""
        return super().run_gh(args, stdin)


class CarryTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGh()
        for mod in (carry, reader.stamp):
            orig = mod.run_gh
            mod.run_gh = self.fake.run_gh
            self.addCleanup(setattr, mod, "run_gh", orig)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def run_main(self, rec=None, verdict="pass", applied_blocking="0", head=FIX):
        path = self.tmp / "evidence.json"
        path.write_text(json.dumps(record() if rec is None else rec))
        argv = [
            "--repo", REPO, "--pr", str(PR), "--reviewed", REVIEWED, "--head", head,
            "--verdict", verdict, "--applied-blocking", applied_blocking,
            "--evidence", str(path), "--summary", "claude ✅ · codex ✅ — 4 fixes",
            "--target-url", f"https://github.com/{REPO}/pull/{PR}",
        ]  # fmt: skip
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = carry.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def assert_refused(self, reason, **kwargs):
        code, out, _ = self.run_main(**kwargs)
        self.assertEqual(code, carry.REFUSED, out)
        self.assertTrue(out.startswith("CARRY: refused reason="), out)
        self.assertIn(reason, out)
        self.assertEqual(self.fake.posted, [])
        self.assertEqual(self.fake.stamps(), [])

    def test_a_passing_round_carries_success_and_evidence_to_the_fix(self):
        code, out, _ = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertTrue(
            out.startswith(f"CARRY: carried from={REVIEWED[:7]} to={FIX[:7]} "), out
        )
        ((sha, payload),) = self.fake.posted
        self.assertEqual(sha, FIX)
        self.assertEqual(payload["state"], "success")
        self.assertEqual(payload["context"], "co-review")
        self.assertTrue(
            payload["description"].startswith(f"carried from {REVIEWED[:7]}")
        )
        got = reader.read_evidence(REPO, PR, FIX)
        self.assertEqual(got, {**record(), "head": FIX, "carried_from": REVIEWED})
        body = self.fake.stamps()[0]["body"]
        self.assertEqual(stamp_tests.stamped_head(body), FIX)
        self.assertIn(f"reviewed `{REVIEWED[:7]}`; its pass was carried", body)

    def test_the_stamp_is_written_before_the_status(self):
        self.run_main()
        writes = self.fake.writes()
        self.assertIn("issues", writes[0][3])
        self.assertIn("/statuses/", writes[-1][3])

    def test_pass_with_suggestions_is_refused(self):
        self.assert_refused("not pass", verdict="pass with suggestions")

    def test_blocking_is_refused(self):
        self.assert_refused("not pass", verdict="blocking")

    def test_an_applied_blocking_fix_is_refused(self):
        self.assert_refused("blocking", applied_blocking="1")

    def test_another_round_is_refused(self):
        self.assert_refused("another round", rec=record(next_round="another round"))

    def test_an_unresolved_finding_is_refused_even_on_pass(self):
        self.assert_refused(
            "1 finding(s) unresolved", rec=record(unresolved_findings=1)
        )

    def test_a_page_without_a_co_review_status_is_skipped(self):
        self.fake.status_pages = "\n\nsuccess\n"
        code, out, _ = self.run_main()
        self.assertEqual(code, 0, out)

    def test_the_newest_page_decides(self):
        self.fake.status_pages = "\nfailure\nsuccess\n"
        self.assert_refused("is failure, not success")

    def test_a_reviewed_head_without_success_is_refused(self):
        self.fake.statuses[REVIEWED] = ["failure", "success"]
        self.assert_refused("is failure, not success")
        self.fake.statuses[REVIEWED] = []
        self.assert_refused("is missing, not success")

    def test_more_than_the_fix_commit_is_refused(self):
        self.fake.ahead_behind = (2, 0)
        self.assert_refused("not one commit on top")
        self.fake.ahead_behind = (1, 1)
        self.assert_refused("not one commit on top")

    def test_a_failed_status_post_is_exit_1_and_names_the_stamp(self):
        self.fake.fail_status_post = True
        code, out, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("CARRY: error reason=stamp created, status not posted", out)

    def test_usage_errors_exit_2_before_any_gh_call(self):
        for kwargs in (
            {"head": REVIEWED},
            {"head": "abc"},
            {"rec": record(head=FIX)},
            {"rec": record(carried_from="c" * 40)},
            {"applied_blocking": "-1"},
        ):
            code, _, _ = self.run_main(**kwargs)
            self.assertEqual(code, 2, kwargs)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
