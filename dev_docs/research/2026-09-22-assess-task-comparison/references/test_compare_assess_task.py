#!/usr/bin/env python3
"""Hermetic checks on compare-assess-task.py, the record's scorer.

    python3 test_compare_assess_task.py

No key, no network, no Jev call. Every fixture is synthetic and built here. Two
tests read committed files: the record's README, to pin each threshold to the text
that fixes it, and `measurement/baseline-prompt.md`, to pin the enums to the contract
the incumbent is given.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "compare_assess_task", HERE / "compare-assess-task.py"
)
ce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ce)

RECORD = (HERE.parent / "README.md").read_text()
PROMPT_MD = (HERE / "measurement" / "baseline-prompt.md").read_text()

DIMS = list(ce.DIMENSIONS)
LOWEST = {d: ce.DIMENSIONS[d][0] for d in DIMS}
PRICE = {"uncached": 3.0, "cache_read": 0.3, "cache_write": 3.75, "output": 15.0}
INC_TOKENS = {"uncached": 9000, "cache_read": 0, "cache_write": 0, "output": 400}
JEV_TOKENS = {"uncached": 800, "cache_read": 0, "cache_write": 0, "output": 20}
CARDS = [f"c{i}" for i in range(20)]
SPLIT = ["standard"] * 10 + ["hard"] * 10  # complexity: measurable, 10 off-mode


# --------------------------------------------------------------------- builders


def block(values: dict) -> str:
    body = "".join(f"  {d}: {v}\n" for d, v in values.items())
    return f"```yaml\ntask_profile:\n{body}  label: standard-pr\n```\n"


def profiles(**columns: list) -> dict[str, dict]:
    """card -> profile: every dimension at its lowest, except the columns given."""
    out = {}
    for i, c in enumerate(CARDS):
        prof = dict(LOWEST)
        for d, col in columns.items():
            prof[d] = col[i]
        out[c] = prof
    return out


def baseline(profs: dict, latency=20.0, model="claude-pinned-20260922", agent=1, **kw):
    return {
        "agent": agent,
        "model": model,
        "run_date": "2026-09-22",
        "pricing_usd_per_mtok": kw.get("pricing", PRICE),
        "cards": {
            c: {
                "raw": block(p),
                "latency_s": latency,
                "tokens": dict(kw.get("tokens", INC_TOKENS)),
                "cache_warm": False,
            }
            for c, p in profs.items()
        },
    }


def run(passes: list[dict], latency=1.0, **kw):
    return {
        "model": "jev-test",
        "run_date": "2026-09-22",
        "pricing_usd_per_mtok": kw.get("pricing", PRICE),
        "passes": [
            {
                "cards": {
                    c: {
                        "latency_s": latency,
                        "tokens": dict(kw.get("tokens", JEV_TOKENS)),
                        "answers": {
                            d: {"value": v, "distribution": {v: 1.0}}
                            for d, v in p.items()
                        },
                    }
                    for c, p in profs.items()
                }
            }
            for profs in passes
        ],
    }


def score(panel_profs, jev_profs, **kw):
    """Three identical agents unless a list is given; three identical passes likewise."""
    agents = panel_profs if isinstance(panel_profs, list) else [panel_profs] * 3
    passes = jev_profs if isinstance(jev_profs, list) else [jev_profs] * 3
    return ce.score(
        run(passes, **kw.get("run_kw", {})),
        [
            baseline(a, agent=i, **kw.get("base_kw", {}))
            for i, a in enumerate(agents, 1)
        ],
        CARDS,
        kw.get("adjudication"),
        kw.get("can_come_off"),
    )


def col(**cards: str) -> dict:
    """A one-dimension answer map over a few named cards."""
    return dict(cards)


def dim(passes, agents, cards, delta=Fraction(0)):
    return ce.score_dimension(passes, agents, cards, ce.DIMENSIONS["complexity"], delta)


# ------------------------------------------------------------------- the record


class Thresholds(unittest.TestCase):
    """Each constant against the literal the record fixes, and that literal against
    the record's own text, so a drift on either side fails here."""

    def test_literals(self):
        self.assertEqual(ce.MIN_JEV_PASSES, 3)
        self.assertEqual(ce.PANEL_AGENTS, 3)
        self.assertEqual(ce.MEASURABLE_MIN_CARDS, 5)
        self.assertEqual(ce.GATE_D_MIN_CARDS, 5)
        self.assertEqual(ce.GATE_D_ONE_SIDE_SHARE, Fraction(80, 100))
        self.assertEqual(ce.R_FLOOR, 2)
        self.assertEqual(ce.R_BAND, 10)
        self.assertEqual(ce.DELTA_MID, 0)
        self.assertEqual(ce.DELTA_TUPLE_MID, 0)
        self.assertEqual(ce.DELTA_TOP, Fraction(5, 100))
        self.assertEqual(ce.DELTA_TUPLE_TOP, Fraction(10, 100))

    def test_the_record_says_so(self):
        text = re.sub(r"\s+", " ", RECORD)
        for phrase in (
            "at least 3 passes, per [#813]",
            "`baseline/agent-{1,2,3}.json`: three incumbent runs",
            "measurable only if at least 5 cards have a panel majority on a value "
            "other than its most frequent one",
            "cover at least 5 distinct cards and at least 80% of the pooled pairs "
            "fall on one side",
            "If `R < 2`, or the typed call's dollars per packet are not lower",
            "| `2–10` | 0 points | 0 points |",
            "| `≥ 10` | 5 points | 10 points |",
            "picks Jev's answer on at least one card",
        ):
            self.assertIn(phrase, text)

    def test_enums_are_the_contract(self):
        # `  complexity: mechanical | standard | hard # ...` in the quoted contract.
        for d, enum in ce.DIMENSIONS.items():
            m = re.search(rf"^  {d}: ([^#\n]+?)\s*#", PROMPT_MD, re.M)
            self.assertIsNotNone(m, d)
            self.assertEqual(tuple(v.strip() for v in m.group(1).split("|")), enum)

    def test_the_six_dimensions(self):
        self.assertEqual(
            DIMS,
            [
                "complexity",
                "creativity",
                "autonomy",
                "speed_sensitivity",
                "cost_sensitivity",
                "verification_criticality",
            ],
        )

    def test_no_network_anywhere(self):
        src = (HERE / "compare-assess-task.py").read_text()
        for mod in ("urllib", "http", "socket", "requests", "subprocess"):
            self.assertNotRegex(src, rf"^\s*(import|from) {mod}", mod)


# ------------------------------------------------------------------- parsing


class Parse(unittest.TestCase):
    def test_a_full_block(self):
        got = ce.parse_block(block(dict(LOWEST, complexity="hard")))
        self.assertEqual(got["complexity"], "hard")
        self.assertEqual(got["autonomy"], "bounded")

    def test_comments_and_quotes(self):
        got = ce.parse_block('task_profile:\n  complexity: "hard" # why\n')
        self.assertEqual(got["complexity"], "hard")

    def test_out_of_enum_is_missing(self):
        got = ce.parse_block("task_profile:\n  complexity: gnarly\n")
        self.assertIsNone(got["complexity"])

    def test_an_unparseable_block_is_missing_everywhere(self):
        for raw in ("complexity: hard", None, "", "I cannot read files."):
            self.assertEqual(set(ce.parse_block(raw).values()), {None})

    def test_a_dimension_named_twice_is_missing(self):
        got = ce.parse_block("task_profile:\n  complexity: hard\n  complexity: hard\n")
        self.assertIsNone(got["complexity"])

    def test_jev_value(self):
        self.assertEqual(ce.jev_value({"value": "hard"}, "complexity"), "hard")
        self.assertIsNone(ce.jev_value({"value": "HARD"}, "complexity"))
        self.assertIsNone(ce.jev_value(None, "complexity"))


# ---------------------------------------------------------------------- terms


class Terms(unittest.TestCase):
    AGENTS = [col(x="standard", y="standard")] * 2 + [col(x="hard", y="standard")]
    PASSES = [col(x="standard", y="standard"), col(x="hard", y="standard")]

    def test_a_panel(self):
        # pairs (1,2): 2 hits, (1,3): 1, (2,3): 1 -> 4 of 6
        self.assertEqual(ce.mean_pairwise(self.AGENTS, ["x", "y"]), Fraction(4, 6))

    def test_a_jev_is_every_pass_against_every_agent(self):
        self.assertEqual(
            ce.mean_cross(self.PASSES, self.AGENTS, ["x", "y"]), Fraction(9, 12)
        )

    def test_s_jev(self):
        self.assertEqual(ce.mean_pairwise(self.PASSES, ["x", "y"]), Fraction(1, 2))

    def test_a_const_is_the_base_rate(self):
        self.assertEqual(ce.a_const(self.AGENTS, ["x", "y"]), Fraction(5, 6))

    def test_a_missing_answer_disagrees_with_everything(self):
        self.assertEqual(ce.agree(None, None), 0)
        self.assertEqual(ce.agree(None, "hard"), 0)
        self.assertEqual(ce.agree("hard", "hard"), 1)

    def test_majority_and_contested(self):
        mk = lambda *vs: [col(x=v) for v in vs]  # noqa: E731
        self.assertEqual(ce.majority(mk("hard", "hard", "standard"), "x"), "hard")
        self.assertEqual(ce.majority(mk("hard", "hard", None), "x"), "hard")
        self.assertIsNone(ce.majority(mk("hard", "standard", "mechanical"), "x"))
        self.assertIsNone(ce.majority(mk("hard", None, None), "x"))

    def test_jev_plurality_and_the_tie_rule(self):
        mk = lambda *vs: [col(x=v) for v in vs]  # noqa: E731
        self.assertEqual(ce.jev_plurality(mk("hard", "hard", "standard"), "x"), "hard")
        self.assertEqual(ce.jev_plurality(mk("hard", None, None), "x"), "hard")
        self.assertIsNone(ce.jev_plurality(mk("hard", "standard", "mechanical"), "x"))
        self.assertIsNone(ce.jev_plurality(mk("hard", "standard", None), "x"))
        self.assertIsNone(
            ce.jev_plurality(mk("hard", "hard", "standard", "standard"), "x")
        )
        self.assertIsNone(ce.jev_plurality(mk(None, None, None), "x"))

    def test_disagreement_cards_skip_contested_and_tied(self):
        cards = ["agree", "differ", "contested", "tied"]
        agents = [
            dict(agree="hard", differ="hard", contested="hard", tied="hard"),
            dict(agree="hard", differ="hard", contested="standard", tied="hard"),
            dict(agree="hard", differ="hard", contested="mechanical", tied="hard"),
        ]
        passes = [
            dict(agree="hard", differ="standard", contested="standard", tied="hard"),
            dict(
                agree="hard", differ="standard", contested="standard", tied="standard"
            ),
            dict(agree="hard", differ="hard", contested="standard", tied="mechanical"),
        ]
        self.assertEqual(
            ce.disagreement_cards(passes, agents, cards),
            [("differ", "standard", "hard")],
        )


class Measurability(unittest.TestCase):
    @staticmethod
    def panel(off: int, n: int = 20) -> list[dict]:
        cards = {f"c{i}": ("hard" if i < off else "standard") for i in range(n)}
        return [cards] * 3

    def test_four_off_mode_cards_is_unmeasurable(self):
        self.assertFalse(ce.is_measurable(self.panel(4), CARDS))

    def test_five_is_measurable(self):
        self.assertTrue(ce.is_measurable(self.panel(5), CARDS))

    def test_a_contested_card_is_not_off_mode(self):
        agents = self.panel(5)
        agents = [dict(agents[0]), dict(agents[0], c0="mechanical"), dict(agents[0])]
        agents[2]["c0"] = "standard"
        self.assertFalse(ce.is_measurable(agents, CARDS))

    def test_a_tie_for_most_frequent_takes_the_smaller_count(self):
        # c0-c9 (hard, hard, standard), c10-c14 all standard, c15-c19 (mechanical,
        # mechanical, hard): `hard` and `standard` both have 25 answers. Majorities:
        # hard on 10 cards, standard on 5, mechanical on 5. Off-mode against `hard`
        # is 10, against `standard` 15; the rule takes 10.
        rows = (
            [("hard", "hard", "standard")] * 10
            + [("standard",) * 3] * 5
            + [("mechanical", "mechanical", "hard")] * 5
        )
        agents = [{c: row[i] for c, row in zip(CARDS, rows)} for i in range(3)]
        self.assertEqual(ce.panel_modes(agents, CARDS), ["hard", "standard"])
        self.assertEqual(ce.off_mode_majority_cards(agents, CARDS), 10)


# ---------------------------------------------------------------------- gates

AGREED = {c: v for c, v in zip(CARDS, SPLIT)}


def jev_off(cells: dict[tuple[int, str], str], n: int = 3) -> list[dict]:
    """Three passes equal to the panel, except the (pass, card) cells given."""
    passes = [dict(AGREED) for _ in range(n)]
    for (i, c), v in cells.items():
        passes[i][c] = v
    return passes


class GateA(unittest.TestCase):
    def test_exactly_a_panel_minus_delta_passes(self):
        # A_panel = 1; each off cell loses 3 of 180 -> 3 cells is 0.95 exactly
        cells = {(0, "c0"): "mechanical", (0, "c1"): "mechanical", (1, "c2"): "hard"}
        res = dim(jev_off(cells), [AGREED] * 3, CARDS, Fraction(5, 100))
        self.assertEqual(res["a_jev"], Fraction(95, 100))
        self.assertTrue(res["gates"]["a"])

    def test_one_cell_below_fails(self):
        cells = {(0, f"c{i}"): "mechanical" for i in range(4)}
        res = dim(jev_off(cells), [AGREED] * 3, CARDS, Fraction(5, 100))
        self.assertFalse(res["gates"]["a"])

    def test_zero_delta_needs_the_panels_own_rate(self):
        res = dim(jev_off({(0, "c0"): "mechanical"}), [AGREED] * 3, CARDS)
        self.assertFalse(res["gates"]["a"])
        self.assertTrue(dim(jev_off({}), [AGREED] * 3, CARDS)["gates"]["a"])


class GateB(unittest.TestCase):
    def test_steady_passes_pass(self):
        self.assertTrue(dim(jev_off({}), [AGREED] * 3, CARDS)["gates"]["b"])

    def test_unsteady_passes_fail(self):
        # pass 0 flips two cards: S_jev = (60 - 4) / 60 < 1 = A_panel
        cells = {(0, "c0"): "mechanical", (0, "c1"): "mechanical"}
        res = dim(jev_off(cells), [AGREED] * 3, CARDS)
        self.assertEqual(res["s_jev"], Fraction(56, 60))
        self.assertFalse(res["gates"]["b"])


class GateC(unittest.TestCase):
    def test_beating_the_constant_passes(self):
        self.assertTrue(dim(jev_off({}), [AGREED] * 3, CARDS)["gates"]["c"])

    def test_answering_the_constant_fails(self):
        const = [{c: "standard" for c in CARDS}] * 3
        res = dim(const, [AGREED] * 3, CARDS)
        self.assertEqual(res["a_jev"], res["a_const"])
        self.assertFalse(res["gates"]["c"])


class GateD(unittest.TestCase):
    """AGREED is `standard` on c0-c9, `hard` on c10-c19."""

    def gd(self, cells, agents=None):
        return ce.gate_d(
            jev_off(cells), agents or [AGREED] * 3, CARDS, ce.DIMENSIONS["complexity"]
        )

    def test_five_cards_all_one_way_fails(self):
        res = self.gd({(0, f"c{i}"): "hard" for i in range(5)})
        self.assertEqual((res["cards"], res["higher"], res["lower"]), (5, 5, 0))
        self.assertFalse(res["passes"])

    def test_four_cards_pass(self):
        self.assertTrue(self.gd({(0, f"c{i}"): "hard" for i in range(4)})["passes"])

    def test_exactly_eighty_percent_fails(self):
        cells = {(0, f"c{i}"): "hard" for i in range(4)}
        cells[(0, "c4")] = "mechanical"
        res = self.gd(cells)
        self.assertEqual((res["higher"], res["lower"]), (4, 1))
        self.assertFalse(res["passes"])

    def test_below_eighty_percent_passes(self):
        cells = {(0, f"c{i}"): "hard" for i in range(7)}
        cells.update({(1, f"c{i}"): "mechanical" for i in range(3)})
        res = self.gd(cells)
        self.assertEqual((res["higher"], res["lower"]), (7, 3))
        self.assertTrue(res["passes"])

    def test_passes_are_pooled_but_cards_are_counted_distinct(self):
        # 4 cards, every pass off on each: 12 pairs, all higher, still only 4 cards
        cells = {(p, f"c{i}"): "hard" for p in range(3) for i in range(4)}
        res = self.gd(cells)
        self.assertEqual((res["pooled"], res["cards"]), (12, 4))
        self.assertTrue(res["passes"])

    def test_a_missing_answer_is_never_pooled(self):
        cells = {(0, f"c{i}"): "hard" for i in range(4)}
        cells[(0, "c4")] = None  # missing: a 5th card if it were pooled
        cells[(1, "c5")] = None
        res = self.gd(cells)
        self.assertEqual((res["pooled"], res["cards"]), (4, 4))
        self.assertTrue(res["passes"])

    def test_contested_cards_are_left_out(self):
        agents = [dict(AGREED), dict(AGREED, c0="mechanical"), dict(AGREED, c0="hard")]
        res = self.gd({(0, f"c{i}"): "hard" for i in range(5)}, agents)
        self.assertEqual(res["cards"], 4)
        self.assertTrue(res["passes"])

    def test_nothing_pooled_passes(self):
        self.assertTrue(self.gd({})["passes"])


# ----------------------------------------------------------- latency, dollars


class Tolerance(unittest.TestCase):
    def test_band_edges(self):
        self.assertIsNone(ce.tolerance(1.99))
        self.assertEqual(ce.tolerance(2.0), (0, 0))
        self.assertEqual(ce.tolerance(9.99), (0, 0))
        self.assertEqual(ce.tolerance(10.0), (Fraction(5, 100), Fraction(10, 100)))

    def test_p95_is_nearest_rank(self):
        self.assertEqual(ce.p95([float(x) for x in range(1, 101)]), 95.0)
        self.assertEqual(ce.p95([float(x) for x in range(20, 0, -1)]), 19.0)
        self.assertEqual(ce.p95([3.0]), 3.0)

    def test_dollars_price_each_class_at_its_own_rate(self):
        tokens = {"uncached": 1e6, "cache_read": 1e6, "cache_write": 1e6, "output": 1e6}
        self.assertAlmostEqual(ce.call_dollars(tokens, PRICE), 3 + 0.3 + 3.75 + 15)


class Floors(unittest.TestCase):
    PANEL = profiles(complexity=SPLIT)

    def test_r_below_two_is_dont_adopt(self):
        rep = score(self.PANEL, self.PANEL, base_kw={"latency": 1.99})
        self.assertFalse(rep["tolerance_granted"])
        self.assertEqual(rep["verdict"]["verdict"], "don't adopt")

    def test_r_of_two_clears_the_floor_with_no_tolerance(self):
        rep = score(self.PANEL, self.PANEL, base_kw={"latency": 2.0})
        self.assertEqual((rep["delta"], rep["delta_tuple"]), (0, 0))
        self.assertEqual(rep["verdict"]["verdict"], "adopt")

    def test_dollars_not_lower_is_dont_adopt(self):
        rep = score(self.PANEL, self.PANEL, run_kw={"tokens": INC_TOKENS})
        self.assertFalse(rep["dollars_lower"])
        self.assertEqual(rep["verdict"]["verdict"], "don't adopt")

    def test_an_unpriced_side_cannot_clear_the_floor(self):
        rep = score(self.PANEL, self.PANEL, run_kw={"pricing": None})
        self.assertFalse(rep["dollars_known"])
        self.assertEqual(rep["verdict"]["verdict"], "don't adopt")


# -------------------------------------------------------------------- verdict


class Verdict(unittest.TestCase):
    def test_adopt(self):
        panel = profiles(complexity=SPLIT)
        rep = score(panel, panel)
        self.assertEqual(rep["measurable"], ["complexity"])
        self.assertEqual(rep["r"], 20.0)
        self.assertEqual(rep["verdict"]["verdict"], "adopt")

    def test_no_measurable_dimension_is_dont_adopt(self):
        panel = profiles()
        rep = score(panel, panel)
        self.assertEqual(rep["measurable"], [])
        self.assertIsNone(rep["profile"])
        self.assertEqual(rep["verdict"]["verdict"], "don't adopt")
        self.assertIn("no dimension is measurable", rep["verdict"]["why"])

    def failing_creativity(self, **kw):
        panel = profiles(complexity=SPLIT, creativity=["medium"] * 10 + ["high"] * 10)
        jev = profiles(complexity=SPLIT)  # creativity: `low` everywhere
        return score(panel, jev, **kw)

    def test_a_failing_dimension_without_the_human_call_is_pending(self):
        rep = self.failing_creativity()
        self.assertEqual(rep["failing"], ["creativity"])
        self.assertEqual(rep["verdict"]["verdict"], "pending")

    def test_it_can_come_off_so_adopt_for_these_only(self):
        rep = self.failing_creativity(can_come_off={"creativity"})
        self.assertEqual(rep["verdict"]["verdict"], "adopt for these dimensions only")
        self.assertEqual(rep["verdict"]["why"], "complexity")

    def test_it_cannot_come_off_so_dont_adopt(self):
        rep = self.failing_creativity(can_come_off={"autonomy"})
        self.assertEqual(rep["verdict"]["verdict"], "don't adopt")

    def test_whole_profile_gate(self):
        # each dimension alone is within δ=5, the tuple is not within δ_tuple=10
        panel = profiles(complexity=SPLIT, creativity=["low"] * 10 + ["high"] * 10)
        jev = profiles(
            complexity=["mechanical"] + SPLIT[1:],
            creativity=["low"] * 10 + ["high"] * 9 + ["medium"],
        )
        rep = score(panel, jev)
        self.assertEqual(rep["failing"], [])
        self.assertEqual(rep["profile"]["a_jev"], Fraction(18, 20))
        self.assertTrue(rep["profile"]["passes"])
        # three measurable dimensions, each off on one different card: every one
        # holds at 19/20, the tuple drops to 17/20 < 1 - 0.10
        long = ["bounded"] * 10 + ["long-horizon"] * 10
        panel = profiles(
            complexity=SPLIT, creativity=["low"] * 10 + ["high"] * 10, autonomy=long
        )
        jev = profiles(
            complexity=["mechanical"] + SPLIT[1:],
            creativity=["low"] * 10 + ["high"] * 9 + ["medium"],
            autonomy=long[:5] + ["long-horizon"] + long[6:],
        )
        rep = score(panel, jev)
        self.assertEqual(rep["failing"], [])
        self.assertEqual(rep["profile"]["a_jev"], Fraction(17, 20))
        self.assertFalse(rep["profile"]["passes"])
        self.assertEqual(rep["verdict"]["verdict"], "don't adopt")


# --------------------------------------------------------------- adjudication


def picks(verdicts, jev="mechanical", panel="standard"):
    """An adjudication as read_adjudication returns it, every pick made on the
    same (jev, panel) pair."""
    return {
        "complexity": {
            c: {"verdict": v, "jev": jev, "panel": panel} for c, v in verdicts.items()
        }
    }


class Adjudication(unittest.TestCase):
    PANEL = profiles(complexity=SPLIT)
    # Jev says `mechanical` on c0 and c1: A_jev 0.90 < 0.95, every other gate holds
    JEV = profiles(complexity=["mechanical"] * 2 + SPLIT[2:])

    def test_the_override_rule(self):
        self.assertTrue(ce.apply_override(["jev", "panel"])["passes"])
        self.assertTrue(ce.apply_override(["jev", "both", "both"])["passes"])
        self.assertFalse(ce.apply_override(["jev", "panel", "panel"])["passes"])
        # the one-card floor: a round of abstentions shows no case of Jev being right
        self.assertFalse(ce.apply_override(["both", "both"])["passes"])
        self.assertFalse(ce.apply_override([])["passes"])

    def test_eligible_only_when_a_alone_fails(self):
        rep = score(self.PANEL, self.JEV)
        res = rep["dimensions"]["complexity"]
        self.assertEqual(res["gates"], {"a": False, "b": True, "c": True, "d": True})
        self.assertTrue(res["eligible"])
        self.assertEqual(
            res["adjudication_cards"],
            [("c0", "mechanical", "standard"), ("c1", "mechanical", "standard")],
        )
        self.assertEqual(rep["verdict"]["verdict"], "don't adopt")

    def test_a_passing_adjudication_flips_gate_a(self):
        adj = picks({"c0": "jev", "c1": "panel"})
        rep = score(self.PANEL, self.JEV, adjudication=adj)
        res = rep["dimensions"]["complexity"]
        self.assertTrue(res["override"]["passes"])
        self.assertTrue(res["matches"])
        self.assertEqual(rep["verdict"]["verdict"], "adopt")

    def test_abstentions_do_not(self):
        adj = picks({"c0": "both", "c1": "both"})
        rep = score(self.PANEL, self.JEV, adjudication=adj)
        self.assertFalse(rep["dimensions"]["complexity"]["gates"]["a"])

    def test_an_incomplete_sheet_moves_nothing(self):
        adj = picks({"c0": "jev", "c1": None})
        rep = score(self.PANEL, self.JEV, adjudication=adj)
        self.assertTrue(rep["dimensions"]["complexity"]["override"]["incomplete"])
        self.assertFalse(rep["dimensions"]["complexity"]["gates"]["a"])

    def test_a_stale_sheet_is_refused(self):
        with self.assertRaises(ValueError):
            score(self.PANEL, self.JEV, adjudication=picks({"c0": "jev"}))

    def test_a_sheet_filled_against_other_answers_is_refused(self):
        # same two cards, but the sheet judged `hard` where this run says `mechanical`
        adj = picks({"c0": "jev", "c1": "jev"}, jev="hard")
        with self.assertRaises(ValueError):
            score(self.PANEL, self.JEV, adjudication=adj)
        # and a moved panel answer is refused the same way
        adj = picks({"c0": "jev", "c1": "jev"}, panel="hard")
        with self.assertRaises(ValueError):
            score(self.PANEL, self.JEV, adjudication=adj)

    def test_an_ineligible_dimension_is_not_overridden(self):
        # six cards off, all lower: fails (a) and (d)
        jev = profiles(complexity=["mechanical"] * 6 + SPLIT[6:])
        adj = picks({f"c{i}": "jev" for i in range(6)})
        rep = score(self.PANEL, jev, adjudication=adj)
        res = rep["dimensions"]["complexity"]
        self.assertFalse(res["eligible"])
        self.assertIsNone(res["override"])
        self.assertFalse(res["gates"]["a"])
        self.assertFalse(res["gates"]["d"])

    def test_the_sheet_is_blind_and_reproducible(self):
        corpus = {c: {"title": f"T {c}", "card": f"body {c}"} for c in CARDS}
        items = [("complexity", f"c{i}", "hard", "standard") for i in range(8)]
        sheet, key = ce.build_sheet(items, corpus, seed=7)
        again, key2 = ce.build_sheet(items, corpus, seed=7)
        self.assertEqual((sheet, key), (again, key2))
        other, _ = ce.build_sheet(items, corpus, seed=8)
        self.assertNotEqual(sheet, other)
        self.assertNotIn("jev", json.dumps(sheet["items"]).lower())
        for it in sheet["items"]:
            self.assertEqual({it["A"], it["B"]}, {"hard", "standard"})
            self.assertIsNone(it["pick"])
        sides = {k["jev"] for k in key["items"].values()}
        self.assertEqual(sides, {"A", "B"})
        order = [it["card"] for it in sheet["items"]]
        self.assertNotEqual(order, [f"c{i}" for i in range(8)])

    def test_reading_a_filled_sheet(self):
        corpus = {c: {"title": c, "card": c} for c in CARDS}
        items = [("complexity", "c0", "hard", "standard"), ("autonomy", "c1", "a", "b")]
        sheet, key = ce.build_sheet(items, corpus, seed=1)
        for it in sheet["items"]:
            jev_side = key["items"][str(it["item"])]["jev"]
            it["pick"] = jev_side if it["card"] == "c0" else "both"
        self.assertEqual(
            ce.read_adjudication(sheet, key),
            {
                "complexity": {
                    "c0": {"verdict": "jev", "jev": "hard", "panel": "standard"}
                },
                "autonomy": {"c1": {"verdict": "both", "jev": "a", "panel": "b"}},
            },
        )
        sheet["items"][0]["pick"] = "C"
        with self.assertRaises(ValueError):
            ce.read_adjudication(sheet, key)


# -------------------------------------------------------------------- loading


class Loading(unittest.TestCase):
    PANEL = profiles(complexity=SPLIT)

    def test_fewer_than_three_passes_is_refused(self):
        with self.assertRaises(ValueError):
            score(self.PANEL, [self.PANEL] * 2)

    def test_a_run_with_no_model_is_refused(self):
        for model in (None, ""):
            r = run([self.PANEL] * 3)
            r["model"] = model
            with self.subTest(model=model), self.assertRaises(ValueError):
                ce.check_run(r, CARDS)
            with self.subTest(model=model), self.assertRaises(ValueError):
                ce.format_analysis(r, CARDS)

    def test_the_panel_is_three_agents(self):
        with self.assertRaises(ValueError):
            score([self.PANEL] * 2, self.PANEL)

    def test_one_pinned_model(self):
        r = run([self.PANEL] * 3)
        bs = [baseline(self.PANEL, agent=i) for i in (1, 2, 3)]
        bs[2]["model"] = "claude-other"
        with self.assertRaises(ValueError):
            ce.score(r, bs, CARDS)

    def test_one_agent_three_times_is_refused(self):
        r = run([self.PANEL] * 3)
        ce.score(r, [baseline(self.PANEL, agent=i) for i in (1, 2, 3)], CARDS)
        for ids in ((1, 1, 1), (1, 2, 2), (1, 2, 4), (1, 2, None)):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                ce.score(r, [baseline(self.PANEL, agent=i) for i in ids], CARDS)

    def test_an_unknown_card_is_refused(self):
        bs = [baseline(self.PANEL, agent=i) for i in (1, 2, 3)]
        with self.assertRaises(ValueError):
            ce.score(run([self.PANEL] * 3), bs, CARDS[:-1])

    def test_an_absent_card_is_missing_not_an_error(self):
        short = {c: p for c, p in self.PANEL.items() if c != "c19"}
        rep = score(self.PANEL, short)
        self.assertEqual(rep["dimensions"]["complexity"]["jev_missing"], 3)


# ------------------------------------------------------------------------ cli


class Cli(unittest.TestCase):
    def test_analyze_score_and_sheet(self):
        panel = profiles(complexity=SPLIT)
        jev = profiles(complexity=["mechanical"] * 2 + SPLIT[2:])
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            corpus = {"cases": [{"id": c, "title": c, "card": c} for c in CARDS]}
            (t / "corpus.json").write_text(json.dumps(corpus))
            (t / "run.json").write_text(json.dumps(run([jev] * 3)))
            for i in (1, 2, 3):
                (t / f"a{i}.json").write_text(json.dumps(baseline(panel, agent=i)))
            base = ["--corpus", str(t / "corpus.json")]
            files = [str(t / n) for n in ("run.json", "a1.json", "a2.json", "a3.json")]

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ce.main(base + ["--analyze", str(t / "run.json")])
            self.assertIn("S_jev", out.getvalue())
            self.assertIn("p95 1.000s", out.getvalue())

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ce.main(
                    base
                    + ["--score", *files]
                    + ["--adjudication-sheet", str(t / "adj"), "--seed", "3"]
                )
            self.assertIn("verdict: DON'T ADOPT", out.getvalue())
            sheet = json.loads((t / "adj" / "sheet.json").read_text())
            key = json.loads((t / "adj" / "key.json").read_text())
            self.assertEqual(len(sheet["items"]), 2)

            for it in sheet["items"]:
                it["pick"] = key["items"][str(it["item"])]["jev"]
            (t / "adj" / "sheet.json").write_text(json.dumps(sheet))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ce.main(
                    base
                    + ["--score", *files]
                    + ["--adjudication", str(t / "adj" / "sheet.json")]
                    + ["--adjudication-key", str(t / "adj" / "key.json")]
                )
            self.assertIn("verdict: ADOPT", out.getvalue())

            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    ce.main(base + ["--score", *files, "--adjudication-sheet", tmp])


if __name__ == "__main__":
    unittest.main()
