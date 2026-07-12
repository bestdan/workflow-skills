---
title: "doctor I5: \"provably safe\" must not mean \"git didn't error\""
priority: high
size: 2
status: new
created: 2026-07-12
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
  - skills/auto-pilot/references/run-state.md
is_blocked_by: [autopilot_hardening_task_14]
parent: autopilot_hardening
tags: [auto-pilot, doctor, data-loss, fail-closed, p1]
---

[[autopilot_hardening_plan]]

## Context

Found co-reviewing task 14 (#189). Invariant 5 removes orphan worker worktrees with `git worktree remove --force`, and its comment is emphatic: *"deleting a live one destroys work — the asymmetry is why every condition below must hold, not most of them."*

Each of those conditions **fails open when git itself errors**:

- `dirty="$(git -C "$wt" status --porcelain 2>/dev/null)"` → a git failure yields `""`, which reads as **clean**.
- `local_tip="$(git -C "$wt" rev-parse HEAD 2>/dev/null)"` → empty reads as **"no commits" → pushed=1**.
- `wtbranch="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null)"` → empty reads as **unmatched**.

So on a worktree whose true state is **unknown**, all guards pass. Tested empirically with a corrupted `.git` link over uncommitted work: the guards did all pass, removal **was attempted**, and the WIP survived only because `git worktree remove --force` **also** failed on the same corruption, and D5 correctly reported "FAILED to remove … left in place."

The protection was **accidental** — the destructive step happened to share the failure — not designed. "Provably safe" currently means "git didn't error."

The sibling defect (an unmatched branch treated as safe while a dispatch is live) was fixed in #189 with a liveness gate. This is the remaining half: the guards themselves must fail **closed**.

## Task

- Check the **exit code** of each git read I5 depends on. A failed read is **undetermined** → skip the prune and report it as skipped, consistent with the D2 posture already stated for I3/I6 ("undetermined never green-lights a destructive action").
- Distinguish "clean" from "could not determine cleanliness". Same for "no commits" vs "could not read HEAD".
- Keep the fix from #189 intact (unmatched branch requires a provably dead orchestrator).

## Acceptance Criteria

- A worktree whose git reads **fail** (corrupted `.git`, unreadable object store) is **skipped**, and the skip is reported — the prune is never *attempted*, rather than attempted-and-failing-by-luck.
- A genuinely orphaned, readable worktree with a dead orchestrator is still pruned (invariant 5 not disabled).
- Mutation check: reverting the rc checks makes the corrupted-worktree test attempt a removal and fail.
- `bash scripts/check.sh` green.
