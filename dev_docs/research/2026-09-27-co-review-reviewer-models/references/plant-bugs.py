#!/usr/bin/env python3
# Plant the two fixture bugs into PR #913's diff.
#   gh pr diff 913 --repo bestdan/workflow-skills > orig.diff
#   python3 plant-bugs.py orig.diff bug.diff
# No dependencies. Fails if either anchor is missing, so a changed diff cannot
# silently yield a fixture with fewer than two bugs.
import sys

src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
plants = [
    # 1. doctor(): `break` ends the whole task loop at the first skipped row.
    (
        "      skipped) continue ;;    # claim refused — holds nothing this run acquired",
        "      skipped) break ;;       # claim refused — holds nothing this run acquired",
    ),
    # 2. status_report(): the one-line summary prints the parked count as skipped.
    (
        "parked=$counts_parked skipped=$counts_skipped delta=",
        "parked=$counts_parked skipped=$counts_parked delta=",
    ),
]
for old, new in plants:
    assert s.count(old) == 1, old
    s = s.replace(old, new)
open(dst, "w").write(s)
