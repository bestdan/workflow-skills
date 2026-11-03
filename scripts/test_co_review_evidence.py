#!/usr/bin/env python3
"""Hermetic tests for the co-review evidence record.

Writes a record with co-review-stamp.py --evidence and reads it back with
co-review-evidence.py, both against one in-memory comment thread (the FakeGh
from test_co_review_stamp.py), so the round trip covers the real body format
rather than a copy of it.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

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
stamp_mod = _load("co_review_stamp_w", ROOT / "scripts" / "co-review-stamp.py")
reader_mod = _load("co_review_evidence", ROOT / "scripts" / "co-review-evidence.py")
FakeGh = stamp_tests.FakeGh
REPO, PR = stamp_tests.REPO, stamp_tests.PR
HEAD_A, HEAD_B = stamp_tests.HEAD_A, stamp_tests.HEAD_B


def record(head=HEAD_A, **overrides):
    rec = {
        "head": head,
        "unresolved_findings": 1,
        "verification_open": 0,
        "consult_calls": [{"confidence": 80, "kind": "technical"}],
        "next_round": "no further round",
    }
    rec.update(overrides)
    return rec


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGh()
        for mod in (stamp_mod, reader_mod.stamp):
            orig = mod.run_gh
            mod.run_gh = self.fake.run_gh
            self.addCleanup(setattr, mod, "run_gh", orig)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def write(self, rec, head=HEAD_A, summary="claude ✅ — 1 finding"):
        path = self.tmp / "evidence.json"
        path.write_text(json.dumps(rec))
        out, err = io.StringIO(), io.StringIO()
        argv = ["--repo", REPO, "--pr", str(PR), "--head", head, "--summary", summary]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stamp_mod.main([*argv, "--evidence", str(path)])
        return code, out.getvalue(), err.getvalue()

    def read(self, head=HEAD_A):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = reader_mod.main(["--repo", REPO, "--pr", str(PR), "--head", head])
        return code, out.getvalue(), err.getvalue()

    def test_round_trip(self):
        rec = record()
        code, out, _ = self.write(rec)
        self.assertEqual(code, 0, out)
        code, out, _ = self.read()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), rec)

    def test_a_record_for_another_head_reads_as_absent(self):
        self.write(record())
        code, out, err = self.read(head=HEAD_B)
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("no record", err)

    def test_no_stamp_reads_as_absent(self):
        self.assertEqual(self.read()[0], 3)

    def test_a_stamp_without_evidence_reads_as_absent(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            stamp_mod.main(
                ["--repo", REPO, "--pr", str(PR), "--head", HEAD_A, "--summary", "s"]
            )
        self.assertEqual(self.read()[0], 3)

    def test_restamping_without_evidence_drops_the_record(self):
        self.write(record())
        with contextlib.redirect_stdout(io.StringIO()):
            stamp_mod.main(
                ["--repo", REPO, "--pr", str(PR), "--head", HEAD_A, "--summary", "s"]
            )
        self.assertEqual(self.read()[0], 3)
        self.assertEqual(len(self.fake.stamps()), 1)

    def test_the_stamp_marker_stays_first_for_the_fleet_parser(self):
        self.write(record())
        body = self.fake.stamps()[0]["body"]
        self.assertTrue(body.startswith(f"<!-- co-review-stamp v1 head={HEAD_A} -->\n"))
        self.assertEqual(stamp_tests.stamped_head(body), HEAD_A)

    def test_another_accounts_record_is_ignored(self):
        body = stamp_mod.stamp_body(HEAD_A, "x", record())
        self.fake.comments.append({"id": 7, "login": "someone", "body": body})
        self.assertEqual(self.read()[0], 3)

    def test_an_unreadable_record_is_exit_1(self):
        body = (
            stamp_mod.stamp_body(HEAD_A, "x")
            + "\n<!-- co-review-evidence v1\n{not json\n-->"
        )
        self.fake.comments.append({"id": 7, "login": "me", "body": body})
        code, _, err = self.read()
        self.assertEqual(code, 1)
        self.assertIn("unreadable evidence record", err)

    def test_invalid_records_are_refused_before_gh(self):
        cases = [
            ("wrong head", record(head=HEAD_B)),
            ("missing key", {k: v for k, v in record().items() if k != "next_round"}),
            ("extra key", record(extra=1)),
            ("negative count", record(unresolved_findings=-1)),
            ("bool count", record(verification_open=True)),
            ("bad kind", record(consult_calls=[{"confidence": 90, "kind": "vibes"}])),
            (
                "confidence over 100",
                record(consult_calls=[{"confidence": 101, "kind": "stack"}]),
            ),
            ("bad next_round", record(next_round="maybe")),
        ]
        for name, rec in cases:
            with self.subTest(name=name):
                with self.assertRaises(SystemExit) as cm:
                    self.write(rec)
                self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.fake.calls, [])

    def test_a_valid_json_but_malformed_record_is_exit_1(self):
        for block in ("{}", "[]"):
            with self.subTest(block=block):
                self.fake.comments = [
                    {
                        "id": 7,
                        "login": "me",
                        "body": stamp_mod.stamp_body(HEAD_A, "x")
                        + f"\n<!-- co-review-evidence v1\n{block}\n-->",
                    }
                ]
                code, _, err = self.read()
                self.assertEqual(code, 1)
                self.assertIn("unreadable evidence record", err)

    def test_a_record_under_another_heads_marker_reads_as_absent(self):
        # The marker names HEAD_B, while the record claims HEAD_A.
        body = (
            stamp_mod.stamp_body(HEAD_B, "x")
            + "\n<!-- co-review-evidence v1\n"
            + json.dumps(record())
            + "\n-->"
        )
        self.fake.comments.append({"id": 7, "login": "me", "body": body})
        self.assertEqual(self.read()[0], 3)

    def test_the_reader_uses_the_oldest_stamp_as_the_writer_does(self):
        self.fake.comments = [
            {"id": 5, "login": "me", "body": stamp_mod.stamp_body(HEAD_A, "x")},
            {
                "id": 9,
                "login": "me",
                "body": stamp_mod.stamp_body(HEAD_A, "x", record()),
            },
        ]
        self.assertEqual(self.read()[0], 3)

    def test_a_missing_or_non_json_evidence_file_is_a_usage_error(self):
        bad = self.tmp / "bad.json"
        bad.write_text("not json")
        for path in (self.tmp / "missing.json", bad):
            with self.subTest(path=path.name):
                argv = [
                    "--repo",
                    REPO,
                    "--pr",
                    str(PR),
                    "--head",
                    HEAD_A,
                    "--summary",
                    "s",
                ]
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm:
                        stamp_mod.main([*argv, "--evidence", str(path)])
                self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.fake.calls, [])

    def test_a_gh_failure_is_exit_1(self):
        self.fake.fail_on = "GET"
        code, _, err = self.read()
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("co-review-evidence: "), err)

    def test_a_bad_head_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                reader_mod.main(["--repo", REPO, "--pr", "1", "--head", "abc"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
