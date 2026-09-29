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
    python3 enumerate-label-table.py --variants  # plus four readings and the bound

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


MECHANICAL_BULK_READINGS = ("inclusive", "mechanical-only", "conjunctive", "deleted")


def fires(
    t, *, architecture_includes_whole_codebase=False, mechanical_bulk="inclusive"
):
    """The table read literally, keeping only what the seven dimensions express.

    `standard-pr` carries three conjuncts, not two -- "`standard` complexity,
    `pr-sized`, **nothing extreme**" -- so it is evaluated last and fires only
    when no other row did. Dropping that third conjunct is what an earlier
    revision of this script did, and it inflated the ambiguity count by 9
    tuples: the row then fired beside every extreme row that happened to share
    its complexity and scope.

    Two cells say things no dimension carries, and both are dropped here rather
    than guessed at:

      * `architecture`'s "or a genuinely hard bug" -- there is no bug dimension.
      * `mechanical-bulk`'s "high-volume simple work" -- a gloss on
        `cost_sensitivity: high`, not a further input. Its "and/or" is read as
        the inclusive or it spells.

    That second one is the load-bearing reading in the headline counts, so
    `mechanical_bulk` exists to price it. Its first three values are the three
    things "A and/or B" can be taken to mean, and none of them is the obviously
    right one:

      * `inclusive` (the default) -- `A or B`, which is what "and/or"
        conventionally spells, and what the headline counts use.
      * `mechanical-only` -- `A` alone, on the argument that the trailing gloss
        "high-volume simple work" disqualifies the `cost_sensitivity: high`
        disjunct, since the rubric also assigns `high` to work merely "only
        worth doing cheaply", which can be hard or creative rather than bulk.
      * `conjunctive` -- `A and B`, the "and" half read as binding.

    Pricing all three is the point. They are strictly nested, so the narrowest
    of them does bound the other two -- what it cannot bound is a reading not on
    the list, and narrower ones exist (binding the gloss to `creativity: low`
    fires 32 tuples; adding `scope: single-file` fires 8, both strictly inside
    `conjunctive`). The list does not terminate, so the fourth value is where a
    bound over readings in general has to come from.

      * `deleted` -- the row does not fire at all. Not a reading: every reading
        fires some subset of `inclusive`'s tuples, and ambiguity falls as that
        set shrinks, so this run brackets all of them.
    """
    if mechanical_bulk not in MECHANICAL_BULK_READINGS:
        raise ValueError(
            f"mechanical_bulk must be one of {MECHANICAL_BULK_READINGS}, "
            f"got {mechanical_bulk!r}"
        )
    out = []
    arch_scopes = {"multi-file"}
    if architecture_includes_whole_codebase:
        arch_scopes.add("whole-codebase")
    if t["complexity"] == "hard" and t["scope"] in arch_scopes:
        out.append("architecture")
    mechanical = t["complexity"] == "mechanical"
    cost_sensitive = t["cost_sensitivity"] == "high"
    if mechanical_bulk == "inclusive":
        bulk = mechanical or cost_sensitive
    elif mechanical_bulk == "conjunctive":
        bulk = mechanical and cost_sensitive
    elif mechanical_bulk == "deleted":
        bulk = False
    else:
        bulk = mechanical
    if bulk:
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
    # Last, and only into an empty list: "nothing extreme" is the row's own
    # third conjunct, not a tie-break this script is imposing.
    if not out and t["complexity"] == "standard" and t["scope"] == "pr-sized":
        out.append("standard-pr")
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
        help="also report the four alternative readings and the row-deleted bound",
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

    # Readings 3 and 4: the two narrower things `mechanical-bulk`'s "and/or"
    # can mean. Unlike the two repairs above, neither is more faithful than the
    # inclusive default -- the row is genuinely ambiguous -- so these price the
    # default rather than replacing it.
    #
    # Their figures are prices and NOT bounds, which is the whole reason both
    # are here. The two are nested, so the narrower does bound the wider -- but
    # neither bounds a reading absent from this list, and narrower ones exist.
    # So adding readings one at a time can never establish a floor, and quoting
    # whichever happens to be lowest as one is the mistake this pair exists to
    # make visible.
    for reading in ("mechanical-only", "conjunctive"):
        report(
            f"Variant: mechanical-bulk read as {reading}",
            [(t, fires(t, mechanical_bulk=reading)) for t in tuples()],
        )

    # The bound, which is not a reading. Monotonicity does the work: every
    # reading fires some subset of `inclusive`'s tuples, and ambiguity falls
    # while silence rises as that set shrinks -- so this run brackets every
    # reading, enumerated here or not, from both sides at once. It is reported
    # rather than asserted because the record's load-bearing claim quotes it.
    report(
        "Bound (not a reading): mechanical-bulk deleted outright",
        [(t, fires(t, mechanical_bulk="deleted")) for t in tuples()],
    )


if __name__ == "__main__":
    main()
