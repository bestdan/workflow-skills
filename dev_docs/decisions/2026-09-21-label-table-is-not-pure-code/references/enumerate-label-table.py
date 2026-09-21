#!/usr/bin/env python3
"""Enumerate the **Deriving `label`** table in `skills/assess-task/SKILL.md`
over every dimension tuple it can be handed, and report where it fires no label
and where it fires several.

That table is the only thing standing behind the design's claim that `label` is
"derived, not judged". Whether the claim holds is a question about the table's
totality and determinism, which is arithmetic over a finite product of enums --
576 tuples -- not something an API call can answer. This script is that
arithmetic, so the record's counts can be re-checked without re-reading the
table by hand.

Usage:
    python3 enumerate-label-table.py             # the literal reading
    python3 enumerate-label-table.py --variants  # plus the two repair readings

Exit status is always 0; this reports, it does not gate.
"""

import argparse
import itertools
from collections import Counter

# The seven scored dimensions, in the order skills/assess-task/SKILL.md lists
# them, with their fixed enums. Nothing in the dimension rubric forbids any
# combination, so the full product is the space the table has to cover.
DIMENSIONS = {
    "complexity": ["mechanical", "standard", "hard"],
    "creativity": ["low", "medium", "high"],
    "scope": ["single-file", "pr-sized", "multi-file", "whole-codebase"],
    "autonomy": ["bounded", "long-horizon"],
    "speed_sensitivity": ["low", "high"],
    "cost_sensitivity": ["low", "high"],
    "verification_criticality": ["low", "high"],
}

LABELS = [
    "architecture",
    "standard-pr",
    "mechanical-bulk",
    "frontend-creative",
    "latency-loop",
    "whole-codebase",
    "verification-sensitive",
    "long-horizon",
]


def fires(t, *, architecture_includes_whole_codebase=False):
    """The table read literally, keeping only what the seven dimensions express.

    Two cells say things no dimension carries, and both are dropped here rather
    than guessed at:

      * `architecture`'s "or a genuinely hard bug" -- there is no bug dimension.
      * `mechanical-bulk`'s "high-volume simple work" -- a gloss on
        `cost_sensitivity: high`, not a further input. Its "and/or" is read as
        the inclusive or it spells.

    `standard-pr`'s "nothing extreme" is dropped here too, because it is a
    reference to the other seven rows rather than a condition on the tuple;
    --variants reads it as the negation it implies.
    """
    out = []
    arch_scopes = {"multi-file"}
    if architecture_includes_whole_codebase:
        arch_scopes.add("whole-codebase")
    if t["complexity"] == "hard" and t["scope"] in arch_scopes:
        out.append("architecture")
    if t["complexity"] == "standard" and t["scope"] == "pr-sized":
        out.append("standard-pr")
    if t["complexity"] == "mechanical" or t["cost_sensitivity"] == "high":
        out.append("mechanical-bulk")
    if t["creativity"] == "high":
        out.append("frontend-creative")
    if t["speed_sensitivity"] == "high":
        out.append("latency-loop")
    if t["scope"] == "whole-codebase":
        out.append("whole-codebase")
    if t["verification_criticality"] == "high":
        out.append("verification-sensitive")
    if t["autonomy"] == "long-horizon":
        out.append("long-horizon")
    return out


def tuples():
    keys = list(DIMENSIONS)
    for combo in itertools.product(*(DIMENSIONS[k] for k in keys)):
        yield dict(zip(keys, combo))


def render(t):
    return ", ".join(f"{k}={t[k]}" for k in DIMENSIONS)


def report(name, rows):
    total = len(rows)
    counts = Counter(len(labels) for _, labels in rows)
    print(f"\n## {name}")
    print(f"tuples: {total}")
    for n in sorted(counts):
        print(
            f"  fires {n} label(s): {counts[n]:4d}  ({100.0 * counts[n] / total:5.1f}%)"
        )

    silent = [t for t, labels in rows if not labels]
    if silent:
        print(f"\n  first tuple that fires nothing:\n    {render(silent[0])}")

    decided = Counter(labels[0] for _, labels in rows if len(labels) == 1)
    if decided:
        print("\n  the tuples that fire exactly one label resolve to:")
        for label, n in decided.most_common():
            print(f"    {n:4d}  {label}")

    ambiguous = [labels for _, labels in rows if len(labels) > 1]
    if ambiguous:
        pairs = Counter()
        for labels in ambiguous:
            for a, b in itertools.combinations(sorted(labels), 2):
                pairs[(a, b)] += 1
        print("\n  most frequent label collisions:")
        for (a, b), n in pairs.most_common(8):
            print(f"    {n:4d}  {a} + {b}")

    print("\n  per-label firing rate:")
    marginal = Counter()
    for _, labels in rows:
        marginal.update(labels)
    for label in LABELS:
        print(
            f"    {marginal[label]:4d}  ({100.0 * marginal[label] / total:5.1f}%)  {label}"
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--variants",
        action="store_true",
        help="also report the two readings that try to repair the table",
    )
    args = ap.parse_args()

    report("The table read literally", [(t, fires(t)) for t in tuples()])

    if not args.variants:
        return

    # Repair 1: `architecture` read to cover whole-codebase scope as well, since
    # whole-codebase is a wider blast radius than the multi-file the cell names.
    report(
        "Variant: architecture also covers scope=whole-codebase",
        [(t, fires(t, architecture_includes_whole_codebase=True)) for t in tuples()],
    )

    # Repair 2: `standard-pr` read as "nothing extreme" -- the default branch
    # that fires only when no other row does. This is the reading that makes the
    # table total; it is a decision about the table, not something it states.
    defaulted = []
    for t in tuples():
        labels = [x for x in fires(t) if x != "standard-pr"]
        defaulted.append((t, labels or ["standard-pr"]))
    report("Variant: standard-pr as the default branch", defaulted)


if __name__ == "__main__":
    main()
