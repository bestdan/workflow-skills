---
title: "verify: run the verify command outside the jail (resolve sandbox↔verify conflict)"
priority: 2
size: 3
status: done # merged as PR #172 (detached run #2, 2026-07-11)
created: 2026-07-10
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/launch-runtime.md
  - skills/auto-pilot/references/run-state.md
  - scripts/spawn-orchestrator.sh
  - commands/deliver-task.md
is_blocked_by: autopilot_hardening_task_1
parent: autopilot_hardening
tags: [auto-pilot, sandbox, verify, p1]
---

[[autopilot_hardening_plan]]

## Context

Findings **P1 #4, #6** — the most substantive design issue from run #1. The run's
declared verify command (`bash scripts/check.sh`, pre-flight §5) **cannot pass
inside the jail**: `check.sh` runs `bash scripts/test-*.sh`, and under the two-layer
jail those harnesses fail on `execve` of `#!/usr/bin/env bash` scripts-under-test
(`bad interpreter: Operation not permitted`, exit 126) **regardless of the diff** —
they fail identically on pristine `main`. Nested `sandbox-exec` (codex's own
`--sandbox`, `test-spawn-orchestrator.sh`, `smoke-confinement.sh`) compounds it
(#6). The orchestrator degraded gracefully (decision Q6: ran the content gates that
execute, classified harness failures as environmental, flagged "re-run outside the
jail" in each PR body) — correct, but it means **"definition of done" is never
reached in-jail** and a human must remember to re-run.

**Resolution (DECIDED — verify outside the jail via an out-of-jail broker).**
Run `check.sh` outside the jail so it completes fully and the run's verify is
authoritative again, keeping the two walls that held (write-confinement,
network-egress) tight rather than widening exec to run every repo script.

**Mechanism correction (load-bearing — the naive framing is impossible).** A
`sandbox-exec`-confined process's **children inherit the profile** — the jailed
orchestrator *cannot* spawn an un-sandboxed subprocess. So "verify outside the
jail" requires an **out-of-jail broker**, and the natural home is the **launchd
supervisor that already exists for relaunch** (why this task is `is_blocked_by`
task 1's launch/plist work):

1. The jailed orchestrator writes a **verify-request sentinel** into the worker
   worktree — the worktree path + the **pinned** command (the fixed
   `verify_command` resolved at pre-flight, plus a hash so the broker never runs
   an agent-composed string).
2. The **unjailed supervisor** (outside seatbelt) polls for the sentinel, runs the
   pinned command in that worktree, and writes a **result file** back into the
   worktree.
3. The orchestrator reads the result and treats *that* as the authoritative gate;
   the in-jail content gates become a fast pre-check, not the definition of done.

**Codex sub-sandbox (finding #6, folded here).** `codex --sandbox
workspace-write` nests seatbelt inside the orchestrator's jail. Inside the jail,
invoke codex with its **own sandbox disabled** — the outer jail already confines it
and is strictly stronger. Document this as a one-line policy (here or task 2) so it
isn't re-derived at 3am.

## Task

- Add the **verify broker** to the launchd supervisor path in
  `scripts/spawn-orchestrator.sh` (built on task 1's launch/plist): the
  sentinel-handshake protocol above, with **command pinning** (fixed
  `verify_command` + hash; never an agent-authored command) and execution **only**
  in the worker worktree.
- Wire the orchestrator's per-task verify step (`SKILL.md`, `deliver-task.md`) to
  drop the request and consume the result.
- Set the **codex-own-sandbox-off** policy where coders are invoked.
- Document the posture + its **trust boundary** in `launch-runtime.md` ("Sandbox
  profile") and `SKILL.md`: verify runs un-sandboxed and therefore executes the
  diff-under-test with full privileges + network — acceptable because it is the
  same trust the human extends re-running `check.sh` before merge, moved earlier,
  and mitigated by command-pinning + worktree-only execution.
- Update `run-state.md`'s `verify_command` note; drop the per-task "re-run check.sh
  outside the jail" PR-body caveat once verify actually runs there.

**Third option (follow-up, not v1):** the broker could wrap verify in its **own
fresh single-layer** profile (repo RO, worktree+tmp RW, exec subpath over the repo,
no network) — tighter than fully unjailed. But the two `test-*.sh` harnesses that
themselves call `sandbox-exec` still won't nest, so ship the **unjailed broker**
in v1 and leave the thin verify-jail as a tracked follow-up.

## Acceptance Criteria

**Code-enforced:**
- A test (or a documented manual harness) shows the verify command running to
  completion (`check.sh` exit 0 on a clean tree) via the out-of-jail path, where
  the same command exits 126 under the jail.
- `bash scripts/check.sh` green.

**User-run:**
- A scratch detached run delivers a task whose verify **fully passes** without a
  human needing to re-run `check.sh` — the PR body reports a complete gate, not a
  partial one.
