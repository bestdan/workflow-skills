#!/usr/bin/env python3
"""Pick the one LOW reason a tracker promoter records for a candidate.

The model decides which Confidence-check conditions failed; this script turns
those names into the reason string. Written by the model, the string drifted:
glosses appended to it, two reasons joined into one, and the first failure
recorded on an issue estimated over size 5, where `task-fill.md` -> **Reason
precedence** gives the slot to the split reason (bestdan/workflow-skills#1571).

The names and strings are read from the Confidence check table in
`skills/task/SKILL.md` at run time, so that table stays their only home. A
table this script cannot parse is an error, not an empty map.

Rules applied, in order:

1. An estimate over 5 records the `scope_fits` reason, whatever else failed.
2. Otherwise the failed check that comes first in the table wins. The order
   the names are passed in does not matter.

`size_valid`'s `<n>` placeholder is filled with `--estimate`, or `None` when
none is given, as `scripts/task-scan.py` renders a missing size.

    promote-reason.py [--estimate N] CHECK [CHECK ...]

Prints the reason on stdout. Exit 2 on an unknown check name, on no failed
check with no over-ceiling estimate, or on an unreadable table.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

SKILL = Path(__file__).resolve().parents[3] / "skills" / "task" / "SKILL.md"
CEILING = 5
SCOPE_CHECK = "scope_fits"
ROW = re.compile(r"^\|\s*`(?P<name>[a-z_]+)`\s*\|.*\|\s*`(?P<reason>[^`]+)`\s*\|\s*$")


class ReasonError(Exception):
    pass


def load_table(path: Path = SKILL) -> List[Tuple[str, str]]:
    """The Confidence check rows as (name, LOW reason), in table order."""
    rows: List[Tuple[str, str]] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not in_table:
            in_table = line.startswith("| Name") and "LOW reason" in line
            continue
        if not line.startswith("|"):
            break
        m = ROW.match(line)
        if m:
            rows.append((m.group("name"), m.group("reason")))
    if not rows or SCOPE_CHECK not in dict(rows):
        raise ReasonError(f"no Confidence check table with {SCOPE_CHECK} in {path}")
    return rows


def pick(
    failed: Sequence[str],
    estimate: Optional[int],
    table: Optional[List[Tuple[str, str]]] = None,
) -> str:
    rows = table if table is not None else load_table()
    reasons = dict(rows)
    unknown = [name for name in failed if name not in reasons]
    if unknown:
        raise ReasonError(
            f"unknown check name(s): {', '.join(unknown)}; known: {', '.join(reasons)}"
        )
    if estimate is not None and estimate > CEILING:
        return reasons[SCOPE_CHECK]
    for name, reason in rows:
        if name in failed:
            return reason.replace("<n>", str(estimate))
    raise ReasonError("no failed check and no estimate over 5: the candidate is HIGH")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checks", nargs="*", help="failed Confidence check names")
    parser.add_argument("--estimate", type=int, help="the honest size estimate")
    args = parser.parse_args(argv)
    try:
        print(pick(args.checks, args.estimate))
    except (ReasonError, OSError) as exc:
        print(f"promote-reason: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
