# prose_to_code — parallel work plan

Plan scaffolding; task 15 graduates and deletes it with the folder.

## Waves

```
Wave 1 (no blockers, start today)        Wave 2 (one blocker)       Wave 3
 4  linear-graph-analyze        3  ─┐
 5  gh-issue-graph --sort       2  ─┴─►  6  body_references   3  ─┐
 1  jira-resolve-transition     3  ───►  9  acquire-ref       2   │
 8  reserve-gate                5  ───► 10  preflight --scout 2   ├─► 15  graduate + delete  1
14  _linear_rank module         3  ───► 13  kanban-classify   3   │
 7  linear-pr-resolve           5  ──────────────────────────────┤
 2  diff-anchor-check           2  ──────────────────────────────┤
 3  tutorial-root-guard         1  ──────────────────────────────┤
11  false-closures --from-mcp   2  ──────────────────────────────┤
12  check-reproducibility       2  ──────────────────────────────┘
```

- 15 PRs, 39 points. 10 start today (30 points), 4 in wave 2, 1 cleanup.
- Critical path: 4/5 → 6 → 15, or 14 → 13 → 15. Three merge rounds deep.
- Why the wave-2 edges exist: 6 edits both graph scripts; 9 and 1 both edit
  `jira-claim.md`; 10 and 8 both edit auto-pilot references; 13 imports the
  module 14 creates.

## Schedule, no WIP limit

| Round | Tasks                              | Points | Starts when                                  |
| ----- | ---------------------------------- | ------ | -------------------------------------------- |
| 1     | 1, 2, 3, 4, 5, 7, 8, 11, 12, 14    | 30     | now, all ten at once                         |
| 2     | 6, 9, 10, 13                       | 10     | each as its own blocker merges, not as a set |
| 3     | 15                                 | 1      | the last of the other 14 merges              |

Round 2 is not a barrier: 9 can start the moment 1 merges, while 7 and 8 are
still open. Wall-clock is bounded by the slowest chain, 4/5 → 6 → 15 or
14 → 13 → 15, and by review capacity — ten PRs green at once means ten
co-reviews and ten rebases the moment the first one lands.

## PR sequencing rules

1. Every PR branches from `main`; none are stacked. The repo squash-merges,
   so a stacked child loses its base when the parent lands. Wave-2 work starts
   after its blocker merges.
2. Inside wave 1, merge in whatever order goes green. Prioritise review on
   4, 5, 14 when they are ready — each unblocks a wave-2 task — and on 7 and
   8, the size-5s, so they do not become the tail.
3. After every merge, open PRs rebase on `origin/main`. Conflicts are expected
   only on the pairs that are already edges (1→9, 8→10, 4/5→6). Any other
   conflict is a surprise worth a look.
4. The PR title is the release lever: each `feat(<scope>):` merge bumps a minor
   version, so about 14 releases. To cluster releases, hold green PRs and merge
   by round. Task 15 is `docs:` and ships nothing.
5. Same gate per PR: `just check`, non-interactive `/co-review`, PR-fix guard
   around any review push. No PR is done without its
   `scripts/test-<name>.sh` pair.
6. The `is_blocked_by` edges are what `/do-tasks` and auto-pilot read, so the
   graph enforces the ordering without a human remembering it.
