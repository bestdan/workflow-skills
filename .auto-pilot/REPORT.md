# Report — auto-pilot/2026-11-02-gh-issue-proof-1024

**Done: 2 handed-off, 0 parked, 0 skipped.** Neither PR got a co-review (item 1 below).

Source: milestone #16 on bestdan/workflow-skills. Ready at launch: #1063, #1064.
Held back: #1066, blocked_by #1064. The gh-issue adapter works only the
unblocked frontier, so #1066 waits for a later run, after #1064's PR merges.

Resumed 2026-11-03T11:44Z after a forced crash while #1063 was `claimed`. #1063
matched G1, so its delivery continued under the existing claim and adopted the
crashed worker's uncommitted diff (Q1).

## Outcomes

- #1063 — **handed-off**: PR #1357, issue at `status:4_needs_review`. Check 5 now describes `cards` as a status-keyed dict and gives the `jq` extraction.
- #1064 — **handed-off**: PR #1359, issue at `status:4_needs_review`. co-review's validator rule is scoped to fixtures changed in the diff, and disputed out-of-diff claims are marked unverified instead of tested.

## Evidence

- #1063 / PR #1357: the `jq` filter was exercised against the documented shape and the empty-scan shape, and the shape was checked against the emitter in `scripts/task-scan.py`. PR CI passed. The local broker `just check` failed twice in unrelated suites (Q2).
- #1064 / PR #1359: in the broker run, `validate.py` was OK with co-review/SKILL.md at 500 lines, the hard cap. PR CI passed. The broker failed only in the same flaky suites (Q4).

## Review classes per PR

- PR #1357: no co-review ran. Every class was skipped because the jail cannot route to the PR branch's tree (Q3).
- PR #1359: same as #1357 (Q4).

## Spend

Usage reads failed (`claude-usage.sh`: no session window), so every reserve gate returned `fallback`. Bounds and the proxy are in `skills/auto-pilot/references/run-budget.md`.

## Follow-ups

None filed.

## For you

1. **Neither PR was co-reviewed (Q3, Q4).** In the jail, `reach-pr-branch.sh` calls `worktree-config.sh`, whose `/usr/bin/env` shebang is execve-denied, so `/co-review --non-interactive` cannot reach a PR's tree. Review #1357 and #1359 by hand, or run `/co-review <pr>` attended. This affects every jailed gh-issue run until the helper is invoked through `bash`.
2. **The local verify broker is red on host-flaky suites (Q2, Q4).** `test_session_state_hook.py` `test_fast` (a 1.0s timing threshold), `test-task-dashboard-agent-remote.sh` (`BrokenPipeError`), eval-tree-guard bats 201 and, intermittently, local-review server tests fail on this host while CI is green. The run handed off on CI's word. A "no" means both PRs should have been parked.
3. **#1066 is next.** It becomes ready once #1064's PR #1359 merges. Re-run `/auto-pilot gh-issue:bestdan/workflow-skills#milestone/16` then.
4. **Housekeeping.** The local branches `bestdan/task-1063` and `bestdan/task-1064` remain (their PRs are open). A stray local branch `bestdan/task-1064-wip` (identical to `main`, no commits) was left behind because force-deleting it needs confirmation. Delete it with the plugin's `branch-remove.sh`. Separately, `deliver-task` step 1 could not fetch into local `main`, which is checked out at `~/src/workflow-skills`, so #1064 was claimed from `origin/main` (d74c6b2c).
