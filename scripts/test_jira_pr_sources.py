"""Hermetic tests for jira-pr-sources.py.

Loaded via importlib since the asset's filename is hyphenated. The one seam is
`run_acli`, stubbed per test, so no test needs `acli` or a network.

The fixture comments are the real bodies this plugin writes (`jira-claim.md`,
"Claim the issue" step 6 and "PR"), and the acli shape is the one
`acli jira workitem comment list --json` printed against a live site on
2026-11-06.
"""

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "jira-pr-sources.py"

_spec = importlib.util.spec_from_file_location("jira_pr_sources", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
sources = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sources)

CLAIM = "Claimed by /deliver-task. Working on branch `bestdan/IP-1032-inflation-factor`; PR link will follow."
OPENED = "PR opened: https://github.com/Betterment/retail/pull/63954"


def acli_comments(*bodies):
    return {
        "comments": [
            {"author": "A", "body": b, "id": str(i), "visibility": "public"}
            for i, b in enumerate(bodies)
        ],
        "isLast": True,
        "maxResults": 50,
        "startAt": 0,
        "total": len(bodies),
    }


def adf(*inlines):
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": list(inlines)}],
    }


def text(t, *marks):
    node = {"type": "text", "text": t}
    if marks:
        node["marks"] = [{"type": m} for m in marks]
    return node


class AcliStub:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if not self.replies:
            raise AssertionError(f"unscripted acli call: {args}")
        return self.replies.pop(0)


class SeamTestCase(unittest.TestCase):
    def setUp(self):
        real = sources.run_acli
        self.addCleanup(setattr, sources, "run_acli", real)

    def stub(self, *replies):
        acli = AcliStub(replies)
        sources.run_acli = acli
        return acli


class TestExtract(unittest.TestCase):
    def test_the_plugin_comments_yield_url_and_branch(self):
        urls, branch = sources.extract(acli_comments(CLAIM, OPENED)["comments"])
        self.assertEqual(urls, ["https://github.com/Betterment/retail/pull/63954"])
        self.assertEqual(branch, "bestdan/IP-1032-inflation-factor")

    def test_a_cited_pr_is_not_a_link(self):
        """Citation is not linkage: the over-close bug this sweep must not
        reproduce. Only a comment that starts `PR opened:` counts."""
        comments = acli_comments(
            "Blocked on https://github.com/acme/w/pull/9 merging first",
            "see PR opened: https://github.com/acme/w/pull/10",
        )["comments"]
        self.assertEqual(sources.extract(comments), ([], None))

    def test_every_pr_opened_is_kept_and_the_last_branch_wins(self):
        comments = acli_comments(
            "Claimed by /do-tasks. Working on branch `task/P-1`; PR link will follow.",
            "PR opened: https://github.com/acme/w/pull/1",
            "Claimed by /do-tasks. Working on branch `task/P-1-retry`; PR link will follow.",
            "PR opened: https://github.com/acme/w/pull/2",
            "PR opened: https://github.com/acme/w/pull/2",
        )["comments"]
        urls, branch = sources.extract(comments)
        self.assertEqual(
            urls,
            ["https://github.com/acme/w/pull/1", "https://github.com/acme/w/pull/2"],
        )
        self.assertEqual(branch, "task/P-1-retry")

    def test_acli_rendered_code_mark_without_backticks(self):
        """acli renders the ADF code mark as bare spaces (observed live)."""
        comments = acli_comments(
            "Claimed by /do-tasks. Working on branch  task/IP-722 ; PR link will follow."
        )["comments"]
        self.assertEqual(sources.extract(comments), ([], "task/IP-722"))

    def test_mcp_markdown_escaped_backticks(self):
        """The MCP's markdown rendering escapes the code mark (observed live)."""
        comments = acli_comments(
            "Claimed by /deliver-task. Working on branch "
            "\\`bestdan/IP-1032-inflation-factor\\`; PR link will follow."
        )["comments"]
        self.assertEqual(
            sources.extract(comments), ([], "bestdan/IP-1032-inflation-factor")
        )

    def test_markdown_escapes_inside_the_branch_are_undone(self):
        """`task/MY_PROJ-12` must not be cut at an escaped underscore."""
        comments = acli_comments(
            "Claimed by /do-tasks. Working on branch "
            "\\`task/MY\\_PROJ-12\\`; PR link will follow."
        )["comments"]
        self.assertEqual(sources.extract(comments), ([], "task/MY_PROJ-12"))

    def test_a_non_pr_url_is_dropped(self):
        comments = acli_comments("PR opened: https://example.com/nope")["comments"]
        self.assertEqual(sources.extract(comments), ([], None))

    def test_adf_bodies_match_with_code_marks_rewrapped(self):
        """The MCP / REST v3 shape: the branch arrives as a code-marked text
        node, with no literal backticks."""
        comments = [
            {
                "body": adf(
                    text("Claimed by /do-tasks. Working on branch "),
                    text("task/P-7", "code"),
                    text("; PR link will follow."),
                )
            },
            {
                "body": adf(
                    text("PR opened: "),
                    text("https://github.com/acme/w/pull/7", "link"),
                )
            },
        ]
        urls, branch = sources.extract(comments)
        self.assertEqual(urls, ["https://github.com/acme/w/pull/7"])
        self.assertEqual(branch, "task/P-7")

    def test_a_smart_link_carries_the_url_in_attrs(self):
        """Jira can store a pasted url as an inlineCard, with no text node."""
        card = {
            "type": "inlineCard",
            "attrs": {"url": "https://github.com/acme/w/pull/8"},
        }
        comments = [{"body": adf(text("PR opened: "), card)}]
        self.assertEqual(
            sources.extract(comments), (["https://github.com/acme/w/pull/8"], None)
        )

    def test_a_block_card_carries_the_url_in_attrs(self):
        card = {
            "type": "blockCard",
            "attrs": {"url": "https://github.com/acme/w/pull/9"},
        }
        comments = [{"body": adf(text("PR opened: "), card)}]
        self.assertEqual(
            sources.extract(comments), (["https://github.com/acme/w/pull/9"], None)
        )


class TestToRow(SeamTestCase):
    def test_mcp_shape_needs_no_acli(self):
        acli = self.stub()
        issue = {
            "id": "1",
            "key": "IP-1032",
            "fields": {"comment": {"comments": acli_comments(OPENED)["comments"]}},
        }
        row = sources.to_row(issue, use_acli=True)
        self.assertEqual(row["identifier"], "IP-1032")
        self.assertEqual(row["attachments"], [OPENED.split(": ", 1)[1]])
        self.assertEqual(row["source_errors"], [])
        self.assertEqual(acli.calls, [])

    def test_a_truncated_comment_page_is_a_source_error(self):
        """A cut-short thread may hide the `PR opened:` line, so the row must
        land in unresolved rather than read as no PR."""
        page = {"comments": acli_comments(CLAIM)["comments"], "total": 40}
        row = sources.to_row(
            {"id": "1", "key": "IP-9", "fields": {"comment": page}}, False
        )
        self.assertEqual(
            row["source_errors"], ["only 1 of 40 comments returned for IP-9"]
        )

    def test_a_complete_comment_page_is_not_truncated(self):
        page = {"comments": acli_comments(CLAIM)["comments"], "total": 1}
        row = sources.to_row(
            {"id": "1", "key": "IP-9", "fields": {"comment": page}}, False
        )
        self.assertEqual(row["source_errors"], [])

    def test_acli_fills_missing_comments(self):
        acli = self.stub((0, json.dumps(acli_comments(CLAIM, OPENED)), ""))
        row = sources.to_row({"id": "1", "key": "IP-1032"}, use_acli=True)
        self.assertEqual(row["branchName"], "bestdan/IP-1032-inflation-factor")
        self.assertEqual(len(row["attachments"]), 1)
        self.assertEqual(
            acli.calls,
            [
                [
                    "jira",
                    "workitem",
                    "comment",
                    "list",
                    "--key",
                    "IP-1032",
                    "--json",
                    "--paginate",
                ]
            ],
        )

    def test_paginated_output_is_several_documents(self):
        """`--paginate` prints one JSON document per page, back to back --
        observed live 2026-11-06, where a single-document parse failed every
        issue. Comments from every page are kept."""
        pages = (
            json.dumps(acli_comments(CLAIM), indent=2)
            + "\n"
            + json.dumps(acli_comments(OPENED), indent=2)
            + "\n"
        )
        self.stub((0, pages, ""))
        row = sources.to_row({"key": "IP-1032"}, use_acli=True)
        self.assertEqual(row["source_errors"], [])
        self.assertEqual(row["branchName"], "bestdan/IP-1032-inflation-factor")
        self.assertEqual(len(row["attachments"]), 1)

    def test_a_failed_acli_read_is_a_source_error(self):
        """Feeds linear-pr-resolve.py's fail-closed rule: a row whose comments
        could not be read must land in `unresolved`, never `no_pr`."""
        self.stub((1, "", "unauthorized"))
        row = sources.to_row({"key": "P-1"}, use_acli=True)
        self.assertEqual(row["attachments"], [])
        self.assertIn("unauthorized", row["source_errors"][0])

    def test_no_comments_and_no_acli_is_a_source_error(self):
        row = sources.to_row({"key": "P-1"}, use_acli=False)
        self.assertEqual(len(row["source_errors"]), 1)


class TestMain(SeamTestCase):
    def run_main(self, payload, argv):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                code = sources.main(argv)
            return code, json.loads(out.getvalue())
        finally:
            sys.stdin = old

    def test_one_row_per_issue_in_order_skipping_nulls(self):
        self.stub(
            (0, json.dumps(acli_comments()), ""),
            (0, json.dumps(acli_comments(OPENED)), ""),
        )
        code, rows = self.run_main([{"key": "A-1"}, None, {"key": "A-2"}], ["--acli"])
        self.assertEqual(code, 0)
        self.assertEqual([r["identifier"] for r in rows], ["A-1", "A-2"])
        self.assertEqual(rows[0]["attachments"], [])
        self.assertEqual(len(rows[1]["attachments"]), 1)

    def test_a_missing_key_stops_the_run(self):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps([{"id": "1"}]))
        try:
            with self.assertRaises(SystemExit):
                sources.main([])
        finally:
            sys.stdin = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
