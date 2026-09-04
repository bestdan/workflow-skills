#!/usr/bin/env python3
"""Assert every tracked .py file sits in exactly one typecheck.sh tier.

The gap this closes, twice over in one PR: `scripts/local-review/server.py` was
checked at the contributor floor when consumers execute it, and
`scripts/bump-version.py` and `scripts/coreview-rule-drift.py` matched no tier at
all, so mypy never read either. Both were invisible because tier membership was
a hand-maintained list and nothing compared it to reality.

## Why this is an exhaustive partition, not an allowlist

A check with a suppression list drifts into an excuse. This one instead requires
the four tier arrays plus EXCLUDED_FROM_TYPECHECK to *partition* the tracked .py
files: every file lands in exactly one, or the gate fails naming it. Excluding a
file is then a visible declaration in typecheck.sh with a comment beside it,
rather than an absence nobody can see. There is no heuristic here and nothing to
tune — the answer is derived from `git ls-files` and the arrays themselves.

Failing on files in *two* tiers matters as much as zero: the tiers differ by
`--python-version`, so a double-listed file would be checked against two
different floors and the stricter result silently ignored.

Run directly: python3 scripts/tier-coverage.py
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPECHECK = ROOT / "scripts" / "typecheck.sh"
ARRAYS = (
    "STRICT_FILES",
    "CONSUMER_FILES",
    "DEV_FILES",
    "TEST_FILES",
    "EXCLUDED_FROM_TYPECHECK",
)


def patterns(source: str, name: str) -> list:
    """The entries of one bash array literal, or [] if it is absent."""
    m = re.search(rf"^{name}=\(([^)]*)\)", source, re.M)
    if not m:
        return []
    # Strip comments and blank tokens; entries are bare paths or globs.
    body = re.sub(r"#[^\n]*", "", m.group(1))
    return body.split()


def tracked_python() -> list:
    """Every .py the repository would ship or keep — tracked, plus untracked
    files that are not ignored.

    `--others --exclude-standard` matters: a brand-new file is exactly when you
    want to hear that it has no tier, and that moment is before it is committed.
    Ignored paths stay out, so a scratch file never fails the gate.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "*.py",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"tier-coverage: git ls-files failed: {proc.stderr.strip()}")
    return proc.stdout.split()


def classify(files, by_array):
    """Split `files` into (uncovered, doubled) against the tier arrays.

    Pure, so the tests can drive it with a literal file list — no repository
    fixture, no subprocess, no temp checkout.
    """
    uncovered, doubled = [], []
    for f in files:
        hits = [
            name
            for name, pats in by_array.items()
            if any(f == p or Path(f).match(p) for p in pats)
        ]
        if not hits:
            uncovered.append(f)
        elif len(hits) > 1:
            doubled.append((f, hits))
    return uncovered, doubled


def main() -> int:
    source = TYPECHECK.read_text()
    by_array = {name: patterns(source, name) for name in ARRAYS}

    missing = [n for n, pats in by_array.items() if not pats]
    if missing:
        print(
            "tier-coverage: FAIL — no entries found for "
            + ", ".join(missing)
            + f" in {TYPECHECK.relative_to(ROOT)}.\n"
            "  Either the array was renamed or this script's list is stale; a "
            "silently-empty array would make every file look uncovered.",
            file=sys.stderr,
        )
        return 1

    uncovered, doubled = classify(tracked_python(), by_array)

    if not uncovered and not doubled:
        print("tier-coverage: OK")
        return 0

    print("tier-coverage: FAIL", file=sys.stderr)
    for f in uncovered:
        print(
            f"  ✘ {f}: in no tier — mypy never reads it.\n"
            "     Add it to the tier matching WHO RUNS IT (consumer floor if it "
            "ships and is executed as bare python3, dev otherwise), or to "
            "EXCLUDED_FROM_TYPECHECK with a reason.",
            file=sys.stderr,
        )
    for f, hits in doubled:
        print(
            f"  ✘ {f}: in {len(hits)} tiers ({', '.join(hits)}) — they pin "
            "different --python-version floors, so one result is silently "
            "discarded. Pick one.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
