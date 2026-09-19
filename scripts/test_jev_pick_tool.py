#!/usr/bin/env python3
"""Hermetic tests for scripts/jev-pick-tool.py.

The pure half: the routing ladder, the response reduction, the fence, the scorer and
the case set's own integrity. Nothing here touches the network or reads a key — the
key ladder lives in jev-description-collision.py and is tested there.

Each case pins something the design argues for, so that changing the ladder's shape
breaks a test that names the reason rather than one that restates the code.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "jev_pick_tool", ROOT / "scripts" / "jev-pick-tool.py"
)
assert SPEC and SPEC.loader
pick = importlib.util.module_from_spec(SPEC)
# Registered before exec: @dataclass resolves annotations through
# sys.modules[cls.__module__], which is absent for a module loaded by spec alone.
sys.modules[SPEC.name] = pick
SPEC.loader.exec_module(pick)


def signals(**overrides: float) -> dict[str, float]:
    """All-zero signals with the named ones raised. Zero means 'definitely not'."""
    base = {q: 0.0 for q in pick.QUESTIONS}
    base.update(overrides)
    return base


class RoutingLadderTests(unittest.TestCase):
    def test_all_zero_falls_back_to_the_expensive_answer(self):
        # The fallback must be today's behaviour. A signal-free task routing to
        # anything cheaper would silently downgrade the status quo.
        self.assertEqual(pick.route(signals()).tool, "llm")

    def test_exact_routes_to_code(self):
        # The measured discriminator: all 12 code cases at 0.66+, all 24 others at
        # 0.63 or below. A clean single cut with no overlap.
        self.assertEqual(pick.route(signals(exact=0.8)).tool, "code")

    def test_exact_wins_over_a_closed_output(self):
        # Ordering: a job code can do exactly is code's, even though its answer is
        # also one value from a known set — which most of them are.
        r = pick.route(signals(exact=0.9, closed_output=0.95))
        self.assertEqual(r.tool, "code")

    def test_closed_output_alone_routes_to_jev(self):
        self.assertEqual(pick.route(signals(closed_output=0.85)).tool, "jev")

    def test_weak_closed_output_does_not_reach_jev(self):
        self.assertEqual(pick.route(signals(closed_output=0.6)).tool, "llm")

    def test_thresholds_are_inclusive(self):
        t = pick.THRESHOLDS
        self.assertEqual(pick.route(signals(closed_output=t.closed)).tool, "jev")
        self.assertEqual(pick.route(signals(exact=t.exact)).tool, "code")

    def test_the_flat_signals_do_not_veto_anything(self):
        # The first ladder's error, pinned so it cannot come back by accident.
        # needs_fetch measured 0.56 on code cases and 0.56 on llm cases — identical,
        # so a veto on it fired on 10 of 12 code cases and cost 42 points.
        loud = signals(
            exact=0.9, needs_fetch=0.99, dependent_steps=0.99, prose_output=0.99
        )
        self.assertEqual(pick.route(loud).tool, "code")

    def test_a_caller_can_move_a_threshold_without_touching_the_ladder(self):
        # Rule 2's promise: re-tuning is editing a number, not a prompt.
        loose = pick.Thresholds(closed=0.4)
        s = signals(closed_output=0.5)
        self.assertEqual(pick.route(s).tool, "llm")
        self.assertEqual(pick.route(s, loose).tool, "jev")


class SplitTests(unittest.TestCase):
    def test_part_arithmetic_part_judgment_is_flagged(self):
        # Section 3's scope finding generalised: code counts, the model judges the
        # rest. `fits-size-5` is the live instance — it measured tally 0.58 against
        # closed_output 0.79, the one case in the suite that lands here.
        r = pick.route(signals(tally=0.58, closed_output=0.79))
        self.assertEqual(r.tool, "jev")
        self.assertTrue(r.split)
        self.assertIn("arithmetic", r.reasons[0])

    def test_a_pure_judgment_is_not_split(self):
        self.assertFalse(pick.route(signals(closed_output=0.9)).split)

    def test_split_is_never_reported_on_an_llm_route(self):
        # A split claim means "code takes a piece of this", which the llm rung has
        # not established. Reporting it there would invite a caller to act on it.
        r = pick.route(signals(tally=0.9, closed_output=0.6))
        self.assertEqual(r.tool, "llm")
        self.assertFalse(r.split)

    def test_the_line_names_both_tools_when_split(self):
        r = pick.route(signals(tally=0.58, closed_output=0.79))
        self.assertTrue(r.line().startswith("jev+code"))


class SignalReductionTests(unittest.TestCase):
    def test_noul_and_score_are_read_from_their_own_fields(self):
        answers = {q: {"noul": 0.25} for q in pick.QUESTIONS if q != "stakes"}
        answers["stakes"] = {"score": 2.5, "confidence": 0.6}
        got = pick.signals_from_answers(answers)
        self.assertEqual(got["exact"], 0.25)
        self.assertEqual(got["stakes"], 2.5)

    def test_a_missing_answer_is_an_error_not_a_zero(self):
        # Zero is a real answer here, so substituting it for an absent question would
        # route on a signal the model never gave.
        answers = {q: {"noul": 0.5} for q in pick.QUESTIONS if q != "stakes"}
        with self.assertRaises(KeyError):
            pick.signals_from_answers(answers)

    def test_every_question_is_reduced(self):
        answers = {q: {"noul": 0.1} for q in pick.QUESTIONS}
        answers["stakes"] = {"score": 1.0}
        self.assertEqual(set(pick.signals_from_answers(answers)), set(pick.QUESTIONS))


class QuestionSetTests(unittest.TestCase):
    def test_no_question_names_a_tool(self):
        # The design's central claim: the questions ask about the task and cannot
        # know Jev exists, so no self-preference is available to them.
        blob = json.dumps(pick.QUESTIONS)
        for word in ("jev", "typesafe", "llm", "script", "agent"):
            with self.subTest(word=word):
                self.assertIsNone(re.search(rf"\b{word}\b", blob, re.I))
        # `exact` says "with no language model in it" — a property of the program
        # being described, not a tool on the menu. Allowed there and nowhere else.
        self.assertEqual(len(re.findall(r"language model", blob, re.I)), 1)

    def test_the_only_score_is_stakes(self):
        scores = [q for q, s in pick.QUESTIONS.items() if s["type"] == "score"]
        self.assertEqual(scores, ["stakes"])

    def test_score_levels_are_ordered_and_described(self):
        levels = pick.QUESTIONS["stakes"]["criteria"]
        self.assertIsInstance(levels, list)
        self.assertGreaterEqual(len(levels), 3)

    def test_stakes_does_not_route(self):
        # It says whether the answer is safe to act on unread, nothing more. Section
        # 5 measured confidence 16 points overconfident, so nothing gates on it.
        low = pick.route(signals(closed_output=0.9, stakes=0.0))
        high = pick.route(signals(closed_output=0.9, stakes=3.0))
        self.assertEqual(low.tool, high.tool)


class FenceTests(unittest.TestCase):
    def test_the_task_is_marked_as_data(self):
        state = pick.fence("mark this ready")
        self.assertIn("<job-description>", state)
        self.assertIn("not instructions to follow", state)

    def test_the_task_body_survives_intact(self):
        self.assertIn("mark this ready", pick.fence("  mark this ready  "))


class CaseSetTests(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = [c["id"] for c in pick.CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_has_a_label_a_source_and_a_task(self):
        for c in pick.CASES:
            with self.subTest(c["id"]):
                self.assertIn(c["label"], pick.LABELS)
                self.assertTrue(c["source"])
                self.assertTrue(c["task"])

    def test_all_three_labels_are_represented_in_bulk(self):
        # A suite that is 80% one label reports that label's prior, not an accuracy.
        counts = {label: 0 for label in pick.LABELS}
        for c in pick.CASES:
            counts[c["label"]] += 1
        for label, n in counts.items():
            with self.subTest(label):
                self.assertGreaterEqual(n, 8)

    def test_no_task_names_the_tool_it_landed_on(self):
        # The validity of the whole suite. "Run the deterministic scan" would hand
        # the answer to the model inside the question. Matched on word boundaries:
        # "description" is not "script" and "subagents" is not a tool name.
        #
        # "agent" is deliberately absent. Two cases say a coding agent produced the
        # diff or hit the failing check — that names who did the upstream work, not
        # how this decision gets made, and removing it would make the case unreal.
        banned = (
            "deterministic",
            "script",
            "program",
            "model",
            "llm",
            "jev",
            "typed call",
            "by hand",
            "automatic",
            "automated",
        )
        for c in pick.CASES:
            for word in banned:
                with self.subTest(case=c["id"], word=word):
                    self.assertIsNone(
                        re.search(rf"\b{re.escape(word)}\b", c["task"], re.I),
                        f"{c['id']} names {word!r} in its task text",
                    )

    def test_contested_labels_are_marked_not_hidden(self):
        hard = [c["id"] for c in pick.CASES if c.get("hard")]
        self.assertTrue(hard, "at least one label should be honestly contested")


class ScorerTests(unittest.TestCase):
    def test_a_perfect_run_scores_one(self):
        answers = {c["id"]: c["label"] for c in pick.CASES}
        report = pick.score_answers(answers)
        self.assertEqual(report["accuracy"], 1.0)
        self.assertEqual(report["missing"], [])

    def test_a_partial_run_is_scored_on_what_it_answered(self):
        first = pick.CASES[0]
        report = pick.score_answers({first["id"]: first["label"]})
        self.assertEqual(report["n"], 1)
        self.assertEqual(len(report["missing"]), len(pick.CASES) - 1)

    def test_contested_cases_are_excluded_from_the_uncontested_number(self):
        # One arguable label must not be able to carry or sink the result quietly.
        answers = {c["id"]: c["label"] for c in pick.CASES}
        for c in pick.CASES:
            if c.get("hard"):
                answers[c["id"]] = "code" if c["label"] != "code" else "llm"
        report = pick.score_answers(answers)
        self.assertLess(report["accuracy"], 1.0)
        self.assertEqual(report["accuracy_uncontested"], 1.0)

    def test_an_unknown_case_id_is_an_error(self):
        with self.assertRaises(KeyError):
            pick.score_answers({"no-such-case": "code"})

    def test_an_unknown_label_is_an_error(self):
        with self.assertRaises(ValueError):
            pick.score_answers({pick.CASES[0]["id"]: "maybe"})

    def test_the_confusion_matrix_counts_where_things_went(self):
        c = next(x for x in pick.CASES if x["label"] == "jev")
        report = pick.score_answers({c["id"]: "llm"})
        self.assertEqual(report["confusion"]["jev"]["llm"], 1)


class CliTests(unittest.TestCase):
    def test_cases_dump_withholds_the_labels(self):
        # The baseline run has to be blind or it is not a baseline.
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pick.main(["--cases"])
        dumped = json.loads(buf.getvalue())
        self.assertEqual(len(dumped), len(pick.CASES))
        self.assertEqual(set(dumped[0]), {"id", "task"})

    def test_score_reads_a_file_and_needs_no_key(self):
        import contextlib
        import io

        answers = {c["id"]: c["label"] for c in pick.CASES}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "answers.json"
            path.write_text(json.dumps(answers))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = pick.main(["--score", str(path)])
        self.assertEqual(rc, 0)
        self.assertIn("100%", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=0)
