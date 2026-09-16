#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-export.py.

Stubs the module's gql() seam so nothing reaches Linear, and records every
query/variables pair so a test can assert what the cursor loop actually asked
for rather than only what it returned.

The properties under test are the ones whose failure is INVISIBLE in the output
file — a truncated export is a well-formed JSON document, so nothing downstream
can notice it: the top-level loop follows `endCursor` to exhaustion, archived
issues survive into the file, a nested connection that overflows the cap raises
instead of writing its first page, and today's export is not silently replaced.
"""

import contextlib
import importlib.util
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "linear-export.py"

_spec = importlib.util.spec_from_file_location("linear_export", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
linear_export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(linear_export)

# Every nested connection ISSUES_QUERY selects, read out of the query text.
# Anchored on `$nested` rather than on `first:` — the top-level
# `issues(first: $first, …)` is not a nested connection and must not match.
NESTED_RE = re.compile(r"(\w+)\s*\(\s*first\s*:\s*\$nested\b")


def conn(nodes, has_next=False):
    """A nested connection as Linear returns one."""
    return {"nodes": list(nodes), "pageInfo": {"hasNextPage": has_next}}


def issue(identifier, archived=False, comments=(), overflow=None):
    """A fully-shaped issue node — every connection the query selects.

    `overflow` names one connection that reports hasNextPage, which is the
    truncation case; everything else stays well-formed so a test can attribute
    a raise to that one field.
    """
    node = {
        "id": "id-" + identifier,
        "identifier": identifier,
        "title": "Title " + identifier,
        "description": "Body " + identifier,
        "priority": 2,
        "estimate": 3,
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-02T00:00:00.000Z",
        "archivedAt": "2026-02-01T00:00:00.000Z" if archived else None,
        "completedAt": None,
        "canceledAt": None,
        "startedAt": None,
        "url": "https://linear.app/x/issue/" + identifier,
        "branchName": "bestdan/" + identifier.lower(),
        "state": {"name": "Todo", "type": "unstarted"},
        "project": {"id": "proj-1", "name": "workflow-skills backlog"},
        "parent": {"identifier": "PRE-1"},
        "assignee": {"name": "Dan", "email": "dan@example.com"},
        "creator": {"name": "Dan"},
        "children": conn([{"identifier": "PRE-999"}]),
        "labels": conn([{"name": "migration"}]),
        "relations": conn(
            [{"type": "blocks", "relatedIssue": {"identifier": "PRE-634"}}]
        ),
        "inverseRelations": conn(
            [{"type": "blocks", "issue": {"identifier": "PRE-654"}}]
        ),
        "attachments": conn(
            [{"title": "GitHub #288 (migrated)", "url": "https://x/288"}]
        ),
        "comments": conn(
            [
                {
                    "body": b,
                    "createdAt": "2026-01-03T00:00:00.000Z",
                    "user": {"name": "Dan"},
                }
                for b in comments
            ]
        ),
    }
    if overflow:
        node[overflow]["pageInfo"]["hasNextPage"] = True
    return node


def page(nodes, has_next=False, end_cursor=None):
    return {
        "issues": {
            "nodes": list(nodes),
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        }
    }


class Recorder:
    """Serves canned pages in order and records what was asked for.

    The team lookup is answered from the query text rather than from call
    order, so a test that adds a page does not have to re-count calls.
    """

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def gql(self, key, query, variables=None):
        self.calls.append((query, variables or {}))
        if "teams(" in query:
            return {
                "teams": {"nodes": [{"id": "team-1", "key": "PRE", "name": "PreThink"}]}
            }
        return self.pages.pop(0)

    def issue_cursors(self):
        return [v.get("cursor") for q, v in self.calls if "issues(" in q]


class AuthHeaderTests(unittest.TestCase):
    """A correctly-valued key in the wrong envelope fails as 'bad key'.

    Both framings are live: the repo's own `.task-config.local.yml` points at a
    personal key, and a client_credentials grant hands back an OAuth token
    instead. Neither works in the other's envelope.
    """

    def test_personal_key_is_sent_bare(self):
        self.assertEqual(linear_export.auth_header("lin_api_abc123"), "lin_api_abc123")

    def test_oauth_token_gets_the_bearer_scheme(self):
        self.assertEqual(linear_export.auth_header("abc123"), "Bearer abc123")

    def test_an_already_framed_token_is_not_double_framed(self):
        self.assertEqual(linear_export.auth_header("Bearer abc123"), "Bearer abc123")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._orig_gql = linear_export.gql
        self._orig_key = linear_export.get_key
        linear_export.get_key = lambda: "lin_api_STUB"
        self.addCleanup(self._restore)

    def _restore(self):
        linear_export.gql = self._orig_gql
        linear_export.get_key = self._orig_key

    def _tmpdir(self):
        """A scratch export directory that is removed when the test ends.

        Every destination here holds a full export document, so an unswept
        mkdtemp leaves one per test per run.
        """
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

    def _run(self, pages, extra_args=(), out_dir=None):
        recorder = Recorder(pages)
        linear_export.gql = recorder.gql
        tmp = out_dir or self._tmpdir()
        out, err = io.StringIO(), io.StringIO()
        argv = ["--team", "PreThink", "--out", tmp, "--json"] + list(extra_args)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = linear_export.main(argv)
        return code, json.loads(out.getvalue()), recorder, tmp

    def _document(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_two_pages_are_both_exported(self):
        pages = [
            page([issue("PRE-1"), issue("PRE-2")], has_next=True, end_cursor="cur-1"),
            page([issue("PRE-3")]),
        ]
        code, info, recorder, _ = self._run(pages)
        self.assertEqual(code, 0)
        self.assertEqual(info["issue_count"], 3)
        doc = self._document(info["path"])
        self.assertEqual(
            [i["identifier"] for i in doc["issues"]], ["PRE-1", "PRE-2", "PRE-3"]
        )

    def test_loop_follows_end_cursor(self):
        pages = [
            page([issue("PRE-1")], has_next=True, end_cursor="cur-1"),
            page([issue("PRE-2")], has_next=True, end_cursor="cur-2"),
            page([issue("PRE-3")]),
        ]
        _, _, recorder, _ = self._run(pages)
        # The first request carries no cursor; each later one carries the
        # PREVIOUS page's endCursor. A loop that re-sent None would fetch page
        # one forever and still terminate here with the right count.
        self.assertEqual(recorder.issue_cursors(), [None, "cur-1", "cur-2"])

    def test_archived_issues_are_included(self):
        pages = [page([issue("PRE-1"), issue("PRE-730", archived=True)])]
        _, info, recorder, _ = self._run(pages)
        doc = self._document(info["path"])
        archived = [i for i in doc["issues"] if i["archivedAt"]]
        self.assertEqual([i["identifier"] for i in archived], ["PRE-730"])
        self.assertEqual(info["archived"], 1)
        # The query must ASK for them, not merely tolerate them arriving.
        issues_query = [q for q, _ in recorder.calls if "issues(" in q][0]
        self.assertIn("includeArchived: true", issues_query)

    def test_nested_comments_are_captured(self):
        pages = [page([issue("PRE-1", comments=("first", "second"))])]
        _, info, _, _ = self._run(pages)
        doc = self._document(info["path"])
        bodies = [c["body"] for c in doc["issues"][0]["comments"]]
        self.assertEqual(bodies, ["first", "second"])
        self.assertEqual(info["with_comments"], 1)

    def test_nested_overflow_raises_instead_of_truncating(self):
        node = issue("PRE-1", comments=("only page one",), overflow="comments")
        with self.assertRaises(linear_export.ExportError) as ctx:
            linear_export.shape(node)
        self.assertIn("comments", str(ctx.exception))
        self.assertIn("PRE-1", str(ctx.exception))

    def test_nested_overflow_aborts_the_run_and_writes_nothing(self):
        pages = [page([issue("PRE-1", overflow="relations")])]
        tmp = self._tmpdir()
        with self.assertRaises(SystemExit):
            self._run(pages, out_dir=tmp)
        self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_relations_flatten_to_type_and_identifier(self):
        pages = [page([issue("PRE-746")])]
        _, info, _, _ = self._run(pages)
        doc = self._document(info["path"])
        row = doc["issues"][0]
        self.assertEqual(
            row["relations"], [{"type": "blocks", "identifier": "PRE-634"}]
        )
        self.assertEqual(
            row["inverseRelations"], [{"type": "blocks", "identifier": "PRE-654"}]
        )
        self.assertEqual(row["attachments"][0]["title"], "GitHub #288 (migrated)")

    def test_existing_file_is_not_overwritten_without_force(self):
        tmp = self._tmpdir()
        _, info, _, _ = self._run([page([issue("PRE-1")])], out_dir=tmp)
        with self.assertRaises(SystemExit):
            self._run([page([issue("PRE-2")])], out_dir=tmp)
        # Untouched: the refusal must not have half-written the replacement.
        doc = self._document(info["path"])
        self.assertEqual([i["identifier"] for i in doc["issues"]], ["PRE-1"])

    def test_force_replaces_todays_export(self):
        tmp = self._tmpdir()
        self._run([page([issue("PRE-1")])], out_dir=tmp)
        _, info, _, _ = self._run(
            [page([issue("PRE-2")])], extra_args=("--force",), out_dir=tmp
        )
        doc = self._document(info["path"])
        self.assertEqual([i["identifier"] for i in doc["issues"]], ["PRE-2"])

    def test_document_shape_and_filename(self):
        _, info, _, tmp = self._run([page([issue("PRE-1")])])
        self.assertTrue(info["path"].endswith("-prethink.json"), info["path"])
        doc = self._document(info["path"])
        self.assertEqual(sorted(doc), ["exported_at", "issue_count", "issues", "team"])
        self.assertEqual(
            doc["team"], {"id": "team-1", "key": "PRE", "name": "PreThink"}
        )
        self.assertEqual(doc["issue_count"], len(doc["issues"]))

    def test_guard_list_matches_the_query(self):
        """The guard list and the query must not drift apart.

        `check_nested()` walks `NESTED_FIELDS`, so a connection selected in
        `ISSUES_QUERY` but missing from that tuple overflows unchecked — and an
        entry in the tuple that the query no longer selects is a dead guard.
        Both are invisible at runtime, which is why the invariant is asserted
        textually against the query rather than trusted to review.
        """
        selected = NESTED_RE.findall(linear_export.ISSUES_QUERY)
        self.assertEqual(len(selected), len(set(selected)), "duplicate selection")
        self.assertEqual(
            set(selected),
            set(linear_export.NESTED_FIELDS),
            "ISSUES_QUERY and NESTED_FIELDS have drifted",
        )
        # An alias renames the response key, so check_nested() would look under
        # the alias while NESTED_FIELDS names the field. Forbid them outright
        # rather than teaching the regex to resolve them.
        self.assertNotRegex(linear_export.ISSUES_QUERY, r"\w+\s*:\s*\w+\s*\(")

    def test_every_selected_connection_is_overflow_checked(self):
        # Iterates the QUERY, not NESTED_FIELDS: drawing the cases from the
        # tuple this guard polices would close the test over itself, leaving a
        # newly added connection green while its overflow went unchecked.
        for field in NESTED_RE.findall(linear_export.ISSUES_QUERY):
            with self.subTest(field=field):
                with self.assertRaises(linear_export.ExportError):
                    linear_export.shape(issue("PRE-1", overflow=field))


if __name__ == "__main__":
    unittest.main(verbosity=2)
