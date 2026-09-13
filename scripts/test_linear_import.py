#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-import.py's --plan mode.

Stubs the GitHub read so nothing reaches the network, and drives `main()` over a
fixture export that carries one issue per crosswalk row. The crosswalk is the
deliverable and nothing downstream re-derives it: a wrong rung here becomes a
wrong GitHub issue in the apply task, and a wrong label set is indistinguishable
from a right one once it has landed. So the labels are asserted ROW BY ROW
against the table in the milestone-1 plan rather than in aggregate, and every
refusal path — an unreadable or still-open reopen target, a Linear label inside
a managed namespace, a renamed review state, a mistyped project name — is
asserted to write no file at all.
"""

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "linear-import.py"

_spec = importlib.util.spec_from_file_location("linear_import", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
linear_import = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(linear_import)

REPO = "bestdan/workflow-skills"
SELECTED_PROJECT = "workflow-skills backlog"
MILESTONE_PROJECT = "reviewer-quality"
EMPTY_PROJECT = "Linear MCP token-cost fix — GraphQL fast-path for find-candidates"
OTHER_PROJECT = "finplan backlog"
VIEWER = "dp.egan@gmail.com"


def issue(
    identifier,
    state=("backlog", "Backlog"),
    project=SELECTED_PROJECT,
    priority=3,
    estimate=3,
    labels=(),
    parent=None,
    blocked_by=(),
    blocks=(),
    related=(),
    attachments=(),
    comments=(),
    assignee=None,
    description=None,
):
    """One issue in the shape linear-export.py writes.

    Only the fields --plan reads are spelled out; the export carries more, and a
    fixture that mirrored all of it would hide which ones the crosswalk depends
    on.
    """
    state_type, state_name = state
    return {
        "id": "id-" + identifier,
        "identifier": identifier,
        "title": "Title " + identifier,
        "description": (
            description if description is not None else f"Body of {identifier}."
        ),
        "priority": priority,
        "estimate": estimate,
        "url": f"https://linear.app/prethinkio/issue/{identifier}/slug",
        "state": {"type": state_type, "name": state_name},
        "project": {"id": "proj-" + (project or "none"), "name": project}
        if project
        else None,
        "parent": parent,
        "children": [],
        "assignee": {"name": assignee, "email": assignee} if assignee else None,
        "creator": {"name": "Dan Egan"},
        "labels": list(labels),
        # Direction mirrors the export: `relations` is what this issue points at,
        # `inverseRelations` is what points at it — so a `blocks` entry there is
        # a blocker OF this issue.
        "relations": [{"type": "blocks", "identifier": k} for k in blocks]
        + [{"type": "related", "identifier": k} for k in related],
        "inverseRelations": [{"type": "blocks", "identifier": k} for k in blocked_by],
        "attachments": [{"title": t, "url": "https://x"} for t in attachments],
        "comments": [
            {
                "body": b,
                "createdAt": "2026-03-01T00:00:00.000Z",
                "user": {"name": "Dan Egan"},
            }
            for b in comments
        ],
    }


def fixture_issues():
    """One issue per crosswalk row, plus the four edge cases the task names."""
    return [
        # backlog, no routing label, priority 0 (None -> no prio:), no estimate
        issue("PRE-1", priority=0, estimate=None),
        # backlog + human-approval-requested, priority 1 (Urgent -> prio:0)
        issue("PRE-2", labels=("human-approval-requested",), priority=1, estimate=1),
        # unstarted (Todo) + auto-eligible, parent and blocker both in selection
        issue(
            "PRE-3",
            state=("unstarted", "Todo"),
            labels=("auto-eligible",),
            priority=2,
            estimate=3,
            parent="PRE-4",
            blocked_by=("PRE-4",),
        ),
        # the parent/blocker side of that pair
        issue(
            "PRE-4",
            state=("unstarted", "Todo"),
            priority=3,
            estimate=5,
            blocks=("PRE-3",),
        ),
        # started, not the review state; auto-claimed is dropped, not carried
        issue(
            "PRE-5",
            state=("started", "In Progress"),
            labels=("auto-eligible", "auto-claimed"),
            priority=4,
            estimate=13,
            assignee=VIEWER,
        ),
        # started, the review state; off-vocabulary estimate; carried labels;
        # assigned to somebody else
        issue(
            "PRE-6",
            state=("started", "In Review"),
            project=MILESTONE_PROJECT,
            labels=("papercut", "Bug"),
            estimate=7,
            assignee="someone.else@example.com",
        ),
        # a reopen candidate, oversized, with a related relation for the footer
        issue(
            "PRE-7",
            estimate=8,
            related=("PRE-9",),
            attachments=("GitHub #288 (migrated)",),
            comments=("first comment",),
        ),
        # blocked by an issue outside the selection
        issue("PRE-8", blocked_by=("PRE-99",)),
        # selected only by --issue, and unprojected, so it gets no milestone
        issue("PRE-10", project=None),
        # in the selection's projects but terminal, so never imported
        issue("PRE-11", state=("completed", "Done")),
        # the selected project whose every issue is terminal: it is a real
        # project, it contributes nothing, and that zero is not a selection bug
        issue("PRE-16", state=("canceled", "Canceled"), project=EMPTY_PROJECT),
        # live, but in a project nobody selected
        issue("PRE-9", project=OTHER_PROJECT),
        issue("PRE-99", project=OTHER_PROJECT),
    ]


def export_document(issues=None):
    return {
        "exported_at": "2026-09-13T12:00:00Z",
        "team": {"id": "team-1", "key": "PRE", "name": "PreThink"},
        "issue_count": len(issues or fixture_issues()),
        "issues": issues if issues is not None else fixture_issues(),
    }


class Reader:
    """A stubbed `gh issue view`, recording what it was asked about."""

    def __init__(self, states=None):
        self.states = {288: "CLOSED", **(states or {})}
        self.calls = []

    def __call__(self, repo, number):
        self.calls.append((repo, number))
        return self.states.get(number)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = str(Path(self.tmp) / "2026-09-13-import-plan.json")
        self._orig_reader = linear_import.github_issue_state
        self.reader = Reader()
        linear_import.github_issue_state = self.reader
        self.addCleanup(self._restore)

    def _restore(self):
        linear_import.github_issue_state = self._orig_reader

    def _export(self, issues=None):
        path = Path(self.tmp) / "export.json"
        path.write_text(json.dumps(export_document(issues)), encoding="utf-8")
        return str(path)

    def _run(self, issues=None, extra_args=(), export_path=None):
        argv = [
            "--plan",
            "--export",
            export_path or self._export(issues),
            "--repo",
            REPO,
            "--project",
            SELECTED_PROJECT,
            "--project",
            MILESTONE_PROJECT,
            "--project",
            EMPTY_PROJECT,
            "--issue",
            "PRE-10",
            "--date",
            "2026-09-13",
            "--out",
            self.out,
            *extra_args,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = linear_import.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _plan(self, **kwargs):
        code, _, err = self._run(**kwargs)
        self.assertEqual(code, 0, err)
        with open(self.out, encoding="utf-8") as fh:
            return json.load(fh)

    def _entries(self, **kwargs):
        return {entry["key"]: entry for entry in self._plan(**kwargs)["entries"]}


class SelectionTests(PlanTests):
    def test_only_live_issues_in_the_enumerated_projects_are_selected(self):
        entries = self._entries()
        self.assertEqual(
            sorted(entries, key=linear_import.key_number),
            [
                "PRE-1",
                "PRE-2",
                "PRE-3",
                "PRE-4",
                "PRE-5",
                "PRE-6",
                "PRE-7",
                "PRE-8",
                "PRE-10",
            ],
        )

    def test_a_terminal_issue_in_a_selected_project_is_left_behind(self):
        self.assertNotIn("PRE-11", self._entries())

    def test_a_live_issue_in_an_unselected_project_is_left_behind(self):
        entries = self._entries()
        self.assertNotIn("PRE-9", entries)
        self.assertNotIn("PRE-99", entries)

    def test_a_mistyped_project_name_refuses_rather_than_selecting_nothing(self):
        code, _, err = self._run(extra_args=("--project", "workflow-skills-backlog"))
        self.assertEqual(code, 2)
        self.assertIn("no project in the export is named", err)
        self.assertFalse(Path(self.out).exists())

    def test_an_absent_extra_key_refuses(self):
        code, _, err = self._run(extra_args=("--issue", "PRE-4242"))
        self.assertEqual(code, 2)
        self.assertIn("not in the export", err)
        self.assertFalse(Path(self.out).exists())

    def test_a_terminal_extra_key_refuses(self):
        """An explicitly named issue that is not live is a caller error.

        Dropping it the way the project filter drops terminal issues would make
        `--issue PRE-11` a no-op, and the enumeration exists precisely so that
        nothing is dropped quietly.
        """
        code, _, err = self._run(extra_args=("--issue", "PRE-11"))
        self.assertEqual(code, 2)
        self.assertIn("not live", err)
        self.assertIn("PRE-11", err)

    def test_a_selected_project_with_no_live_issues_is_reported_as_zero(self):
        summary = self._plan()["summary"]
        self.assertEqual(summary["by_project"][EMPTY_PROJECT], 0)
        self.assertEqual(summary["empty_projects"], [EMPTY_PROJECT])


class CrosswalkTests(PlanTests):
    """Every row of the milestone-1 crosswalk table, asserted per issue."""

    EXPECTED = {
        "PRE-1": ["status:0_untriaged", "auto:human-review-needed"],
        "PRE-2": [
            "status:1_needs_refinement",
            "auto:human-review-needed",
            "prio:0",
            "est:1",
        ],
        "PRE-3": ["status:2_ready", "auto:eligible", "prio:1", "est:3"],
        "PRE-4": ["status:2_ready", "auto:human-review-needed", "prio:2", "est:5"],
        "PRE-5": ["status:3_started", "auto:eligible", "prio:3", "est:13"],
        "PRE-6": ["status:4_needs_review", "auto:human-review-needed", "prio:2"],
        "PRE-7": ["status:0_untriaged", "auto:human-review-needed", "prio:2", "est:8"],
        "PRE-8": ["status:0_untriaged", "auto:human-review-needed", "prio:2", "est:3"],
        "PRE-10": ["status:0_untriaged", "auto:human-review-needed", "prio:2", "est:3"],
    }

    def test_managed_labels_row_by_row(self):
        entries = self._entries()
        for key, expected in self.EXPECTED.items():
            with self.subTest(key=key):
                self.assertEqual(entries[key]["managed_labels"], expected)

    def test_linear_priority_zero_means_none_not_highest(self):
        """The one row where a mechanical mapping is wrong in both directions."""
        entries = self._entries()
        self.assertNotIn("prio:0", entries["PRE-1"]["managed_labels"])
        self.assertIn("prio:0", entries["PRE-2"]["managed_labels"])

    def test_in_review_is_the_only_started_row_that_is_needs_review(self):
        entries = self._entries()
        self.assertEqual(entries["PRE-5"]["managed_labels"][0], "status:3_started")
        self.assertEqual(entries["PRE-6"]["managed_labels"][0], "status:4_needs_review")

    def test_an_off_vocabulary_estimate_gets_no_est_label(self):
        # 7 is a real Linear estimate and has no label in labels.yml; emitting
        # `est:7` anyway would CREATE it on the first REST write.
        entries = self._entries()
        self.assertEqual(
            [
                label
                for label in entries["PRE-6"]["managed_labels"]
                if label.startswith("est:")
            ],
            [],
        )

    def test_a_float_estimate_maps_to_the_integer_label(self):
        issues = fixture_issues()
        for row in issues:
            if row["identifier"] == "PRE-1":
                row["estimate"] = 5.0
        self.assertIn("est:5", self._entries(issues=issues)["PRE-1"]["managed_labels"])

    def test_carried_labels_are_mapped_and_consumed_ones_are_not_duplicated(self):
        entries = self._entries()
        self.assertEqual(entries["PRE-6"]["carried_labels"], ["papercut", "bug"])
        # auto-eligible decided `auto:eligible`; auto-claimed is expressed by the
        # `status:3_started` rung. Neither may also ride along as a label.
        self.assertEqual(entries["PRE-5"]["carried_labels"], [])

    def test_a_linear_label_with_no_crosswalk_row_is_dropped_and_reported(self):
        issues = fixture_issues()
        for row in issues:
            if row["identifier"] == "PRE-1":
                row["labels"] = ["needs_refinement"]
        plan = self._plan(issues=issues)
        entry = {e["key"]: e for e in plan["entries"]}["PRE-1"]
        self.assertEqual(entry["dropped_labels"], ["needs_refinement"])
        self.assertEqual(entry["carried_labels"], [])
        self.assertEqual(plan["summary"]["dropped_labels"], {"needs_refinement": 1})

    def test_a_linear_label_inside_a_managed_namespace_refuses(self):
        """The reachable `status:` collision, refused where the cause is legible.

        A hand-typed Linear label `status:3_started` carried alongside the rung
        the crosswalk computed is a set with two `status:` labels. validate()
        would catch it at the end of the plan; refusing here names the label and
        the issue instead of the arithmetic.
        """
        issues = fixture_issues()
        for row in issues:
            if row["identifier"] == "PRE-1":
                row["labels"] = ["status:3_started"]
        code, _, err = self._run(issues=issues)
        self.assertEqual(code, 2)
        self.assertIn("status:3_started", err)
        self.assertIn("PRE-1", err)
        self.assertFalse(Path(self.out).exists())

    def test_the_validator_is_the_write_helpers_own(self):
        """Not a second copy of the invariants.

        If build_entries() validated with a local reimplementation, this plan
        could pass a set the write helper later refuses — and the refusal would
        land mid-import, after earlier issues had already been created.
        """
        validator = linear_import.load_validator()
        with self.assertRaises(validator.InvalidLabelSet):
            validator.validate(
                ["status:2_ready", "status:3_started", "auto:eligible"],
                {"status:2_ready": "x", "status:3_started": "x", "auto:eligible": "x"},
            )

    def test_a_colliding_crosswalk_refuses_instead_of_writing(self):
        """The guard, exercised through the seam a crosswalk bug would come from.

        Nothing in the fixture can make managed_labels() emit two rungs today;
        the check exists because a future crosswalk edit could. Patching the
        mapping is the only way to prove the refusal is wired rather than merely
        present.
        """
        original = linear_import.managed_labels
        linear_import.managed_labels = lambda *a, **k: [
            "status:2_ready",
            "status:3_started",
            "auto:eligible",
        ]
        self.addCleanup(setattr, linear_import, "managed_labels", original)
        code, _, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("unwritable", err)
        self.assertIn("status:", err)
        self.assertFalse(Path(self.out).exists())


class ReviewStateTests(PlanTests):
    def test_a_team_without_the_review_state_refuses(self):
        """A renamed review state must not route every in-review issue to started.

        That failure is invisible: `status:3_started` is a legal rung, so the
        plan would read as healthy while the review column had silently vanished.
        """
        issues = fixture_issues()
        for row in issues:
            if row["identifier"] == "PRE-6":
                row["state"] = {"type": "started", "name": "Under review"}
        code, _, err = self._run(issues=issues)
        self.assertEqual(code, 2)
        self.assertIn("In Review", err)
        self.assertFalse(Path(self.out).exists())

    def test_a_renamed_review_state_is_honoured_by_flag(self):
        issues = fixture_issues()
        for row in issues:
            if row["identifier"] == "PRE-6":
                row["state"] = {"type": "started", "name": "Under review"}
        entries = self._entries(
            issues=issues, extra_args=("--review-state", "Under review")
        )
        self.assertEqual(entries["PRE-6"]["managed_labels"][0], "status:4_needs_review")


class ReopenTests(PlanTests):
    def test_a_migrated_issue_reopens_its_original(self):
        entries = self._entries()
        self.assertEqual(entries["PRE-7"]["action"], "reopen")
        self.assertEqual(entries["PRE-7"]["number"], 288)
        self.assertIn((REPO, 288), self.reader.calls)

    def test_everything_else_is_created(self):
        entries = self._entries()
        self.assertEqual(
            {e["action"] for k, e in entries.items() if k != "PRE-7"}, {"create"}
        )
        self.assertIsNone(entries["PRE-1"]["number"])

    def test_an_open_original_refuses(self):
        self.reader.states[288] = "OPEN"
        code, _, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("two live homes", err)
        self.assertFalse(Path(self.out).exists())

    def test_an_unreadable_original_refuses(self):
        self.reader.states[288] = None
        code, _, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)
        self.assertFalse(Path(self.out).exists())

    def test_every_bad_reopen_target_is_reported_in_one_run(self):
        issues = fixture_issues()
        issues.append(issue("PRE-12", attachments=("GitHub #400 (migrated)",)))
        self.reader.states[288] = "OPEN"
        self.reader.states[400] = None
        code, _, err = self._run(issues=issues)
        self.assertEqual(code, 2)
        self.assertIn("PRE-7", err)
        self.assertIn("PRE-12", err)

    def test_the_description_footer_is_the_second_detector(self):
        """The live export writes it markdown-wrapped, mid-body.

        An anchored bare-URL pattern matches none of the eight real candidates,
        and a missed candidate is a duplicate GitHub issue rather than a loud
        failure — so this is the form the regex must accept.
        """
        footer_re = linear_import.migrated_footer_re(REPO)
        row = issue(
            "PRE-13",
            description=(
                "Body.\n\nMigrated from "
                f"[https://github.com/{REPO}/issues/288]"
                f"(<https://github.com/{REPO}/issues/288>)\n\n---\n\n"
                "> **Sizing flag (added during migration):** estimated **8**.\n"
            ),
        )
        self.assertEqual(linear_import.migrated_number(row, footer_re), 288)

    def test_a_footer_naming_another_repo_is_not_an_original(self):
        footer_re = linear_import.migrated_footer_re(REPO)
        row = issue(
            "PRE-14",
            description="Migrated from https://github.com/bestdan/dotfiles/issues/288",
        )
        self.assertIsNone(linear_import.migrated_number(row, footer_re))

    def test_disagreeing_markers_refuse(self):
        footer_re = linear_import.migrated_footer_re(REPO)
        row = issue(
            "PRE-15",
            attachments=("GitHub #288 (migrated)",),
            description=f"Migrated from https://github.com/{REPO}/issues/999",
        )
        with self.assertRaises(linear_import.PlanError) as ctx:
            linear_import.migrated_number(row, footer_re)
        self.assertIn("disagree", str(ctx.exception))


class GraphTests(PlanTests):
    def test_a_parent_in_the_selection_becomes_a_sub_issue_link(self):
        entries = self._entries()
        self.assertEqual(entries["PRE-3"]["parent"], "PRE-4")
        self.assertIsNone(entries["PRE-3"]["dropped_parent"])

    def test_a_parent_outside_the_selection_is_dropped_and_reported(self):
        issues = fixture_issues()
        for row in issues:
            if row["identifier"] == "PRE-3":
                row["parent"] = "PRE-99"
        plan = self._plan(issues=issues)
        entry = {e["key"]: e for e in plan["entries"]}["PRE-3"]
        self.assertIsNone(entry["parent"])
        self.assertEqual(entry["dropped_parent"], "PRE-99")
        self.assertEqual(
            plan["summary"]["parents_outside_selection"], {"PRE-3": "PRE-99"}
        )

    def test_a_blocks_relation_is_read_from_one_end_only(self):
        """PRE-4 blocks PRE-3, so only PRE-3 carries the edge.

        Reading `relations` as well would double every edge, and reading it
        instead of `inverseRelations` would write each dependency backwards —
        which nothing downstream could notice.
        """
        plan = self._plan()
        entries = {e["key"]: e for e in plan["entries"]}
        self.assertEqual(entries["PRE-3"]["blocked_by"], ["PRE-4"])
        self.assertEqual(entries["PRE-4"]["blocked_by"], [])
        self.assertEqual(plan["summary"]["edges"], 1)

    def test_a_blocker_outside_the_selection_is_dropped_and_footnoted(self):
        plan = self._plan()
        entry = {e["key"]: e for e in plan["entries"]}["PRE-8"]
        self.assertEqual(entry["blocked_by"], [])
        self.assertEqual(entry["dropped_blockers"], ["PRE-99"])
        self.assertEqual(
            plan["summary"]["blockers_outside_selection"], {"PRE-8": ["PRE-99"]}
        )

    def test_a_dropped_blocker_is_not_spelled_blocked_by(self):
        """`Blocked by:` is an echo of a native edge, and there is none here.

        /reoptimize-tasks reads that spelling as a dependency claim, so writing
        it for a blocker that is not being imported would mint a dependency
        nothing can ever satisfy.
        """
        body = {e["key"]: e for e in self._plan()["entries"]}["PRE-8"]["body"]
        self.assertIn("Blockers not migrated: PRE-99", body)
        self.assertNotIn("Blocked by:", body)


class BodyTests(PlanTests):
    def test_the_footer_cites_the_canonical_linear_url_and_the_date(self):
        body = {e["key"]: e for e in self._plan()["entries"]}["PRE-1"]["body"]
        self.assertIn("Body of PRE-1.", body)
        self.assertIn(
            "Migrated from Linear PRE-1 "
            "(https://linear.app/prethinkio/issue/PRE-1) on 2026-09-13.",
            body,
        )

    def test_related_relations_become_a_footer_line_not_an_edge(self):
        entry = {e["key"]: e for e in self._plan()["entries"]}["PRE-7"]
        self.assertEqual(entry["related"], ["PRE-9"])
        self.assertEqual(entry["blocked_by"], [])
        self.assertIn("Related: PRE-9", entry["body"])

    def test_an_issue_with_no_related_relation_has_no_related_line(self):
        body = {e["key"]: e for e in self._plan()["entries"]}["PRE-1"]["body"]
        self.assertNotIn("Related:", body)

    def test_inline_issue_markup_is_rewritten_to_a_plain_key(self):
        # The key must survive as plain text: the linking task rewrites keys that
        # landed in this import to `#number`, and it can only find plain ones.
        text = (
            'see <issue id="uuid" href="https://linear.app/prethinkio/issue/PRE-9/s">'
            "PRE-9</issue> first"
        )
        self.assertEqual(
            linear_import.rewrite_issue_mentions(text),
            "see PRE-9 (https://linear.app/prethinkio/issue/PRE-9/s) first",
        )

    def test_a_mention_without_an_href_degrades_to_the_bare_key(self):
        self.assertEqual(
            linear_import.rewrite_issue_mentions('a <issue id="u">PRE-9</issue> b'),
            "a PRE-9 b",
        )

    def test_comments_carry_author_and_timestamp(self):
        plan = self._plan()
        entry = {e["key"]: e for e in plan["entries"]}["PRE-7"]
        self.assertEqual(
            entry["comments"],
            [
                {
                    "author": "Dan Egan",
                    "at": "2026-03-01T00:00:00.000Z",
                    "body": "first comment",
                }
            ],
        )
        self.assertEqual(plan["summary"]["with_comments"], 1)


class MilestoneTests(PlanTests):
    def test_a_named_project_becomes_a_milestone(self):
        self.assertEqual(self._entries()["PRE-6"]["milestone"], MILESTONE_PROJECT)

    def test_the_catch_all_backlog_gets_no_milestone(self):
        self.assertIsNone(self._entries()["PRE-1"]["milestone"])

    def test_an_unprojected_issue_gets_no_milestone(self):
        self.assertIsNone(self._entries()["PRE-10"]["milestone"])

    def test_the_no_milestone_list_is_overridable(self):
        entries = self._entries(extra_args=("--no-milestone", MILESTONE_PROJECT))
        self.assertIsNone(entries["PRE-6"]["milestone"])
        self.assertEqual(entries["PRE-1"]["milestone"], SELECTED_PROJECT)

    def test_the_document_lists_the_milestones_to_create(self):
        self.assertEqual(self._plan()["milestones"], [MILESTONE_PROJECT])


class AssigneeTests(PlanTests):
    def test_a_started_issue_assigned_to_the_viewer_gets_me(self):
        entries = self._entries(extra_args=("--viewer", VIEWER))
        self.assertEqual(entries["PRE-5"]["assignee"], "@me")
        self.assertIsNone(entries["PRE-5"]["assignee_mismatch"])

    def test_someone_elses_started_issue_is_left_unassigned_and_reported(self):
        plan = self._plan(extra_args=("--viewer", VIEWER))
        entry = {e["key"]: e for e in plan["entries"]}["PRE-6"]
        self.assertIsNone(entry["assignee"])
        self.assertEqual(entry["assignee_mismatch"], "someone.else@example.com")
        self.assertEqual(
            plan["summary"]["assignee_mismatches"],
            {"PRE-6": "someone.else@example.com"},
        )

    def test_without_a_viewer_nothing_is_assigned(self):
        plan = self._plan()
        entries = {e["key"]: e for e in plan["entries"]}
        self.assertIsNone(entries["PRE-5"]["assignee"])
        self.assertEqual(
            sorted(plan["summary"]["assignee_mismatches"]), ["PRE-5", "PRE-6"]
        )

    def test_an_unstarted_issue_is_never_assigned(self):
        entries = self._entries(extra_args=("--viewer", VIEWER))
        self.assertIsNone(entries["PRE-3"]["assignee"])
        self.assertIsNone(entries["PRE-3"]["assignee_mismatch"])


class OutputTests(PlanTests):
    def test_summary_counts(self):
        summary = self._plan()["summary"]
        self.assertEqual(summary["entries"], 9)
        self.assertEqual(summary["by_action"], {"create": 8, "reopen": 1})
        self.assertEqual(summary["by_status"]["status:0_untriaged"], 4)
        self.assertEqual(summary["reopen_targets"], {"PRE-7": 288})
        self.assertEqual(summary["sub_issues"], 1)

    def test_oversized_estimates_are_flagged_for_break_down_task(self):
        summary = self._plan()["summary"]
        self.assertEqual(summary["oversized"], {"PRE-5": 13, "PRE-7": 8})

    def test_the_human_summary_names_the_zero_categories(self):
        _, out, _ = self._run()
        self.assertIn("selected but contributing nothing", out)
        self.assertIn("blockers outside the selection", out)
        self.assertIn("PRE-7->#288", out)

    def test_an_existing_plan_is_not_replaced_without_force(self):
        self._plan()
        code, _, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("--force", err)

    def test_force_replaces_the_plan(self):
        first = self._plan()
        second = self._plan(extra_args=("--force",))
        self.assertEqual(first["summary"]["entries"], second["summary"]["entries"])

    def test_json_summary_carries_the_path(self):
        _, out, _ = self._run(extra_args=("--json",))
        self.assertEqual(json.loads(out)["path"], self.out)

    def test_selecting_nothing_is_a_usage_error(self):
        argv = [
            "--plan",
            "--export",
            self._export(),
            "--repo",
            REPO,
            "--out",
            self.out,
        ]
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                linear_import.main(argv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
