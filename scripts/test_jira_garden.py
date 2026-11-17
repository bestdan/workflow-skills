"""Hermetic tests for jira-garden.py, the jira /garden-backlog handler helper.

Loaded via importlib since the asset's filename is hyphenated. The one seam is
`run_twg`, stubbed per test, so no test needs `twg` or a network. The page
shapes are the ones twg 1.4.4 and the Atlassian MCP returned for IP on
2026-11-16.
"""

import argparse
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "jira-garden.py"

_spec = importlib.util.spec_from_file_location("jira_garden", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
garden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(garden)

PMAP = {"major": "P2-Major", "minor": "P3-Minor", "high": "P2-Major"}
EST = "customfield_10300"


def cfg(**over: Any) -> argparse.Namespace:
    base: Dict[str, Any] = {
        "ready_status": "Ready for Development",
        "refinement_status": "Needs Refinement",
        "blocked_status": ["On Hold/Blocked"],
        "estimate_field": EST,
        "default_priority": "P3-Minor",
        "site": "acme.atlassian.net",
        "priority_map_json": json.dumps(PMAP),
        "issue_type": "Task",
        "issue_types": None,
    }
    base.update(over)
    return argparse.Namespace(**base)


def status(name: str, category: str) -> Dict[str, Any]:
    return {"name": name, "statusCategory": {"key": category}}


def issue(
    key: str = "IP-1", comments: Optional[List[Dict[str, Any]]] = None, **fields: Any
) -> Dict[str, Any]:
    f: Dict[str, Any] = {
        "summary": "Fix it",
        "status": status("Needs Refinement", "new"),
        "issuetype": {"name": "Task"},
        "priority": {"name": "P3-Minor"},
        "updated": "2026-11-10T10:00:00.000-0500",
        "comment": {"comments": comments or [], "total": len(comments or [])},
        EST: None,
    }
    f.update(fields)
    return {"key": key, "id": "1", "fields": f}


def estimated(i: Dict[str, Any], value: float) -> Dict[str, Any]:
    i["fields"][EST] = value
    return i


def gcomment(body: Any, created: str) -> Dict[str, Any]:
    return {"body": body, "created": created, "updated": created}


def row(
    i: Dict[str, Any], changed: Optional[set] = None, **over: Any
) -> Dict[str, Any]:
    return garden.build_row(i, changed or set(), cfg(**over), PMAP)


class JqlTest(unittest.TestCase):
    def test_project_epic_and_fragment(self) -> None:
        self.assertTrue(
            garden.build_jql("IP", wide=True)["candidates"].startswith(
                'project = "IP" AND statusCategory != Done'
            )
        )
        self.assertIn('parent = "IP-12" AND', garden.build_jql("IP-12")["candidates"])
        q = garden.build_jql("project = IP AND labels = x", wide=True)
        self.assertTrue(q["candidates"].startswith("(project = IP AND labels = x) AND"))
        self.assertIn("AND priority CHANGED ORDER BY key ASC", q["priority_changed"])

    def test_wide_scope_without_flag_names_the_flag(self) -> None:
        for scope in ("IP", "project = IP", "labels = garden"):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(garden.InputError, "--wide"):
                    garden.build_jql(scope)

    def test_cli_refuses_wide_scope_with_exit_2(self) -> None:
        with mock.patch("sys.stderr") as err, mock.patch("sys.stdout"):
            self.assertEqual(garden.main(["jql", "--scope", "IP"]), 2)
        self.assertIn("--wide", "".join(c.args[0] for c in err.write.call_args_list))
        with mock.patch("sys.stdout"):
            self.assertEqual(garden.main(["jql", "--scope", "IP", "--wide"]), 0)

    def test_key_query_is_one_ticket_and_needs_no_flag(self) -> None:
        q = garden.build_key_jql("IP-7")["candidates"]
        self.assertTrue(q.startswith('key = "IP-7" AND statusCategory != Done'))
        with self.assertRaises(garden.InputError):
            garden.build_key_jql("IP")

    def test_excludes_epics_done_and_active_sprints(self) -> None:
        q = garden.build_jql("IP-12")["candidates"]
        for clause in (
            "issuetype != Epic",
            "statusCategory != Done",
            "(sprint IS EMPTY OR sprint NOT IN openSprints())",
        ):
            self.assertIn(clause, q)

    def test_order_by_in_scope_rejected(self) -> None:
        with self.assertRaises(garden.InputError):
            garden.build_jql("project = IP order by created", wide=True)


class PageTest(unittest.TestCase):
    def test_twg_shape(self) -> None:
        page = {
            "data": {"issues": [{"key": "IP-1"}]},
            "pageInfo": {"hasNextPage": True, "nextCursor": "c1"},
        }
        self.assertEqual(len(garden.page_issues(page)), 1)
        self.assertEqual(garden.page_next(page), "c1")
        self.assertIsNone(
            garden.page_next(
                {"data": {"issues": []}, "pageInfo": {"hasNextPage": False}}
            )
        )

    def test_twg_next_without_cursor_is_an_error(self) -> None:
        with self.assertRaises(garden.InputError):
            garden.page_next({"pageInfo": {"hasNextPage": True, "nextCursor": ""}})

    def test_mcp_shape(self) -> None:
        page = {"issues": [{"key": "IP-1"}], "nextPageToken": "t", "isLast": False}
        self.assertEqual(garden.page_next(page), "t")
        self.assertIsNone(
            garden.page_next({"issues": [], "nextPageToken": "t", "isLast": True})
        )

    def test_mcp_not_last_without_token_is_an_error(self) -> None:
        with self.assertRaises(garden.InputError):
            garden.page_next({"issues": [], "isLast": False})

    def test_no_issue_list_is_an_error(self) -> None:
        with self.assertRaises(garden.InputError):
            garden.page_issues({"data": {}})


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.calls: List[List[str]] = []
        self.orig = garden.run_twg

    def tearDown(self) -> None:
        garden.run_twg = self.orig
        self.tmp.cleanup()

    def stub(self, pages: List[Any], rc: int = 0) -> None:
        def fake(args: List[str]) -> Any:
            self.calls.append(list(args))
            return garden.TwgResult(
                returncode=rc, stdout=json.dumps(pages[len(self.calls) - 1]), stderr=""
            )

        garden.run_twg = fake

    def page(self, keys: List[str], cursor: Optional[str]) -> Dict[str, Any]:
        return {
            "data": {"issues": [{"key": k} for k in keys]},
            "pageInfo": {"hasNextPage": cursor is not None, "nextCursor": cursor},
        }

    def test_pages_to_completion(self) -> None:
        self.stub([self.page(["A-1", "A-2"], "c1"), self.page(["A-3"], None)])
        out = garden.fetch("s", "jql", "summary", self.tmp.name, None, 2)
        self.assertEqual(out["count"], 3)
        self.assertFalse(out["limit_cut"])
        self.assertEqual(len(out["pages"]), 2)
        self.assertNotIn("--after", self.calls[0])
        self.assertEqual(self.calls[1][self.calls[1].index("--after") + 1], "c1")

    def test_limit_cuts(self) -> None:
        self.stub([self.page(["A-1", "A-2"], "c1")])
        out = garden.fetch("s", "jql", "summary", self.tmp.name, 2, 100)
        self.assertTrue(out["limit_cut"])
        self.assertEqual(self.calls[0][self.calls[0].index("--limit") + 1], "2")

    def test_repeated_cursor_stops(self) -> None:
        self.stub([self.page(["A-1"], "c1"), self.page(["A-2"], "c1")])
        with self.assertRaises(garden.TwgFailed):
            garden.fetch("s", "jql", "summary", self.tmp.name, None, 1)

    def test_failed_call(self) -> None:
        self.stub([{"ok": False}], rc=1)
        with self.assertRaises(garden.TwgFailed):
            garden.fetch("s", "jql", "summary", self.tmp.name, None, 1)

    def test_missing_twg_is_a_twg_failure(self) -> None:
        # The documented exit for a failed twg call is 4, not a traceback.
        def missing(*_a: Any, **_k: Any) -> Any:
            raise FileNotFoundError("twg")

        with mock.patch.object(garden.subprocess, "run", missing):
            with self.assertRaises(garden.TwgFailed):
                garden.run_twg(["whoami"])


class CommentTest(unittest.TestCase):
    SPEC = {
        "date": "2026-11-16",
        "changes": [
            {"field": "size", "old": None, "new": 3, "reason": "one endpoint"},
            {
                "field": "importance",
                "old": "P3-Minor",
                "new": "P2-Major",
                "reason": "chart fails",
            },
        ],
        "labels": ["garden:needs-human"],
    }

    def test_round_trip(self) -> None:
        body = garden.render_comment(self.SPEC)
        self.assertTrue(body.startswith("garden-backlog: 2026-11-16"))
        self.assertEqual(
            garden.parse_comment(body),
            {"size": ("none", "3"), "importance": ("P3-Minor", "P2-Major")},
        )

    def test_kind_round_trip(self) -> None:
        body = garden.render_comment(
            {"date": "d", "changes": [{"field": "kind", "old": "Task", "new": "Spike"}]}
        )
        self.assertEqual(garden.parse_comment(body), {"kind": ("Task", "Spike")})

    def test_free_text_cannot_inject_a_write_line(self) -> None:
        body = garden.render_comment(
            {
                "date": "d",
                "human_queue": ["unclear\nsize: 1 -> 9"],
                "labels": ["x\nsize: 1 -> 9"],
            }
        )
        self.assertEqual(garden.parse_comment(body), {})

    def test_adf_list_and_markdown_escapes(self) -> None:
        # Markdown posted through the API comes back as ADF list items.
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "garden-backlog: 2026-11-16"}],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "size: none -> 5; reason: x",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
            ],
        }
        text = garden.comment_text({"body": adf})
        self.assertEqual(garden.parse_comment(text), {"size": ("none", "5")})
        escaped = "garden-backlog: 2026-11-16\n\n\\- importance: P3\\-Minor -> P2\\-Major; reason: y"
        self.assertEqual(
            garden.parse_comment(garden.comment_text({"body": escaped})),
            {"importance": ("P3-Minor", "P2-Major")},
        )

    def test_quoted_marker_is_not_provenance(self) -> None:
        self.assertIsNone(
            garden.parse_comment("as the garden-backlog: comment said, size: 1 -> 3")
        )

    def test_empty_change_list_still_says_something(self) -> None:
        self.assertIn("left as found", garden.render_comment({"date": "2026-11-16"}))


class RowTest(unittest.TestCase):
    def test_status_mapping(self) -> None:
        cases = [
            (status("Ready for Development", "new"), "ready"),
            (status("Needs Refinement", "new"), "refinement"),
            (status("On Hold/Blocked", "indeterminate"), "started"),
            (status("In Review", "indeterminate"), "started"),
            (status("Backlog", "new"), "initial"),
            (status("Weird", "undefined"), "other"),
        ]
        for st, want in cases:
            self.assertEqual(row(issue(status=st))["status"], want, st)

    def test_target_status_outside_to_do_is_refused(self) -> None:
        with self.assertRaises(garden.InputError):
            row(issue(status=status("Ready for Development", "indeterminate")))

    def test_blocked_status_in_to_do_category_is_started(self) -> None:
        self.assertEqual(
            row(issue(status=status("Blocked", "new")), blocked_status=["Blocked"])[
                "status"
            ],
            "started",
        )

    def test_own_comment_does_not_count_as_an_update(self) -> None:
        body = garden.render_comment({"date": "2026-11-12"})
        r = row(
            issue(
                comments=[gcomment(body, "2026-11-12T09:00:00.000-0500")],
                updated="2026-11-12T09:00:00.150-0500",
            )
        )
        self.assertEqual(r["updated_at"], r["gardened_at"])

    def test_later_edit_counts(self) -> None:
        body = garden.render_comment({"date": "2026-11-12"})
        r = row(
            issue(
                comments=[gcomment(body, "2026-11-12T09:00:00.000-0500")],
                updated="2026-11-13T09:00:00.000-0500",
            )
        )
        self.assertGreater(r["updated_at"], r["gardened_at"])

    def test_newest_marker_wins_and_others_ignored(self) -> None:
        old = garden.render_comment({"date": "2026-11-01"})
        new = garden.render_comment({"date": "2026-11-12"})
        r = row(
            issue(
                comments=[
                    gcomment(old, "2026-11-01T09:00:00.000-0500"),
                    gcomment(new, "2026-11-12T09:00:00.000-0500"),
                    gcomment("looks good", "2026-11-14T09:00:00.000-0500"),
                ],
                updated="2026-11-14T09:00:00.000-0500",
            )
        )
        self.assertTrue(r["gardened_at"].startswith("2026-11-12T09:00:00"))

    def test_size_human_set(self) -> None:
        self.assertFalse(row(issue())["size_human_set"])
        self.assertTrue(row(estimated(issue(), 3.0))["size_human_set"])
        self.assertEqual(row(estimated(issue(), 3.0))["size"], 3)

    def test_size_written_by_garden_is_not_human_set(self) -> None:
        body = garden.render_comment(
            {"date": "d", "changes": [{"field": "size", "old": None, "new": 3}]}
        )
        c = [gcomment(body, "2026-11-12T09:00:00.000-0500")]
        self.assertFalse(row(estimated(issue(comments=c), 3.0))["size_human_set"])
        self.assertTrue(row(estimated(issue(comments=c), 5.0))["size_human_set"])

    def test_importance_human_set(self) -> None:
        self.assertFalse(row(issue())["importance_human_set"])
        self.assertTrue(row(issue(), changed={"IP-1"})["importance_human_set"])
        self.assertTrue(
            row(issue(priority={"name": "P2-Major"}))["importance_human_set"]
        )

    def test_importance_written_by_garden_is_not_human_set(self) -> None:
        body = garden.render_comment(
            {
                "date": "d",
                "changes": [
                    {"field": "importance", "old": "P3-Minor", "new": "P2-Major"}
                ],
            }
        )
        c = [gcomment(body, "2026-11-12T09:00:00.000-0500")]
        self.assertFalse(
            row(issue(comments=c, priority={"name": "P2-Major"}), changed={"IP-1"})[
                "importance_human_set"
            ]
        )
        # A human moved it again afterwards.
        self.assertTrue(
            row(issue(comments=c, priority={"name": "P1-Critical"}), changed={"IP-1"})[
                "importance_human_set"
            ]
        )

    def test_kind_from_issue_type(self) -> None:
        r = row(issue())
        self.assertEqual((r["kind"], r["kind_human_set"]), ("fix", False))
        r = row(issue(issuetype={"name": "Spike"}))
        self.assertEqual((r["kind"], r["kind_human_set"]), ("spike", True))
        # Any type other than the creation type was chosen by someone.
        self.assertTrue(row(issue(issuetype={"name": "Bug"}))["kind_human_set"])
        # Without the creation type nothing can be shown to be the default.
        self.assertTrue(row(issue(), issue_type=None)["kind_human_set"])

    def test_kind_written_by_garden_is_not_human_set(self) -> None:
        body = garden.render_comment(
            {"date": "d", "changes": [{"field": "kind", "old": "Task", "new": "Spike"}]}
        )
        c = [gcomment(body, "2026-11-12T09:00:00.000-0500")]
        self.assertFalse(
            row(issue(comments=c, issuetype={"name": "Spike"}))["kind_human_set"]
        )
        # A human changed it back to the creation type afterwards.
        self.assertTrue(
            row(issue(comments=c, issuetype={"name": "Task"}))["kind_human_set"]
        )

    def test_importance_reverse_map_uses_only_the_importance_scale(self) -> None:
        self.assertEqual(
            row(issue(priority={"name": "P2-Major"}))["importance"], "major"
        )
        self.assertIsNone(row(issue(priority={"name": "P0-Blocker"}))["importance"])

    def test_twg_flattened_issue_and_pr_trail(self) -> None:
        flat = {
            "key": "IP-9",
            "id": "9",
            "url": "https://x/browse/IP-9",
            "summary": "s",
            "status": status("Needs Refinement", "new"),
            "priority": {"name": "P3-Minor"},
            "updated": "2026-11-10T10:00:00.000-0500",
            "parent": {"key": "IP-2"},
            "comment": {
                "comments": [
                    gcomment(
                        "PR opened: https://github.com/a/b/pull/7",
                        "2026-11-01T00:00:00.000-0500",
                    )
                ],
                "total": 1,
            },
        }
        r = row(flat)
        self.assertEqual(r["parent"], "IP-2")
        self.assertEqual(r["url"], "https://x/browse/IP-9")
        self.assertEqual(
            r["linked_prs"]["attachments"], ["https://github.com/a/b/pull/7"]
        )
        # linear-pr-resolve.py refuses a row without its identifier.
        self.assertEqual(r["linked_prs"]["identifier"], "IP-9")

    def test_missing_comment_field_is_a_source_error(self) -> None:
        i = issue()
        del i["fields"]["comment"]
        self.assertTrue(row(i)["linked_prs"]["source_errors"])

    def test_build_rows_requires_default_priority_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.json")
            Path(p).write_text(
                json.dumps({"issues": [issue("IP-1"), issue("IP-1"), issue("IP-2")]})
            )
            args = cfg(pages=[p], changed_pages=None, limit=None, limit_cut=False)
            out = garden.build_rows(args)
            self.assertEqual([r["key"] for r in out["rows"]], ["IP-1", "IP-2"])
            args.limit = 1
            self.assertTrue(garden.build_rows(args)["limit_cut"])
            args.default_priority = None
            with self.assertRaises(garden.InputError):
                garden.build_rows(args)


class FieldsTest(unittest.TestCase):
    def run_fields(
        self, writes: List[Dict[str, Any]], labels: List[str], **over: Any
    ) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory() as d:
            w, lab = os.path.join(d, "w.json"), os.path.join(d, "l.json")
            Path(w).write_text(json.dumps(writes))
            Path(lab).write_text(json.dumps(labels))
            if "types" in over:
                t = os.path.join(d, "t.json")
                Path(t).write_text(json.dumps(over.pop("types")))
                over["issue_types"] = t
            return garden.build_fields(cfg(writes=w, labels=lab, **over))

    TYPES = {
        "issueTypes": [
            {"id": "10001", "name": "Task", "subtask": False},
            {"id": "10002", "name": "Spike", "subtask": False},
            {"id": "10003", "name": "Sub-task", "subtask": True},
        ]
    }

    def test_kind_resolves_the_type_id_apart_from_fields(self) -> None:
        out = self.run_fields(
            [{"group": "kind", "value": "spike"}, {"group": "size", "value": 2}],
            [],
            types=self.TYPES,
        )
        self.assertEqual(out["issuetype"], {"id": "10002"})
        self.assertEqual(out["fields"], {EST: 2})
        fix = self.run_fields([{"group": "kind", "value": "fix"}], [], types=self.TYPES)
        self.assertEqual(fix["issuetype"], {"id": "10001"})

    def test_missing_target_type_is_a_report_line(self) -> None:
        types = [
            {"id": "10001", "name": "Task"},
            {"id": "10009", "name": "Spike", "subtask": True},
        ]
        out = self.run_fields([{"group": "kind", "value": "spike"}], [], types=types)
        self.assertIsNone(out["issuetype"])
        self.assertEqual(
            out["report_only"], ["kind spike: the project has no `Spike` issue type"]
        )
        bare = self.run_fields([{"group": "kind", "value": "spike"}], [])
        self.assertIsNone(bare["issuetype"])
        self.assertEqual(len(bare["report_only"]), 1)

    def test_all_groups(self) -> None:
        out = self.run_fields(
            [
                {"group": "size", "value": 3},
                {"group": "importance", "value": "major"},
                {"group": "human-queue"},
                {"group": "status", "value": "ready"},
                {"group": "gardened"},
            ],
            ["keep"],
        )
        self.assertEqual(
            out["fields"],
            {
                EST: 3,
                "priority": {"name": "P2-Major"},
                "labels": ["keep", "garden:needs-human"],
            },
        )
        self.assertEqual(out["transition_to"], "Ready for Development")
        self.assertEqual(out["report_only"], [])

    def test_unset_keys_are_report_only(self) -> None:
        out = self.run_fields(
            [{"group": "size", "value": 3}, {"group": "importance", "value": "major"}],
            [],
            estimate_field=None,
            priority_map_json=None,
        )
        self.assertEqual(out["fields"], {})
        self.assertEqual(len(out["report_only"]), 2)

    def test_label_already_present_is_not_rewritten(self) -> None:
        out = self.run_fields([{"group": "closure"}], ["garden:close-candidate"])
        self.assertNotIn("labels", out["fields"])

    def test_unknown_group(self) -> None:
        with self.assertRaises(garden.InputError):
            self.run_fields([{"group": "issuetype", "value": "Spike"}], [])


if __name__ == "__main__":
    unittest.main()
