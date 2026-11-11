#!/usr/bin/env python3
"""Hermetic tests for scripts/merge-audit.py, both modes.

`gh` is replaced by FakeGh, which answers from recorded JSON shaped like the
real `gh pr list`, commit-status, rule-suite, comment and commit responses.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "merge_audit", ROOT / "scripts" / "merge-audit.py"
)
assert _spec is not None and _spec.loader is not None
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)
land = audit.land

REPO = "acme/widgets"
SINCE = "2026-11-01"
POLICY_300 = """\
auto:
  - "scripts/**"
allowed_types: [chore, fix, feat]
"""
POLICY_400 = POLICY_300 + "size_cap: 400\n"


def sha(n):
    return f"{n:x}".rjust(40, "a")


def pr(
    number,
    merged,
    title="fix(scripts): tidy",
    body="",
    head=None,
    merge=None,
    merged_by="acme-bot",
):
    return {
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/{REPO}/pull/{number}",
        "mergedAt": merged,
        "headRefOid": head or sha(number),
        "baseRefName": "main",
        "mergeCommit": {"oid": merge or sha(1000 + number)},
        "labels": [],
        "mergedBy": {"login": merged_by},
    }


def lander_comment(verdict, head, reasons, cid=1):
    lines = [
        land.MARKER,
        f"**land-pr** dry run on `{head[:7]}`: would land: **{verdict}**",
        "",
    ]
    lines += [f"- {r}" for r in reasons]
    return {"id": cid, "body": "\n".join(lines)}


class FakeGh:
    def __init__(self):
        self.prs = []
        self.statuses = {}  # head -> {context: state}
        self.rules = []  # rule suites
        self.comments = {}  # pr -> [comment]
        self.commits = {}  # pr -> [commit]
        self.files = {}  # pr -> [file]
        self.messages = {}  # merge sha -> commit message
        self.fail = set()  # endpoint substrings that fail
        self.fail_err = "HTTP 500"  # the stderr a failing call returns

    def __call__(self, args, stdin=None):
        joined = " ".join(args)
        for bad in self.fail:
            if bad in joined:
                return 1, "", self.fail_err
        if args[:2] == ["pr", "list"]:
            return 0, json.dumps(self.prs), ""
        endpoint = next(a for a in args if a.startswith("repos/"))
        if "/status" in endpoint:
            head = endpoint.split("/commits/")[1].split("/")[0]
            ctx = self.statuses.get(head, {})
            return (
                0,
                json.dumps(
                    [{"statuses": [{"context": c, "state": s} for c, s in ctx.items()]}]
                ),
                "",
            )
        if "rule-suites" in endpoint:
            return 0, json.dumps([self.rules]), ""
        if "/commits/" in endpoint:  # one commit's message, read with --jq
            return 0, self.messages.get(endpoint.split("/commits/")[1], "x\n"), ""
        number = int(endpoint.split("/")[4])
        if "/comments" in endpoint:
            return 0, json.dumps([self.comments.get(number, [])]), ""
        if endpoint.endswith("/commits"):
            return 0, json.dumps([self.commits.get(number, [])]), ""
        if endpoint.endswith("/files"):
            return 0, json.dumps([self.files.get(number, [])]), ""
        raise AssertionError(f"unexpected gh call: {joined}")


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGh()
        original = land.run_gh
        land.run_gh = self.fake
        self.addCleanup(setattr, land, "run_gh", original)

    def run_audit(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = audit.main(["--repo", REPO, "--since", SINCE, *extra])
        return code, out.getvalue().splitlines()


class ReviewTests(Base):
    def test_lists_only_prs_without_success_and_names_each(self):
        ok, bypassed, ungated, unknown = (
            pr(1, "2026-11-02T10:00:00Z"),
            pr(2, "2026-11-02T11:00:00Z"),
            pr(3, "2026-11-02T12:00:00Z"),
            pr(4, "2026-11-02T13:00:00Z"),
        )
        self.fake.prs = [ok, bypassed, ungated, unknown]
        self.fake.statuses = {
            sha(1): {"co-review": "success"},
            sha(2): {"co-review": "pending"},
            sha(3): {"ci": "success"},
        }
        self.fake.rules = [
            {"after_sha": sha(1002), "result": "bypass", "actor_name": "mallory"},
            {"after_sha": sha(1003), "result": "pass", "actor_name": "acme-bot"},
        ]
        code, lines = self.run_audit("--mode", "review")
        self.assertEqual(code, 0)
        self.assertFalse(any(line.startswith("#1 ") for line in lines))
        self.assertTrue(
            any(
                line.startswith("#2 bypass")
                and "pending" in line
                and "bypassed by mallory" in line
                for line in lines
            )
        )
        self.assertTrue(
            any(
                line.startswith("#3 ungated")
                and "missing" in line
                and "merged by acme-bot" in line
                for line in lines
            )
        )
        self.assertTrue(any(line.startswith("#4 unknown") for line in lines))
        self.assertEqual(
            lines[-1],
            "AUDIT: review merged=4 lacking=3 owner=0 bypass=1 ungated=1 unknown=1",
        )

    def test_a_bypass_by_the_owner_is_owner_not_an_alarm(self):
        # The default owner is the repo's owner segment, matched case-blind.
        self.fake.prs = [pr(7, "2026-11-02T10:00:00Z", merged_by="Acme")]
        self.fake.rules = [
            {"after_sha": sha(1007), "result": "bypass", "actor_name": "Acme"}
        ]
        code, lines = self.run_audit("--mode", "review")
        self.assertEqual(code, 0)
        self.assertTrue(lines[0].startswith("#7 owner"))
        self.assertIn("bypassed by Acme", lines[0])
        self.assertIn("owner=1 bypass=0", lines[-1])

    def test_owner_flag_names_the_expected_bypasser(self):
        self.fake.prs = [pr(8, "2026-11-02T10:00:00Z")]
        self.fake.rules = [
            {"after_sha": sha(1008), "result": "bypass", "actor_name": "jane"}
        ]
        _, lines = self.run_audit("--mode", "review", "--owner", "jane")
        self.assertTrue(lines[0].startswith("#8 owner"))
        _, lines = self.run_audit("--mode", "review")
        self.assertTrue(lines[0].startswith("#8 bypass"))

    def test_a_bypass_with_no_actor_falls_back_to_merged_by(self):
        self.fake.prs = [pr(9, "2026-11-02T10:00:00Z", merged_by="acme")]
        self.fake.rules = [{"after_sha": sha(1009), "result": "bypass"}]
        _, lines = self.run_audit("--mode", "review")
        self.assertTrue(lines[0].startswith("#9 owner"))
        self.assertIn("bypassed by acme (from mergedBy)", lines[0])

    def test_unreadable_rule_suites_are_unknown_not_a_crash(self):
        self.fake.prs = [pr(5, "2026-11-02T10:00:00Z")]
        self.fake.fail = {"rule-suites"}
        self.fake.fail_err = "Not Found (HTTP 404)"
        code, lines = self.run_audit("--mode", "review")
        self.assertEqual(code, 0)
        self.assertTrue(lines[0].startswith("note: cannot read rule suites"))
        self.assertIn("needs Administration read", lines[0])
        self.assertTrue(lines[1].startswith("#5 unknown"))
        self.assertIn("merged by acme-bot", lines[1])
        self.assertIn("rule suites unreadable", lines[1])
        self.assertTrue(lines[-1].endswith("unknown=1"))

    def test_a_non_404_rule_suite_failure_names_no_permission(self):
        self.fake.prs = [pr(5, "2026-11-02T10:00:00Z")]
        self.fake.fail = {"rule-suites"}
        _, lines = self.run_audit("--mode", "review")
        self.assertTrue(lines[0].startswith("note: cannot read rule suites"))
        self.assertNotIn("Administration", lines[0])

    def test_unreadable_rule_suites_are_noted_once_per_base(self):
        self.fake.prs = [pr(n, "2026-11-02T10:00:00Z") for n in (5, 6)]
        self.fake.fail = {"rule-suites"}
        _, lines = self.run_audit("--mode", "review")
        notes = [line for line in lines if line.startswith("note: cannot read")]
        self.assertEqual(len(notes), 1)
        self.assertTrue(lines[-1].endswith("unknown=2"))

    def write_suites(self, data):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        path = Path(d) / "suites.json"
        path.write_text(json.dumps(data))
        return str(path)

    def test_a_saved_rule_suites_file_replaces_the_api_read(self):
        # The API read fails, as it does for a Write-role bot; the file wins.
        self.fake.prs = [pr(7, "2026-11-02T10:00:00Z"), pr(8, "2026-11-02T11:00:00Z")]
        self.fake.fail = {"rule-suites"}
        suite = {"repository_name": "widgets", "result": "bypass"}
        slurped = [
            [{**suite, "after_sha": sha(1007), "actor_name": "acme"}],
            [{**suite, "after_sha": sha(1008), "actor_name": "mallory"}],
        ]
        for data in (slurped, slurped[0] + slurped[1]):
            with self.subTest(shape="slurped" if data is slurped else "one page"):
                path = self.write_suites(data)
                code, lines = self.run_audit("--mode", "review", "--rule-suites", path)
                self.assertEqual(code, 0)
                self.assertFalse(any(line.startswith("note:") for line in lines))
                self.assertTrue(lines[0].startswith("#7 owner"))
                self.assertTrue(lines[1].startswith("#8 bypass"))
                self.assertIn("owner=1 bypass=1", lines[-1])

    def test_a_rule_suites_file_from_another_repo_is_a_usage_error(self):
        path = self.write_suites(
            [{"repository_name": "gadgets", "after_sha": sha(1), "result": "bypass"}]
        )
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                self.run_audit("--mode", "review", "--rule-suites", path)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("gadgets", err.getvalue())

    def test_unreadable_statuses_are_unknown_not_a_crash(self):
        self.fake.prs = [pr(6, "2026-11-02T10:00:00Z")]
        self.fake.fail = {"/status"}
        code, lines = self.run_audit("--mode", "review")
        self.assertEqual(code, 0)
        self.assertIn("cannot read statuses", lines[0])
        self.assertIn("merged by acme-bot", lines[0])
        self.assertTrue(lines[-1].endswith("unknown=1"))

    def test_a_result_at_the_search_limit_is_an_error_not_a_short_count(self):
        self.fake.prs = [pr(n, "2026-11-02T10:00:00Z") for n in range(audit.PR_LIMIT)]
        code, lines = self.run_audit("--mode", "review")
        self.assertEqual(code, 1)
        self.assertIn("search limit", lines[-1])

    def test_a_window_older_than_the_rule_suites_says_so(self):
        self.fake.prs = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            audit.main(["--repo", REPO, "--since", "2020-01-01", "--mode", "review"])
        self.assertTrue(out.getvalue().startswith("note: rule suites cover"))

    def test_unreadable_pr_list_exits_1(self):
        self.fake.fail = {"pr list"}
        code, lines = self.run_audit("--mode", "review")
        self.assertEqual(code, 1)
        self.assertTrue(lines[-1].startswith("AUDIT: error"))


class ShadowTests(Base):
    def test_would_land_pr_lists_later_owner_commit_and_follow_up(self):
        landed = pr(10, "2026-11-02T10:00:00Z", head=sha(12))
        fix = pr(
            11,
            "2026-11-05T10:00:00Z",
            title="fix(scripts): undo the tidy",
            body="Follow-up to #10.",
        )
        late = pr(
            13, "2026-11-12T10:00:00Z", title="fix(scripts): more", body="See #10."
        )
        unrelated = pr(
            14, "2026-11-03T10:00:00Z", title="fix(scripts): other", body="See #100."
        )
        self.fake.prs = [landed, fix, late, unrelated]
        self.fake.comments = {10: [lander_comment("yes", sha(11), ["checks green"])]}
        self.fake.commits = {
            10: [
                {
                    "sha": sha(11),
                    "author": {"login": "agent"},
                    "commit": {"message": "fix: tidy"},
                },
                {
                    "sha": sha(12),
                    "author": {"login": "owner"},
                    "commit": {"message": "tweak the wording\n\nbody"},
                },
            ]
        }
        code, lines = self.run_audit("--mode", "shadow")
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("#10 would-land", text)
        self.assertIn(f"later commit {sha(12)[:7]} by owner: tweak the wording", text)
        self.assertIn("follow-up #11", text)
        self.assertNotIn("follow-up #13", text)  # past 7 days
        self.assertNotIn("follow-up #14", text)  # cites #100, not #10
        self.assertEqual(lines[-1], "AUDIT: shadow shadowed=1 would-land=1/30")

    def test_a_judged_head_missing_from_the_commits_is_said_and_still_counted(self):
        self.fake.prs = [pr(40, "2026-11-02T10:00:00Z")]
        self.fake.comments = {40: [lander_comment("yes", "b" * 40, ["checks green"])]}
        self.fake.commits = {40: [{"sha": sha(40), "commit": {"message": "x"}}]}
        _, lines = self.run_audit("--mode", "shadow")
        self.assertTrue(any("is not among the PR's commits" in line for line in lines))
        self.assertEqual(lines[-1], "AUDIT: shadow shadowed=1 would-land=1/30")

    def test_hold_verdict_is_not_counted_and_unshadowed_prs_are_skipped(self):
        self.fake.prs = [pr(20, "2026-11-02T10:00:00Z"), pr(21, "2026-11-02T11:00:00Z")]
        self.fake.comments = {
            20: [
                lander_comment(
                    "no", sha(20), ["no co-review evidence for head aaaaaaa"]
                )
            ]
        }
        code, lines = self.run_audit("--mode", "shadow")
        self.assertEqual(lines[-1], "AUDIT: shadow shadowed=1 would-land=0/30")

    def _sized_pr(self):
        self.fake.prs = [pr(30, "2026-11-02T10:00:00Z")]
        self.fake.comments = {
            30: [
                lander_comment(
                    "no",
                    sha(30),
                    ["tier human: size 350 changed lines over size_cap 300"],
                )
            ]
        }
        self.fake.files = {
            30: [
                {
                    "filename": "scripts/a.py",
                    "additions": 300,
                    "deletions": 50,
                    "status": "modified",
                }
            ]
        }
        self.fake.commits = {30: [{"sha": sha(30), "commit": {"message": "x"}}]}

    def run_with_policy(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
            fh.write(text)
        self.addCleanup(Path(fh.name).unlink)
        return self.run_audit("--mode", "shadow", "--policy", fh.name)

    def test_350_line_pr_counts_under_a_400_cap_policy(self):
        self._sized_pr()
        _, lines = self.run_with_policy(POLICY_400)
        self.assertEqual(lines[-1], "AUDIT: shadow shadowed=1 would-land=1/30")

    def test_350_line_pr_does_not_count_under_the_default_300(self):
        self._sized_pr()
        _, lines = self.run_audit("--mode", "shadow")
        self.assertEqual(lines[-1], "AUDIT: shadow shadowed=1 would-land=0/30")
        _, lines = self.run_with_policy(POLICY_300)
        self.assertEqual(lines[-1], "AUDIT: shadow shadowed=1 would-land=0/30")

    def test_policy_does_not_override_a_non_tier_hold(self):
        self._sized_pr()
        self.fake.comments[30] = [
            lander_comment(
                "no",
                sha(30),
                [
                    "tier human: size 350 changed lines over size_cap 300",
                    "`co-review` status on aaaaaaa is missing, not success",
                ],
            )
        ]
        _, lines = self.run_with_policy(POLICY_400)
        self.assertEqual(lines[-1], "AUDIT: shadow shadowed=1 would-land=0/30")


LANDED_MESSAGE = (
    "fix(scripts): tidy (#50)\n\nLanded-by: land-pr.sh\nCo-review-head: abc\n"
)


class LandedTests(Base):
    def test_lists_trailer_merges_with_follow_ups_and_missing_labels(self):
        labelled = pr(50, "2026-11-02T10:00:00Z")
        labelled["labels"] = [{"name": "landed-by-agent"}]
        unlabelled = pr(51, "2026-11-02T11:00:00Z")
        by_hand = pr(52, "2026-11-02T12:00:00Z")
        revert = pr(
            53,
            "2026-11-04T10:00:00Z",
            title="fix: revert #50 (fix(scripts): tidy)",
        )
        late = pr(
            54, "2026-11-12T10:00:00Z", title="fix(scripts): again", body="See #51."
        )
        self.fake.prs = [labelled, unlabelled, by_hand, revert, late]
        self.fake.messages = {sha(1050): LANDED_MESSAGE, sha(1051): LANDED_MESSAGE}
        code, lines = self.run_audit("--mode", "landed")
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("#50 landed 2026-11-02", text)
        self.assertIn("#51 landed 2026-11-02", text)
        self.assertNotIn("#52 landed", text)
        self.assertIn("follow-up #53", text)
        self.assertNotIn("follow-up #54", text)  # past 7 days
        self.assertEqual(text.count("lacks the `landed-by-agent` label"), 1)
        self.assertEqual(
            lines[-1], "AUDIT: landed landed=2 followed-up=1 unlabeled=1 unknown=0"
        )

    def test_a_trailer_quoted_mid_line_is_not_a_landing(self):
        self.fake.prs = [pr(55, "2026-11-02T10:00:00Z")]
        self.fake.messages = {
            sha(1055): "docs: say `Landed-by: land-pr.sh` is the trailer\n"
        }
        _, lines = self.run_audit("--mode", "landed")
        self.assertEqual(
            lines[-1], "AUDIT: landed landed=0 followed-up=0 unlabeled=0 unknown=0"
        )

    def test_an_unreadable_merge_commit_is_unknown_not_a_crash(self):
        self.fake.prs = [pr(56, "2026-11-02T10:00:00Z")]
        self.fake.fail = {"/commits/"}
        code, lines = self.run_audit("--mode", "landed")
        self.assertEqual(code, 0)
        self.assertIn("#56 unknown", lines[0])
        self.assertTrue(lines[-1].endswith("unknown=1"))


class UsageTests(Base):
    def test_bad_since_and_policy_outside_shadow_are_usage_errors(self):
        for argv in (
            ["--repo", REPO, "--since", "11/01/2026", "--mode", "review"],
            ["--repo", REPO, "--since", SINCE, "--mode", "review", "--policy", "x.yml"],
            ["--repo", REPO, "--since", SINCE, "--mode", "landed", "--owner", "x"],
            [
                "--repo",
                REPO,
                "--since",
                SINCE,
                "--mode",
                "shadow",
                "--rule-suites",
                "x",
            ],
            [
                "--repo",
                REPO,
                "--since",
                SINCE,
                "--mode",
                "review",
                "--rule-suites",
                "/nonexistent/x.json",
            ],
            ["--repo", "widgets", "--since", SINCE, "--mode", "review"],
        ):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    audit.main(argv)
                self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
