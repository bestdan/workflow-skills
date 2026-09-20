#!/usr/bin/env python3
"""Build the predictive-scope corpus from this repository's own closed issues.

An artifact of `dev_docs/research/2026-09-20-predictive-scope/`, not standing tooling.
It is dev-only: never invoked by a skill or command at runtime, never part of
`just check`.

The corpus pairs a task card with the blast radius the work actually had. Each case is
an issue whose text existed BEFORE the pull request that closed it, paired with that
pull request's changed-file count.

    python3 build-corpus.py --from-api > measurement/corpus.json   # needs gh
    python3 build-corpus.py --profile measurement/corpus.json      # no network

Why the provenance filter is the whole point. Section 3 of
`../../2026-09-17-jev-applications.md` scored `scope` against 116 merged pull requests
and measured 71.6% exact, with 31 of 33 misses under-reading blast radius. That run is
post-hoc: ground truth was the file count of a merged pull request, so every case
handed the model a change that had already happened. `assess-task` runs before the work
exists. A corpus that let a card be written, or rewritten, after its pull request
would reproduce exactly the defect this record exists to remove, and would do it
invisibly.

So `created_at(issue) < created_at(pull request)` is a gate, not a nicety, and cases
failing it are dropped rather than repaired.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter

REPO = "bestdan/workflow-skills"

# The design's cut points, from `../../../designs/2026-09-18-assess-task-typed-profile.md`.
# They are the contract, not an implementation detail: the section "The cut points,
# since 'bucket boundaries' is not a specification" makes them part of the design so
# they cannot silently diverge from what was measured. The input is the changed-file
# count alone — the ~300-line figure in that design's "Task size" never entered the
# measurement and is not an input here.
LEVELS = ("single-file", "pr-sized", "multi-file", "whole-codebase")


def bucket(changed_files: int, tracked_files: int | None = None) -> str:
    """Changed-file count to level.

    `whole-codebase` is the one cut with no measurement behind it — the design
    proposes "changed files >= half the tracked files" and says plainly that it
    invented the rule. It is implemented here so the boundary is inspectable, and the
    record reports that no case in this corpus reaches it.
    """
    if tracked_files and changed_files >= tracked_files / 2:
        return "whole-codebase"
    if changed_files == 1:
        return "single-file"
    if changed_files <= 5:
        return "pr-sized"
    return "multi-file"


def _gh_json(args: list[str]) -> list[dict]:
    out = subprocess.run(
        ["gh", *args, "--repo", REPO], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def fetch() -> tuple[list[dict], dict[int, dict]]:
    issues = _gh_json(
        [
            "issue",
            "list",
            "--state",
            "closed",
            "--limit",
            "200",
            "--json",
            "number,title,body,createdAt,closedByPullRequestsReferences",
        ]
    )
    prs = _gh_json(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            "400",
            "--json",
            "number,title,createdAt,mergedAt,changedFiles,additions,deletions",
        ]
    )
    return issues, {p["number"]: p for p in prs}


def build(issues: list[dict], prs: dict[int, dict]) -> dict:
    cases, dropped = [], Counter()
    for issue in issues:
        refs = issue.get("closedByPullRequestsReferences") or []
        merged = [prs[r["number"]] for r in refs if r["number"] in prs]
        if not merged:
            dropped["no merged pull request closed it"] += 1
            continue
        # Only a pull request opened AFTER the card can be the work the card forecast.
        # One opened before it — #430's PR 415, opened two days before the issue and
        # later edited to reference it — is not, whatever it went on to touch. Among the
        # survivors take the first to open: a later one was written with the earlier
        # one's work already visible. The first version of this rule took the earliest
        # opener BEFORE filtering, selected that pre-card PR for #430, and then dropped
        # the whole case at the provenance check — which the record then misreported as
        # the gate catching a rewritten card. Review caught it.
        after = [p for p in merged if issue["createdAt"] < p["createdAt"]]
        if not after:
            dropped["no closing pull request postdates the card"] += 1
            continue
        pr = min(after, key=lambda p: p["createdAt"])
        body = (issue.get("body") or "").strip()
        if not body:
            dropped["card has no body to judge"] += 1
            continue
        cases.append(
            {
                "id": f"issue-{issue['number']}",
                "issue": issue["number"],
                "pr": pr["number"],
                "title": issue["title"],
                "card": body,
                "issue_created": issue["createdAt"],
                "pr_created": pr["createdAt"],
                "changed_files": pr["changedFiles"],
                "churn": pr["additions"] + pr["deletions"],
                "label": bucket(pr["changedFiles"]),
            }
        )
    cases.sort(key=lambda c: c["issue"])
    return {
        "repo": REPO,
        "cases": cases,
        "dropped": dict(dropped),
        "note": (
            "Ground truth is the changed-file count of the pull request that closed "
            "the card. Cards are as filed; see the record for the residual edit risk."
        ),
    }


def profile(corpus: dict) -> str:
    cases = corpus["cases"]
    counts = Counter(c["label"] for c in cases)
    lines = [f"cases: {len(cases)}", "", "ground truth:"]
    for level in LEVELS:
        mark = "   <-- no instance" if counts[level] == 0 else ""
        lines.append(f"  {level:<15} {counts[level]:>3}{mark}")
    lines += ["", "dropped:"]
    for reason, n in sorted(corpus["dropped"].items()):
        lines.append(f"  {reason:<38} {n:>3}")
    if cases:
        top = counts.most_common(1)[0]
        lines += [
            "",
            f"majority-class base rate: {top[1]}/{len(cases)} "
            f"= {top[1] / len(cases):.1%} (always answer {top[0]!r})",
            "",
            "A result must clear that base rate, not 25%. Section 3's 71.6% was "
            "measured\nagainst a different distribution and the two are not comparable.",
            "",
            "changed-file counts: "
            + ", ".join(
                str(c["changed_files"])
                for c in sorted(cases, key=lambda c: c["changed_files"])
            ),
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-api", action="store_true", help="rebuild via gh")
    mode.add_argument("--profile", metavar="CORPUS", help="describe a built corpus")
    args = p.parse_args(argv)

    if args.profile:
        with open(args.profile) as fh:
            print(profile(json.load(fh)))
        return 0

    issues, prs = fetch()
    # indent=2 matches dprint, which formats the committed corpus. A rebuild that
    # emitted anything else would show up as a whole-file diff against evidence that
    # had not changed.
    json.dump(build(issues, prs), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
