# Report — auto-pilot/2026-11-02-gh-issue-proof-1024

Source: milestone #16 on bestdan/workflow-skills. Ready at launch: #1063, #1064.
Held back: #1066, blocked_by #1064. The gh-issue adapter works only the
unblocked frontier, so #1066 waits for a later run.

Resumed 2026-11-03T11:44Z after a forced crash while #1063 was `claimed`. #1063
matched G1, so its delivery continued under the existing claim.

## Outcomes

- #1063 — **handed-off**: PR #1357, issue at `status:4_needs_review`. Check 5 now describes `cards` as a status-keyed dict and gives the `jq` extraction.
- #1064 — pending.

## Evidence

- #1063 / PR #1357: the `jq` filter was exercised against the documented shape and the empty-scan shape, and the shape was checked against the emitter in `scripts/task-scan.py`. PR CI passed. The local broker `just check` failed twice in unrelated suites (QUESTIONS Q2).

## Review classes per PR

- PR #1357: no co-review ran. Every class was skipped because the jail cannot route to the PR branch's tree (QUESTIONS Q3).

## Spend

Usage reads failed (`claude-usage.sh`: no session window), so every reserve gate returned `fallback`. Bounds and the proxy are in `skills/auto-pilot/references/run-budget.md`.

## Follow-ups

None filed.

## For you

1. **Co-review is unreachable from the jail (Q3).** It applies to every task in this run: `reach-pr-branch.sh` → `worktree-config.sh` has a `/usr/bin/env` shebang the jail execve-denies. Review PR #1357 by hand, or run `/co-review 1357` attended from the `task-1063` worktree. If you do nothing, PRs hand off with no automated review.
2. **The local verify broker is red on flaky suites (Q2).** `test_session_state_hook.py` `test_fast` (a 1.0s timing threshold), `test-task-dashboard-agent-remote.sh` (`BrokenPipeError`) and eval-tree-guard bats test 201 fail on this host while CI is green. A "no" here means a task the run handed off on CI's word should have been parked.
