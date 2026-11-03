---
created: 2026-11-03
---

# Auto-pilot gh-issue run, end to end

**Run 2026-11-03** on the Mac mini (macOS, `local-full`), installed plugin
2.181.0, against milestone #16 of this repo. This is the record for
[issue #1024](https://github.com/bestdan/workflow-skills/issues/1024). It
answers whether a gh-issue `/auto-pilot` run can be launched, survive a crash,
and hand off. It can, with hand-assembled launch inputs and one gap: no
co-review ran before hand-off. That gap fails one of the issue's four
acceptance criteria.

## The run

- **Source:** milestone #16, holding #1063, #1064 and #1066, all `est:1`,
  `status:2_ready` and `auto:eligible`. #1066 was given a native `blocked_by`
  edge on #1064 for the test.
- **Run:** `auto-pilot/2026-11-02-gh-issue-proof-1024`, whose run files
  ([`RUN.md`, `QUESTIONS.md`, `REPORT.md`](https://github.com/bestdan/workflow-skills/tree/auto-pilot/2026-11-02-gh-issue-proof-1024/.auto-pilot))
  are the "run QUESTIONS.md" cited below; `--until`
  2026-11-03T13:33:12Z, coder `codex` for both tasks, reviewer set codex plus
  Claude plus reconciler (`min_task_budget` 24m), verify pinned to `just check`
  through the verify broker.
- **Ready at launch:** #1063 and #1064. `gh-issue-ready.py` held #1066 back as
  blocked on #1064.
- **Crash:** at 11:40:46Z the launchd job was booted out while #1063 was
  `claimed`. Its lock ref `bestdan/task-1063` sat at `origin/main` with no
  commits, and the issue was on `status:3_started`, assigned.
- **Resume:** `/auto-pilot 16 --resume` at 11:44Z. The stale-orchestrator
  guard, `alarm-clear`, `clear-exit-state`, the doctor (7 of 7 invariants ok),
  the auth and fingerprint probes and the scout all passed. Base freshness
  blocked until local `main` was fast-forwarded. #1063 matched crash row G1.
- **End:** both tasks `handed-off` (#1063 as PR #1357, #1064 as PR #1359), and
  `orchestrator.done` written, about 12:50Z.

## Acceptance criteria

| Criterion                                                                                                        | Result | Evidence                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Every task ends `handed-off`, issue on `status:4_needs_review`, PR carries `Closes #<n>`, no stray `task/*` refs | pass   | Both RUN.md rows read `handed-off`. #1063 went to `status:4_needs_review` at 12:24:54Z and #1064 at 12:50:22Z. PRs #1357 and #1359 each open with `Closes #<n>`. `git ls-remote origin 'refs/heads/task/*'` returned nothing. #1066 is not among them: see "The blocked issue" below.                                                                    |
| The forced crash resumes to the same end state with no duplicate PR                                              | pass   | One PR for `bestdan/task-1063` (#1357). The resumed orchestrator found the worker's uncommitted diff from before the crash and adopted it rather than dispatching a second coder (run QUESTIONS.md Q1).                                                                                                                                                  |
| Each issue's `status:4_needs_review` is written after that PR's co-review                                        | fail   | No co-review ran in the run. #1357 opened at 12:17:08Z and its issue moved at 12:24:54Z; #1359 opened at 12:42:05Z and its issue moved at 12:50:22Z. In between, `/co-review --non-interactive` could not reach the PR branch from inside the jail, so every review class was skipped and the hand-off went ahead (run QUESTIONS.md Q3). Filed as #1364. |
| Nothing was merged or closed by the run                                                                          | pass   | Neither PR was merged and no issue was closed while the run was live. Both PRs were co-reviewed and merged afterwards by the owner's session, which is outside the run.                                                                                                                                                                                  |

### The blocked issue

The issue asked for one task blocked by another "to exercise the stacked base".
The gh-issue adapter does not stack:
[`adapters/gh-issue.md`](../../skills/auto-pilot/references/adapters/gh-issue.md)
works only the unblocked frontier, because a blocker stays open until its PR
merges. So #1066 cannot reach `handed-off` in the same run, and the first
criterion's "every task" can only cover the ready ones. What the run did check
is that #1066 was left alone. After the run it was still `status:2_ready`,
unassigned, with no branch.

## Defects found

Each is filed as its own issue.

| Issue | Defect                                                                                                                                                                                       |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #1364 | Co-review cannot reach the PR branch from inside the jail (`reach-pr-branch.sh` → `worktree-config.sh` exec refused), so every PR hands off unreviewed. Related to #706.                     |
| #1365 | `/deliver-task` never uses the verify broker. The run used it only because the hand-written orchestrator prompt spelled out the handshake.                                                   |
| #1366 | No command composes a production launch. Profile grants, PATH, labels and the orchestrator prompt were all assembled by hand, and the first broker PATH lacked shfmt, shellcheck and dprint. |
| #1367 | The launch smoke test (`claude -p 'ok' --max-turns 1`) failed three times on resume with working auth, because the model used its one turn on a tool call.                                   |
| #1369 | `launch` writes the orchestrator PID only to its handle file, so `RUN.md`'s `orchestrator_pid` stays empty and the resume guard has nothing to read.                                         |
| #1370 | Crash row G1 says "resume `implementing`", but the run loop picks only `pending` tasks. The resume prompt had to tell the orchestrator to continue #1063.                                    |
| #1371 | Launch writes run state into a separate run worktree, which the foreign-write guard refuses. The run used the launching session's own worktree as the run worktree instead.                  |
| #1372 | The crush reviewer's version pin (v0.98.1) is behind the installed v0.98.2, so the gate skips crush on every co-review.                                                                      |

Two more things are true of this harness but are not defects in this repo:

- Network egress inside the jail is unfiltered. `spawn-orchestrator.sh`'s header
  says so: macOS refuses a nested Seatbelt profile, so the harness's own egress
  allowlist cannot run inside the jail. Filesystem and exec confinement held.
- The verify broker's `just check` failed on this host in suites the two diffs
  do not touch (`test-task-dashboard-agent-remote.sh` with a `BrokenPipeError`,
  among others). The orchestrator handed off on PR CI instead and logged the
  call (run QUESTIONS.md Q2). CI was green on both PRs.

## After the run

Both PRs were co-reviewed with the owner attended and then merged: #1357 after
two rounds (the second round's fixes added `task-scan.py --expired` in place of
a `jq` pipe that hid the scanner's fail-closed exit), and #1359 after one.
#1066's test-only `blocked_by` edge was removed and #1066 taken off milestone
#16, leaving the milestone with no open issues.
