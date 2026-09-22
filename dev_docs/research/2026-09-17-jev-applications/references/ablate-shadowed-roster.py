#!/usr/bin/env python3
"""Score three rosters on the same manifest prompts, to separate two changes.

Section 1's numbers were measured with the roster built from `skills/*/SKILL.md`. That
was wrong for six names: where a `commands/<name>.md` sits beside a SKILL.md, the
command's `description` is what reaches the model's skill listing, and the SKILL.md one
routes nothing (`dev_docs/decisions/2026-09-21-command-descriptions-shadow-skill-descriptions.md`).
Four of the six are eval manifest rows.

Fixing the loader and re-running moved the `select-coder` vs `orchestrate-coders`
margin from ~0.42 to ~0.96 — but two things had changed at once, because #834 also
rewrote those command descriptions in the same window. This scores all three rosters so
each contribution is a column rather than an assumption, per the convention's rule 9.

  shadowed   the SKILL.md descriptions, as section 1 originally measured
  old-cmd    the command descriptions at BASE — surfaced, before #834
  new-cmd    the command descriptions at HEAD — surfaced, after #834

The row that matters is `old-cmd`: the correct strings, before the fix. If the typed
call had reported a misfire there, section 1's blindness claim would have been an
artifact of reading the wrong text.

An artifact of this record. Nothing imports it, no gate runs it, and it costs a
TypeSafe key and about 40 cents of requests. Run by path from the repository root:

    python3 dev_docs/research/2026-09-17-jev-applications/references/ablate-shadowed-roster.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

# The commit this branch was cut from: the last one before #834 rewrote the two
# command descriptions. Pinned rather than resolved, so the columns stay comparable
# however far main moves afterwards.
BASE = "be2847e"
PAIR = ("select-coder", "orchestrate-coders")
RUNS = 3

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]

spec = importlib.util.spec_from_file_location(
    "jev", HERE / "jev-description-collision.py"
)
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)


def at(rev: str, path: str) -> str | None:
    """A file's contents at a revision, or None when it did not exist there."""
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{rev}:{path}"],
        capture_output=True,
        text=True,
    )
    return out.stdout if out.returncode == 0 else None


def main() -> int:
    skill_files = {
        str(p): p.read_text() for p in sorted((ROOT / "skills").glob("*/SKILL.md"))
    }
    shadowed = jev.parse_descriptions(skill_files)

    def roster(rev: str) -> dict[str, str]:
        out = dict(shadowed)
        for name in list(out):
            text = at(rev, f"commands/{name}.md")
            if text:
                desc = jev.parse_one_description(text)
                if desc:
                    out[name] = desc
        return out

    rosters = {"shadowed": shadowed, "old-cmd": roster(BASE), "new-cmd": roster("HEAD")}
    key = jev.resolve_key(ROOT)
    rows = jev.load_manifest_cases(ROOT)

    print(f"{'roster':<10} {'misfires':<10} {PAIR[0]} vs {PAIR[1]}, {RUNS} runs")
    for name, criteria in rosters.items():
        margins, misfires = [], 0
        for _ in range(RUNS):
            out = jev.run_suite(key, criteria, rows, jev.DEFAULT_MARGIN, True)
            for record in out["results"]:
                if record["misfire"]:
                    misfires += 1
                if record["label"] == PAIR[0]:
                    margins.append(record["margin"])
        joined = ", ".join(f"{m:.3f}" for m in margins)
        print(f"{name:<10} {misfires:<10} {joined}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
