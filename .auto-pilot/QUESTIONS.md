# Questions — auto-pilot/2026-11-02-gh-issue-proof-1024

## Q1 — #1063 — adopt the crashed worker's uncommitted diff, or re-dispatch a coder

- **Options:** adopt the diff left uncommitted in `task-1063` (written 06:41, before the crash) | discard it and re-dispatch codex
- **Call:** adopt it, after diff judgment against the issue's acceptance criteria
- **Why:** the diff is scoped to the two files the issue names and meets both criteria. Re-dispatching would spend a coder run to reproduce it.
- **Reversible:** yes. The PR is unmerged and the commit can be dropped.

## Q2 — #1063 — hand off despite a red local `just check`

- **Options:** park #1063 | hand off and let PR CI decide
- **Call:** hand off. PR #1357's CI passed (Deterministic gate, Host-sensitive suites (macOS)).
- **Why:** both broker runs failed only in suites a doc and docstring edit cannot reach: `test_session_state_hook.py` `test_fast` timing (1.14–1.27s vs a 1.0s threshold), a `BrokenPipeError` in `test-task-dashboard-agent-remote.sh`, and eval-tree-guard bats test 201 (mangled bytes in `also PASS`). `test-local-review.sh` server races failed once and passed on the rerun. These look like host-load or locale flakes in the broker environment, and they would recur on every task.
- **Reversible:** yes. The PR is unmerged.

## Q3 — #1063 — co-review could not run

- **Options:** another round | no further round
- **Call:** deferred to the human. No co-review round ran: `reach-pr-branch.sh` returned `ROUTE: none reason=config-failed` because `worktree-config.sh`'s `/usr/bin/env` shebang is execve-denied in the jail, so `/co-review --non-interactive` cannot reach the PR branch's tree from the run worktree.
- **Why:** review is advisory in `/deliver-task`, so the run proceeded to hand-off. The diff is two short doc passages; a human read of PR #1357 is the review.
- **Reversible:** yes. Run `/co-review 1357` attended from `task-1063`.
