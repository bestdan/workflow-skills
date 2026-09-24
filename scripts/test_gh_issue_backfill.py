#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/gh-issue-backfill.py.

Stubs every `gh` seam the backfill path reaches — its own run_gh, the one it
borrows from gh-issue-state.py for the read and the PATCH, and the one it
borrows from gh-issue-ready.py for the dependency graph — so nothing shells out
or touches the network.

What is pinned here is what the by-hand path could not be: the `prio:` encoding
in BOTH directions, so a Linear integer leaking in fails the gate rather than
inverting a board; that a backfill leaves the `status:`/`auto:` rungs
byte-identical; that a held or already-complete issue receives no write; the
`urgent` guard; and that a batch emits one provenance comment rather than one
per issue.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "gh-issue-backfill.py"
LABELS_FILE = ROOT / "commands" / "handlers" / "assets" / "labels.yml"

_spec = importlib.util.spec_from_file_location("gh_issue_backfill", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

gh_issue_state = backfill.gh_issue_state
gh_issue_ready = backfill.gh_issue_ready


class FakeRepo:
    """An open-issue board plus its native dependency graph.

    `issues` maps number -> {"title", "body", "labels", "state"}. `blocked_by`
    maps number -> [(blocker, state)]. Every `gh` call the backfill path makes
    lands here, and `patches` / `comments` record the writes so a test can
    assert that a path wrote nothing at all.

    `fail_on` is an optional predicate over the `gh` argv; returning a truthy
    stderr string makes that one call fail, which is how the transient-failure
    tests inject a flaky `gh` without reassigning a method.
    """

    def __init__(self, issues, blocked_by=None, fail_on=None):
        self.issues = {n: dict(v) for n, v in issues.items()}
        self.blocked_by = dict(blocked_by or {})
        self.fail_on = fail_on
        self.patches = []
        self.comments = []
        self.calls = []

    def _issue(self, number):
        return self.issues[number]

    def run_gh(self, args, stdin=None):
        self.calls.append(args)
        if self.fail_on:
            stderr = self.fail_on(args)
            if stderr:
                return 1, "", stderr
        if args[:2] == ["issue", "list"]:
            payload = [
                {
                    "number": n,
                    "title": v.get("title", ""),
                    "body": v.get("body", ""),
                    "labels": [{"name": name} for name in v.get("labels", [])],
                }
                for n, v in self.issues.items()
                if v.get("state", "open") == "open"
            ]
            return 0, json.dumps(payload), ""
        if args[:2] == ["issue", "view"]:
            issue = self._issue(int(args[2]))
            view = {
                "labels": [{"name": name} for name in issue.get("labels", [])],
                "state": issue.get("state", "open").upper(),
            }
            return 0, json.dumps(view), ""
        if args[:2] == ["issue", "comment"]:
            self.comments.append((int(args[2]), args[args.index("--body") + 1]))
            return 0, "", ""
        if args[0] == "api" and "--method" in args:
            number = int(args[args.index("--method") + 2].rsplit("/", 1)[1])
            self.patches.append((number, json.loads(stdin)))
            return 0, "{}", ""
        if args[0] == "api":
            path = next(a for a in args if "/issues/" in a)
            number = int(path.split("/issues/")[1].split("/")[0])
            entries = [
                {"number": n, "state": s} for n, s in self.blocked_by.get(number, [])
            ]
            return 0, json.dumps([entries]), ""
        raise AssertionError(f"unstubbed gh call: {args}")


@contextlib.contextmanager
def wired(repo):
    """Point all three run_gh seams at one fake repo."""
    originals = (backfill.run_gh, gh_issue_state.run_gh, gh_issue_ready.run_gh)
    backfill.run_gh = lambda args: repo.run_gh(args)
    gh_issue_state.run_gh = lambda args, stdin=None: repo.run_gh(args, stdin)
    gh_issue_ready.run_gh = lambda args: repo.run_gh(args)
    try:
        yield
    finally:
        backfill.run_gh, gh_issue_state.run_gh, gh_issue_ready.run_gh = originals


def run_main(argv):
    """Run main() with stdout captured; returns (exit code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = backfill.main(argv)
    return code, buf.getvalue()


def plan_file(entries):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(entries, handle)
    handle.close()
    return handle.name


VOCAB_GROUPS, VOCAB_COLORS = backfill.load_vocabulary(LABELS_FILE)


class EncodingTests(unittest.TestCase):
    """The one mistake the by-hand path invited: the GitHub/Linear inversion."""

    def test_priority_encodes_highest_first(self):
        table = backfill.priority_encoding(VOCAB_GROUPS)
        self.assertEqual(
            table,
            {
                "urgent": "prio:0",
                "high": "prio:1",
                "medium": "prio:2",
                "low": "prio:3",
            },
        )

    def test_priority_decodes_back_to_the_same_word(self):
        """The reverse direction. An encoding that is not injective inverts."""
        table = backfill.priority_encoding(VOCAB_GROUPS)
        reverse = {label: word for word, label in table.items()}
        self.assertEqual(len(reverse), len(table))
        for word, label in table.items():
            self.assertEqual(reverse[label], word)

    def test_linear_integer_is_refused_not_reinterpreted(self):
        """Linear's `medium` is 3; GitHub's `prio:3` is `low`.

        Passing the integer through would demote every medium card. It has to
        fail, not encode.
        """
        with self.assertRaises(backfill.BackfillError):
            backfill.encode(VOCAB_GROUPS, priority=3)
        self.assertEqual(
            backfill.encode(VOCAB_GROUPS, priority="medium")[0], ["prio:2"]
        )
        self.assertEqual(backfill.encode(VOCAB_GROUPS, priority="low")[0], ["prio:3"])

    def test_none_writes_no_label(self):
        labels, notes = backfill.encode(VOCAB_GROUPS, priority="none")
        self.assertEqual(labels, [])
        self.assertTrue(any("no prio: label" in note for note in notes))

    def test_urgent_is_refused(self):
        with self.assertRaises(backfill.BackfillError) as caught:
            backfill.encode(VOCAB_GROUPS, priority="urgent")
        self.assertIn("urgent", str(caught.exception))

    def test_human_set_urgent_encodes_as_the_top_rung(self):
        """A card a human drafted may say `urgent`; the guard is for guesses."""
        labels, _notes = backfill.encode(
            VOCAB_GROUPS, priority="urgent", human_set=True
        )
        self.assertEqual(labels, ["prio:0"])

    def test_human_set_lifts_only_the_urgent_refusal(self):
        with self.assertRaises(backfill.BackfillError):
            backfill.encode(VOCAB_GROUPS, priority=3, human_set=True)
        with self.assertRaises(backfill.BackfillError):
            backfill.encode(VOCAB_GROUPS, estimate=7, human_set=True)

    def test_estimate_on_and_off_the_ladder(self):
        self.assertEqual(backfill.encode(VOCAB_GROUPS, estimate=3)[0], ["est:3"])
        self.assertEqual(backfill.encode(VOCAB_GROUPS, estimate=13)[0], ["est:13"])
        # Over the ladder's top: unset, never a bogus top rung.
        labels, notes = backfill.encode(VOCAB_GROUPS, estimate=21)
        self.assertEqual(labels, [])
        self.assertTrue(any("over ladder" in note for note in notes))
        # Merely off the ladder: refused, because the write would fail anyway.
        with self.assertRaises(backfill.BackfillError):
            backfill.encode(VOCAB_GROUPS, estimate=7)

    def test_vocabulary_drift_fails_loudly(self):
        with self.assertRaises(backfill.VocabularyError):
            backfill.priority_encoding({"prio": ["0", "1"]})


class ScanTests(unittest.TestCase):
    def test_scan_partitions_by_missing_field_and_hold(self):
        repo = FakeRepo(
            {
                1: {"title": "both", "labels": ["status:2_ready", "prio:1", "est:3"]},
                2: {"title": "no est", "labels": ["status:2_ready", "prio:1"]},
                3: {"title": "neither", "labels": ["status:1_needs_refinement"]},
                4: {"title": "held label", "labels": ["status:2_ready", "blocked"]},
                5: {"title": "held dep", "labels": ["status:2_ready"]},
            },
            blocked_by={5: [(9, "open")], 3: [(8, "closed")]},
        )
        with wired(repo):
            result = backfill.scan("o/r", LABELS_FILE, 500)
        self.assertEqual(result["complete"], [1])
        self.assertEqual([c["number"] for c in result["candidates"]], [2, 3])
        self.assertEqual(
            [c["missing"] for c in result["candidates"]], [["est"], ["prio", "est"]]
        )
        self.assertEqual([h["number"] for h in result["held"]], [4, 5])
        self.assertEqual(result["held"][0]["reason"], "blocked label")
        self.assertEqual(result["held"][1]["reason"], "blocked by #9")
        self.assertEqual(repo.patches, [])
        self.assertFalse(result["truncated"])

    def test_a_result_at_the_cap_is_flagged_truncated(self):
        """A sweep that could not see the whole backlog must not read as one."""
        repo = FakeRepo(
            {n: {"title": "t", "labels": ["status:2_ready"]} for n in (1, 2)}
        )
        with wired(repo):
            result = backfill.scan("o/r", LABELS_FILE, 2)
        self.assertTrue(result["truncated"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            backfill.report_scan(result)
        self.assertIn("cap", buf.getvalue().splitlines()[0])


class ApplyTests(unittest.TestCase):
    def outcome(self, repo, entries, do_apply=True):
        with wired(repo):
            return backfill.apply_plan("o/r", entries, LABELS_FILE, do_apply)

    def test_missing_both_gets_both_and_keeps_its_rungs(self):
        repo = FakeRepo(
            {7: {"labels": ["status:2_ready", "auto:eligible", "follow-up"]}}
        )
        outcome = self.outcome(
            repo, [{"number": 7, "priority": "medium", "estimate": 2}]
        )
        self.assertEqual(len(outcome["backfilled"]), 1)
        self.assertEqual(outcome["backfilled"][0]["added"], ["prio:2", "est:2"])
        number, payload = repo.patches[0]
        self.assertEqual(number, 7)
        written = payload["labels"]
        # The acceptance criterion: the rungs are byte-identical, the unmanaged
        # label survives, and `state` never rides along.
        self.assertIn("status:2_ready", written)
        self.assertIn("auto:eligible", written)
        self.assertIn("follow-up", written)
        self.assertEqual(
            sorted(written),
            sorted(["status:2_ready", "auto:eligible", "prio:2", "est:2", "follow-up"]),
        )
        self.assertNotIn("state", payload)

    def test_an_undefined_managed_label_is_dropped_but_reported(self):
        """The full-set write purges `status:custom`; saying so is the contract."""
        repo = FakeRepo(
            {
                26: {
                    "labels": ["status:2_ready", "auto:eligible", "status:custom"],
                }
            }
        )
        outcome = self.outcome(repo, [{"number": 26, "estimate": 2}])
        result = outcome["backfilled"][0]
        self.assertEqual(result["dropped"], ["status:custom"])
        self.assertNotIn("status:custom", repo.patches[0][1]["labels"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            backfill.report_apply(outcome)
        self.assertIn("status:custom", buf.getvalue())

    def test_missing_only_est_keeps_the_humans_priority(self):
        repo = FakeRepo({8: {"labels": ["status:2_ready", "auto:eligible", "prio:0"]}})
        outcome = self.outcome(
            repo, [{"number": 8, "priority": "medium", "estimate": 5}]
        )
        self.assertEqual(outcome["backfilled"][0]["added"], ["est:5"])
        written = repo.patches[0][1]["labels"]
        self.assertIn("prio:0", written)
        self.assertNotIn("prio:2", written)

    def test_already_complete_is_not_rewritten(self):
        repo = FakeRepo(
            {9: {"labels": ["status:2_ready", "auto:eligible", "prio:1", "est:3"]}}
        )
        outcome = self.outcome(repo, [{"number": 9, "priority": "low", "estimate": 1}])
        self.assertEqual(repo.patches, [])
        self.assertEqual(
            outcome["skipped"][0]["reason"], "already carries prio: and est:"
        )

    def test_held_issue_gets_no_write(self):
        repo = FakeRepo(
            {
                10: {"labels": ["status:2_ready", "auto:eligible", "blocked"]},
                11: {"labels": ["status:2_ready", "auto:eligible"]},
            },
            blocked_by={11: [(12, "open")]},
        )
        outcome = self.outcome(
            repo,
            [
                {"number": 10, "priority": "medium", "estimate": 2},
                {"number": 11, "priority": "medium", "estimate": 2},
            ],
        )
        self.assertEqual(repo.patches, [])
        self.assertEqual(
            [h["reason"] for h in outcome["held"]], ["blocked label", "blocked by #12"]
        )

    def test_closed_blocker_does_not_hold(self):
        repo = FakeRepo(
            {13: {"labels": ["status:2_ready", "auto:eligible"]}},
            blocked_by={13: [(14, "closed")]},
        )
        outcome = self.outcome(
            repo, [{"number": 13, "priority": "medium", "estimate": 1}]
        )
        self.assertEqual(len(outcome["backfilled"]), 1)
        self.assertEqual(len(repo.patches), 1)

    def test_urgent_in_a_plan_is_refused_and_writes_nothing(self):
        repo = FakeRepo({15: {"labels": ["status:2_ready", "auto:eligible"]}})
        outcome = self.outcome(
            repo, [{"number": 15, "priority": "urgent", "estimate": 2}]
        )
        self.assertEqual(repo.patches, [])
        self.assertIn("urgent", outcome["errors"][0]["error"])

    def test_absent_priority_falls_back_to_medium(self):
        repo = FakeRepo({16: {"labels": ["status:3_started", "auto:eligible"]}})
        outcome = self.outcome(repo, [{"number": 16, "estimate": 1}])
        self.assertEqual(outcome["backfilled"][0]["added"], ["prio:2", "est:1"])

    def test_over_ladder_estimate_writes_only_the_priority(self):
        repo = FakeRepo({17: {"labels": ["status:2_ready", "auto:eligible"]}})
        outcome = self.outcome(
            repo, [{"number": 17, "priority": "high", "estimate": 21}]
        )
        result = outcome["backfilled"][0]
        self.assertEqual(result["added"], ["prio:1"])
        self.assertTrue(any("over ladder" in note for note in result["notes"]))

    def test_closed_issue_is_skipped(self):
        repo = FakeRepo({18: {"labels": ["prio:1"], "state": "closed"}})
        outcome = self.outcome(repo, [{"number": 18, "estimate": 2}])
        self.assertEqual(repo.patches, [])
        self.assertEqual(outcome["skipped"][0]["reason"], "closed")

    def test_dry_run_writes_nothing(self):
        repo = FakeRepo({19: {"labels": ["status:2_ready", "auto:eligible"]}})
        outcome = self.outcome(repo, [{"number": 19, "estimate": 2}], do_apply=False)
        self.assertEqual(len(outcome["backfilled"]), 1)
        self.assertEqual(repo.patches, [])

    def test_a_rung_change_fails_closed(self):
        """A backfill that would touch a rung is refused, not written.

        Exercised against a deliberately broken encoder. Which of the two
        checks catches it is not the point — the point is that no PATCH is
        sent, so the contract holds however the bug is shaped.
        """
        repo = FakeRepo({20: {"labels": ["status:2_ready", "auto:eligible"]}})
        original = backfill.encode
        backfill.encode = lambda groups, p=None, e=None: (["status:3_started"], [])
        try:
            outcome = self.outcome(repo, [{"number": 20, "estimate": 2}])
        finally:
            backfill.encode = original
        self.assertEqual(repo.patches, [])
        self.assertEqual(len(outcome["errors"]), 1)
        self.assertEqual(outcome["backfilled"], [])

    def test_an_issue_with_no_rungs_is_skipped_not_written(self):
        """A pre-migration issue is the promote flow's lane, not this one."""
        repo = FakeRepo({25: {"labels": ["follow-up"]}})
        outcome = self.outcome(repo, [{"number": 25, "estimate": 2}])
        self.assertEqual(repo.patches, [])
        self.assertIn("status:/auto: rung", outcome["skipped"][0]["reason"])


class BatchResilienceTests(unittest.TestCase):
    """One entry's failure must not take the batch — or its report — with it."""

    def test_a_gh_failure_midway_does_not_abort_the_batch(self):
        repo = FakeRepo(
            {n: {"labels": ["status:2_ready", "auto:eligible"]} for n in (30, 31, 32)},
            fail_on=lambda args: (
                "gh: authentication token expired"
                if args[:3] == ["issue", "view", "31"]
                else None
            ),
        )
        with wired(repo):
            outcome = backfill.apply_plan(
                "o/r",
                [{"number": n, "estimate": 2} for n in (30, 31, 32)],
                LABELS_FILE,
                True,
            )
        # #31 is recorded as an error; its siblings are still written.
        self.assertEqual([r["number"] for r in outcome["backfilled"]], [30, 32])
        self.assertEqual(outcome["errors"][0]["number"], 31)
        self.assertEqual([n for n, _ in repo.patches], [30, 32])

    def test_a_failed_patch_is_one_entrys_error_not_the_runs(self):
        def patch_33_fails(args):
            if args[0] == "api" and "--method" in args:
                number = int(args[args.index("--method") + 2].rsplit("/", 1)[1])
                if number == 33:
                    return "gh: 503 Service Unavailable"
            return None

        repo = FakeRepo(
            {n: {"labels": ["status:2_ready", "auto:eligible"]} for n in (33, 34)},
            fail_on=patch_33_fails,
        )
        with wired(repo):
            outcome = backfill.apply_plan(
                "o/r",
                [{"number": n, "estimate": 2} for n in (33, 34)],
                LABELS_FILE,
                True,
            )
        self.assertEqual(outcome["errors"][0]["number"], 33)
        self.assertEqual([r["number"] for r in outcome["backfilled"]], [34])


class ProvenanceTests(unittest.TestCase):
    def test_batch_posts_one_comment_not_one_per_issue(self):
        repo = FakeRepo(
            {n: {"labels": ["status:2_ready", "auto:eligible"]} for n in (21, 22, 23)}
        )
        path = plan_file([{"number": n, "estimate": 2} for n in (21, 22, 23)])
        with wired(repo):
            code, _out = run_main(
                [
                    "apply",
                    "--repo",
                    "o/r",
                    "--plan",
                    path,
                    "--apply",
                    "--provenance-issue",
                    "99",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(repo.patches), 3)
        self.assertEqual(len(repo.comments), 1)
        issue, body = repo.comments[0]
        self.assertEqual(issue, 99)
        for n in (21, 22, 23):
            self.assertIn(f"#{n}", body)

    def test_a_failed_comment_still_prints_the_report(self):
        """The comment is what failed, so the report is the only record left."""
        repo = FakeRepo(
            {27: {"labels": ["status:2_ready", "auto:eligible"]}},
            fail_on=lambda args: (
                "gh: could not resolve to an Issue"
                if args[:2] == ["issue", "comment"]
                else None
            ),
        )
        path = plan_file([{"number": 27, "estimate": 2}])
        with wired(repo):
            code, out = run_main(
                [
                    "apply",
                    "--repo",
                    "o/r",
                    "--plan",
                    path,
                    "--apply",
                    "--provenance-issue",
                    "99",
                ]
            )
        self.assertEqual(len(repo.patches), 1)
        self.assertIn("#27", out)
        self.assertEqual(code, 1)

    def test_dry_run_posts_no_comment(self):
        repo = FakeRepo({24: {"labels": ["status:2_ready", "auto:eligible"]}})
        path = plan_file([{"number": 24, "estimate": 2}])
        with wired(repo):
            run_main(
                ["apply", "--repo", "o/r", "--plan", path, "--provenance-issue", "99"]
            )
        self.assertEqual(repo.comments, [])
        self.assertEqual(repo.patches, [])


class CliTests(unittest.TestCase):
    def test_encode_prints_the_label_pair(self):
        code, out = run_main(["encode", "--priority", "medium", "--estimate", "8"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "prio:2,est:8")

    def test_encode_refuses_urgent_with_a_nonzero_exit(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _out = run_main(["encode", "--priority", "urgent"])
        self.assertEqual(code, 2)
        self.assertIn("urgent", buf.getvalue())

    def test_human_set_passes_urgent(self):
        code, out = run_main(["encode", "--human-set", "--priority", "urgent"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "prio:0")

    def test_a_card_with_neither_field_encodes_to_nothing(self):
        code, out = run_main(["encode", "--human-set"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_a_quoted_estimate_is_refused_before_any_write(self):
        """The whole plan fails at load, so nothing is half-written."""
        path = plan_file([{"number": 1, "estimate": "3"}])
        with self.assertRaises(SystemExit) as caught:
            backfill.load_plan(path)
        self.assertIn("must be an integer", str(caught.exception))

    def test_duplicate_plan_entry_is_refused(self):
        path = plan_file([{"number": 1}, {"number": 1}])
        with self.assertRaises(SystemExit):
            backfill.load_plan(path)


class CreateStampTests(unittest.TestCase):
    """gh-issue.md step 4: a new issue's initial stamp carries the card's fields.

    `/push-plan` and `/add-task` both create through that step. It composes the
    `status:`/`auto:` pair with `encode --human-set` output and hands the result
    to `gh-issue-state.py`; this runs that composition end to end. A card's own
    priority and size must survive it, or `/promote-tasks` backfills defaults
    over them (#888).
    """

    PAIR = "status:0_untriaged,auto:human-review-needed"

    def stamp(self, repo, number, encode_args):
        code, out = run_main(["encode", "--human-set", *encode_args])
        self.assertEqual(code, 0)
        labels = f"{self.PAIR},{out.strip()}"
        with wired(repo), contextlib.redirect_stdout(io.StringIO()):
            code = gh_issue_state.main(
                ["--repo", "o/r", "--issue", str(number), "--labels", labels, "--apply"]
            )
        self.assertEqual(code, 0)

    def test_two_card_plan_keeps_what_each_card_says(self):
        # Fresh issues, as `gh issue create` leaves them: configured labels only.
        repo = FakeRepo(
            {
                1: {"labels": ["follow-up"]},
                2: {"labels": ["follow-up"]},
            }
        )
        self.stamp(repo, 1, ["--priority", "high", "--estimate", "2"])
        self.stamp(repo, 2, [])

        written = {number: body["labels"] for number, body in repo.patches}
        self.assertEqual(
            sorted(written[1]),
            sorted(
                [
                    "status:0_untriaged",
                    "auto:human-review-needed",
                    "prio:1",
                    "est:2",
                    "follow-up",
                ]
            ),
        )
        # Neither field on the card: no prio:/est:, so the promoter backfills it.
        self.assertEqual(
            sorted(written[2]),
            sorted(["status:0_untriaged", "auto:human-review-needed", "follow-up"]),
        )

    def test_backfill_scan_sees_the_stamped_card_as_complete(self):
        """The second acceptance bullet: nothing left for /promote-tasks to fill."""
        repo = FakeRepo({1: {"labels": ["follow-up"]}, 2: {"labels": ["follow-up"]}})
        self.stamp(repo, 1, ["--priority", "high", "--estimate", "2"])
        self.stamp(repo, 2, [])
        for number, body in repo.patches:
            repo.issues[number]["labels"] = body["labels"]
        with wired(repo):
            result = backfill.scan("o/r", LABELS_FILE, 500)
        missing = {entry["number"] for entry in result["candidates"]}
        self.assertEqual(missing, {2})


if __name__ == "__main__":
    unittest.main(verbosity=2)
