#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-successor.py.

Stubs the module's gql() seam against a fixture workspace, so nothing reaches
Linear and every test states exactly what the issue already carries versus what
the script decides to write.

The property under test is the one whose failure is expensive and invisible:
**idempotence is per write, not per issue.** A crash between any two of the
three writes is normal, so a rerun must finish exactly the rest — and the way to
get that wrong is to gate all three on one question. Every combination of
already-present writes is therefore asserted independently, along with the two
truncation cases, which are the only way the guards could answer "already
posted?" wrong in the direction that duplicates a comment.

Also asserted: `--cancel` is the only path that issues `issueUpdate`, and the
canceled state is resolved by TYPE — a fixture whose `completed` state is
*named* "Canceled" catches a resolver that matched on the display name.
"""

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "linear-successor.py"


def load_asset(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


successor = load_asset("linear_successor", ASSET)

REPO = "bestdan/workflow-skills"
TEAM = {"id": "team-uuid", "name": "PreThink"}

# The display name is a trap on purpose: the `completed` state is called
# "Canceled" and the `canceled` state is called "Dropped". A resolver that
# matched on name would pick the wrong one, and the issue would be marked done.
STATES = [
    {"id": "s-todo", "name": "Todo", "type": "unstarted", "position": 1},
    {"id": "s-done", "name": "Canceled", "type": "completed", "position": 2},
    {"id": "s-cancel", "name": "Dropped", "type": "canceled", "position": 3},
]


def gh_url(number):
    return f"https://github.com/{REPO}/issues/{number}"


def issue(
    key,
    number,
    *,
    has_comment=False,
    has_attachment=False,
    state="s-todo",
    comment_overflow=False,
    attachment_overflow=False,
    states=None,
    archived_at=None,
):
    """A live Linear issue as the fixture workspace holds it."""
    url = gh_url(number)
    comments = [{"id": "c0", "body": "An unrelated remark."}]
    if has_comment:
        comments.append({"id": "c1", "body": successor.comment_body(REPO, number, url)})
    attachments = [{"id": "a0", "url": "https://example.com/other"}]
    if has_attachment:
        attachments.append({"id": "a1", "url": url})
    pool = states or STATES
    node = next(s for s in pool if s["id"] == state)
    return {
        "id": f"uuid-{key}",
        "identifier": key,
        "url": f"https://linear.app/acme/issue/{key}",
        "archivedAt": archived_at,
        "state": {"id": node["id"], "name": node["name"], "type": node["type"]},
        "team": TEAM,
        "comments": {
            "nodes": comments,
            "pageInfo": {"hasNextPage": comment_overflow},
        },
        "attachments": {
            "nodes": attachments,
            "pageInfo": {"hasNextPage": attachment_overflow},
        },
    }


def mapping(*entries, repo=REPO, **extra):
    """An import mapping. Each entry is (key, number) or (key, number, phase)."""
    out = {}
    for entry in entries:
        key, number = entry[0], entry[1]
        phase = entry[2] if len(entry) > 2 else "done"
        out[key] = {
            "key": key,
            "number": number,
            "url": gh_url(number) if number else None,
            "phase": phase,
        }
    doc = {"repo": repo, "entries": out}
    doc.update(extra)
    return doc


class Workspace:
    """The stub gql(). Serves the fixture issues and records every mutation."""

    def __init__(self, issues, states=None, fail=(), missing=(), explode=()):
        self.issues = {i["identifier"]: i for i in issues}
        self.states = STATES if states is None else states
        self.fail = set(fail)  # mutation field names that return success: false
        # (field, issue-uuid) pairs where gql() should sys.exit, as it does on a
        # real GraphQL error. The live run met this as an archived issue
        # refusing commentCreate 19 issues in.
        self.explode = set(explode)
        self.missing = set(missing)  # keys Linear does not have
        self.calls = []
        self.mutations = []

    def __call__(self, key, query, variables=None):
        variables = variables or {}
        self.calls.append((query, variables))
        if query is successor.ISSUE_QUERY:
            ident = variables["key"]
            if ident in self.missing:
                return {"issue": None}
            return {"issue": self.issues[ident]}
        if query is successor.STATES_QUERY:
            return {
                "team": {
                    "states": {
                        "nodes": self.states,
                        "pageInfo": {"hasNextPage": False},
                    }
                }
            }
        field = {
            successor.COMMENT_MUTATION: "commentCreate",
            successor.ATTACHMENT_MUTATION: "attachmentCreate",
            successor.STATE_MUTATION: "issueUpdate",
        }[query]
        self.mutations.append((field, variables))
        target = variables.get("issue") or variables.get("id")
        if (field, target) in self.explode:
            raise SystemExit("GraphQL error: Entity not found: Issue")
        return {field: {"success": field not in self.fail}}

    @property
    def written(self):
        """The mutation field names, in the order they were issued."""
        return [field for field, _ in self.mutations]

    def state_queries(self):
        return [q for q, _ in self.calls if q is successor.STATES_QUERY]


class Harness(unittest.TestCase):
    def run_script(self, doc, workspace, cancel=True, apply_writes=True):
        original = successor.gql
        successor.gql = workspace
        try:
            return successor.run("api-key", doc, cancel, apply_writes)
        finally:
            successor.gql = original


class PerWriteIdempotence(Harness):
    """Each of the three writes is gated by its own question of the live issue."""

    def plan_for(self, **state):
        ws = Workspace([issue("PRE-1", 101, **state)])
        summary = self.run_script(mapping(("PRE-1", 101)), ws, apply_writes=False)
        return summary["issues"][0]["pending"], ws

    def test_nothing_present_writes_all_three(self):
        pending, _ = self.plan_for()
        self.assertEqual(pending, ["comment", "attachment", "state"])

    def test_attachment_present_writes_comment_and_state_only(self):
        pending, _ = self.plan_for(has_attachment=True)
        self.assertEqual(pending, ["comment", "state"])

    def test_comment_present_writes_attachment_and_state_only(self):
        pending, _ = self.plan_for(has_comment=True)
        self.assertEqual(pending, ["attachment", "state"])

    def test_canceled_already_writes_comment_and_attachment_only(self):
        pending, _ = self.plan_for(state="s-cancel")
        self.assertEqual(pending, ["comment", "attachment"])

    def test_comment_and_state_present_writes_attachment_only(self):
        pending, _ = self.plan_for(has_comment=True, state="s-cancel")
        self.assertEqual(pending, ["attachment"])

    def test_all_present_writes_nothing(self):
        ws = Workspace(
            [
                issue(
                    "PRE-1",
                    101,
                    has_comment=True,
                    has_attachment=True,
                    state="s-cancel",
                )
            ]
        )
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(summary["issues"][0]["pending"], [])
        self.assertEqual(ws.written, [])

    def test_half_finished_issue_applies_exactly_the_rest(self):
        """The crash-between-writes case, end to end rather than as a plan."""
        ws = Workspace([issue("PRE-1", 101, has_attachment=True)])
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(ws.written, ["commentCreate", "issueUpdate"])
        self.assertEqual(summary["counts"], {"comment": 1, "attachment": 0, "state": 1})

    def test_comment_match_is_the_successor_url_not_any_comment(self):
        """A comment mentioning a DIFFERENT successor does not count."""
        node = issue("PRE-1", 101)
        node["comments"]["nodes"].append(
            {"id": "cx", "body": f"See also {gh_url(999)}"}
        )
        ws = Workspace([node])
        summary = self.run_script(mapping(("PRE-1", 101)), ws, apply_writes=False)
        self.assertIn("comment", summary["issues"][0]["pending"])


class Truncation(Harness):
    """A page cut short reads exactly like an absent write. Refuse, never guess."""

    def test_truncated_comments_page_is_fatal(self):
        ws = Workspace([issue("PRE-1", 101, comment_overflow=True)])
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(ws.written, [])
        self.assertEqual(len(summary["unreadable"]), 1)
        self.assertIn("truncated page", summary["unreadable"][0][1])

    def test_truncated_attachments_page_is_fatal(self):
        ws = Workspace([issue("PRE-1", 101, attachment_overflow=True)])
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(ws.written, [])
        self.assertIn("attachments", summary["unreadable"][0][1])

    def test_truncation_on_one_issue_does_not_stop_the_others(self):
        ws = Workspace(
            [issue("PRE-1", 101, comment_overflow=True), issue("PRE-2", 102)]
        )
        summary = self.run_script(mapping(("PRE-1", 101), ("PRE-2", 102)), ws)
        self.assertEqual([r["key"] for r in summary["issues"]], ["PRE-2"])
        self.assertEqual(
            ws.written, ["commentCreate", "attachmentCreate", "issueUpdate"]
        )


class CancelFlag(Harness):
    """--cancel is the only path that mutates state."""

    def test_without_cancel_no_state_write_is_planned(self):
        ws = Workspace([issue("PRE-1", 101)])
        summary = self.run_script(
            mapping(("PRE-1", 101)), ws, cancel=False, apply_writes=False
        )
        self.assertEqual(summary["issues"][0]["pending"], ["comment", "attachment"])

    def test_without_cancel_no_issue_update_is_issued(self):
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(mapping(("PRE-1", 101)), ws, cancel=False)
        self.assertEqual(ws.written, ["commentCreate", "attachmentCreate"])
        self.assertEqual(ws.state_queries(), [], "states were resolved for no reason")

    def test_with_cancel_the_state_write_lands(self):
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertIn("issueUpdate", ws.written)


class StateResolution(Harness):
    def state_variables(self, ws):
        return next(v for f, v in ws.mutations if f == "issueUpdate")

    def test_state_is_resolved_by_type_not_display_name(self):
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(self.state_variables(ws)["state"], "s-cancel")

    def test_several_canceled_states_take_the_lowest_position(self):
        states = STATES + [
            {"id": "s-cancel-2", "name": "Wontfix", "type": "canceled", "position": 0}
        ]
        ws = Workspace([issue("PRE-1", 101, states=states)], states=states)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(self.state_variables(ws)["state"], "s-cancel-2")
        self.assertIn("2 canceled-type states", buf.getvalue())

    def test_no_canceled_state_is_an_error(self):
        states = [s for s in STATES if s["type"] != "canceled"]
        ws = Workspace(
            [issue("PRE-1", 101, state="s-todo", states=STATES)], states=states
        )
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertIn("no `canceled`-type", summary["unreadable"][0][1])
        self.assertEqual(ws.written, ["commentCreate", "attachmentCreate"])

    def test_the_state_lookup_is_cached_per_team(self):
        ws = Workspace([issue(f"PRE-{n}", 100 + n) for n in (1, 2, 3)])
        self.run_script(mapping(("PRE-1", 101), ("PRE-2", 102), ("PRE-3", 103)), ws)
        self.assertEqual(len(ws.state_queries()), 1)


class WriteContents(Harness):
    def test_comment_names_the_mappings_repo_and_the_successor(self):
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(mapping(("PRE-1", 101)), ws)
        body = next(v for f, v in ws.mutations if f == "commentCreate")["body"]
        self.assertIn(f"{REPO}#101", body)
        self.assertIn(gh_url(101), body)
        self.assertIn("PR #503", body)

    def test_comment_follows_the_mapping_to_a_different_repo(self):
        doc = mapping(("PRE-1", 101), repo="someone/elsewhere")
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(doc, ws)
        body = next(v for f, v in ws.mutations if f == "commentCreate")["body"]
        self.assertIn("someone/elsewhere#101", body)

    def test_attachment_carries_the_title_and_the_url(self):
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(mapping(("PRE-1", 101)), ws)
        variables = next(v for f, v in ws.mutations if f == "attachmentCreate")
        self.assertEqual(variables["title"], "GitHub #101 (migrated)")
        self.assertEqual(variables["url"], gh_url(101))

    def test_mutations_target_the_linear_uuid_not_the_key(self):
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(mapping(("PRE-1", 101)), ws)
        for _, variables in ws.mutations:
            self.assertIn("uuid-PRE-1", json.dumps(variables))


class DryRun(Harness):
    def test_dry_run_writes_nothing_but_counts_everything(self):
        ws = Workspace([issue("PRE-1", 101), issue("PRE-2", 102, has_comment=True)])
        summary = self.run_script(
            mapping(("PRE-1", 101), ("PRE-2", 102)), ws, apply_writes=False
        )
        self.assertEqual(ws.written, [])
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["counts"], {"comment": 1, "attachment": 2, "state": 2})

    def test_dry_run_does_not_resolve_the_canceled_state(self):
        """Reading is free; a dry run that needed a state id would be doing
        work the apply run does, and would fail on a team that has none."""
        ws = Workspace([issue("PRE-1", 101)])
        self.run_script(mapping(("PRE-1", 101)), ws, apply_writes=False)
        self.assertEqual(ws.state_queries(), [])


class Failures(Harness):
    def test_a_failed_write_does_not_abort_the_others(self):
        ws = Workspace([issue("PRE-1", 101)], fail={"commentCreate"})
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(
            ws.written, ["commentCreate", "attachmentCreate", "issueUpdate"]
        )
        self.assertEqual(summary["issues"][0]["failed"], ["comment"])
        self.assertEqual(summary["issues"][0]["done"], ["attachment", "state"])
        self.assertEqual(summary["failures"], 1)

    def test_an_issue_linear_does_not_have_is_reported(self):
        ws = Workspace([issue("PRE-2", 102)], missing={"PRE-1"})
        summary = self.run_script(mapping(("PRE-1", 101), ("PRE-2", 102)), ws)
        self.assertEqual(summary["unreadable"][0][0], "PRE-1")
        self.assertIn("no such issue", summary["unreadable"][0][1])


class MappingReading(Harness):
    def test_an_entry_short_of_done_is_skipped_and_listed(self):
        ws = Workspace([issue("PRE-2", 102)])
        summary = self.run_script(
            mapping(("PRE-1", 101, "labelled"), ("PRE-2", 102)), ws
        )
        self.assertEqual(summary["pending_import"], ["PRE-1"])
        self.assertEqual([r["key"] for r in summary["issues"]], ["PRE-2"])

    def test_an_entry_with_no_number_is_skipped(self):
        ws = Workspace([issue("PRE-2", 102)])
        summary = self.run_script(mapping(("PRE-1", None), ("PRE-2", 102)), ws)
        self.assertEqual(summary["pending_import"], ["PRE-1"])

    def write(self, tmp, payload):
        path = Path(tmp) / "mapping.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_a_real_mapping_file_round_trips(self):
        with TemporaryDirectory() as tmp:
            path = self.write(tmp, mapping(("PRE-1", 101)))
            self.assertEqual(successor.load_mapping(path)["repo"], REPO)

    def test_a_file_that_is_not_a_mapping_is_refused(self):
        with TemporaryDirectory() as tmp:
            path = self.write(tmp, {"repo": REPO})
            with self.assertRaises(successor.SuccessorError) as caught:
                successor.load_mapping(path)
            self.assertIn("not an import mapping", str(caught.exception))

    def test_a_mapping_with_no_repo_is_refused(self):
        """The comment body names the repo; a mapping that does not say which
        board it belongs to cannot be used to write one."""
        with TemporaryDirectory() as tmp:
            payload = mapping(("PRE-1", 101))
            del payload["repo"]
            path = self.write(tmp, payload)
            with self.assertRaises(successor.SuccessorError) as caught:
                successor.load_mapping(path)
            self.assertIn("no `repo`", str(caught.exception))

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(successor.SuccessorError):
            successor.load_mapping("/nonexistent/mapping.json")


class Rendering(unittest.TestCase):
    """Both renderings are executed — a summary path that has never run is a
    traceback waiting for the one run that needed it."""

    def summary(self, **overrides):
        doc = {
            "repo": REPO,
            "dry_run": True,
            "cancel": True,
            "issues": [
                {
                    "key": "PRE-1",
                    "number": 101,
                    "url": gh_url(101),
                    "state": "Todo",
                    "pending": ["comment", "state"],
                },
                {
                    "key": "PRE-2",
                    "number": 102,
                    "url": gh_url(102),
                    "state": "Dropped",
                    "pending": [],
                },
            ],
            "unreadable": [("PRE-3", "no such issue in Linear")],
            "pending_import": ["PRE-4"],
            "counts": {"comment": 1, "attachment": 0, "state": 1},
            "failures": 0,
        }
        doc.update(overrides)
        return doc

    def render(self, doc, as_json):
        buf = io.StringIO()
        with redirect_stdout(buf):
            successor.render(doc, as_json)
        return buf.getvalue()

    def test_dry_run_text_says_nothing_was_written(self):
        out = self.render(self.summary(), False)
        self.assertIn("DRY RUN — nothing written", out)
        self.assertIn("would comment, state", out)
        self.assertIn("up to date", out)
        self.assertIn("PRE-3: no such issue", out)
        self.assertIn("PRE-4", out)

    def test_apply_text_reports_failures(self):
        doc = self.summary(dry_run=False, failures=1)
        doc["issues"][0]["failed"] = ["comment"]
        out = self.render(doc, False)
        self.assertIn("FAILED PRE-1: comment", out)
        self.assertIn("1 failed", out)

    def test_json_is_the_summary_verbatim(self):
        doc = self.summary()
        self.assertEqual(
            json.loads(self.render(doc, True)), json.loads(json.dumps(doc))
        )


class ArchivedIssues(Harness):
    """Linear serves an archived issue to `issue(id:)` and then refuses every
    mutation against it with "Entity not found: Issue", which names neither the
    issue nor the reason. Two of the real 125 were archived."""

    def test_an_archived_issue_is_skipped_with_a_reason(self):
        ws = Workspace([issue("PRE-1", 101, archived_at="2026-08-01T08:39:10.813Z")])
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertEqual(ws.written, [], "wrote to an archived issue")
        self.assertEqual(summary["unreadable"][0][0], "PRE-1")
        self.assertIn("archived 2026-08-01", summary["unreadable"][0][1])

    def test_an_archived_issue_does_not_stop_the_live_ones(self):
        ws = Workspace(
            [
                issue("PRE-1", 101, archived_at="2026-08-01T08:39:10.813Z"),
                issue("PRE-2", 102),
            ]
        )
        summary = self.run_script(mapping(("PRE-1", 101), ("PRE-2", 102)), ws)
        self.assertEqual([r["key"] for r in summary["issues"]], ["PRE-2"])
        self.assertEqual(summary["counts"], {"comment": 1, "attachment": 1, "state": 1})


class OneBadIssueDoesNotEndTheRun(Harness):
    """`gql()` answers a GraphQL error with sys.exit, as every sibling's does.
    Caught per write, or one bad row in 125 takes the rest with it — which is
    exactly what happened on the first live apply."""

    def test_a_graphql_error_is_recorded_not_fatal(self):
        ws = Workspace([issue("PRE-1", 101)], explode={("commentCreate", "uuid-PRE-1")})
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        row = summary["issues"][0]
        self.assertEqual(row["failed"], ["comment"])
        self.assertEqual(row["done"], ["attachment", "state"])
        self.assertIn("Entity not found", row["errors"][0])

    def test_the_remaining_issues_are_still_processed(self):
        ws = Workspace(
            [issue(f"PRE-{n}", 100 + n) for n in (1, 2, 3)],
            explode={("commentCreate", "uuid-PRE-1")},
        )
        summary = self.run_script(
            mapping(("PRE-1", 101), ("PRE-2", 102), ("PRE-3", 103)), ws
        )
        self.assertEqual(len(summary["issues"]), 3)
        self.assertEqual(summary["counts"]["comment"], 2)
        self.assertEqual(summary["failures"], 1)

    def test_a_success_false_result_carries_a_reason_too(self):
        ws = Workspace([issue("PRE-1", 101)], fail={"issueUpdate"})
        summary = self.run_script(mapping(("PRE-1", 101)), ws)
        self.assertIn("success=false", summary["issues"][0]["errors"][0])


class AuthFraming(unittest.TestCase):
    """The same `$LINEAR_API_KEY` slot now holds either kind of credential, and
    the writes here are the first Linear mutations that can be made with an
    OAuth token. A correctly-valued token in the wrong envelope fails as an
    authentication error, which reads like a bad key."""

    def test_a_personal_api_key_goes_bare(self):
        self.assertEqual(successor.auth_header("lin_api_abc"), "lin_api_abc")

    def test_a_client_credentials_token_is_framed_as_bearer(self):
        self.assertEqual(successor.auth_header("oauth_tok"), "Bearer oauth_tok")

    def test_an_already_framed_token_is_left_alone(self):
        self.assertEqual(successor.auth_header("Bearer tok"), "Bearer tok")

    def test_the_rule_has_one_home(self):
        """Imported from `_linear_auth`, not restated — `linear-export.py` reads
        the same function, so the two cannot disagree about a live key."""
        export = load_asset("linear_export", ASSET.parent / "linear-export.py")
        shared = str(ASSET.parent / "_linear_auth.py")
        self.assertEqual(successor.auth_header.__code__.co_filename, shared)
        self.assertEqual(export.auth_header.__code__.co_filename, shared)


class ExitCode(Harness):
    """`main()`'s contract, over the same stub — the caller's only signal."""

    def main_with(self, doc, workspace, argv):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            original_gql, original_key = successor.gql, successor.get_key
            successor.gql = workspace
            successor.get_key = lambda: "api-key"
            try:
                with redirect_stdout(io.StringIO()) as buf:
                    code = successor.main(["--mapping", str(path), *argv])
                return code, buf.getvalue()
            finally:
                successor.gql, successor.get_key = original_gql, original_key

    def test_a_clean_run_exits_zero(self):
        ws = Workspace([issue("PRE-1", 101)])
        code, _ = self.main_with(mapping(("PRE-1", 101)), ws, ["--cancel", "--apply"])
        self.assertEqual(code, 0)
        self.assertEqual(
            ws.written, ["commentCreate", "attachmentCreate", "issueUpdate"]
        )

    def test_default_invocation_is_a_dry_run(self):
        ws = Workspace([issue("PRE-1", 101)])
        code, out = self.main_with(mapping(("PRE-1", 101)), ws, [])
        self.assertEqual(code, 0)
        self.assertEqual(ws.written, [])
        self.assertIn("DRY RUN", out)

    def test_a_failed_write_exits_nonzero(self):
        ws = Workspace([issue("PRE-1", 101)], fail={"issueUpdate"})
        code, _ = self.main_with(mapping(("PRE-1", 101)), ws, ["--cancel", "--apply"])
        self.assertEqual(code, 1)

    def test_an_unreadable_issue_exits_nonzero(self):
        ws = Workspace([], missing={"PRE-1"})
        code, _ = self.main_with(mapping(("PRE-1", 101)), ws, ["--cancel", "--apply"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
