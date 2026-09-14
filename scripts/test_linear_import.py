#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/linear-import.py.

Nothing reaches the network: `--plan`'s GitHub read is stubbed, and `--apply`'s
two write seams — `run_gh` and `run_state_helper` — are replaced by one Recorder
over a single ordered call log.

`--plan` is driven over a fixture export carrying one issue per crosswalk row.
The crosswalk is the deliverable and nothing downstream re-derives it: a wrong
rung here becomes a wrong GitHub issue, and a wrong label set is
indistinguishable from a right one once it has landed. So the labels are asserted
ROW BY ROW against the table in the milestone-1 plan rather than in aggregate,
and every refusal path — an unreadable or still-open reopen target, a Linear
label inside a managed namespace, a renamed review state, a mistyped project
name — is asserted to write no file at all.

`--apply` is driven over a plan this file BUILDS with `--plan`, not over a
hand-written one: the two modes agree about every field name, and a hand-rolled
fixture would keep passing after a rename while the apply half silently wrote
nothing. What is asserted is what a rerun must not repeat — a done key makes no
call, each phase is recorded before the next begins, a lost create is adopted
rather than created twice, a posted transcript is not posted again — plus the
orderings the writes depend on and the refusals that must fire before anything
lands.
"""

import contextlib
import importlib.util
import io
import json
import os
import re
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


def attachment(entry):
    """One attachment, as the export records it — `{title, url}`.

    A bare string is a marker whose url points at the target repo's issue of the
    same number, which is what the eight real candidates look like. A
    `(title, url)` pair spells the url out, for the cases where it names another
    repo or a different number.
    """
    if isinstance(entry, tuple):
        title, url = entry
        return {"title": title, "url": url}
    number = re.search(r"#(\d+)", entry)
    url = (
        f"https://github.com/{REPO}/issues/{number.group(1)}" if number else "https://x"
    )
    return {"title": entry, "url": url}


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
        "attachments": [attachment(entry) for entry in attachments],
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

    def _show(self, *keys, extra_args=()):
        """Build a plan, then read it back through --show."""
        self._plan()
        argv = [
            "--export",
            str(Path(self.tmp) / "export.json"),
            "--plan-file",
            self.out,
        ]
        for key in keys:
            argv += ["--show", key]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = linear_import.main(argv + list(extra_args))
        return code, out.getvalue(), err.getvalue()


class ShowTests(PlanTests):
    """--show is what makes the plan checkable by a person.

    The acceptance criterion on a 125-entry plan is a human deciding whether the
    crosswalk did the right thing to a handful of known issues, which means seeing
    the Linear row and the GitHub row together.
    """

    def test_both_sides_of_one_entry_are_printed(self):
        code, out, err = self._show("PRE-3")
        self.assertEqual(code, 0, err)
        self.assertIn("Todo (unstarted)", out)
        self.assertIn("status:2_ready", out)
        self.assertIn("blocked_by: PRE-4", out)
        self.assertIn("parent: PRE-4", out)

    def test_relation_direction_is_shown_not_flattened(self):
        """`-> blocks` and `<- blocks` are the fact being checked.

        Only the incoming one becomes a `blocked_by` edge, so a merged list would
        hide exactly what a reader is here to verify.
        """
        code, out, _ = self._show("PRE-3", "PRE-4")
        self.assertEqual(code, 0)
        self.assertIn("<- blocks PRE-4", out)
        self.assertIn("-> blocks PRE-3", out)

    def test_the_footer_starts_at_the_last_rule_not_the_first(self):
        """A Linear description may carry its own `---`."""
        issues = fixture_issues()
        for row in issues:
            if row["identifier"] == "PRE-1":
                row["description"] = "Body.\n\n---\n\nA note after a rule."
        self._plan(issues=issues)
        argv = [
            "--export",
            str(Path(self.tmp) / "export.json"),
            "--plan-file",
            self.out,
            "--show",
            "PRE-1",
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(linear_import.main(argv), 0)
        rendered = out.getvalue()
        self.assertIn("Migrated from Linear PRE-1", rendered)
        self.assertNotIn("A note after a rule.", rendered)

    def test_a_key_absent_from_the_plan_refuses(self):
        """Silence would read as "nothing to say", not "never selected"."""
        code, _, err = self._show("PRE-99")
        self.assertEqual(code, 2)
        self.assertIn("not in the plan", err)
        self.assertIn("PRE-99", err)

    def test_show_without_a_plan_file_is_a_usage_error(self):
        argv = ["--show", "PRE-1", "--export", self._export()]
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                linear_import.main(argv)

    def test_the_two_modes_are_mutually_exclusive(self):
        argv = ["--plan", "--show", "PRE-1", "--export", self._export()]
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                linear_import.main(argv)

    def test_plan_still_needs_its_own_flags(self):
        for missing in ("--repo", "--out"):
            with self.subTest(missing=missing):
                argv = ["--plan", "--export", self._export(), "--project", "x"]
                if missing != "--repo":
                    argv += ["--repo", REPO]
                if missing != "--out":
                    argv += ["--out", self.out]
                with self.assertRaises(SystemExit):
                    with contextlib.redirect_stderr(io.StringIO()):
                        linear_import.main(argv)


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

    def test_no_selected_started_issue_means_the_check_cannot_fire(self):
        """With nothing `started` selected, the review row applies to no entry.

        The export carries no state catalogue, so the name is checked against the
        states issues occupy — and a review column nobody currently sits in would
        otherwise refuse every run. Refusing over a row that cannot apply is a
        false alarm, so the gate is what the selection actually needs.
        """
        issues = [
            row
            for row in fixture_issues()
            if (row["state"]["type"] != "started" or row["identifier"] == "PRE-6")
        ]
        for row in issues:
            # PRE-6 keeps the milestone project in the selection but stops being
            # `started`, so no selected issue is.
            if row["identifier"] == "PRE-6":
                row["state"] = {"type": "backlog", "name": "Backlog"}
        entries = self._entries(issues=issues)
        self.assertEqual(entries["PRE-6"]["managed_labels"][0], "status:0_untriaged")
        self.assertNotIn("PRE-5", entries)


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

    def test_two_issues_claiming_one_number_refuse(self):
        """Otherwise --apply reopens one issue twice and loses the other.

        Unlike the open and unreadable cases, this one fails silently: both
        entries are legal on their own, and the loss only shows up as a Linear
        key with no GitHub home once the import has run.
        """
        issues = fixture_issues()
        issues.append(issue("PRE-19", attachments=("GitHub #288 (migrated)",)))
        code, _, err = self._run(issues=issues)
        self.assertEqual(code, 2)
        self.assertIn("already claimed by", err)
        self.assertIn("PRE-7", err)
        self.assertIn("PRE-19", err)
        self.assertFalse(Path(self.out).exists())

    def test_the_description_footer_is_the_second_detector(self):
        """The live export writes it markdown-wrapped, mid-body.

        An anchored bare-URL pattern matches none of the eight real candidates,
        and a missed candidate is a duplicate GitHub issue rather than a loud
        failure — so this is the form the regex must accept.
        """
        markers = linear_import.migrated_markers(REPO)
        row = issue(
            "PRE-13",
            description=(
                "Body.\n\nMigrated from "
                f"[https://github.com/{REPO}/issues/288]"
                f"(<https://github.com/{REPO}/issues/288>)\n\n---\n\n"
                "> **Sizing flag (added during migration):** estimated **8**.\n"
            ),
        )
        self.assertEqual(linear_import.migrated_number(row, markers), 288)

    def test_a_footer_naming_another_repo_is_not_an_original(self):
        markers = linear_import.migrated_markers(REPO)
        row = issue(
            "PRE-14",
            description="Migrated from https://github.com/bestdan/dotfiles/issues/288",
        )
        self.assertIsNone(linear_import.migrated_number(row, markers))

    def test_disagreeing_markers_refuse(self):
        markers = linear_import.migrated_markers(REPO)
        row = issue(
            "PRE-15",
            attachments=("GitHub #288 (migrated)",),
            description=f"Migrated from https://github.com/{REPO}/issues/999",
        )
        with self.assertRaises(linear_import.PlanError) as ctx:
            linear_import.migrated_number(row, markers)
        self.assertIn("disagree", str(ctx.exception))

    def test_an_attachment_naming_another_repo_is_not_an_original(self):
        """An attachment TITLE carries no repository identity.

        `GitHub #288 (migrated)` left by another repo's migration would reopen
        #288 here on the title alone, which is a write against an unrelated
        issue. The url is the only thing that says which repo the marker is
        about, so it decides.
        """
        markers = linear_import.migrated_markers(REPO)
        row = issue(
            "PRE-17",
            attachments=(
                (
                    "GitHub #288 (migrated)",
                    "https://github.com/bestdan/dotfiles/issues/288",
                ),
            ),
        )
        self.assertIsNone(linear_import.migrated_number(row, markers))

    def test_an_attachment_disagreeing_with_its_own_url_refuses(self):
        markers = linear_import.migrated_markers(REPO)
        row = issue(
            "PRE-18",
            attachments=(
                ("GitHub #288 (migrated)", f"https://github.com/{REPO}/issues/290"),
            ),
        )
        with self.assertRaises(linear_import.PlanError) as ctx:
            linear_import.migrated_number(row, markers)
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

    def test_the_viewer_matches_either_the_linear_name_or_the_email(self):
        """Linear carries both, and `--viewer` accepts either.

        Everywhere else in the fixture an assignee has one identity under both
        fields, so this branch — the reason build_entries reads a set rather than
        a single field — would otherwise never run.
        """
        for viewer in ("Dan Egan", "dan@example.com"):
            with self.subTest(viewer=viewer):
                issues = fixture_issues()
                for row in issues:
                    if row["identifier"] == "PRE-5":
                        row["assignee"] = {
                            "name": "Dan Egan",
                            "email": "dan@example.com",
                        }
                entries = self._entries(
                    issues=issues, extra_args=("--viewer", viewer, "--force")
                )
                self.assertEqual(entries["PRE-5"]["assignee"], "@me")
                self.assertIsNone(entries["PRE-5"]["assignee_mismatch"])

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
        # Named, not None: json.dump would write that key as the string "null"
        # while the printed summary called the same bucket "(none)".
        self.assertEqual(summary["by_milestone"], {"(none)": 8, MILESTONE_PROJECT: 1})
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


class Recorder:
    """A stubbed `gh` and gh-issue-state.py over ONE ordered call log.

    One log rather than two because the ordering between the two seams is itself
    a contract: the carried labels must be added before gh-issue-state.py reads
    the issue, or its full-set PATCH deletes them. Two separate recorders could
    assert that both calls happened and not that they happened in that order.

    Every `--body-file` is read at call time and kept, since the real code
    unlinks the file straight afterwards — and the body is most of what a write
    is.
    """

    def __init__(self, labels=(), milestones=(), search=(), issues=None, first=900):
        self.calls = []
        self.bodies = []
        self.labels = list(labels) or [
            "papercut",
            "papercut-fix-now",
            "bug",
            "enhancement",
        ]
        self.milestones = [dict(entry) for entry in milestones]
        self.search = [dict(entry) for entry in search]
        self.issues = {int(k): dict(v) for k, v in (issues or {}).items()}
        self.next_issue = first
        self.next_milestone = 50
        self.fail_on = None
        self.state_result = (0, "", "")

    # -- the seams ---------------------------------------------------------
    def gh(self, args):
        args = list(args)
        self.calls.append(("gh", args))
        self._keep_body(args)
        if self.fail_on:
            forced = self.fail_on(args)
            if forced:
                return forced
        return (0, self._respond(args), "")

    def state(self, args):
        args = list(args)
        self.calls.append(("state", args))
        if self.state_result[0] == 0:
            number = int(args[args.index("--issue") + 1])
            issue = self.issues.setdefault(
                number, {"number": number, "state": "OPEN", "body": "", "comments": []}
            )
            if "--reopen" in args:
                issue["state"] = "OPEN"
        return self.state_result

    def install(self, case):
        case.addCleanup(setattr, linear_import, "run_gh", linear_import.run_gh)
        case.addCleanup(
            setattr, linear_import, "run_state_helper", linear_import.run_state_helper
        )
        linear_import.run_gh = self.gh
        linear_import.run_state_helper = self.state
        return self

    # -- canned responses --------------------------------------------------
    def _keep_body(self, args):
        if "--body-file" not in args:
            return
        path = args[args.index("--body-file") + 1]
        with open(path, encoding="utf-8") as fh:
            self.bodies.append((" ".join(args[:2]), fh.read()))

    def _respond(self, args):
        if args[:1] == ["api"]:
            if "--slurp" in args:
                return json.dumps([self.milestones])
            return self._create_milestone(args)
        if args[:2] == ["label", "list"]:
            return json.dumps([{"name": name} for name in self.labels])
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.search)
        if args[:2] == ["issue", "create"]:
            number, self.next_issue = self.next_issue, self.next_issue + 1
            self.issues[number] = {
                "number": number,
                "state": "OPEN",
                "body": self.bodies[-1][1],
                "comments": [],
            }
            return f"https://{HOST}/{REPO}/issues/{number}\n"
        if args[:2] == ["issue", "view"]:
            return self._view(args)
        return ""

    def _create_milestone(self, args):
        title = next(a.split("=", 1)[1] for a in args if a.startswith("title="))
        number, self.next_milestone = self.next_milestone, self.next_milestone + 1
        self.milestones.append({"title": title, "number": number})
        return json.dumps({"number": number, "title": title})

    def _view(self, args):
        number = int(args[2])
        issue = self.issues.get(
            number, {"number": number, "state": "OPEN", "body": "", "comments": []}
        )
        fields = args[args.index("--json") + 1].split(",")
        return json.dumps({field: issue.get(field) for field in fields})

    # -- what the assertions read -----------------------------------------
    def gh_calls(self, *prefix):
        return [
            args
            for kind, args in self.calls
            if kind == "gh" and args[: len(prefix)] == list(prefix)
        ]

    def state_calls(self):
        return [args for kind, args in self.calls if kind == "state"]

    def order(self, predicate):
        """Indices in the single log of every call matching `predicate`."""
        return [i for i, (kind, args) in enumerate(self.calls) if predicate(kind, args)]


HOST = "github.com"


def closed_original(number, key, comments=()):
    """A closed GitHub issue that still names its Linear key — a reopen target
    as the eight real ones look: the key is in the body, not only in a marker."""
    return {
        number: {
            "number": number,
            "state": "CLOSED",
            "body": f"Original body. Moved to Linear {key}.",
            "comments": [{"body": body} for body in comments],
        }
    }


class PlanFixture(unittest.TestCase):
    """A written plan, a mapping path, and a stubbed clock — for any write mode.

    The plan is GENERATED by `--plan` rather than hand-written, because the write
    modes and the plan builder have to agree about every field name: a hand-rolled
    fixture would keep passing after `--plan` renamed `carried_labels`, while the
    write half silently wrote nothing. Building it through main() makes the
    contract between the modes the thing under test.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plan_path = str(Path(self.tmp) / "plan.json")
        self.mapping = str(Path(self.tmp) / "mapping.json")
        self.addCleanup(setattr, linear_import, "nap", linear_import.nap)
        self.napped: list = []
        linear_import.nap = self.napped.append
        self._write_plan()
        self._watch_mapping()

    def _write_plan(self):
        export = Path(self.tmp) / "export.json"
        export.write_text(json.dumps(export_document()), encoding="utf-8")
        reader = Reader()
        original = linear_import.github_issue_state
        linear_import.github_issue_state = reader
        try:
            argv = [
                "--plan",
                "--export",
                str(export),
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
                "--viewer",
                VIEWER,
                "--out",
                self.plan_path,
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                code = linear_import.main(argv)
        finally:
            linear_import.github_issue_state = original
        assert code == 0, "the fixture plan must build"
        with open(self.plan_path, encoding="utf-8") as fh:
            self.plan = json.load(fh)

    def _watch_mapping(self):
        """Assert the mapping parses after EVERY append, not only at the end.

        The file is the one thing standing between a crash and a duplicate
        import, so "valid JSON once the run finished" is the wrong tense.
        """
        original = linear_import.save_mapping
        self.addCleanup(setattr, linear_import, "save_mapping", original)
        self.saves: list = []

        def watched(mapping, path):
            original(mapping, path)
            with open(path, encoding="utf-8") as fh:
                self.saves.append(json.load(fh))

        linear_import.save_mapping = watched

    def _mapping(self):
        with open(self.mapping, encoding="utf-8") as fh:
            return json.load(fh)


class ApplyTests(PlanFixture):
    """--apply: what a rerun must not repeat, and what must refuse before a write."""

    def _apply(self, recorder=None, extra_args=(), expect=0):
        recorder = (recorder or Recorder()).install(self)
        argv = [
            "--apply",
            "--plan-file",
            self.plan_path,
            "--mapping",
            self.mapping,
            *extra_args,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = linear_import.main(argv)
        self.assertEqual(code, expect, err.getvalue() or out.getvalue())
        return recorder, out.getvalue(), err.getvalue()

    def _reopen_recorder(self, **kwargs):
        """The default board: #288 closed and still naming PRE-7, the plan's only
        reopen target."""
        issues = kwargs.pop("issues", None) or closed_original(288, "PRE-7")
        return Recorder(issues=issues, **kwargs)

    def _seed(self, **entries):
        mapping = {"repo": REPO, "milestones": {}, "entries": dict(entries)}
        with open(self.mapping, "w", encoding="utf-8") as fh:
            json.dump(mapping, fh)

    # -- the plan this all rests on ---------------------------------------
    def test_the_fixture_plan_has_the_shapes_the_tests_need(self):
        entries = {entry["key"]: entry for entry in self.plan["entries"]}
        self.assertEqual(entries["PRE-7"]["action"], "reopen")
        self.assertEqual(entries["PRE-7"]["number"], 288)
        self.assertTrue(entries["PRE-7"]["comments"])
        self.assertEqual(entries["PRE-6"]["carried_labels"], ["papercut", "bug"])
        self.assertEqual(entries["PRE-5"]["assignee"], "@me")

    # -- resume ------------------------------------------------------------
    def test_a_key_at_phase_done_makes_no_call_of_its_own(self):
        keys = [entry["key"] for entry in self.plan["entries"]]
        self._seed(
            **{
                key: {"key": key, "number": 700 + i, "phase": "done"}
                for i, key in enumerate(keys)
            }
        )
        recorder, out, _ = self._apply()
        self.assertEqual(recorder.gh_calls("issue", "create"), [])
        self.assertEqual(recorder.gh_calls("issue", "edit"), [])
        self.assertEqual(recorder.state_calls(), [])
        self.assertEqual(recorder.gh_calls("issue", "comment"), [])
        self.assertIn(f"already done={len(keys)}", out)
        self.assertIn("every planned key is in the mapping at phase done", out)

    def test_a_key_at_phase_created_skips_the_create_and_finishes_the_rest(self):
        # PRE-1 is the plan's first entry, so --limit 1 lands on exactly it.
        self._seed(**{"PRE-1": {"key": "PRE-1", "number": 601, "phase": "created"}})
        recorder, _, _ = self._apply(
            self._reopen_recorder(), extra_args=("--limit", "1")
        )
        self.assertEqual(
            recorder.gh_calls("issue", "create"), [], "the create phase was recorded"
        )
        self.assertEqual(
            [args[args.index("--issue") + 1] for args in recorder.state_calls()],
            ["601"],
        )
        self.assertEqual(self._mapping()["entries"]["PRE-1"]["phase"], "done")

    def test_a_key_at_phase_labelled_only_has_its_comments_left(self):
        self._seed(**{"PRE-7": {"key": "PRE-7", "number": 288, "phase": "labelled"}})
        recorder, _, _ = self._apply(self._reopen_recorder())
        self.assertEqual(
            [args for args in recorder.state_calls() if "288" in args],
            [],
            "the label phase was already recorded for this key",
        )
        self.assertEqual(
            [args[2] for args in recorder.gh_calls("issue", "comment")], ["288"]
        )

    def test_the_mapping_records_each_phase_before_the_next_one_starts(self):
        self._apply(self._reopen_recorder(), extra_args=("--limit", "1"))
        phases = [
            snapshot["entries"]["PRE-1"]["phase"]
            for snapshot in self.saves
            if "PRE-1" in snapshot["entries"]
        ]
        self.assertEqual(phases, ["created", "labelled", "commented", "done"])

    def test_limit_bounds_the_run_and_the_summary_names_what_is_left(self):
        _, out, _ = self._apply(self._reopen_recorder(), extra_args=("--limit", "2"))
        done = [
            key
            for key, entry in self._mapping()["entries"].items()
            if entry["phase"] == "done"
        ]
        self.assertEqual(len(done), 2)
        self.assertIn("NOT yet done in the mapping", out)

    def test_only_restricts_the_run_to_the_named_keys(self):
        recorder, _, _ = self._apply(
            self._reopen_recorder(), extra_args=("--only", "PRE-7", "--only", "PRE-10")
        )
        done = sorted(
            key
            for key, entry in self._mapping()["entries"].items()
            if entry["phase"] == "done"
        )
        self.assertEqual(done, ["PRE-10", "PRE-7"])
        self.assertEqual(len(recorder.gh_calls("issue", "create")), 1)

    def test_only_with_a_key_the_plan_lacks_refuses_before_any_write(self):
        recorder, _, err = self._apply(
            self._reopen_recorder(), extra_args=("--only", "PRE-404"), expect=2
        )
        self.assertIn("--only names a key the plan does not carry: PRE-404", err)
        self.assertEqual(recorder.gh_calls("issue", "create"), [])
        self.assertEqual(
            [a for a in recorder.gh_calls("api") if "--slurp" not in a], []
        )

    # -- labels ------------------------------------------------------------
    def test_carried_labels_are_written_before_the_state_write(self):
        recorder, _, _ = self._apply(self._reopen_recorder())
        add = recorder.order(lambda kind, args: kind == "gh" and "--add-label" in args)
        state = recorder.order(lambda kind, args: kind == "state")
        self.assertTrue(add, "PRE-6 carries papercut and bug")
        self.assertLess(min(add), max(state))
        # The pair for PRE-6 specifically: its --add-label immediately precedes
        # its own state write, because the helper's read has to see them.
        for index in add:
            following = [i for i in state if i > index]
            self.assertTrue(following)
            self.assertEqual(
                recorder.calls[index][1][2],
                recorder.calls[following[0]][1][
                    recorder.calls[following[0]][1].index("--issue") + 1
                ],
            )

    def test_the_managed_set_goes_through_the_helper_and_never_through_gh(self):
        recorder, _, _ = self._apply(self._reopen_recorder())
        for args in recorder.gh_calls("issue", "create") + recorder.gh_calls(
            "issue", "edit"
        ):
            self.assertNotIn("--label", args)
        for args in recorder.gh_calls("issue", "edit"):
            for flag in args:
                self.assertFalse(flag.startswith("status:"))
        self.assertTrue(recorder.state_calls())
        for args in recorder.state_calls():
            self.assertIn("--apply", args)

    def test_a_carried_label_the_board_lacks_refuses_before_any_write(self):
        recorder = Recorder(labels=["papercut"], issues=closed_original(288, "PRE-7"))
        recorder, _, err = self._apply(recorder, expect=2)
        self.assertIn("has no label: bug", err)
        self.assertEqual(recorder.gh_calls("issue", "create"), [])
        self.assertFalse(os.path.exists(self.mapping))

    # -- reopen ------------------------------------------------------------
    def test_the_reopen_path_checks_state_and_key_then_reopens_via_the_helper(self):
        recorder, _, _ = self._apply(self._reopen_recorder())
        views = [
            args for args in recorder.gh_calls("issue", "view") if args[2] == "288"
        ]
        self.assertTrue(views, "the reopen target is read before it is edited")
        self.assertIn("number,state,body,comments", views[0])
        edit = [args for args in recorder.gh_calls("issue", "edit") if args[2] == "288"]
        self.assertTrue(edit)
        self.assertLess(
            recorder.calls.index(("gh", views[0])),
            recorder.calls.index(("gh", edit[0])),
        )
        reopen = [args for args in recorder.state_calls() if "288" in args]
        self.assertTrue(reopen)
        self.assertIn("--reopen", reopen[0])

    def test_a_reopen_target_that_is_already_open_is_refused(self):
        issues = closed_original(288, "PRE-7")
        issues[288]["state"] = "OPEN"
        recorder, _, err = self._apply(self._reopen_recorder(issues=issues), expect=2)
        self.assertIn("refusing to reopen", err)
        self.assertIn("Two live homes", err)
        self.assertEqual(
            [args for args in recorder.gh_calls("issue", "edit") if args[2] == "288"],
            [],
        )

    def test_a_reopen_target_that_no_longer_names_the_key_is_refused(self):
        issues = closed_original(288, "PRE-7")
        issues[288]["body"] = "Some unrelated issue."
        _, _, err = self._apply(self._reopen_recorder(issues=issues), expect=2)
        self.assertIn("name PRE-7", err)

    def test_the_refusal_names_the_key_the_rerun_resumes_at(self):
        issues = closed_original(288, "PRE-7")
        issues[288]["state"] = "OPEN"
        _, _, err = self._apply(self._reopen_recorder(issues=issues), expect=2)
        self.assertIn("Stopped at PRE-7", err)
        self.assertIn("resume from PRE-7", err)

    # -- recovery ----------------------------------------------------------
    def test_a_create_whose_response_was_lost_is_adopted_not_created_again(self):
        key = "PRE-1"
        body = next(e for e in self.plan["entries"] if e["key"] == key)["body"]
        recorder = self._reopen_recorder(search=[{"number": 777, "body": body}])
        recorder, out, _ = self._apply(recorder, extra_args=("--limit", "1"))
        self.assertIn(f"adopting already-landed {REPO}#777", out)
        self.assertEqual(recorder.gh_calls("issue", "create"), [])
        entry = self._mapping()["entries"][key]
        self.assertEqual((entry["number"], entry["resolution"]), (777, "adopted"))

    def test_one_linear_key_on_two_issues_refuses_rather_than_picking(self):
        body = self.plan["entries"][0]["body"]
        recorder = self._reopen_recorder(
            search=[{"number": 777, "body": body}, {"number": 778, "body": body}]
        )
        _, _, err = self._apply(recorder, expect=2)
        self.assertIn("two GitHub homes", err)
        self.assertIn("#777, #778", err)

    def test_a_stranger_carrying_another_keys_footer_is_not_adopted(self):
        recorder = self._reopen_recorder(
            search=[{"number": 777, "body": "Migrated from Linear ZZZ-1 (…)."}]
        )
        recorder, out, _ = self._apply(recorder, extra_args=("--limit", "1"))
        self.assertNotIn("adopting", out)
        self.assertEqual(len(recorder.gh_calls("issue", "create")), 1)

    # -- comments ----------------------------------------------------------
    def test_the_consolidated_comment_is_one_call_carrying_the_marker(self):
        recorder, _, _ = self._apply(self._reopen_recorder())
        posts = recorder.gh_calls("issue", "comment")
        self.assertEqual(len(posts), 1, "only PRE-7 has comments in the fixture")
        body = next(text for kind, text in recorder.bodies if kind == "issue comment")
        self.assertTrue(body.startswith("<!-- linear-import: comments PRE-7 -->"))
        self.assertIn("Returned from Linear PRE-7.", body)
        self.assertIn("**Dan Egan, 2026-03-01:**", body)
        self.assertIn("first comment", body)

    def test_a_comment_whose_marker_is_already_present_is_not_posted_again(self):
        issues = closed_original(
            288, "PRE-7", comments=("<!-- linear-import: comments PRE-7 -->\nold",)
        )
        recorder, _, _ = self._apply(self._reopen_recorder(issues=issues))
        self.assertEqual(recorder.gh_calls("issue", "comment"), [])
        self.assertIs(self._mapping()["entries"]["PRE-7"]["commented"], False)

    def test_an_issue_with_no_comments_reads_nothing_and_posts_nothing(self):
        recorder, _, _ = self._apply(
            self._reopen_recorder(), extra_args=("--limit", "1")
        )
        self.assertEqual(recorder.gh_calls("issue", "comment"), [])
        self.assertEqual(recorder.gh_calls("issue", "view"), [])

    # -- milestones --------------------------------------------------------
    def test_a_milestone_with_the_planned_title_is_reused_not_recreated(self):
        title = self.plan["milestones"][0]
        recorder = self._reopen_recorder(milestones=[{"title": title, "number": 7}])
        recorder, _, _ = self._apply(recorder)
        creates = [args for args in recorder.gh_calls("api") if "--slurp" not in args]
        self.assertEqual(creates, [])
        self.assertEqual(self._mapping()["milestones"][title], 7)

    def test_a_missing_milestone_is_created_once_and_recorded_by_number(self):
        recorder, out, _ = self._apply(self._reopen_recorder())
        creates = [args for args in recorder.gh_calls("api") if "--slurp" not in args]
        self.assertEqual(len(creates), len(self.plan["milestones"]))
        self.assertIn("milestones created:", out)
        self.assertEqual(
            sorted(self._mapping()["milestones"]), sorted(self.plan["milestones"])
        )

    def test_the_milestone_reaches_gh_as_a_title_not_a_number(self):
        """gh 2.98.0's `--milestone` is documented "by name" and looks the title
        up: `--milestone 8` exits 1 with `could not add to milestone '8'`. This
        cost the first live run its first create, and push-plan.md §5.3 still
        says to pass the number — so the shape is asserted, not assumed."""
        title = self.plan["milestones"][0]
        recorder, _, _ = self._apply(self._reopen_recorder())
        passed = [
            args[args.index("--milestone") + 1]
            for args in recorder.gh_calls("issue", "create")
            if "--milestone" in args
        ]
        self.assertEqual(passed, [title])

    def test_two_milestones_sharing_a_title_refuse_before_any_write(self):
        title = self.plan["milestones"][0]
        recorder = self._reopen_recorder(
            milestones=[{"title": title, "number": 7}, {"title": title, "number": 8}]
        )
        recorder, _, err = self._apply(recorder, expect=2)
        self.assertIn("two milestones share a title", err)
        self.assertEqual(recorder.gh_calls("issue", "create"), [])

    # -- throttle and rate limits -----------------------------------------
    def test_the_throttle_sleeps_between_issues_and_not_before_the_first(self):
        self._apply(
            self._reopen_recorder(), extra_args=("--limit", "3", "--sleep", "5")
        )
        self.assertEqual(self.napped, [5.0, 5.0])

    def test_a_secondary_rate_limit_waits_its_retry_after_then_succeeds(self):
        recorder = self._reopen_recorder()
        seen: list = []

        def once(args):
            if args[:2] == ["issue", "create"] and not seen:
                seen.append(args)
                return (
                    1,
                    "",
                    "HTTP 403: You have exceeded a secondary rate limit. retry-after: 7",
                )
            return None

        recorder.fail_on = once
        recorder, _, _ = self._apply(
            recorder, extra_args=("--limit", "1", "--sleep", "0")
        )
        self.assertIn(7, self.napped)
        self.assertEqual(len(recorder.gh_calls("issue", "create")), 2)
        self.assertEqual(self._mapping()["entries"]["PRE-1"]["phase"], "done")

    def test_a_permissions_403_is_not_retried(self):
        recorder = self._reopen_recorder()
        recorder.fail_on = lambda args: (
            (1, "", "HTTP 403: Resource not accessible by integration")
            if args[:2] == ["issue", "create"]
            else None
        )
        recorder, _, err = self._apply(recorder, extra_args=("--limit", "1"), expect=2)
        self.assertEqual(len(recorder.gh_calls("issue", "create")), 1)
        self.assertIn("not accessible", err)
        self.assertEqual(self.napped, [])

    def test_a_failed_label_write_stops_at_that_key_with_the_create_recorded(self):
        recorder = self._reopen_recorder()
        recorder.state_result = (2, "", "refusing to write: est:7 is not in labels.yml")
        _, _, err = self._apply(recorder, extra_args=("--limit", "1"), expect=2)
        self.assertIn("est:7", err)
        self.assertEqual(self._mapping()["entries"]["PRE-1"]["phase"], "created")

    # -- the mapping's own guards -----------------------------------------
    def test_a_mapping_from_another_board_refuses(self):
        with open(self.mapping, "w", encoding="utf-8") as fh:
            json.dump({"repo": "someone/else", "entries": {}}, fh)
        recorder, _, err = self._apply(self._reopen_recorder(), expect=2)
        self.assertIn("a mapping belongs to one board", err)
        self.assertEqual(recorder.gh_calls("issue", "create"), [])

    def test_a_plan_for_another_board_refuses(self):
        _, _, err = self._apply(
            self._reopen_recorder(), extra_args=("--repo", "someone/else"), expect=2
        )
        self.assertIn("refusing to land a plan on another board", err)

    def test_apply_needs_a_plan_file_and_a_mapping(self):
        for argv in (
            ["--apply", "--mapping", self.mapping],
            ["--apply", "--plan-file", self.plan_path],
        ):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    linear_import.main(argv)

    def test_apply_needs_no_export(self):
        """The export is 3.3 MB of provenance the plan already answers for."""
        self._apply(self._reopen_recorder(), extra_args=("--limit", "1"))
        self.assertEqual(self._mapping()["entries"]["PRE-1"]["phase"], "done")

    def test_a_full_run_lands_every_planned_key(self):
        _, out, _ = self._apply(self._reopen_recorder())
        planned = {entry["key"] for entry in self.plan["entries"]}
        landed = {
            key
            for key, entry in self._mapping()["entries"].items()
            if entry["phase"] == "done"
        }
        self.assertEqual(landed, planned)
        self.assertIn("every planned key is in the mapping at phase done", out)

    def test_the_assignee_is_written_on_the_create_that_plans_one(self):
        recorder, _, _ = self._apply(self._reopen_recorder())
        with_assignee = [
            args
            for args in recorder.gh_calls("issue", "create")
            if "--assignee" in args
        ]
        self.assertEqual(len(with_assignee), 1)
        self.assertEqual(
            with_assignee[0][with_assignee[0].index("--assignee") + 1], "@me"
        )

    def test_a_rerun_after_a_complete_run_writes_nothing(self):
        self._apply(self._reopen_recorder())
        recorder, out, _ = self._apply(self._reopen_recorder())
        self.assertEqual(recorder.gh_calls("issue", "create"), [])
        self.assertEqual(recorder.state_calls(), [])
        self.assertIn("worked this run=0", out)


class LinkRecorder(Recorder):
    """The apply Recorder plus a stubbed gh-issue-deps.py, on the same call log.

    One log again, because `--link`'s passes have a required order (edges, then
    sub-issues, then bodies) and the reason is about which failure is cheapest to
    see first — an ordering two recorders could not assert.
    """

    def __init__(self, sub_issues=None, deps_result=None, **kwargs):
        super().__init__(**kwargs)
        # parent number -> the child numbers already attached
        self.sub_issues = {int(k): list(v) for k, v in (sub_issues or {}).items()}
        self.deps_result = deps_result
        self.post_fail = None

    def install(self, case):
        super().install(case)
        case.addCleanup(
            setattr, linear_import, "run_deps_helper", linear_import.run_deps_helper
        )
        linear_import.run_deps_helper = self.deps
        return self

    def deps(self, args):
        args = list(args)
        self.calls.append(("deps", args))
        if self.deps_result is not None:
            return self.deps_result
        edges = [args[i + 1].split(":") for i, a in enumerate(args) if a == "--edge"]
        return (
            0,
            json.dumps(
                {
                    "repo": REPO,
                    "applied": "--apply" in args,
                    "created": [
                        {"blocked": int(b), "blocker": int(k)} for b, k in edges
                    ],
                    "existing": [],
                    "skipped": [],
                    "refused": [],
                }
            ),
            "",
        )

    def _respond(self, args):
        # The sub-issue list and the sub-issue POST are the two shapes the apply
        # Recorder never saw.
        if args[:1] == ["api"] and args[-1].endswith("/sub_issues"):
            parent = int(args[-1].split("/")[-2])
            return json.dumps(
                [[{"number": n} for n in self.sub_issues.get(parent, [])]]
            )
        if args[:1] == ["api"] and "--method" in args and "/sub_issues" in args[-3]:
            return ""
        return super()._respond(args)

    def gh(self, args):
        args = list(args)
        if "--method" in args and args and any("/sub_issues" in a for a in args):
            self.calls.append(("gh", args))
            parent = int([a for a in args if "/sub_issues" in a][0].split("/")[-2])
            child_id = int(
                [a for a in args if a.startswith("sub_issue_id=")][0].split("=")[1]
            )
            child = self.child_for(child_id)
            if self.post_fail:
                forced = self.post_fail(parent, child)
                if forced:
                    return forced
            self.sub_issues.setdefault(parent, []).append(child)
            return (0, json.dumps({"number": parent}), "")
        return super().gh(args)

    def child_for(self, database_id):
        """The number whose synthetic database id this is. See _respond's `.id`."""
        return database_id - 1_000_000

    def database_id(self, number):
        return number + 1_000_000


def _id_response(recorder, args):
    """`--jq '{id: .id}'` on an issue read — a synthetic but stable database id."""
    number = int(args[1].rstrip("/").split("/")[-1])
    return json.dumps({"id": recorder.database_id(number)})


class LinkTests(PlanFixture):
    """--link, over the same generated plan and a mapping --apply would produce.

    Uses PlanFixture so the plan is built by `--plan` for the reason the apply
    suite gives: three files now have to agree about field names, and a
    hand-written fixture keeps passing after a rename while the linking half
    silently writes nothing.
    """

    def setUp(self):
        super().setUp()
        # A completed import, so --link's own precondition is satisfied.
        self.entries = {e["key"]: e for e in self.plan["entries"]}
        self._seed_done()

    def _seed_done(self, **overrides):
        entries = {}
        for index, entry in enumerate(self.plan["entries"]):
            key = entry["key"]
            entries[key] = {
                "key": key,
                "number": entry["number"] or (900 + index),
                "action": entry["action"],
                "resolution": "reopened" if entry["action"] == "reopen" else "created",
                "phase": "done",
            }
        entries.update(overrides)
        with open(self.mapping, "w", encoding="utf-8") as fh:
            json.dump({"repo": REPO, "milestones": {}, "entries": entries}, fh)
        self.numbers = {k: v["number"] for k, v in entries.items()}

    def _link(self, recorder=None, extra_args=(), expect=0):
        recorder = (recorder or LinkRecorder()).install(self)
        # The issue-read `.id` shape --link needs for the sub-issue POST body.
        original = recorder._respond

        def respond(args):
            if args[:1] == ["api"] and "--jq" in args and args[-1] == "{id: .id}":
                return _id_response(recorder, args)
            return original(args)

        recorder._respond = respond
        argv = [
            "--link",
            "--plan-file",
            self.plan_path,
            "--mapping",
            self.mapping,
            *extra_args,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = linear_import.main(argv)
        self.assertEqual(code, expect, err.getvalue() or out.getvalue())
        return recorder, out.getvalue(), err.getvalue()

    # -- the join ----------------------------------------------------------
    def test_the_join_takes_relationships_from_the_plan_and_numbers_from_the_mapping(
        self,
    ):
        recorder, _, _ = self._link(extra_args=("--apply",))
        deps = [args for kind, args in recorder.calls if kind == "deps"]
        self.assertEqual(len(deps), 1, "one invocation for the whole batch")
        edges = {
            tuple(int(x) for x in deps[0][i + 1].split(":"))
            for i, a in enumerate(deps[0])
            if a == "--edge"
        }
        # PRE-3 is blocked_by PRE-4 in the plan; both numbers come from the mapping.
        self.assertIn((self.numbers["PRE-3"], self.numbers["PRE-4"]), edges)
        self.assertEqual(len(edges), 1, "the fixture plan has exactly one edge")

    def test_the_sub_issue_link_uses_the_parent_from_the_plan(self):
        recorder, _, _ = self._link(extra_args=("--apply",))
        posts = [
            args
            for kind, args in recorder.calls
            if kind == "gh"
            and any("/sub_issues" in a for a in args)
            and "--method" in args
        ]
        self.assertEqual(len(posts), 1)
        # PRE-3's parent is PRE-4, so PRE-4 is the parent endpoint.
        self.assertIn(
            f"repos/{REPO}/issues/{self.numbers['PRE-4']}/sub_issues", posts[0]
        )
        self.assertIn(
            f"sub_issue_id={recorder.database_id(self.numbers['PRE-3'])}", posts[0]
        )

    def test_an_incomplete_import_refuses_before_any_write(self):
        self._seed_done(
            **{"PRE-3": {"key": "PRE-3", "number": 901, "phase": "labelled"}}
        )
        recorder, _, err = self._link(extra_args=("--apply",), expect=2)
        self.assertIn("the import is not complete", err)
        self.assertIn("PRE-3 (phase labelled)", err)
        self.assertEqual([k for k, _ in recorder.calls if k == "deps"], [])

    def test_a_key_missing_from_the_mapping_refuses(self):
        entries = {
            e["key"]: {"key": e["key"], "number": 900 + i, "phase": "done"}
            for i, e in enumerate(self.plan["entries"])
            if e["key"] != "PRE-8"
        }
        with open(self.mapping, "w", encoding="utf-8") as fh:
            json.dump({"repo": REPO, "milestones": {}, "entries": entries}, fh)
        _, _, err = self._link(extra_args=("--apply",), expect=2)
        self.assertIn("not in the mapping: PRE-8", err)

    # -- dry run -----------------------------------------------------------
    def test_without_apply_nothing_is_written(self):
        recorder, out, _ = self._link()
        posts = [
            args for kind, args in recorder.calls if kind == "gh" and "--method" in args
        ]
        self.assertEqual(posts, [])
        self.assertEqual(recorder.gh_calls("issue", "edit"), [])
        deps = [args for kind, args in recorder.calls if kind == "deps"]
        self.assertNotIn("--apply", deps[0])
        self.assertIn("nothing changed (pass --apply to write)", out)

    def test_the_preview_counts_what_the_real_run_would_do(self):
        """A dry run that skips the existing-links read reports every link as
        new. It claimed 15 against a board already holding one."""
        already = {self.numbers["PRE-4"]: [self.numbers["PRE-3"]]}
        _, preview, _ = self._link(LinkRecorder(sub_issues=dict(already)))
        _, real, _ = self._link(
            LinkRecorder(sub_issues=dict(already)), extra_args=("--apply",)
        )

        def counts(text):
            return [line for line in text.splitlines() if "sub-issues:" in line]

        self.assertEqual(counts(preview), counts(real))
        self.assertIn("created=0 already=1", preview)

    # -- sub-issue idempotence --------------------------------------------
    def test_an_existing_sub_issue_link_is_not_reposted(self):
        recorder = LinkRecorder(
            sub_issues={self.numbers["PRE-4"]: [self.numbers["PRE-3"]]}
        )
        recorder, out, _ = self._link(recorder, extra_args=("--apply",))
        posts = [
            args
            for kind, args in recorder.calls
            if kind == "gh"
            and "--method" in args
            and any("/sub_issues" in a for a in args)
        ]
        self.assertEqual(posts, [])
        self.assertIn("created=0 already=1", out)

    def test_a_duplicate_422_is_reread_and_counted_as_already_linked(self):
        """The parent's list, not the error text, decides what a 422 meant."""
        recorder = LinkRecorder()

        def race(parent, child):
            recorder.sub_issues.setdefault(parent, []).append(child)
            return (1, "", "HTTP 422: Issue may not contain duplicate sub-issues")

        recorder.post_fail = race
        recorder, out, _ = self._link(recorder, extra_args=("--apply",))
        self.assertIn("created=0 already=1", out)
        self.assertIn("sub-issue links refused: 0", out)

    def test_a_422_for_a_child_parented_elsewhere_is_refused_not_swallowed(self):
        recorder = LinkRecorder()
        recorder.post_fail = lambda parent, child: (
            1,
            "",
            "HTTP 422: Issue may not contain duplicate sub-issues and Sub issue "
            "may only have one parent",
        )
        recorder, out, _ = self._link(recorder, extra_args=("--apply",))
        self.assertIn("sub-issue links refused: 1", out)
        self.assertIn("already has a different parent", out)

    def test_a_non_422_sub_issue_failure_stops_the_run(self):
        recorder = LinkRecorder()
        recorder.post_fail = lambda parent, child: (1, "", "HTTP 404: Not Found")
        _, _, err = self._link(recorder, extra_args=("--apply",), expect=2)
        self.assertIn("404", err)

    # -- body rewrite ------------------------------------------------------
    def test_a_body_with_no_migrated_key_is_not_edited(self):
        recorder, out, _ = self._link(extra_args=("--apply",))
        edits = [args for args in recorder.gh_calls("issue", "edit")]
        self.assertEqual(edits, [], "no fixture body carries a migrated md link")
        self.assertIn(f"rewritten=0 unchanged={len(self.plan['entries'])}", out)

    def test_a_migrated_markdown_link_becomes_the_number(self):
        target = self.numbers["PRE-4"]
        body = (
            "See [PRE-4](https://linear.app/prethinkio/issue/PRE-4/slug) first.\n\n"
            "---\n"
            "Migrated from Linear PRE-8 (https://linear.app/prethinkio/issue/PRE-8) "
            "on 2026-09-13.\n"
        )
        recorder = LinkRecorder(
            issues={
                self.numbers["PRE-8"]: {
                    "number": self.numbers["PRE-8"],
                    "state": "OPEN",
                    "body": body,
                    "comments": [],
                }
            }
        )
        recorder, out, _ = self._link(recorder, extra_args=("--apply",))
        written = [text for kind, text in recorder.bodies if kind == "issue edit"]
        self.assertEqual(len(written), 1)
        self.assertIn(f"See #{target} first.", written[0])
        self.assertIn("Migrated from Linear PRE-8 (https://linear.app/", written[0])
        self.assertIn("rewritten=1", out)

    def test_the_provenance_footer_is_never_rewritten(self):
        """The regression guard for the whole rewrite.

        The footer's own key IS in the mapping and the footer DOES match the
        `KEY (url)` pattern #513's task file specifies — so this is what fails if
        anyone implements that pattern. Verified load-bearing by mutation: adding
        it fails three cases here.
        """
        numbers = {"PRE-8": 42, "PRE-4": 7}
        body = (
            "Body.\n\n---\n"
            "Migrated from Linear PRE-8 (https://linear.app/prethinkio/issue/PRE-8) "
            "on 2026-09-13.\n"
        )
        out = linear_import.rewrite_body(body, numbers)
        self.assertEqual(out, body)
        self.assertNotIn("#42", out)

    def test_an_unmapped_key_is_left_alone(self):
        body = "See [PRE-99](https://linear.app/prethinkio/issue/PRE-99/slug).\n"
        out = linear_import.rewrite_body(body, {"PRE-4": 7})
        self.assertEqual(out, body)

    def test_a_bare_key_in_prose_is_left_alone(self):
        body = "Reverted in [PRE-4] fix(x): thing, see PRE-4 for context.\n"
        out = linear_import.rewrite_body(body, {"PRE-4": 7})
        self.assertEqual(out, body)

    def test_a_link_whose_href_names_something_else_is_left_alone(self):
        """The one real case: a project URL under a key-shaped label."""
        body = "See [PRE-4](https://linear.app/prethinkio/project/reconcile/abc).\n"
        out = linear_import.rewrite_body(body, {"PRE-4": 7})
        self.assertEqual(out, body)

    def test_the_related_footer_line_rewrites_only_mapped_keys(self):
        body = (
            "Body.\n\n---\n"
            "Migrated from Linear PRE-1 (https://linear.app/prethinkio/issue/PRE-1) "
            "on 2026-09-13.\n"
            "Related: PRE-4, PRE-99 (relations of type related/similar/duplicate "
            "are not native on GitHub).\n"
        )
        out = linear_import.rewrite_body(body, {"PRE-4": 7, "PRE-1": 42})
        self.assertIn("Related: #7, PRE-99 (relations of type", out)
        self.assertIn("Migrated from Linear PRE-1 (https://linear.app/", out)

    def test_the_rewrite_is_idempotent(self):
        body = (
            "See [PRE-4](https://linear.app/prethinkio/issue/PRE-4/slug).\n\n---\n"
            "Migrated from Linear PRE-1 (https://linear.app/prethinkio/issue/PRE-1) "
            "on 2026-09-13.\n"
            "Related: PRE-4 (relations of type related/similar/duplicate are not "
            "native on GitHub).\n"
        )
        once = linear_import.rewrite_body(body, {"PRE-4": 7, "PRE-1": 42})
        twice = linear_import.rewrite_body(once, {"PRE-4": 7, "PRE-1": 42})
        self.assertEqual(once, twice)
        self.assertNotEqual(once, body)

    def test_the_live_body_is_read_so_a_human_edit_survives(self):
        edited = (
            "A human added this sentence.\n\n---\n"
            "Migrated from Linear PRE-8 (https://linear.app/prethinkio/issue/PRE-8) "
            "on 2026-09-13.\n"
        )
        recorder = LinkRecorder(
            issues={
                self.numbers["PRE-8"]: {
                    "number": self.numbers["PRE-8"],
                    "state": "OPEN",
                    "body": edited,
                    "comments": [],
                }
            }
        )
        recorder, _, _ = self._link(recorder, extra_args=("--apply",))
        self.assertEqual(recorder.gh_calls("issue", "edit"), [])

    # -- ordering and CLI --------------------------------------------------
    def test_edges_are_written_before_sub_issues_and_bodies(self):
        recorder, _, _ = self._link(extra_args=("--apply",))
        deps = recorder.order(lambda kind, args: kind == "deps")
        posts = recorder.order(
            lambda kind, args: (
                kind == "gh"
                and "--method" in args
                and any("/sub_issues" in a for a in args)
            )
        )
        reads = recorder.order(
            lambda kind, args: kind == "gh" and args[:2] == ["issue", "view"]
        )
        self.assertTrue(deps and posts and reads)
        self.assertLess(max(deps), min(posts))
        self.assertLess(max(posts), min(reads))

    def test_a_deps_helper_failure_stops_the_run(self):
        recorder = LinkRecorder(deps_result=(2, "", "refusing to write edges: bad ref"))
        _, _, err = self._link(recorder, extra_args=("--apply",), expect=2)
        self.assertIn("bad ref", err)

    def test_edges_refused_by_github_are_reported_not_raised(self):
        recorder = LinkRecorder(
            deps_result=(
                0,
                json.dumps(
                    {
                        "created": [],
                        "existing": [],
                        "skipped": [],
                        "refused": [{"blocked": 1, "blocker": 2, "reason": "cycle"}],
                    }
                ),
                "",
            )
        )
        recorder, out, _ = self._link(recorder, extra_args=("--apply",))
        self.assertIn("edges refused by GitHub: 1", out)

    def test_link_needs_a_plan_file_and_a_mapping(self):
        for argv in (
            ["--link", "--mapping", self.mapping],
            ["--link", "--plan-file", self.plan_path],
        ):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    linear_import.main(argv)

    def test_two_modes_at_once_is_a_usage_error(self):
        for argv in (
            ["--plan", "--link"],
            ["--plan", "--apply"],
            ["--link", "--show", "PRE-1"],
        ):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    linear_import.main(argv)

    def test_no_mode_at_all_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                linear_import.main(["--repo", REPO])

    def test_link_with_apply_is_not_the_apply_mode(self):
        """The one legal pairing: --apply is --link's write switch there."""
        recorder, out, _ = self._link(extra_args=("--apply",))
        self.assertIn("Linked", out)
        self.assertEqual(recorder.gh_calls("issue", "create"), [])

    def test_a_plan_for_another_board_refuses_to_link(self):
        _, _, err = self._link(
            extra_args=("--apply", "--repo", "someone/else"), expect=2
        )
        self.assertIn("refusing to link a plan on another board", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
