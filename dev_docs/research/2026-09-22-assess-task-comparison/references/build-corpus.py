#!/usr/bin/env python3
"""Build the assess-task comparison corpus, and render the blind baseline prompt.

An artifact of `dev_docs/research/2026-09-22-assess-task-comparison/`, not standing
tooling. It is dev-only: never invoked by a skill or command at runtime, never part
of `just check`.

    python3 build-corpus.py > measurement/corpus.json          # no network
    python3 build-corpus.py --prompt issue-277                 # one filled prompt

The cards are the predictive-scope record's 50, which already passed its provenance
gate: each is an issue whose text existed before the pull request that closed it.
That record needed the closing pull request because `scope` was scored against its
changed-file count. None of the six dimensions measured here has such a count, so
every field describing the finished work is dropped rather than carried along. It
is hindsight, and a blind case list must not hold it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = (
    HERE.parents[1]
    / "2026-09-20-predictive-scope"
    / "references"
    / "measurement"
    / "corpus.json"
)
PROMPT = HERE / "measurement" / "baseline-prompt.md"

# What a case keeps. Everything else in the source describes the pull request that
# closed the card: `pr`, `pr_created`, `changed_files`, `churn`, and the `scope`
# `label` bucketed from them.
KEEP = ("id", "issue", "issue_created", "title", "card")

# The card is third-party text, fenced in the prompt between these two lines. A
# fence is only a hint, so a card containing either marker is refused rather than
# silently escaping it.
BEGIN = "=====BEGIN CARD====="
END = "=====END CARD====="

PLACEHOLDER = re.compile(r"\{(TITLE|CARD)\}")

# The template is everything in baseline-prompt.md after this line.
TEMPLATE_START = "<!-- template starts on the next line -->\n"


def build(source: dict) -> dict:
    cases = []
    for case in source["cases"]:
        for marker in (BEGIN, END):
            if marker in case["title"] or marker in case["card"]:
                raise ValueError(f"{case['id']} contains the fence marker {marker!r}")
        cases.append({key: case[key] for key in KEEP})
    return {
        "repo": source["repo"],
        # Relative to measurement/corpus.json, where this string is written.
        "source": "../../../2026-09-20-predictive-scope/references/measurement/corpus.json",
        "note": (
            "The predictive-scope corpus with every field describing the closing pull "
            "request removed. Cards are as filed; the edit risk that record names "
            "applies here unchanged."
        ),
        "cases": cases,
    }


def render(template: str, case: dict) -> str:
    # One pass, not str.format or chained str.replace: a card may carry braces of
    # its own, and a title holding "{CARD}" must not be filled by the second pass.
    return PLACEHOLDER.sub(lambda m: case[m.group(1).lower()], template)


def template_of(prompt_md: str) -> str:
    _, found, template = prompt_md.partition(TEMPLATE_START)
    if not found:
        raise ValueError("baseline-prompt.md has no template marker")
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--prompt", metavar="ID", help="print the filled prompt for one case"
    )
    args = parser.parse_args()

    corpus = build(json.loads(SOURCE.read_text()))
    if args.prompt is None:
        json.dump(corpus, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    by_id = {case["id"]: case for case in corpus["cases"]}
    if args.prompt not in by_id:
        print(f"no case {args.prompt!r}", file=sys.stderr)
        return 1
    sys.stdout.write(render(template_of(PROMPT.read_text()), by_id[args.prompt]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
