#!/usr/bin/env python3
"""Score the typed call against the assess-task panel, by the record's fixed rule.

An artifact of `dev_docs/research/2026-09-22-assess-task-comparison/`, not standing
tooling. It is dev-only: never invoked by a skill or command at runtime, never part
of `just check`. Standard library only, no key, no network, and no Jev call: it
reads files that other runs wrote.

Run by path, from the repository root:

    D=dev_docs/research/2026-09-22-assess-task-comparison/references
    # what the Jev run alone supports: S_jev, latency, dollars per task
    python3 $D/compare-assess-task.py --analyze RUN.json
    # the full per-dimension table and the verdict (needs the #814 panel)
    python3 $D/compare-assess-task.py --score RUN.json AGENT-1.json AGENT-2.json AGENT-3.json
    # ... plus the blind adjudication sheet, for a human to fill in
    python3 $D/compare-assess-task.py --score RUN.json AGENT-*.json \
        --adjudication-sheet OUT_DIR --seed 1
    # ... with the filled sheet applied, and the human's partial-adoption call
    python3 $D/compare-assess-task.py --score RUN.json AGENT-*.json \
        --adjudication OUT_DIR/sheet.json --adjudication-key OUT_DIR/key.json \
        --can-come-off speed_sensitivity,cost_sensitivity
    python3 $D/test_compare_assess_task.py                     # hermetic

Every term, gate and threshold is the record's (`../README.md`, "Definitions" and
"Decision rule"). Where the record leaves a choice open, the choice is named in the
docstring of the function that makes it, under "Interpretation:", so it can be
argued with rather than found by reading the arithmetic.

## The run file (the typed call; written by #813's Jev run)

    {
      "model": "<pinned Jev model id>",
      "run_date": "YYYY-MM-DD",
      "pricing_usd_per_mtok": {"uncached": 0, "cache_read": 0,
                               "cache_write": 0, "output": 0},   # optional
      "passes": [                                                # at least 3
        {"cards": {
          "<card id>": {
            "latency_s": 0.55,
            "tokens": {"uncached": 0, "cache_read": 0, "cache_write": 0, "output": 0},
            "answers": {
              "<dimension>": {"value": "<raw answer>",
                              "distribution": {"<enum value>": 0.7, ...}},
              ...
            }
          }, ...
        }}, ...
      ]
    }

## The baseline file (one incumbent agent; written by #814's runs)

`baseline/agent-{1,2,3}.json`, one file per agent:

    {
      "agent": 1,
      "model": "<pinned model id, never a floating alias>",
      "run_date": "YYYY-MM-DD",
      "pricing_usd_per_mtok": {...},                             # optional, as above
      "cards": {
        "<card id>": {
          "raw": "<the agent's reply, verbatim>",
          "latency_s": 21.3,
          "tokens": {"uncached": 0, "cache_read": 0, "cache_write": 0, "output": 0},
          "cache_warm": false        # was the skill prompt already cached?
        }, ...
      }
    }

A card absent from a pass or an agent is scored as a missing answer on every
dimension, and contributes no latency or dollars.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "measurement" / "corpus.json"

# ------------------------------------------------------------------ the dimensions
#
# The six the record measures, each an enum in ascending order. The values and their
# order are the `task_profile` contract's, quoted in `measurement/baseline-prompt.md`
# (`complexity: mechanical | standard | hard`, and so on); the rubric's header, "Pick
# the higher value when…", is what makes the listed order an order. Every one is
# therefore ordinal, which gate (d)'s "lower or higher" needs, and none had to be
# guessed. A test re-reads the contract lines and fails if these drift from them.

DIMENSIONS: dict[str, tuple[str, ...]] = {
    "complexity": ("mechanical", "standard", "hard"),
    "creativity": ("low", "medium", "high"),
    "autonomy": ("bounded", "long-horizon"),
    "speed_sensitivity": ("low", "high"),
    "cost_sensitivity": ("low", "high"),
    "verification_criticality": ("low", "high"),
}

# ------------------------------------------------------------------ the thresholds
#
# Every number the record fixes, named once. "Points" are percentage points on an
# agreement rate between 0 and 1, so 5 points is 0.05. Rates and tolerances are
# Fractions so that a rate sitting exactly on `A_panel - δ` compares exactly rather
# than by floating-point luck. A test pins each literal to the record.

MIN_JEV_PASSES = 3  # "at least 3 passes, per [#813]"
PANEL_AGENTS = 3  # `baseline/agent-{1,2,3}.json`
MEASURABLE_MIN_CARDS = 5  # "at least 5 cards have a panel majority on a value other..."
GATE_D_MIN_CARDS = 5  # "the pooled pairs cover at least 5 distinct cards"
GATE_D_ONE_SIDE_SHARE = Fraction(80, 100)  # "at least 80% of the pooled pairs"
R_FLOOR = 2  # "If `R < 2` ... don't adopt"
R_BAND = 10  # "`≥ 10`" is the top tolerance band
DELTA_MID = Fraction(0, 100)  # `2–10`: 0 points
DELTA_TUPLE_MID = Fraction(0, 100)
DELTA_TOP = Fraction(5, 100)  # `≥ 10`: 5 points
DELTA_TUPLE_TOP = Fraction(10, 100)  # `≥ 10`: 10 points
P95 = Fraction(95, 100)

TOKEN_CLASSES = ("uncached", "cache_read", "cache_write", "output")

# ------------------------------------------------------------------------ parsing

_KEY = re.compile(r"^\s+([A-Za-z_]+)\s*:\s*(.*?)\s*$")


def parse_block(raw: object) -> dict[str, str | None]:
    """An incumbent's verbatim reply -> each dimension's value, or None if missing.

    A value outside the dimension's enum is a missing answer, and so is every
    dimension of a reply with no `task_profile:` block ("an unparseable block").

    Interpretation: this is a line reader, not YAML, because the scorer is standard
    library only. It takes the indented `key: value` lines under `task_profile:`,
    drops a trailing `# comment`, and strips one pair of matching quotes, since
    `"hard"` is `hard` to any YAML reader. A dimension named twice is missing: YAML
    readers disagree on which one wins, so neither is a judgment to score.
    """
    out: dict[str, str | None] = {d: None for d in DIMENSIONS}
    if not isinstance(raw, str):
        return out
    lines = raw.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == "task_profile:"), None
    )
    if start is None:
        return out
    seen: dict[str, list[str]] = {}
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        m = _KEY.match(line)
        if not m:
            break
        value = m.group(2).split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        seen.setdefault(m.group(1), []).append(value)
    for dim, enum in DIMENSIONS.items():
        values = seen.get(dim, [])
        if len(values) == 1 and values[0] in enum:
            out[dim] = values[0]
    return out


def jev_value(answer: object, dim: str) -> str | None:
    """One Jev answer record -> its value, or None when missing or out of enum.

    The distribution rides along in the run file so the answers can be re-analysed,
    but the rule scores the value alone, so this reads nothing else.
    """
    if isinstance(answer, dict):
        value = answer.get("value")
        if value in DIMENSIONS[dim]:
            return value
    return None


# ------------------------------------------------------------------------ loading


def _check_cards(where: str, cards: dict, known: set[str]) -> None:
    unknown = sorted(set(cards) - known)
    if unknown:
        raise ValueError(f"{where} names cards not in the corpus: {unknown}")


def _check_call(where: str, entry: dict) -> None:
    if not isinstance(entry.get("latency_s"), (int, float)):
        raise ValueError(f"{where}: latency_s is required")
    tokens = entry.get("tokens")
    if not isinstance(tokens, dict) or set(tokens) != set(TOKEN_CLASSES):
        raise ValueError(f"{where}: tokens must carry exactly {TOKEN_CLASSES}")


def check_run(run: dict, cards: list[str]) -> None:
    model = run.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("the run must name its pinned Jev model id")
    passes = run.get("passes", [])
    if len(passes) < MIN_JEV_PASSES:
        raise ValueError(
            f"the run has {len(passes)} passes; the record needs at least "
            f"{MIN_JEV_PASSES}"
        )
    for i, p in enumerate(passes, 1):
        _check_cards(f"pass {i}", p["cards"], set(cards))
        for cid, entry in p["cards"].items():
            _check_call(f"pass {i} {cid}", entry)


def check_baselines(baselines: list[dict], cards: list[str]) -> None:
    """Exactly three agents, each once, one pinned model among them.

    Interpretation: "a pinned model version, never a floating alias" cannot be read
    off an id string, so this checks what can be checked: every file names a model,
    and all three name the same one.

    The agent ids must be exactly 1, 2 and 3. One file passed three times would
    agree with itself on every card, so A_panel would read 1 and the whole table
    would describe one run, not a panel.
    """
    if len(baselines) != PANEL_AGENTS:
        raise ValueError(
            f"{len(baselines)} baseline files; the panel is {PANEL_AGENTS} agents"
        )
    ids = [b.get("agent") for b in baselines]
    if sorted(ids, key=str) != list(range(1, PANEL_AGENTS + 1)):
        raise ValueError(
            f"the panel's agent ids are {ids}; they must be 1 to {PANEL_AGENTS}, "
            "each once"
        )
    models = {b.get("model") for b in baselines}
    if None in models or len(models) != 1:
        raise ValueError(f"the panel must share one pinned model id, got {models}")
    for i, b in enumerate(baselines, 1):
        _check_cards(f"agent {i}", b["cards"], set(cards))
        for cid, entry in b["cards"].items():
            _check_call(f"agent {i} {cid}", entry)
            if not isinstance(entry.get("cache_warm"), bool):
                raise ValueError(f"agent {i} {cid}: cache_warm is required")


Answers = dict[str, "str | None"]


def jev_answers(run: dict, cards: list[str]) -> dict[str, list[Answers]]:
    """dimension -> one {card: value or None} per pass."""
    out: dict[str, list[Answers]] = {d: [] for d in DIMENSIONS}
    for p in run["passes"]:
        for dim in DIMENSIONS:
            out[dim].append(
                {
                    c: jev_value(p["cards"].get(c, {}).get("answers", {}).get(dim), dim)
                    for c in cards
                }
            )
    return out


def panel_answers(baselines: list[dict], cards: list[str]) -> dict[str, list[Answers]]:
    """dimension -> one {card: value or None} per agent, parsed from the raw reply."""
    out: dict[str, list[Answers]] = {d: [] for d in DIMENSIONS}
    for b in baselines:
        parsed = {c: parse_block(b["cards"].get(c, {}).get("raw")) for c in cards}
        for dim in DIMENSIONS:
            out[dim].append({c: parsed[c][dim] for c in cards})
    return out


def tuple_answers(
    per_dim: dict[str, list[Answers]], dims: list[str], cards: list[str]
) -> list[dict]:
    """Per source, each card's tuple over `dims`, or None if any member is missing."""
    n = len(per_dim[dims[0]]) if dims else 0
    out = []
    for i in range(n):
        row: dict = {}
        for c in cards:
            values = tuple(per_dim[d][i][c] for d in dims)
            row[c] = None if None in values else values
        out.append(row)
    return out


# -------------------------------------------------------------------- the terms
#
# One agreement function scores agent against agent, pass against pass, and pass
# against agent. That is what "the same `--score` path" means in the record.


def agree(x: object, y: object) -> int:
    """1 when both answered and the answers are equal. A missing answer disagrees
    with everything, another missing answer included."""
    return int(x is not None and x == y)


def mean_pairwise(sources: list[dict], cards: list[str]) -> Fraction:
    """Mean agreement over every unordered pair of sources, over every card.

    `A_panel` over the three agents; `S_jev` over the passes.
    """
    pairs = [(a, b) for i, a in enumerate(sources) for b in sources[i + 1 :]]
    total = len(pairs) * len(cards)
    if not total:
        return Fraction(0)
    return Fraction(sum(agree(a[c], b[c]) for a, b in pairs for c in cards), total)


def mean_cross(left: list[dict], right: list[dict], cards: list[str]) -> Fraction:
    """Mean agreement of each left source with each right source: `A_jev` is every
    pass against every agent (passes x 3 pairs x cards)."""
    total = len(left) * len(right) * len(cards)
    if not total:
        return Fraction(0)
    hits = sum(agree(a[c], b[c]) for a in left for b in right for c in cards)
    return Fraction(hits, total)


def panel_modes(agents: list[dict], cards: list[str]) -> list:
    """The most frequent value across all panel answers, as a list: every value tied
    for most frequent, in first-seen order. Empty when the panel never answered."""
    counts: dict = {}
    for a in agents:
        for c in cards:
            if a[c] is not None:
                counts[a[c]] = counts.get(a[c], 0) + 1
    if not counts:
        return []
    top = max(counts.values())
    return [v for v, n in counts.items() if n == top]


def a_const(agents: list[dict], cards: list[str]) -> Fraction:
    """`A_const`: agreement with the panel of a constant answering the most frequent
    panel value, scored as one more source against each agent. The base rate.

    A tie for most frequent does not matter here: tied values have equal counts, so
    either constant scores the same.
    """
    modes = panel_modes(agents, cards)
    if not modes:
        return Fraction(0)
    const = {c: modes[0] for c in cards}
    return mean_cross([const], agents, cards)


def majority(agents: list[dict], card: str):
    """The value at least two agents gave, or None: the card is contested."""
    counts: dict = {}
    for a in agents:
        if a[card] is not None:
            counts[a[card]] = counts.get(a[card], 0) + 1
    for value, n in counts.items():
        if n >= 2:
            return value
    return None


def jev_plurality(passes: list[dict], card: str):
    """Jev's answer on a card: the value most passes gave, or None with no strict
    plurality. Only adjudication uses it.

    Interpretation: a missing answer is not a value, so only in-enum answers are
    counted. A card whose passes are all missing has no Jev answer.
    """
    counts: dict = {}
    for p in passes:
        if p[card] is not None:
            counts[p[card]] = counts.get(p[card], 0) + 1
    if not counts:
        return None
    ranked = sorted(counts.values(), reverse=True)
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        return None
    return max(counts, key=lambda v: counts[v])


def off_mode_majority_cards(agents: list[dict], cards: list[str]) -> int:
    """How many cards have a panel majority on a value other than the most frequent.

    Interpretation: when two values tie for most frequent, the record's "its most
    frequent one" is ambiguous, so this takes the smaller count over the tied values.
    That is the reading under which a tie can only make a dimension unmeasurable,
    never measurable.
    """
    modes = panel_modes(agents, cards)
    if not modes:
        return 0
    majorities = [majority(agents, c) for c in cards]
    return min(sum(1 for m in majorities if m is not None and m != v) for v in modes)


def is_measurable(agents: list[dict], cards: list[str]) -> bool:
    return off_mode_majority_cards(agents, cards) >= MEASURABLE_MIN_CARDS


def gate_d(
    passes: list[dict], agents: list[dict], cards: list[str], enum: tuple[str, ...]
) -> dict:
    """Gate (d), no one-way error. Pools (pass, card) pairs, not Jev's per-card vote.

    A pair is pooled when the card has a panel majority (contested cards are left
    out), the pass's value is in the enum (a missing answer has no side, so it is
    never pooled), and that value differs from the majority. Its side is lower or
    higher by the enum's order. The dimension fails when the pooled pairs cover at
    least `GATE_D_MIN_CARDS` distinct cards and at least `GATE_D_ONE_SIDE_SHARE` of
    them fall on one side; otherwise, including when nothing is pooled, it passes.
    """
    rank = {v: i for i, v in enumerate(enum)}
    lower = higher = 0
    distinct: set[str] = set()
    for c in cards:
        m = majority(agents, c)
        if m is None:
            continue
        for p in passes:
            v = p[c]
            if v is None or v == m:
                continue
            distinct.add(c)
            if rank[v] < rank[m]:
                lower += 1
            else:
                higher += 1
    pooled = lower + higher
    one_way = (
        pooled > 0
        and len(distinct) >= GATE_D_MIN_CARDS
        and Fraction(max(lower, higher), pooled) >= GATE_D_ONE_SIDE_SHARE
    )
    return {
        "pooled": pooled,
        "cards": len(distinct),
        "lower": lower,
        "higher": higher,
        "passes": not one_way,
    }


# --------------------------------------------------------------- latency, dollars


def p95(xs: list[float]) -> float:
    """Interpretation: nearest-rank p95 — the ceil(0.95 n)-th smallest sample, so the
    result is always a latency that was actually observed, with no interpolation."""
    if not xs:
        raise ValueError("no latencies to take a p95 of")
    ordered = sorted(xs)
    return ordered[math.ceil(P95 * len(ordered)) - 1]


def call_dollars(tokens: dict, pricing: dict) -> float:
    """Interpretation: the price per class is taken as input, from the file's
    `pricing_usd_per_mtok` (USD per million tokens, as the vendors quote it), never
    hard-coded here. The record prices each class at its own rate on the run date,
    and each file carries its own `run_date` beside its rates."""
    return sum(tokens[k] * pricing[k] for k in TOKEN_CLASSES) / 1_000_000


def calls(files: list[dict]) -> list[dict]:
    """Every recorded call: a pass's or an agent's per-card entry."""
    return [e for f in files for e in f["cards"].values()]


def side_costs(files: list[dict], pricing: dict | None) -> dict:
    """Latency median and p95, and dollars per task, for one side.

    Interpretation: both are pooled over every recorded call — for Jev every
    (pass, card), for the incumbent every (agent, card) — so "per packet" is the mean
    over calls and the p95 is over all of them. Dollars are None when the file
    carries no pricing, and an unpriced side cannot clear the dollar floor.
    """
    entries = calls(files)
    latencies = [float(e["latency_s"]) for e in entries]
    dollars = None
    if pricing is not None:
        if set(pricing) != set(TOKEN_CLASSES):
            raise ValueError(f"pricing must carry exactly {TOKEN_CLASSES}")
        dollars = statistics.fmean(call_dollars(e["tokens"], pricing) for e in entries)
    return {
        "calls": len(entries),
        "median": statistics.median(latencies),
        "p95": p95(latencies),
        "dollars": dollars,
    }


def panel_pricing(baselines: list[dict]) -> dict | None:
    """Interpretation: the three agents share one model and one run's rates, so the
    panel's pricing must be identical across its files, or it is refused."""
    prices = [b.get("pricing_usd_per_mtok") for b in baselines]
    if any(p != prices[0] for p in prices):
        raise ValueError("the three baseline files carry different pricing")
    return prices[0]


def tolerance(r: float) -> tuple[Fraction, Fraction] | None:
    """The record's tolerance table: (δ, δ_tuple), or None for "no adoption".

    `R < 2` -> None; `2 <= R < 10` -> 0 points; `R >= 10` -> 5 and 10 points.
    """
    if r < R_FLOOR:
        return None
    if r < R_BAND:
        return DELTA_MID, DELTA_TUPLE_MID
    return DELTA_TOP, DELTA_TUPLE_TOP


# ---------------------------------------------------------------- per dimension


def score_dimension(
    passes: list[dict],
    agents: list[dict],
    cards: list[str],
    enum: tuple[str, ...],
    delta: Fraction,
) -> dict:
    """Every term and all four gates for one dimension."""
    a_panel = mean_pairwise(agents, cards)
    a_jev = mean_cross(passes, agents, cards)
    s_jev = mean_pairwise(passes, cards)
    base = a_const(agents, cards)
    d = gate_d(passes, agents, cards, enum)
    gates = {
        "a": a_jev >= a_panel - delta,
        "b": s_jev >= a_panel - delta,
        "c": a_jev > base,
        "d": d["passes"],
    }
    return {
        "measurable": is_measurable(agents, cards),
        "off_mode_cards": off_mode_majority_cards(agents, cards),
        "a_panel": a_panel,
        "a_jev": a_jev,
        "s_jev": s_jev,
        "a_const": base,
        "contested": sum(1 for c in cards if majority(agents, c) is None),
        "jev_missing": sum(1 for p in passes for c in cards if p[c] is None),
        "panel_missing": sum(1 for a in agents for c in cards if a[c] is None),
        "gate_d": d,
        "gates": gates,
        "override": None,
        "matches": all(gates.values()),
    }


def override_eligible(dim: dict) -> bool:
    """Adjudication applies only to a measurable dimension failing gate (a) alone."""
    g = dim["gates"]
    return dim["measurable"] and not g["a"] and g["b"] and g["c"] and g["d"]


def disagreement_cards(passes: list[dict], agents: list[dict], cards: list[str]):
    """The adjudication set: (card, Jev's answer, the panel majority) wherever the two
    differ, leaving out contested cards and cards with no Jev answer."""
    out = []
    for c in cards:
        m = majority(agents, c)
        j = jev_plurality(passes, c)
        if m is not None and j is not None and j != m:
            out.append((c, j, m))
    return out


def apply_override(verdicts: list[str]) -> dict:
    """The one revision allowed in advance, over one dimension's adjudicated cards.

    `verdicts` holds, per card, "jev", "panel" or "both". Gate (a) passes when Jev's
    answer is picked at least as often as the panel's AND on at least one card;
    "both defensible" counts for neither side.
    """
    jev = verdicts.count("jev")
    panel = verdicts.count("panel")
    return {
        "jev": jev,
        "panel": panel,
        "both": verdicts.count("both"),
        "passes": jev >= panel and jev >= 1,
    }


# ------------------------------------------------------------ adjudication files


def build_sheet(
    items: list[tuple[str, str, str, str]], corpus: dict[str, dict], seed: int
) -> tuple[dict, dict]:
    """(sheet, key) for `items` of (dimension, card, jev answer, panel answer).

    Interpretation: the record wants the order recorded but the adjudicator blind,
    so the recorded order lives in a separate key file the adjudicator never opens.
    The sheet carries the card and two unlabelled answers, A and B, in a random item
    order with a random side for Jev, both drawn from `seed` so the sheet is
    reproducible. The adjudicator fills each `pick` with "A", "B" or "both".
    """
    rng = random.Random(seed)
    order = list(items)
    rng.shuffle(order)
    sheet_items, key_items = [], {}
    for n, (dim, card, jev, panel) in enumerate(order, 1):
        jev_side = "A" if rng.random() < 0.5 else "B"
        a, b = (jev, panel) if jev_side == "A" else (panel, jev)
        case = corpus[card]
        sheet_items.append(
            {
                "item": n,
                "dimension": dim,
                "scale": " < ".join(DIMENSIONS[dim]),
                "card": card,
                "title": case["title"],
                "body": case["card"],
                "A": a,
                "B": b,
                "pick": None,
            }
        )
        key_items[str(n)] = {"dimension": dim, "card": card, "jev": jev_side}
    sheet = {
        "instructions": (
            "For each item, read the card and pick the answer you find more "
            'defensible on that dimension: "A", "B", or "both" if both are '
            "defensible. Do not open key.json."
        ),
        "items": sheet_items,
    }
    return sheet, {"seed": seed, "items": key_items}


def read_adjudication(sheet: dict, key: dict) -> dict[str, dict[str, dict]]:
    """A filled sheet and its key -> dimension -> card -> {"verdict", "jev", "panel"}.

    `verdict` is "jev", "panel" or "both", or None where the pick is still blank.
    `jev` and `panel` are the two answers the pick was made on, recovered from the
    sheet's A and B through the key, so `score` can refuse a sheet whose answers no
    longer match the run being scored.
    """
    out: dict[str, dict[str, dict]] = {}
    by_item = {str(i["item"]): i for i in sheet["items"]}
    if set(by_item) != set(key["items"]):
        raise ValueError("the sheet and the key hold different items")
    for n, k in key["items"].items():
        item = by_item[n]
        if (item["dimension"], item["card"]) != (k["dimension"], k["card"]):
            raise ValueError(f"item {n}: the sheet and the key disagree on its card")
        pick = item.get("pick")
        if pick is None:
            verdict = None
        elif pick == "both":
            verdict = "both"
        elif pick in ("A", "B"):
            verdict = "jev" if pick == k["jev"] else "panel"
        else:
            raise ValueError(f"item {n}: pick must be A, B or both, got {pick!r}")
        if k["jev"] not in ("A", "B"):
            raise ValueError(f"item {n}: the key's jev side must be A or B")
        panel_side = "B" if k["jev"] == "A" else "A"
        out.setdefault(k["dimension"], {})[k["card"]] = {
            "verdict": verdict,
            "jev": item[k["jev"]],
            "panel": item[panel_side],
        }
    return out


# ---------------------------------------------------------------------- scoring


def score(
    run: dict,
    baselines: list[dict],
    cards: list[str],
    adjudication: dict[str, dict[str, dict]] | None = None,
    can_come_off: set[str] | None = None,
) -> dict:
    """The whole rule: floors, tolerance, per-dimension gates, override, whole
    profile, verdict."""
    check_run(run, cards)
    check_baselines(baselines, cards)
    jev = jev_answers(run, cards)
    panel = panel_answers(baselines, cards)

    jev_cost = side_costs(run["passes"], run.get("pricing_usd_per_mtok"))
    inc_cost = side_costs(baselines, panel_pricing(baselines))
    if jev_cost["p95"] <= 0:
        raise ValueError("the typed call's p95 latency must be positive")
    r = inc_cost["p95"] / jev_cost["p95"]
    tol = tolerance(r)
    # Below the floor the table grants no tolerance at all; the gates are still
    # computed, at δ = 0, so the table has something to show.
    delta, delta_tuple = tol if tol else (DELTA_MID, DELTA_TUPLE_MID)
    dollars_known = jev_cost["dollars"] is not None and inc_cost["dollars"] is not None
    dollars_lower = dollars_known and jev_cost["dollars"] < inc_cost["dollars"]
    floors_pass = tol is not None and dollars_lower

    dims = {
        d: score_dimension(jev[d], panel[d], cards, enum, delta)
        for d, enum in DIMENSIONS.items()
    }
    for d, res in dims.items():
        res["eligible"] = override_eligible(res)
        res["adjudication_cards"] = (
            disagreement_cards(jev[d], panel[d], cards) if res["eligible"] else []
        )
        if not res["eligible"] or not adjudication or d not in adjudication:
            continue
        picks = adjudication[d]
        expected = {c: (j, m) for c, j, m in res["adjudication_cards"]}
        if set(picks) != set(expected):
            raise ValueError(
                f"{d}: the adjudication covers {sorted(picks)} but the cards to "
                f"adjudicate are {sorted(expected)}; the sheet is stale"
            )
        # A pick judges a pair of answers, not a card: if either answer moved since
        # the sheet was made, the pick says nothing about the pair now being scored.
        moved = sorted(
            c for c, p in picks.items() if (p["jev"], p["panel"]) != expected[c]
        )
        if moved:
            raise ValueError(
                f"{d}: the answers on {moved} differ from the ones the sheet was "
                "filled against; the sheet is stale"
            )
        verdicts = [p["verdict"] for p in picks.values()]
        if any(v is None for v in verdicts):
            # Interpretation: the rule is over "every card where Jev's answer
            # differs", so a partly filled sheet moves nothing.
            res["override"] = {"incomplete": True, "passes": False}
            continue
        res["override"] = apply_override(verdicts)
        if res["override"]["passes"]:
            res["gates"]["a"] = True
            res["matches"] = all(res["gates"].values())

    measurable = [d for d in DIMENSIONS if dims[d]["measurable"]]
    whole = profile_gate(jev, panel, measurable, cards, delta_tuple)
    failing = [d for d in measurable if not dims[d]["matches"]]
    matching = [d for d in measurable if dims[d]["matches"]]
    partial = profile_gate(jev, panel, matching, cards, delta_tuple)

    return {
        "cards": len(cards),
        "passes": len(run["passes"]),
        "jev_model": run.get("model"),
        "panel_model": baselines[0]["model"],
        "cache_warm": sum(e["cache_warm"] for e in calls(baselines)),
        "jev_cost": jev_cost,
        "inc_cost": inc_cost,
        "r": r,
        "delta": delta,
        "delta_tuple": delta_tuple,
        "tolerance_granted": tol is not None,
        "dollars_known": dollars_known,
        "dollars_lower": dollars_lower,
        "floors_pass": floors_pass,
        "dimensions": dims,
        "measurable": measurable,
        "unmeasurable": [d for d in DIMENSIONS if d not in measurable],
        "profile": whole,
        "partial_profile": partial,
        "failing": failing,
        "matching": matching,
        "verdict": verdict(
            floors_pass, measurable, failing, matching, whole, partial, can_come_off
        ),
    }


def profile_gate(
    jev: dict, panel: dict, dims: list[str], cards: list[str], delta_tuple: Fraction
) -> dict | None:
    """Exact agreement on the tuple of `dims`, pairwise, as for one dimension:
    `A_jev(tuple) >= A_panel(tuple) - δ_tuple`. None over an empty tuple."""
    if not dims:
        return None
    jt = tuple_answers(jev, dims, cards)
    pt = tuple_answers(panel, dims, cards)
    a_panel = mean_pairwise(pt, cards)
    a_jev = mean_cross(jt, pt, cards)
    return {
        "dims": dims,
        "a_panel": a_panel,
        "a_jev": a_jev,
        "s_jev": mean_pairwise(jt, cards),
        "a_const": a_const(pt, cards),
        "passes": a_jev >= a_panel - delta_tuple,
    }


def verdict(
    floors_pass: bool,
    measurable: list[str],
    failing: list[str],
    matching: list[str],
    whole: dict | None,
    partial: dict | None,
    can_come_off: set[str] | None,
) -> dict:
    """Adopt, adopt for these dimensions only, or don't adopt — or pending, when the
    partial verdict turns on a call this scorer is not allowed to make.

    Interpretation: whether a failing dimension "can come off the subagent anyway"
    (dropped, computed, or made a constant) is a human judgment, so it is an explicit
    input, `--can-come-off`. Without it, a result that could only be a partial
    adoption is reported as pending, naming the dimensions the call is needed for.

    Interpretation: for a partial adoption the whole-profile gate is taken over the
    dimensions actually adopted (the measurable ones that match), since the tuple the
    typed call would then produce is those. The record states the tuple gate for the
    full profile only.
    """
    if not floors_pass:
        return {"verdict": "don't adopt", "why": "a floor fails"}
    if not measurable:
        return {"verdict": "don't adopt", "why": "no dimension is measurable"}
    if not failing:
        if whole and whole["passes"]:
            return {"verdict": "adopt", "why": "every measurable dimension matches"}
        return {"verdict": "don't adopt", "why": "the whole-profile gate fails"}
    if not matching:
        return {"verdict": "don't adopt", "why": "no measurable dimension matches"}
    if not (partial and partial["passes"]):
        return {
            "verdict": "don't adopt",
            "why": "the whole-profile gate fails over the matching dimensions",
        }
    if can_come_off is None:
        return {
            "verdict": "pending",
            "why": (
                "adopt for " + ", ".join(matching) + " only, if every failing "
                "dimension (" + ", ".join(failing) + ") can come off the subagent; "
                "otherwise don't adopt. Pass --can-come-off with that call."
            ),
        }
    if set(failing) <= can_come_off:
        return {
            "verdict": "adopt for these dimensions only",
            "why": ", ".join(matching),
        }
    return {
        "verdict": "don't adopt",
        "why": "failing and must stay on the subagent: "
        + ", ".join(d for d in failing if d not in can_come_off),
    }


# -------------------------------------------------------------------- reporting


def pct(x: Fraction | float) -> str:
    return f"{float(x):.3f}"


def usd(x: float | None) -> str:
    return "unpriced" if x is None else f"${x:.6f}"


def format_analysis(run: dict, cards: list[str]) -> str:
    check_run(run, cards)
    jev = jev_answers(run, cards)
    cost = side_costs(run["passes"], run.get("pricing_usd_per_mtok"))
    n = len(run["passes"])
    lines = [
        f"Jev run: model {run.get('model')}, {n} passes over {len(cards)} cards, "
        f"run date {run.get('run_date')}",
        "",
        f"  {'dimension':<26}{'S_jev':>7}   missing (pass, card)",
    ]
    for d in DIMENSIONS:
        missing = sum(1 for p in jev[d] for c in cards if p[c] is None)
        lines.append(
            f"  {d:<26}{pct(mean_pairwise(jev[d], cards)):>7}   {missing}/{n * len(cards)}"
        )
    lines += [
        "",
        f"latency: median {cost['median']:.3f}s, p95 {cost['p95']:.3f}s "
        f"over {cost['calls']} calls",
        f"dollars per task: {usd(cost['dollars'])}",
        "",
        "A_jev, A_panel, A_const and the verdict need the panel: use --score.",
    ]
    return "\n".join(lines)


def format_score(rep: dict) -> str:
    j, i = rep["jev_cost"], rep["inc_cost"]
    band = (
        f"δ {float(rep['delta']) * 100:.0f} points, "
        f"δ_tuple {float(rep['delta_tuple']) * 100:.0f} points"
        if rep["tolerance_granted"]
        else "R < 2: no adoption (gates shown at δ = 0)"
    )
    lines = [
        f"{rep['cards']} cards, {rep['passes']} Jev passes ({rep['jev_model']}), "
        f"3 agents ({rep['panel_model']}); skill prompt cache-warm on "
        f"{rep['cache_warm']} of {i['calls']} incumbent calls",
        "",
        f"latency   incumbent median {i['median']:.3f}s p95 {i['p95']:.3f}s | "
        f"Jev median {j['median']:.3f}s p95 {j['p95']:.3f}s",
        f"R = {i['p95']:.3f} / {j['p95']:.3f} = {rep['r']:.2f}  ->  {band}",
        f"dollars   incumbent {usd(i['dollars'])} | Jev {usd(j['dollars'])} per task"
        f"  ->  floor {'PASS' if rep['dollars_lower'] else 'FAIL'}",
        "",
        f"  {'dimension':<26}{'meas':>6}{'A_panel':>9}{'A_jev':>8}{'S_jev':>8}"
        f"{'A_const':>9}{'cont':>6}{'miss J/P':>10}   a b c d  (d: pooled/cards L:H)"
        "  match",
    ]
    for d, res in rep["dimensions"].items():
        g = res["gates"]
        gd = res["gate_d"]
        marks = " ".join("." if g[k] else "X" for k in "abcd")
        note = ""
        if res["override"]:
            o = res["override"]
            note = (
                "  (a) adjudication incomplete"
                if o.get("incomplete")
                else f"  (a) adjudicated J{o['jev']}:P{o['panel']}:both{o['both']}"
                f" -> {'PASS' if o['passes'] else 'FAIL'}"
            )
        elif res["eligible"]:
            note = f"  (a) adjudicable: {len(res['adjudication_cards'])} cards"
        lines.append(
            f"  {d:<26}{('yes' if res['measurable'] else 'no') + '/' + str(res['off_mode_cards']):>6}"
            f"{pct(res['a_panel']):>9}{pct(res['a_jev']):>8}{pct(res['s_jev']):>8}"
            f"{pct(res['a_const']):>9}{res['contested']:>6}"
            f"{str(res['jev_missing']) + '/' + str(res['panel_missing']):>10}   "
            f"{marks}  ({gd['pooled']}/{gd['cards']} {gd['lower']}:{gd['higher']})"
            f"  {'n/a' if not res['measurable'] else ('yes' if res['matches'] else 'NO')}"
            f"{note}"
        )
    lines += [
        "",
        "whole profile (exact agreement on the tuple of measurable dimensions):",
    ]
    for label, prof in (("all", rep["profile"]), ("matching", rep["partial_profile"])):
        if prof is None:
            lines.append(f"  {label}: empty tuple")
            continue
        lines.append(
            f"  {label} ({', '.join(prof['dims'])}): A_panel {pct(prof['a_panel'])}, "
            f"A_jev {pct(prof['a_jev'])}, S_jev {pct(prof['s_jev'])}, "
            f"A_const {pct(prof['a_const'])} -> {'PASS' if prof['passes'] else 'FAIL'}"
        )
    if rep["unmeasurable"]:
        lines += [
            "",
            "unmeasurable (no accuracy claim; #815 decides unverified vs constant): "
            + ", ".join(rep["unmeasurable"]),
        ]
    v = rep["verdict"]
    lines += ["", f"verdict: {v['verdict'].upper()} — {v['why']}"]
    return "\n".join(lines)


# -------------------------------------------------------------------------- cli


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--analyze", metavar="RUN", help="what the Jev run alone supports"
    )
    mode.add_argument(
        "--score",
        nargs="+",
        metavar="FILE",
        help="the Jev run, then the three baseline files",
    )
    p.add_argument("--corpus", default=str(CORPUS), help="the card set (corpus.json)")
    p.add_argument(
        "--adjudication", metavar="SHEET", help="a filled adjudication sheet"
    )
    p.add_argument("--adjudication-key", metavar="KEY", help="the sheet's key.json")
    p.add_argument(
        "--adjudication-sheet",
        metavar="DIR",
        help="write sheet.json and key.json for the adjudicable cards",
    )
    p.add_argument("--seed", type=int, help="seed for the sheet's order and sides")
    p.add_argument(
        "--can-come-off",
        metavar="DIMS",
        help="comma-separated dimensions a human has judged can come off the "
        "subagent (dropped, computed, or constant); decides the partial verdict",
    )
    args = p.parse_args(argv)

    corpus = {c["id"]: c for c in load(args.corpus)["cases"]}
    cards = list(corpus)

    if args.analyze:
        print(format_analysis(load(args.analyze), cards))
        return 0

    if len(args.score) != 1 + PANEL_AGENTS:
        p.error(f"--score takes the run and {PANEL_AGENTS} baseline files")
    if bool(args.adjudication) != bool(args.adjudication_key):
        p.error("--adjudication and --adjudication-key go together")
    if args.adjudication_sheet and args.seed is None:
        p.error("--adjudication-sheet needs --seed, so the sheet is reproducible")
    come_off = None
    if args.can_come_off is not None:
        come_off = {d.strip() for d in args.can_come_off.split(",") if d.strip()}
        unknown = come_off - set(DIMENSIONS)
        if unknown:
            p.error(f"--can-come-off names unknown dimensions: {sorted(unknown)}")
    adjudication = None
    if args.adjudication:
        adjudication = read_adjudication(
            load(args.adjudication), load(args.adjudication_key)
        )

    run, *baselines = (load(f) for f in args.score)
    rep = score(run, baselines, cards, adjudication, come_off)
    print(format_score(rep))

    if args.adjudication_sheet:
        items = [
            (d, c, j, m)
            for d, res in rep["dimensions"].items()
            for c, j, m in res["adjudication_cards"]
        ]
        sheet, key = build_sheet(items, corpus, args.seed)
        out = Path(args.adjudication_sheet)
        out.mkdir(parents=True, exist_ok=True)
        (out / "sheet.json").write_text(json.dumps(sheet, indent=2) + "\n")
        (out / "key.json").write_text(json.dumps(key, indent=2) + "\n")
        print(
            f"\nadjudication sheet: {len(items)} items -> {out / 'sheet.json'} "
            f"(key: {out / 'key.json'}, seed {args.seed})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
