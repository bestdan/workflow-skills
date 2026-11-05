#!/usr/bin/env python3
"""Hermetic tests for scripts/morning-digest.py.

`gh` is replaced by FakeGh, one in-memory repository answering the PR list,
the lander's per-PR tier reads, and the issue reads and writes the digest
makes. Fixture PR bodies carry real `## Decision brief` sections.
"""

import base64
import contextlib
import datetime as dt
import importlib.util
import io
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "morning_digest", ROOT / "scripts" / "morning-digest.py"
)
assert _spec is not None and _spec.loader is not None
digest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(digest)
land = digest.land

REPO = "acme/widgets"
TODAY = dt.date(2026, 11, 4)
POLICY = """\
auto:
  - "scripts/**"
allowed_types: [chore, fix, feat]
"""
AUTO_FILES = [{"filename": "scripts/a.py", "additions": 1, "deletions": 0}]
HUMAN_FILES = [{"filename": "skills/x/SKILL.md", "additions": 1, "deletions": 0}]


def brief(changed="does a thing", judge="is it right", no="the follow-up"):
    return (
        "Summary.\n\n## Decision brief\n\n"
        f"- **Changed:** {changed}\n- **Judge:** {judge}\n"
        f"- **A no invalidates:** {no}\n\n## Evidence\n\n- **Judge:** not this\n"
    )


def pr(number, files, body="", base="main", head=None, draft=False):
    return {
        "number": number,
        "title": f"fix(x): change {number}",
        "url": f"https://github.com/{REPO}/pull/{number}",
        "body": body,
        "baseRefName": base,
        "headRefName": head or f"branch-{number}",
        "isDraft": draft,
        "_files": files,
    }


class FakeGh:
    def __init__(self):
        self.prs = []
        self.policies = {"main": POLICY}
        self.issues = []
        self.fail = set()  # joined-arg substrings whose call fails
        self.calls = []
        self.bodies = []  # --body-file contents, read when the call is made

    def by_number(self, n):
        return next(p for p in self.prs if p["number"] == int(n))

    def __call__(self, args, stdin=None):
        self.calls.append(list(args))
        joined = " ".join(args)
        if any(f in joined for f in self.fail):
            return 1, "", "gh: boom (HTTP 500)"
        if "--body-file" in args:
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
        if args[:2] == ["pr", "list"]:
            listed = [{k: v for k, v in p.items() if k != "_files"} for p in self.prs]
            return 0, json.dumps(listed), ""
        if args[:2] == ["pr", "view"]:
            p = self.by_number(args[2])
            data = {k: v for k, v in p.items() if k != "_files"}
            data.update(state="OPEN", headRefOid="a" * 40, labels=[])
            return 0, json.dumps(data), ""
        if args[:2] == ["issue", "edit"]:
            issue = next(i for i in self.issues if i["number"] == int(args[2]))
            issue["body"] = self.bodies[-1]
            return 0, issue["url"] + "\n", ""
        if args[:2] == ["issue", "create"]:
            n = 50 + len(self.issues)
            url = f"https://github.com/{REPO}/issues/{n}"
            self.issues.append({"number": n, "body": self.bodies[-1], "url": url})
            return 0, url + "\n", ""
        if args[:2] == ["issue", "pin"]:
            return 0, "", ""
        path = next(a for a in args if a.startswith("repos/"))
        if path == f"repos/{REPO}/issues?state=open&per_page=100":
            # REST lists PRs as issues too; one carries the marker as a decoy.
            items = [
                {"number": i["number"], "body": i["body"], "html_url": i["url"]}
                for i in self.issues
            ]
            pr_item = {"number": 2, "body": digest.MARKER, "pull_request": {}}
            return 0, json.dumps([items, [pr_item]]), ""
        if path.endswith("/files"):
            p = self.by_number(path.split("/")[4])
            files = [dict(f, status="modified") for f in p["_files"]]
            return 0, json.dumps([files]), ""
        if "/contents/.github/merge-policy.yml?ref=" in path:
            ref = path.split("?ref=", 1)[1]
            if ref not in self.policies:
                return 1, "", "gh: Not Found (HTTP 404)"
            content = base64.b64encode(self.policies[ref].encode()).decode()
            return 0, json.dumps({"encoding": "base64", "content": content}), ""
        raise AssertionError(f"unexpected gh call: {joined}")


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGh()
        original = land.run_gh
        land.run_gh = self.fake
        self.addCleanup(setattr, land, "run_gh", original)

    def run_digest(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = digest.main(["--repo", REPO, *extra], today=TODAY)
        return code, out.getvalue(), err.getvalue()

    def headings(self, out):
        return [ln for ln in out.splitlines() if ln.startswith("#")]


class BriefTests(unittest.TestCase):
    def test_a_full_brief(self):
        fields, missing = digest.parse_brief(brief())
        self.assertEqual(
            fields,
            {
                "Changed": "does a thing",
                "Judge": "is it right",
                "A no invalidates": "the follow-up",
            },
        )
        self.assertEqual(missing, [])

    def test_a_wrapped_line_and_crlf(self):
        body = (
            "## Decision brief\r\n\r\n- **Changed:** the digest now\r\n"
            "  groups stacks\r\n- **Judge:** order\r\n- **A no invalidates:** #2\r\n"
        )
        fields, missing = digest.parse_brief(body)
        self.assertEqual(fields["Changed"], "the digest now groups stacks")
        self.assertEqual(missing, [])

    def test_no_section(self):
        self.assertEqual(
            digest.parse_brief("## Summary\n\n- **Judge:** x\n")[1],
            [
                "Changed",
                "Judge",
                "A no invalidates",
            ],
        )

    def test_a_section_missing_one_field(self):
        body = "## Decision brief\n\n- **Changed:** a\n- **Judge:** b\n"
        self.assertEqual(digest.parse_brief(body)[1], ["A no invalidates"])

    def test_a_level_three_heading_does_not_end_the_section(self):
        body = "## Decision brief\n\n- **Changed:** a\n### note\n- **Judge:** b\n"
        self.assertIn("Judge", digest.parse_brief(body)[0])


class DigestTests(Base):
    def test_lists_human_prs_and_excludes_auto_and_draft(self):
        self.fake.prs = [
            pr(1, HUMAN_FILES, brief()),
            pr(2, AUTO_FILES, brief()),
            pr(3, HUMAN_FILES, brief(), draft=True),
            pr(4, HUMAN_FILES, "no brief here"),
            pr(5, HUMAN_FILES, "## Decision brief\n\n- **Changed:** only this\n"),
        ]
        code, out, err = self.run_digest()
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], digest.MARKER)
        self.assertEqual(lines[1], "# Morning digest — 2026-11-04")
        self.assertIn("3 PRs wait on the owner.", out)
        self.assertEqual(
            self.headings(out)[1:],
            [
                "## #1 fix(x): change 1",
                "## #4 fix(x): change 4",
                "## #5 fix(x): change 5",
            ],
        )
        self.assertIn("- **Judge:** is it right", out)
        self.assertNotIn("not this", out)
        self.assertIn("Tier: human — ", out)
        self.assertIn("- brief missing: Changed, Judge, A no invalidates", out)
        self.assertIn(
            "- **Changed:** only this\n- brief missing: Judge, A no invalidates", out
        )
        self.assertFalse(
            any(c[:2] == ["pr", "view"] and c[2] == "3" for c in self.fake.calls)
        )
        self.assertEqual(err, "")

    def test_a_mixed_stack_prints_bottom_first(self):
        # Listed out of order; #20 (auto) is the bottom, #22 the top. Only
        # main carries a policy: the upper PRs are tiered by the stack's base.
        self.fake.prs = [
            pr(22, HUMAN_FILES, brief(changed="top"), base="b21", head="b22"),
            pr(20, AUTO_FILES, brief(), base="main", head="b20"),
            pr(21, HUMAN_FILES, brief(changed="middle"), base="b20", head="b21"),
        ]
        code, out, _ = self.run_digest()
        self.assertEqual(code, 0)
        self.assertEqual(
            self.headings(out)[1:],
            [
                "## Stack: #20 → #21 → #22",
                "### #20 fix(x): change 20",
                "### #21 fix(x): change 21",
                "### #22 fix(x): change 22",
            ],
        )
        self.assertIn(
            f"https://github.com/{REPO}/pull/20 — auto tier, lands itself", out
        )
        self.assertIn("2 PRs wait on the owner.", out)
        self.assertNotIn("Tier: unknown", out)
        self.assertLess(out.index("middle"), out.index("top"))

    def test_siblings_on_one_parent_share_a_level(self):
        self.fake.prs = [
            pr(60, HUMAN_FILES, brief(), base="main", head="b60"),
            pr(62, HUMAN_FILES, brief(), base="b60", head="b62"),
            pr(61, HUMAN_FILES, brief(), base="b60", head="b61"),
        ]
        _, out, _ = self.run_digest()
        self.assertIn("## Stack: #60 → #61, #62", out)

    def test_a_fork_pr_headed_main_is_no_stack_parent(self):
        fork = pr(40, HUMAN_FILES, brief(), base="main", head="main")
        fork["isCrossRepository"] = True
        self.fake.prs = [
            fork,
            pr(41, HUMAN_FILES, brief()),
            pr(42, HUMAN_FILES, brief()),
        ]
        _, out, _ = self.run_digest()
        self.assertNotIn("Stack", out)
        self.assertIn("3 PRs wait on the owner.", out)

    def test_a_full_pr_list_warns_that_older_prs_are_left_out(self):
        self.fake.prs = [pr(n, AUTO_FILES) for n in range(1, digest.PR_LIMIT + 1)]
        code, _, err = self.run_digest()
        self.assertEqual(code, 0)
        self.assertIn(f"listed {digest.PR_LIMIT} open PRs, the limit", err)

    def test_a_stack_with_no_human_member_is_left_out(self):
        self.fake.prs = [
            pr(30, AUTO_FILES, base="main", head="b30"),
            pr(31, AUTO_FILES, base="b30", head="b31"),
        ]
        _, out, _ = self.run_digest()
        self.assertIn("No human-tier PRs are waiting.", out)
        self.assertNotIn("Stack", out)

    def test_singletons_are_ordered_by_number(self):
        self.fake.prs = [pr(9, HUMAN_FILES, brief()), pr(3, HUMAN_FILES, brief())]
        _, out, _ = self.run_digest()
        self.assertEqual(
            self.headings(out)[1:], ["## #3 fix(x): change 3", "## #9 fix(x): change 9"]
        )

    def test_a_policy_read_failure_is_unknown_with_the_reason(self):
        self.fake.prs = [pr(7, AUTO_FILES, brief(), base="release")]
        code, out, _ = self.run_digest()
        self.assertEqual(code, 0)
        self.assertIn(
            "Tier: unknown — tier not computed: no .github/merge-policy.yml", out
        )
        self.assertIn("1 PR waits on the owner.", out)

    def test_an_unreadable_pr_list_exits_1(self):
        self.fake.fail = {"pr list"}
        code, out, err = self.run_digest()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("cannot list open PRs", err)

    def test_a_bad_repo_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                digest.main(["--repo", "nope"])
        self.assertEqual(cm.exception.code, 2)


class PostTests(Base):
    def setUp(self):
        super().setUp()
        self.fake.prs = [pr(1, HUMAN_FILES, brief())]

    def test_creates_and_pins_when_no_issue_exists(self):
        self.fake.issues = [
            {"number": 4, "body": "unrelated morning-digest v1", "url": "u"}
        ]
        code, out, err = self.run_digest("--post")
        self.assertEqual(code, 0, err)
        url = f"https://github.com/{REPO}/issues/51"
        self.assertEqual(err.splitlines()[-1], f"DIGEST: created {url}")
        self.assertEqual(self.fake.bodies, [out])
        self.assertIn(["issue", "pin", "51", "--repo", REPO], self.fake.calls)

    def test_a_failed_pin_is_a_warning(self):
        self.fake.fail = {"issue pin"}
        code, _, err = self.run_digest("--post")
        self.assertEqual(code, 0)
        self.assertIn("warning: could not pin", err)
        self.assertTrue(err.splitlines()[-1].startswith("DIGEST: created "))

    def test_updates_the_marked_issue_in_place(self):
        url = f"https://github.com/{REPO}/issues/12"
        self.fake.issues = [
            # Quotes the marker mid-body: a human's issue, never overwritten.
            {"number": 9, "body": f"see {digest.MARKER} here", "url": "u9"},
            {"number": 15, "body": digest.MARKER + "\nnewer copy", "url": "u15"},
            {"number": 12, "body": digest.MARKER + "\nold", "url": url},
        ]
        code, out, err = self.run_digest("--post")
        self.assertEqual(self.fake.issues[1]["body"], digest.MARKER + "\nnewer copy")
        self.assertEqual(code, 0, err)
        self.assertEqual(err.splitlines()[-1], f"DIGEST: updated {url}")
        self.assertEqual(self.fake.issues[2]["body"], out)
        self.assertFalse(any(c[:2] == ["issue", "create"] for c in self.fake.calls))

    def test_an_identical_body_is_unchanged(self):
        _, out, _ = self.run_digest()
        url = f"https://github.com/{REPO}/issues/12"
        self.fake.issues = [{"number": 12, "body": out.rstrip("\n"), "url": url}]
        code, _, err = self.run_digest("--post")
        self.assertEqual(code, 0)
        self.assertEqual(err.splitlines()[-1], f"DIGEST: unchanged {url}")
        self.assertFalse(any(c[:2] == ["issue", "edit"] for c in self.fake.calls))

    def test_a_failed_post_exits_1_with_the_digest_printed(self):
        self.fake.fail = {"issue create"}
        code, out, err = self.run_digest("--post")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith(digest.MARKER))
        self.assertIn("## #1 fix(x): change 1", out)
        self.assertTrue(err.splitlines()[-1].startswith("DIGEST: error reason="))


if __name__ == "__main__":
    unittest.main()
