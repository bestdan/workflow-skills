#!/usr/bin/env python3
"""Hermetic tests for scripts/land-pr.py: a would-land case and every hold.

`gh` is replaced by FakeGh, one in-memory repository answering exactly the
calls the lander makes. The evidence stamp is written with co-review-stamp.py's
own body format, so the reader under test parses the real thing.
"""

import base64
import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


land = _load("land_pr", ROOT / "scripts" / "land-pr.py")
stamp = land.evidence_mod.stamp

REPO, PR = "acme/widgets", 7
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
MERGE_SHA = "c" * 40
ME = "lander-bot"
POLICY = """\
auto:
  - "scripts/**"
always_human:
  - scripts/deploy.sh
allowed_types: [chore, fix]
"""
PERMISSIVE = """\
auto:
  - "**"
allowed_types: [chore, fix, feat]
"""


def clean_evidence(**overrides):
    rec = {
        "head": HEAD,
        "unresolved_findings": 0,
        "verification_open": 0,
        "consult_calls": [{"confidence": 85, "kind": "technical"}],
        "next_round": "no further round",
    }
    rec.update(overrides)
    return rec


def not_found():
    return 1, "", "gh: Not Found (HTTP 404)"


class FakeGh:
    """One repository whose every PR-facing fact a test can set."""

    def __init__(self):
        self.calls = []
        self.fail = set()  # path fragments whose call fails with rc 1
        self.pr = {
            "number": PR,
            "url": f"https://github.com/{REPO}/pull/{PR}",
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": HEAD,
            "baseRefName": "main",
            "headRefName": "feature",
            "title": "chore(scripts): tidy the helper",
            "body": "",
            "labels": [],
        }
        self.files = [
            {
                "filename": "scripts/helper.py",
                "additions": 3,
                "deletions": 1,
                "status": "modified",
            }
        ]
        self.policies = {"main": POLICY, "feature": PERMISSIVE}
        self.agent_land: Optional[str] = "on"
        self.statuses = {"co-review": "success"}
        self.status_page_2: List[dict] = []
        self.check_runs: List[dict] = [
            {"name": "check", "status": "completed", "conclusion": "success"}
        ]
        self.required = ["check"]
        self.comments = []
        self.next_id = 100
        self.set_evidence(clean_evidence())
        # The PR's head on GitHub at merge time; a push after the check moves it.
        self.head_at_merge = HEAD
        self.merges: List[dict] = []
        self.labels_added: List[str] = []
        self.repo_labels = {"landed-by-agent"}
        self.labels_created: List[str] = []
        # A merge call that fails on the client after GitHub merged (a timeout).
        self.merge_fails_after_merging = False
        # The merge commit's message; None means the body the lander sent.
        self.merge_message: Optional[str] = None

    # --- fixture helpers --------------------------------------------------

    def add_comment(self, body, login=ME):
        self.next_id += 1
        self.comments.append(
            {
                "id": self.next_id,
                "login": login,
                "body": body,
                "url": f"https://github.com/{REPO}/pull/{PR}#c{self.next_id}",
            }
        )

    def set_evidence(self, record, head=HEAD):
        self.comments = [
            c for c in self.comments if not c["body"].startswith(stamp.MARKER_PREFIX)
        ]
        if record is not None:
            self.add_comment(stamp.stamp_body(head, "claude ✅ · codex ✅", record))

    def lander_comments(self):
        return [c for c in self.comments if c["body"].startswith(land.MARKER)]

    # --- the seam ---------------------------------------------------------

    def run_gh(self, args, stdin=None):
        self.calls.append(list(args))
        if any(f in " ".join(args) for f in self.fail):
            return 1, "", "gh: boom (HTTP 500)"
        if args[:2] == ["pr", "view"]:
            if any("mergeCommit" in a for a in args):
                merged = self.pr["state"] == "MERGED"
                return (
                    0,
                    json.dumps(
                        {
                            "state": self.pr["state"],
                            "headRefOid": self.pr["headRefOid"],
                            "mergeCommit": {"oid": MERGE_SHA} if merged else None,
                        }
                    ),
                    "",
                )
            return 0, json.dumps(self.pr), ""
        if args[:2] == ["pr", "merge"]:
            return self.merge(args)
        if args[:2] == ["pr", "edit"]:
            label = args[args.index("--add-label") + 1]
            if label not in self.repo_labels:
                return 1, "", f"'{label}' not found"
            self.labels_added.append(label)
            return 0, "", ""
        if args[:2] == ["label", "create"]:
            if args[2] in self.repo_labels:
                return 1, "", f'label with name "{args[2]}" already exists'
            self.repo_labels.add(args[2])
            self.labels_created.append(args[2])
            return 0, "", ""
        assert args[0] == "api", args
        path = next(a for a in args if a.startswith("repos/") or a == "user")
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        if path == "user":
            return 0, ME + "\n", ""
        base = f"repos/{REPO}"
        if path == f"{base}/commits/{MERGE_SHA}" and self.pr["state"] == "MERGED":
            message = self.merge_message
            if message is None:
                message = (
                    "chore(scripts): tidy the helper (#7)\n\n" + self.merges[-1]["body"]
                )
            return 0, message + "\n", ""
        if path == f"{base}/pulls/{PR}/files":
            return 0, json.dumps([self.files]), ""
        if path.startswith(f"{base}/contents/.github/merge-policy.yml?ref="):
            ref = path.split("?ref=", 1)[1]
            if ref not in self.policies:
                return not_found()
            content = base64.b64encode(self.policies[ref].encode()).decode()
            return 0, json.dumps({"encoding": "base64", "content": content}), ""
        if path == f"{base}/actions/variables/AGENT_LAND":
            if self.agent_land is None:
                return not_found()
            return 0, self.agent_land + "\n", ""
        if path == f"{base}/commits/{HEAD}/status":
            statuses = [{"context": k, "state": v} for k, v in self.statuses.items()]
            pages = [{"state": "x", "statuses": statuses}]
            if self.status_page_2:
                pages.append({"state": "x", "statuses": self.status_page_2})
            return 0, json.dumps(pages), ""
        if path == f"{base}/commits/{HEAD}/check-runs":
            return 0, json.dumps([{"check_runs": self.check_runs}]), ""
        if path == f"{base}/rules/branches/main":
            rules: List[dict] = [{"type": "deletion"}]
            if self.required:
                rules.append(
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "required_status_checks": [
                                {"context": c} for c in self.required
                            ]
                        },
                    }
                )
            return 0, json.dumps([rules]), ""
        if path == f"{base}/issues/{PR}/comments" and method == "GET":
            return 0, "".join(json.dumps(c) + "\n" for c in self.comments), ""
        if path == f"{base}/issues/{PR}/comments" and method == "POST":
            self.add_comment(json.loads(stdin)["body"])
            return 0, json.dumps({"html_url": self.comments[-1]["url"]}), ""
        if path.startswith(f"{base}/issues/comments/") and method == "PATCH":
            cid = int(path.rsplit("/", 1)[1])
            target = next(c for c in self.comments if c["id"] == cid)
            target["body"] = json.loads(stdin)["body"]
            return 0, json.dumps({"html_url": target["url"]}), ""
        raise AssertionError(f"unexpected gh call: {args}")

    def merge(self, args):
        # GitHub's own refusal when --match-head-commit is not the PR's head.
        pinned = args[args.index("--match-head-commit") + 1]
        body = Path(args[args.index("--body-file") + 1]).read_text()
        self.merges.append({"args": list(args), "body": body})
        if pinned != self.head_at_merge:
            return (
                1,
                "",
                "GraphQL: Head branch was modified. Review and try the merge again."
                " (mergePullRequest)",
            )
        self.pr["state"] = "MERGED"
        if self.merge_fails_after_merging:
            return 127, "", "timed out after 60 seconds"
        return 0, "", ""


class LandTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGh()
        orig = land.run_gh
        land.run_gh = self.fake.run_gh
        self.addCleanup(setattr, land, "run_gh", orig)

    def run_land(self, *extra, dry_run=True):
        argv = [str(PR), "--repo", REPO, *(["--dry-run"] if dry_run else []), *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = land.main(argv)
        return code, out.getvalue(), err.getvalue()

    def land_line(self):
        code, out, _ = self.run_land()
        self.assertEqual(code, 0, out)
        last = out.splitlines()[-1]
        self.assertTrue(last.startswith("LAND: "), out)
        return last

    def assert_hold(self, reason):
        line = self.land_line()
        self.assertTrue(line.startswith("LAND: hold reasons="), line)
        self.assertIn(reason, line)
        body = self.fake.lander_comments()[0]["body"]
        self.assertIn("would land: **no**", body)
        self.assertIn(reason, body)

    # --- would land -------------------------------------------------------

    def test_a_clean_pr_would_land(self):
        code, out, _ = self.run_land()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[-1], "LAND: would-land")
        self.assertTrue(out.splitlines()[0].startswith("COMMENT: created "), out)
        (comment,) = self.fake.lander_comments()
        self.assertIn(f"dry run on `{HEAD[:7]}`: would land: **yes**", comment["body"])
        self.assertIn("all 1 paths match auto", comment["body"])

    def test_a_carried_success_would_land_and_says_where_from(self):
        reviewed = "c" * 40
        self.fake.set_evidence(clean_evidence(carried_from=reviewed))
        self.assertEqual(self.land_line(), "LAND: would-land")
        body = self.fake.lander_comments()[0]["body"]
        self.assertIn(f"success on {HEAD[:7]} (carried from {reviewed[:7]})", body)

    def test_a_dry_run_never_merges(self):
        self.run_land()
        for call in self.fake.calls:
            self.assertNotIn("merge", call[:2], call)
            self.assertFalse(any(a.endswith("/merge") for a in call), call)

    # --- the co-review status and CI -------------------------------------

    def test_co_review_status_missing_holds(self):
        del self.fake.statuses["co-review"]
        self.assert_hold(f"`co-review` status on {HEAD[:7]} is missing, not success")

    def test_co_review_status_pending_holds(self):
        self.fake.statuses["co-review"] = "pending"
        self.assert_hold(f"`co-review` status on {HEAD[:7]} is pending, not success")

    def test_a_red_check_holds(self):
        self.fake.check_runs[0]["conclusion"] = "failure"
        self.assert_hold("check `check` is failure")

    def test_a_red_commit_status_holds(self):
        self.fake.statuses["lint"] = "error"
        self.assert_hold("check `lint` is error")

    def test_a_red_status_on_a_later_page_holds(self):
        self.fake.status_page_2 = [{"context": "legacy-ci", "state": "failure"}]
        self.assert_hold("check `legacy-ci` is failure")

    def test_a_pending_check_holds(self):
        self.fake.check_runs[0] = {"name": "check", "status": "in_progress"}
        self.assert_hold("check `check` is pending")

    def test_a_required_check_that_never_reported_holds(self):
        self.fake.required = ["check", "e2e"]
        self.assert_hold("required check `e2e` has not reported")

    def test_no_checks_at_all_holds(self):
        self.fake.check_runs, self.fake.required = [], []
        self.assert_hold("no checks have reported on the head")

    def test_skipped_and_neutral_checks_are_green(self):
        self.fake.check_runs += [
            {"name": "docs", "status": "completed", "conclusion": "skipped"},
            {"name": "info", "status": "completed", "conclusion": "neutral"},
        ]
        self.assertEqual(self.land_line(), "LAND: would-land")

    def test_a_required_co_review_is_judged_by_its_status_only(self):
        self.fake.required = ["check", "co-review"]
        self.assertEqual(self.land_line(), "LAND: would-land")

    # --- the tier ---------------------------------------------------------

    def test_tier_human_holds(self):
        self.fake.files[0]["filename"] = "README.md"
        self.assert_hold("tier human: README.md matches no auto glob")

    def test_a_rename_carries_its_previous_path_into_the_tier(self):
        self.fake.files[0].update(
            filename="scripts/ship.sh",
            status="renamed",
            previous_filename="scripts/deploy.sh",
        )
        self.assert_hold("tier human: scripts/deploy.sh matches always_human")

    def test_a_file_without_status_holds(self):
        del self.fake.files[0]["status"]
        self.assert_hold("tier human: input invalid")

    def test_the_policy_is_read_from_the_base_never_the_head(self):
        # The head branch's policy would allow README.md; the base's does not.
        self.fake.files[0]["filename"] = "README.md"
        self.assert_hold("README.md matches no auto glob")
        refs = [a for call in self.fake.calls for a in call if "?ref=" in a]
        self.assertEqual(
            refs, [f"repos/{REPO}/contents/.github/merge-policy.yml?ref=main"]
        )

    def test_no_policy_on_the_base_holds(self):
        del self.fake.policies["main"]
        self.assert_hold(
            "tier not computed: no .github/merge-policy.yml on base `main`"
        )

    def test_an_invalid_policy_holds(self):
        self.fake.policies["main"] = "auto: scripts/**\n"
        self.assert_hold("tier human: policy invalid")

    def test_agent_land_off_holds(self):
        self.fake.agent_land = "off"
        self.assert_hold("tier human: AGENT_LAND=off")

    def test_agent_land_unset_is_off(self):
        self.fake.agent_land = None
        self.assert_hold("tier human: AGENT_LAND=off")

    def test_agent_land_garbage_is_off_and_says_so(self):
        self.fake.agent_land = "yes"
        self.assert_hold("AGENT_LAND is `yes`, neither on nor off")

    # --- the TIER line ----------------------------------------------------

    def tier_line(self):
        _, out, _ = self.run_land()
        lines = out.splitlines()
        self.assertTrue(lines[-2].startswith("TIER: "), out)
        return lines[-2]

    def test_a_clean_pr_is_tier_auto(self):
        self.assertEqual(self.tier_line(), "TIER: auto")

    def test_the_tier_line_ignores_agent_land_off(self):
        # Shadow mode: the LAND line holds on the kill switch, TIER does not.
        self.fake.agent_land = "off"
        self.assertEqual(self.tier_line(), "TIER: auto")
        self.assertIn("AGENT_LAND=off", self.land_line())

    def test_a_human_path_is_tier_human_with_its_reasons(self):
        self.fake.files[0]["filename"] = "README.md"
        line = self.tier_line()
        self.assertTrue(line.startswith("TIER: human reasons="), line)
        self.assertIn("README.md matches no auto glob", line)

    def test_no_policy_is_tier_unknown(self):
        del self.fake.policies["main"]
        self.assertEqual(
            self.tier_line(),
            "TIER: unknown reason=tier not computed: "
            "no .github/merge-policy.yml on base `main`",
        )

    def test_an_unreadable_pr_is_tier_unknown(self):
        self.fake.fail.add("pr view")
        self.assertTrue(
            self.tier_line().startswith("TIER: unknown reason=cannot read the PR")
        )

    # --- evidence ---------------------------------------------------------

    def test_evidence_missing_holds(self):
        self.fake.set_evidence(None)
        self.assert_hold(f"no co-review evidence for head {HEAD[:7]}")

    def test_evidence_for_another_head_holds(self):
        self.fake.set_evidence(clean_evidence(head=OTHER_HEAD), head=OTHER_HEAD)
        self.assert_hold(f"no co-review evidence for head {HEAD[:7]}")

    def test_unreadable_evidence_holds(self):
        body = stamp.stamp_body(HEAD, "claude ✅", clean_evidence()).replace(
            '"next_round": "no further round"', '"next_round": "maybe"'
        )
        self.fake.set_evidence(None)
        self.fake.add_comment(body)
        self.assert_hold("evidence unreadable")

    def test_an_unresolved_finding_holds(self):
        self.fake.set_evidence(clean_evidence(unresolved_findings=1))
        self.assert_hold("evidence: 1 unresolved finding(s)")

    def test_an_open_verification_item_holds(self):
        self.fake.set_evidence(clean_evidence(verification_open=2))
        self.assert_hold("evidence: 2 verification item(s) open")

    def test_a_consult_call_below_the_bar_holds(self):
        self.fake.set_evidence(
            clean_evidence(consult_calls=[{"confidence": 84, "kind": "technical"}])
        )
        self.assert_hold("evidence: a consult call at 84% is below the 85% bar")

    def test_the_policy_consult_bar_overrides_the_default(self):
        self.fake.policies["main"] = POLICY + "consult_bar: 90\n"
        self.assert_hold("evidence: a consult call at 85% is below the 90% bar")

    def test_a_preference_call_holds_at_any_confidence(self):
        self.fake.set_evidence(
            clean_evidence(consult_calls=[{"confidence": 100, "kind": "preference"}])
        )
        self.assert_hold("evidence: a preference call")

    def test_a_stack_call_holds_at_any_confidence(self):
        self.fake.set_evidence(
            clean_evidence(consult_calls=[{"confidence": 100, "kind": "stack"}])
        )
        self.assert_hold("evidence: a stack-reaching call awaits the owner")

    def test_another_round_holds(self):
        self.fake.set_evidence(clean_evidence(next_round="another round"))
        self.assert_hold("evidence: co-review recommends another round")

    # --- the PR and read errors -------------------------------------------

    def test_a_draft_holds(self):
        self.fake.pr["isDraft"] = True
        self.assert_hold("PR is a draft")

    def test_a_closed_pr_holds(self):
        self.fake.pr["state"] = "MERGED"
        self.assert_hold("PR is merged")

    def test_an_unreadable_pr_holds(self):
        self.fake.fail.add("pr view")
        line = self.land_line()
        self.assertIn("cannot read the PR", line)
        self.assertIn("would land: **no**", self.fake.lander_comments()[0]["body"])

    def test_unreadable_files_hold(self):
        self.fake.fail.add("/files")
        self.assert_hold(
            "tier not computed: gh repos/acme/widgets/pulls/7/files failed"
        )

    def test_unreadable_statuses_hold(self):
        self.fake.fail.add(f"commits/{HEAD}/status")
        self.assert_hold("cannot read commit statuses")

    def test_unreadable_check_runs_hold(self):
        self.fake.fail.add("check-runs")
        self.assert_hold("cannot read checks")

    def test_unreadable_agent_land_holds(self):
        self.fake.fail.add("AGENT_LAND")
        self.assert_hold("cannot read AGENT_LAND")

    # --- the comment ------------------------------------------------------

    def test_the_comment_is_refreshed_not_duplicated(self):
        self.assertIn("COMMENT: created", self.run_land()[1])
        self.assertIn("COMMENT: unchanged", self.run_land()[1])
        self.fake.statuses["co-review"] = "failure"
        self.assertIn("COMMENT: updated", self.run_land()[1])
        (comment,) = self.fake.lander_comments()
        self.assertIn("would land: **no**", comment["body"])

    def test_another_accounts_comment_is_left_alone(self):
        self.fake.add_comment(land.MARKER + "\nsomeone else", login="intruder")
        self.run_land()
        bodies = [c["body"] for c in self.fake.lander_comments()]
        self.assertEqual(len(bodies), 2)
        self.assertIn(land.MARKER + "\nsomeone else", bodies)

    def test_a_failed_comment_exits_1_and_still_prints_the_verdict(self):
        self.fake.fail.add("--method POST")
        code, out, _ = self.run_land()
        self.assertEqual(code, 1)
        self.assertIn("COMMENT: error", out)
        self.assertEqual(out.splitlines()[-1], "LAND: would-land")
        self.assertEqual(out.splitlines()[-2], "TIER: auto")


class RealLandingTests(unittest.TestCase):
    """The path without --dry-run: AGENT_LAND, the pinned merge, the trailers."""

    def setUp(self):
        self.fake = FakeGh()
        orig = land.run_gh
        land.run_gh = self.fake.run_gh
        self.addCleanup(setattr, land, "run_gh", orig)

    def land(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = land.main([str(PR), "--repo", REPO])
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue().splitlines()

    def merge_calls(self):
        return [c for c in self.fake.calls if c[:2] == ["pr", "merge"]]

    def assert_held_without_merging(self, lines, reason):
        self.assertTrue(lines[-1].startswith("LAND: hold reasons="), lines)
        self.assertIn(reason, lines[-1])
        self.assertEqual(self.merge_calls(), [])

    def test_a_clean_pr_lands_squashed_and_pinned_to_the_judged_head(self):
        lines = self.land()
        self.assertEqual(lines[-1], f"LAND: landed sha={MERGE_SHA}")
        (call,) = self.merge_calls()
        self.assertIn("--squash", call)
        self.assertEqual(call[call.index("--match-head-commit") + 1], HEAD)

    def test_agent_land_unset_never_merges(self):
        self.fake.agent_land = None
        self.assert_held_without_merging(self.land(), "AGENT_LAND is off or unset")

    def test_agent_land_off_never_merges(self):
        self.fake.agent_land = "off"
        self.assert_held_without_merging(self.land(), "AGENT_LAND is off or unset")

    def test_agent_land_garbage_never_merges(self):
        self.fake.agent_land = "yes"
        self.assert_held_without_merging(self.land(), "AGENT_LAND is `yes`")

    def test_unreadable_agent_land_never_merges(self):
        self.fake.fail.add("AGENT_LAND")
        self.assert_held_without_merging(self.land(), "cannot read AGENT_LAND")

    def test_a_failed_dry_run_check_holds_without_merging(self):
        self.fake.statuses["co-review"] = "failure"
        self.assert_held_without_merging(self.land(), "`co-review` status")

    def test_a_head_moved_since_the_check_holds(self):
        self.fake.head_at_merge = OTHER_HEAD
        lines = self.land()
        self.assertTrue(lines[-1].startswith("LAND: hold reasons="), lines)
        self.assertIn(f"head moved since the check on {HEAD[:7]}", lines[-1])
        (call,) = self.merge_calls()
        self.assertEqual(call[call.index("--match-head-commit") + 1], HEAD)
        self.assertEqual(self.fake.labels_added, [])
        self.assertFalse(
            any(c["body"].startswith(land.LANDED_MARKER) for c in self.fake.comments)
        )

    def test_another_merge_failure_holds_and_says_so(self):
        self.fake.fail.add("pr merge")
        self.assertIn("gh pr merge failed", self.land()[-1])

    def test_a_failed_call_that_merged_with_both_trailers_is_landed(self):
        self.fake.merge_fails_after_merging = True
        lines = self.land()
        self.assertEqual(lines[-1], f"LAND: landed sha={MERGE_SHA}")
        self.assertEqual(self.fake.labels_added, ["landed-by-agent"])
        self.assertTrue(
            any(c["body"].startswith(land.LANDED_MARKER) for c in self.fake.comments)
        )

    def test_a_failed_call_whose_merge_lacks_the_trailers_holds(self):
        # A hand merge at the same head in the same window: no Landed-by.
        self.fake.merge_fails_after_merging = True
        self.fake.merge_message = "chore(scripts): tidy the helper (#7)\n\n* tidy"
        lines = self.land()
        self.assertTrue(lines[-1].startswith("LAND: hold reasons="), lines)
        self.assertIn("gh pr merge failed: timed out after 60 seconds", lines[-1])
        self.assertEqual(self.fake.labels_added, [])

    def test_a_failed_call_whose_trailer_names_another_head_holds(self):
        self.fake.merge_fails_after_merging = True
        self.fake.merge_message = (
            "x (#7)\n\nLanded-by: land-pr.sh\nCo-review-head: " + OTHER_HEAD
        )
        self.assertIn("gh pr merge failed", self.land()[-1])

    def test_a_failed_call_that_did_not_merge_holds_with_its_reason(self):
        self.fake.fail.add("pr merge")
        lines = self.land()
        self.assertIn("gh pr merge failed: gh: boom (HTTP 500)", lines[-1])
        self.assertTrue(
            any("mergeCommit" in a for c in self.fake.calls for a in c),
            "the lander re-reads the PR after a failed merge call",
        )
        self.assertEqual(self.fake.labels_added, [])

    def test_a_failed_call_whose_re_read_fails_holds(self):
        self.fake.merge_fails_after_merging = True
        self.fake.fail.add("state,headRefOid,mergeCommit")
        self.assertIn("gh pr merge failed", self.land()[-1])

    def test_a_head_moved_refusal_is_not_re_read(self):
        self.fake.head_at_merge = OTHER_HEAD
        self.land()
        self.assertFalse(
            any("state,headRefOid,mergeCommit" in c for c in self.fake.calls)
        )

    def test_the_body_ends_with_both_trailers(self):
        self.land()
        body = self.fake.merges[0]["body"]
        self.assertTrue(
            body.rstrip("\n").endswith(
                f"\n\nLanded-by: land-pr.sh\nCo-review-head: {HEAD}"
            ),
            body,
        )
        # The record: the tier reasons, the verdict's head, the evidence pointer.
        self.assertIn("all 1 paths match auto", body)
        self.assertIn(f"judged on head {HEAD}", body)
        self.assertIn(f"co-review evidence for {HEAD[:7]}", body)

    def test_label_and_comment_follow_a_landing(self):
        lines = self.land()
        self.assertEqual(self.fake.labels_added, ["landed-by-agent"])
        self.assertIn("LABEL: added landed-by-agent", lines)
        (comment,) = [
            c for c in self.fake.comments if c["body"].startswith(land.LANDED_MARKER)
        ]
        self.assertIn(f"Merge commit: {MERGE_SHA}", comment["body"])
        self.assertIn(f"Co-review-head: {HEAD}", comment["body"])

    def test_a_missing_label_is_created_then_added(self):
        self.fake.repo_labels = set()
        lines = self.land()
        self.assertIn("LABEL: created and added landed-by-agent", lines)
        self.assertEqual(self.fake.labels_created, ["landed-by-agent"])
        self.assertEqual(self.fake.labels_added, ["landed-by-agent"])
        create = next(c for c in self.fake.calls if c[:2] == ["label", "create"])
        self.assertNotIn("--force", create)

    def test_an_existing_label_is_never_recreated(self):
        self.land()
        self.assertEqual(self.fake.labels_created, [])

    def test_a_label_that_cannot_be_created_is_reported_not_fatal(self):
        self.fake.repo_labels = set()
        self.fake.fail.add("label create")
        lines = self.land()
        self.assertTrue(any(line.startswith("LABEL: error") for line in lines))
        self.assertEqual(lines[-1], f"LAND: landed sha={MERGE_SHA}")

    def test_label_and_comment_failures_do_not_fail_the_landing(self):
        self.fake.fail.update({"pr edit", "--method POST"})
        lines = self.land()
        self.assertTrue(any(line.startswith("LABEL: error") for line in lines))
        self.assertTrue(any(line.startswith("COMMENT: error") for line in lines))
        self.assertEqual(lines[-1], f"LAND: landed sha={MERGE_SHA}")

    def test_the_landed_comment_is_not_read_as_a_dry_run_verdict(self):
        # merge-audit.py's shadow mode reads comments starting with MARKER.
        self.assertFalse(land.LANDED_MARKER.startswith(land.MARKER))


if __name__ == "__main__":
    unittest.main(verbosity=1)
