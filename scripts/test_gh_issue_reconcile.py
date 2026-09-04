#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-reconcile.py.

Stubs the run_gh() seam in BOTH modules the reconciler reaches through — its own
(the reads) and gh-issue-state.py's (the repair PATCH) — so nothing shells out
to `gh` or touches the network. Stubbing only the reconciler's own seam would
leave the write path live, and the "no-op without --apply" tests would pass
while proving nothing.

Covers each of the three rules, that rules 2 and 3 never write at any flag
combination, that rule 1 writes only under --apply, and the two ways a rule
could be silently wrong instead of loudly: an event past the first page, and a
status ladder whose numbering was renamed away.
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-reconcile.py"
LABELS_FILE = ROOT / "commands" / "handlers" / "assets" / "labels.yml"

_spec = importlib.util.spec_from_file_location("gh_issue_reconcile", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
gh_issue_reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gh_issue_reconcile)
gh_issue_state = gh_issue_reconcile.gh_issue_state
gh_label_sync = gh_issue_reconcile.gh_label_sync

REVIEW = "status:4_needs_review"

# The vocabulary a fully provisioned repo carries. Tests that say nothing about
# provisioning get this, so they read exactly as they did before the row-3
# preflight existed.
FULL_VOCABULARY = sorted(
    gh_issue_reconcile.expected_labels(*gh_issue_reconcile.load_vocabulary(LABELS_FILE))
)


class FakeRepo:
    """A repo's open and closed issues, plus each closed issue's event history.

    `open_issues` and `closed_issues` map issue number -> label name list;
    `events` maps issue number -> the label names ever applied to it.
    `page_size` splits an event history into pages, so a test can put the
    `4_needs_review` event beyond page one — the shape `gh api --paginate
    --slurp` returns.
    """

    def __init__(
        self,
        open_issues=None,
        closed_issues=None,
        events=None,
        page_size=100,
        repo_labels=None,
    ):
        self.open_issues = dict(open_issues or {})
        self.closed_issues = dict(closed_issues or {})
        self.events = dict(events or {})
        self.page_size = page_size
        # `None` means a fully provisioned repo — the state every test that
        # predates the provisioning preflight assumed without saying so.
        self.repo_labels = (
            list(FULL_VOCABULARY) if repo_labels is None else list(repo_labels)
        )
        self.calls = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ["label", "list"]:
            return 0, json.dumps([{"name": n} for n in self.repo_labels]), ""
        if args[:2] == ["issue", "list"]:
            state = args[args.index("--state") + 1]
            issues = self.open_issues if state == "open" else self.closed_issues
            payload = [
                {
                    "number": n,
                    "title": f"issue {n}",
                    "labels": [{"name": name} for name in labels],
                    "stateReason": "COMPLETED",
                }
                for n, labels in issues.items()
            ]
            return 0, json.dumps(payload), ""
        if args[0] == "api" and "--paginate" in args:
            path = next(a for a in args if "/issues/" in a)
            issue = int(path.split("/issues/")[1].split("/")[0])
            applied = self.events.get(issue, [])
            entries = [
                {"event": "labeled", "label": {"name": name}} for name in applied
            ]
            pages = [
                entries[i : i + self.page_size]
                for i in range(0, max(len(entries), 1), self.page_size)
            ]
            return 0, json.dumps(pages), ""
        if args[0] == "api" and "--method" in args:
            return 0, "{}", ""
        raise AssertionError(f"unexpected gh call: {args}")

    def event_reads(self):
        """Every `…/events` call rule 3 made — one per closed issue, or none."""
        return [
            args
            for args, _ in self.calls
            if args[0] == "api" and any(a.endswith("/events") for a in args)
        ]

    def writes(self):
        """Every mutating call either module made."""
        return [args for args, _ in self.calls if "--method" in args]

    def patched_labels(self, issue):
        """The label array the PATCH for `issue` carried."""
        for args, stdin in self.calls:
            if "--method" in args and any(a.endswith(f"/issues/{issue}") for a in args):
                return json.loads(stdin)["labels"]
        raise AssertionError(f"no PATCH for #{issue}")


class ReconcileTestCase(unittest.TestCase):
    def setUp(self):
        originals = (
            gh_issue_reconcile.run_gh,
            gh_issue_state.run_gh,
            gh_label_sync.run_gh,
        )
        self.addCleanup(self._restore, originals)

    def _restore(self, originals):
        (
            gh_issue_reconcile.run_gh,
            gh_issue_state.run_gh,
            gh_label_sync.run_gh,
        ) = originals

    def _compute(self, repo, apply=False, scope_labels=(), labels_file=LABELS_FILE):
        gh_issue_reconcile.run_gh = repo.run_gh
        # gh-issue-state.py's PATCH goes through its OWN seam, so the repair
        # path stays live unless this one is stubbed too. Same for
        # gh-label-sync.py's `gh label list`, which the provisioning preflight
        # reaches through.
        gh_issue_state.run_gh = repo.run_gh
        gh_label_sync.run_gh = repo.run_gh
        return gh_issue_reconcile.compute(
            repo="owner/name",
            labels_file=labels_file,
            limit=50,
            scope_labels=scope_labels,
            apply=apply,
        )


class RuleOneTests(ReconcileTestCase):
    def test_keeps_the_highest_numbered_status_label(self):
        repo = FakeRepo(
            open_issues={
                1: ["status:2_ready", "status:4_needs_review", "auto:eligible"]
            }
        )
        result = self._compute(repo, apply=True)

        finding = result["double_status"][0]
        self.assertEqual(finding["keep"], REVIEW)
        self.assertEqual(finding["drop"], ["status:2_ready"])
        self.assertIn(REVIEW, repo.patched_labels(1))
        self.assertNotIn("status:2_ready", repo.patched_labels(1))

    def test_highest_is_the_ladder_number_not_the_label_order(self):
        """The lower rung listed last must still be the one dropped."""
        repo = FakeRepo(
            open_issues={
                1: ["status:3_started", "status:1_needs_refinement", "auto:eligible"]
            }
        )
        finding = self._compute(repo)["double_status"][0]

        self.assertEqual(finding["keep"], "status:3_started")
        self.assertEqual(finding["drop"], ["status:1_needs_refinement"])

    def test_repair_preserves_unmanaged_and_other_managed_labels(self):
        repo = FakeRepo(
            open_issues={
                1: [
                    "status:2_ready",
                    "status:3_started",
                    "auto:eligible",
                    "prio:1",
                    "follow-up",
                ]
            }
        )
        self._compute(repo, apply=True)

        self.assertEqual(
            sorted(repo.patched_labels(1)),
            sorted(["status:3_started", "auto:eligible", "prio:1", "follow-up"]),
        )

    def test_the_repair_names_every_managed_label_it_deletes(self):
        """`prio:urgent` is purged by the full-set write; silence would hide it."""
        repo = FakeRepo(
            open_issues={
                1: [
                    "status:2_ready",
                    "status:3_started",
                    "auto:eligible",
                    "prio:urgent",
                    "follow-up",
                ]
            }
        )
        finding = self._compute(repo, apply=True)["double_status"][0]

        self.assertEqual(finding["dropped"], ["prio:urgent"])
        self.assertNotIn("prio:urgent", repo.patched_labels(1))
        # The unmanaged label still rides through — only the four namespaces
        # are this helper's to purge.
        self.assertIn("follow-up", repo.patched_labels(1))

    def test_an_out_of_vocabulary_status_label_is_not_a_second_rung(self):
        """`status:blocked` has no ladder position, so it cannot be ranked."""
        repo = FakeRepo(
            open_issues={1: ["status:2_ready", "status:blocked", "auto:eligible"]}
        )
        result = self._compute(repo)

        self.assertEqual(result["double_status"], [])

    def test_every_finding_carries_the_same_keys_whether_repaired_or_refused(self):
        """`--json` is an interface; a key present only on the success path is
        one a consumer discovers by breaking on the first refused issue."""
        repo = FakeRepo(
            open_issues={
                1: ["status:2_ready", "status:3_started", "auto:eligible"],
                2: ["status:2_ready", "status:3_started"],  # no auto: rung
            }
        )
        findings = self._compute(repo, apply=True)["double_status"]

        repaired, refused = findings[0], findings[1]
        self.assertIsNone(repaired["refused"])
        self.assertIsNotNone(refused["refused"])
        self.assertEqual(sorted(repaired), sorted(refused))

    def test_refuses_to_repair_when_the_result_would_still_be_illegal(self):
        """Two status rungs AND no `auto:` rung — inventing one is rule 2's ban."""
        repo = FakeRepo(open_issues={1: ["status:2_ready", "status:3_started"]})
        result = self._compute(repo, apply=True)

        finding = result["double_status"][0]
        self.assertIn("auto", finding["refused"])
        self.assertFalse(finding["applied"])
        self.assertEqual(repo.writes(), [])


class RuleTwoTests(ReconcileTestCase):
    def test_flags_a_missing_rung_rather_than_assigning_a_default(self):
        repo = FakeRepo(open_issues={1: ["auto:eligible"], 2: ["status:2_ready"]})
        result = self._compute(repo, apply=True)

        self.assertEqual(
            [(f["number"], f["missing"]) for f in result["missing_rung"]],
            [(1, ["status"]), (2, ["auto"])],
        )
        # --apply is on and the fix is one PATCH away. Rule 2 still writes
        # nothing, because which rung belongs there is a human's call.
        self.assertEqual(repo.writes(), [])

    def test_an_issue_missing_both_rungs_names_both(self):
        repo = FakeRepo(open_issues={1: ["follow-up"]})
        result = self._compute(repo)

        self.assertEqual(result["missing_rung"][0]["missing"], ["status", "auto"])

    def test_an_out_of_vocabulary_label_does_not_count_as_a_rung(self):
        """`status:blocked` alone would otherwise be invisible to every rule."""
        repo = FakeRepo(open_issues={1: ["status:blocked", "auto:eligible"]})
        result = self._compute(repo)

        finding = result["missing_rung"][0]
        self.assertEqual(finding["missing"], ["status"])
        self.assertEqual(finding["unrecognized"], ["status:blocked"])
        # Rule 1 skips it too — it has no ladder position — so rule 2 is the
        # only rule that can see this issue at all.
        self.assertEqual(result["double_status"], [])

    def test_a_vocabulary_rung_alongside_a_bogus_one_is_not_flagged(self):
        """The rung is present; the bogus name is rule 1's and the writer's problem."""
        repo = FakeRepo(
            open_issues={1: ["status:2_ready", "status:blocked", "auto:eligible"]}
        )
        result = self._compute(repo)

        self.assertEqual(result["missing_rung"], [])

    def test_a_healthy_issue_is_not_flagged(self):
        repo = FakeRepo(open_issues={1: ["status:2_ready", "auto:eligible"]})
        result = self._compute(repo)

        self.assertEqual(result["missing_rung"], [])
        self.assertEqual(result["double_status"], [])


class RuleThreeTests(ReconcileTestCase):
    def test_detects_an_issue_closed_without_ever_being_labelled_needs_review(self):
        repo = FakeRepo(
            closed_issues={1: ["prio:1"]},
            events={1: ["status:2_ready", "status:3_started", "auto:eligible"]},
        )
        result = self._compute(repo, apply=True)

        self.assertEqual([f["number"] for f in result["closed_unreviewed"]], [1])
        self.assertEqual(repo.writes(), [])

    def test_an_issue_that_passed_review_is_not_flagged(self):
        repo = FakeRepo(
            closed_issues={1: ["prio:1"]},
            events={1: ["status:3_started", REVIEW]},
        )
        result = self._compute(repo)

        self.assertEqual(result["closed_unreviewed"], [])

    def test_a_review_label_reached_only_on_page_two_is_still_seen(self):
        """A bare read returns one page; the event past it would read as absent."""
        repo = FakeRepo(
            closed_issues={1: ["prio:1"]},
            events={1: ["status:2_ready", "status:3_started", REVIEW]},
            page_size=2,
        )
        result = self._compute(repo)

        self.assertEqual(result["closed_unreviewed"], [])

    def test_state_reason_travels_with_the_finding(self):
        repo = FakeRepo(closed_issues={1: []}, events={1: []})
        finding = self._compute(repo)["closed_unreviewed"][0]

        self.assertEqual(finding["state_reason"], "completed")


class ProvisioningTests(ReconcileTestCase):
    """A row must check that the labels it looks for exist on the repo.

    Measured 2026-09-04 against `bestdan/dotfiles`, which has never provisioned
    `status:4_needs_review`: rule 3 flagged 50 of 50 closed issues, correctly
    scoped and entirely noise.
    """

    def test_rule_three_is_void_when_the_review_rung_is_not_provisioned(self):
        without_review = [n for n in FULL_VOCABULARY if n != REVIEW]
        repo = FakeRepo(
            closed_issues={1: [], 2: [], 3: []},
            events={1: [], 2: [], 3: []},
            repo_labels=without_review,
        )
        result = self._compute(repo)

        self.assertFalse(result["review_label_provisioned"])
        self.assertEqual(result["closed_unreviewed"], [])
        self.assertIn(REVIEW, result["missing_vocabulary"])
        # The premise is void, so the per-issue reads are not just wasted, they
        # are the entire API cost of the row.
        self.assertEqual(repo.event_reads(), [])

    def test_the_void_row_says_so_rather_than_reading_as_a_clean_board(self):
        without_review = [n for n in FULL_VOCABULARY if n != REVIEW]
        repo = FakeRepo(
            closed_issues={1: []}, events={1: []}, repo_labels=without_review
        )
        result = self._compute(repo)

        printed = io.StringIO()
        with redirect_stdout(printed):
            gh_issue_reconcile.report(result)
        output = printed.getvalue()

        self.assertIn("VOID", output)
        self.assertIn(REVIEW, output)
        self.assertIn("gh-label-sync.py", output)

    def test_rule_three_is_unchanged_when_the_review_rung_is_provisioned(self):
        """The same two issues the un-preflighted row would have judged.

        The repo is deliberately missing OTHER vocabulary labels. Rule 3 asks
        about exactly one, so a guard that voided the row on any provisioning
        gap would silence it on almost every real board — dotfiles was short 12
        labels when this defect was found, and only one of them mattered.
        """
        repo = FakeRepo(
            closed_issues={1: ["prio:1"], 2: ["prio:1"]},
            events={1: ["status:3_started"], 2: ["status:3_started", REVIEW]},
            repo_labels=[n for n in FULL_VOCABULARY if not n.startswith("est:")],
        )
        result = self._compute(repo)

        self.assertTrue(result["missing_vocabulary"])

        self.assertTrue(result["review_label_provisioned"])
        self.assertEqual([f["number"] for f in result["closed_unreviewed"]], [1])
        self.assertEqual(len(repo.event_reads()), 2)

    def test_a_partly_provisioned_status_group_still_leaves_rule_two_answerable(self):
        """dotfiles' real shape: some rungs missing, so a bare issue is a hit."""
        partial = [n for n in FULL_VOCABULARY if n not in {REVIEW, "status:3_started"}]
        repo = FakeRepo(open_issues={1: ["follow-up"]}, repo_labels=partial)
        result = self._compute(repo)

        self.assertEqual(result["unprovisioned_groups"], [])
        self.assertEqual(result["missing_rung"][0]["missing"], ["status", "auto"])

    def test_rule_two_is_void_for_a_group_with_no_label_provisioned_at_all(self):
        no_auto = [n for n in FULL_VOCABULARY if not n.startswith("auto:")]
        repo = FakeRepo(
            open_issues={1: ["status:2_ready"], 2: ["follow-up"]},
            repo_labels=no_auto,
        )
        result = self._compute(repo)

        self.assertEqual(result["unprovisioned_groups"], ["auto"])
        # #1 carried a status rung and is missing only the unassignable auto:
        # one, so it drops out entirely; #2 is still flagged for status:.
        self.assertEqual(
            [(f["number"], f["missing"]) for f in result["missing_rung"]],
            [(2, ["status"])],
        )


class DryRunTests(ReconcileTestCase):
    def test_all_three_rules_are_no_ops_without_apply(self):
        repo = FakeRepo(
            open_issues={
                1: ["status:2_ready", "status:4_needs_review", "auto:eligible"],
                2: ["follow-up"],
            },
            closed_issues={3: []},
            events={3: ["status:3_started"]},
        )
        result = self._compute(repo)

        self.assertEqual(len(result["double_status"]), 1)
        self.assertEqual(len(result["missing_rung"]), 1)
        self.assertEqual(len(result["closed_unreviewed"]), 1)
        self.assertEqual(repo.writes(), [])
        self.assertFalse(result["double_status"][0]["applied"])
        self.assertFalse(result["applied"])


class ScopeTests(ReconcileTestCase):
    def test_scope_labels_narrow_both_list_queries(self):
        repo = FakeRepo(open_issues={1: ["status:2_ready", "auto:eligible"]})
        self._compute(repo, scope_labels=["follow-up"])

        lists = [args for args, _ in repo.calls if args[:2] == ["issue", "list"]]
        self.assertEqual(len(lists), 2)
        for args in lists:
            self.assertIn("--label", args)
            self.assertEqual(args[args.index("--label") + 1], "follow-up")


class VocabularyTests(ReconcileTestCase):
    def _labels_file(self, text):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False, encoding="utf-8"
        )
        handle.write(text)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(path.unlink)
        return path

    def test_a_renamed_review_value_fails_loudly_rather_than_flagging_everything(self):
        path = self._labels_file(
            "status: [0_untriaged, 2_ready, 3_started, 9_in_review]\n"
            "auto: [eligible, human-review-needed]\n"
            "prio: [0, 1, 2, 3]\n"
            "est: [1, 2, 3, 5, 8, 13]\n"
            "colors:\n"
            "  status: 1d76db\n"
            "  auto: 0e8a16\n"
            "  prio: d93f0b\n"
            "  est: 5319e2\n"
        )
        repo = FakeRepo(closed_issues={1: []}, events={1: []})

        with self.assertRaises(gh_issue_reconcile.VocabularyError):
            self._compute(repo, labels_file=path)

    def test_a_status_value_without_a_ladder_number_fails_loudly(self):
        with self.assertRaises(gh_issue_reconcile.VocabularyError):
            gh_issue_reconcile.rung_rank("ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
