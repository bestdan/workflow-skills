---
created: 2026-11-12
question: "How much gate time would choosing test units from a branch's diff save, and how often would the selection miss a failing unit?"
feeds: "https://github.com/bestdan/workflow-skills/issues/1492, https://github.com/bestdan/workflow-skills/issues/1495"
---

# Choosing checks from the diff: replaying merged PRs (2026-11-12)

**On the last 100 merged PRs, the selector `just check --changed` ships with
gave 74 a subset, which ran in a median of 142s against 204s for the full
gate.** Four slow bats files set that median. Narrowing the rule that selects
them would cut it to about 41s, but would skip tests that read markdown
without naming it, so the broad rule stays.

These measurements were taken for #1492 (the simulation) and #1495 (the
shipped selector, PR #1503) on the 18-core Mac, 2026-11-12. They lived in
`dev_docs/gate-performance.md` until #1504 moved them here.

## The shipped selector

Replaying the last 100 merged PRs through `select()` in
`scripts/check-select.py` as shipped: 74 got a subset, 16 the full gate (11 for
infrastructure, 5 for a file no test names), and 10 touched only `dev_docs/`.
The subset runs selected a median of 13 of 136 units and took a median of 142s
against 204s for the full gate, about 127 minutes saved over the 100. 32 of the
74 finished within 60s. A trial run after a one-line edit to
`scripts/check-cache.py` selected one unit and took 11s. The times are upper
bounds, taken from one run with every unit in parallel; a subset runs with
less contention.

The median is high because four bats files of 142–167s each
(`guard-pinned-branch`, `worktree-create-hook`, `co-review-ledger-sync`,
`utilities`) were selected 8–12 times each, mostly through the directory rule
matching a skill's bare directory name such as `references`.

## The narrower rule that was rejected

Narrowing the directory rule for markdown would cut the median to about 41s.
Tests reach markdown without naming it in at least three ways:

- a glob: `test/coreview-conventions.bats` loops over
  `"$SKILL"/reviewers/*.md`;
- a path join: `scripts/test-local-review.sh` builds
  `os.path.join(..., "references", "threads.md")`;
- a recursive grep of the skills tree: `test/auto-pilot-reserve.bats`.

Tightening it needs its own replay, measured against the files each test
actually opens rather than against name matches.

## Simpler rules simulated first (#1492)

On the same PRs, before the selector was built: 3 units and 20s median when
only tests naming the file count, 4 units and 36s with the package and skill
rules added. Following every chain of files that name each other selected 121
units, so the selector never follows chains.

## Misses on failed CI runs

Replaying the 12 failed CI runs whose logs carry the current
`check.sh: FAIL (...)` summary, the selection missed 5 failing units and caught
3. All 5 misses were unrelated to their diffs: four `test-spawn-orchestrator.sh`
wall-clock and launchctl failures, and one `test-local-review.sh` that could not
bind its address. The sample is small, which is the main reason `--changed`
stays an edit loop rather than becoming the gate.
