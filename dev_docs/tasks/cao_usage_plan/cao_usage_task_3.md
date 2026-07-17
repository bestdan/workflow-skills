---
title: Pre-invoke reserve check — gate before each Claude-heavy turn (hardening #1)
priority: high
size: 3
status: new
created: 2026-07-15
source_branch: worktree-bestdan+cao-usage-adapter
related_files:
  - skills/auto-pilot/references/run-budget.md
  - skills/auto-pilot/SKILL.md
  - skills/deliver-task/SKILL.md
  - commands/deliver-task.md
  - scripts/claude-usage.sh
  - test/auto-pilot-reserve.bats
is_blocked_by:
parent: cao_usage
tags: [auto-pilot, run-budget, hardening]
---

Part of [[cao_usage_plan]].

Approved scope expansion: enforce the reserve gate inside `/deliver-task`,
which owns claim, verify, co-review, and iterate substeps; auto-pilot passes
the run state through rather than attempting to intercept opaque substeps.

## Context

Today the rate-window check runs **after** every task's state update (`run-budget.md` "Rate-window check" — "Run after every task's state update"). The Codex review's #1 finding: also check **before** each Claude-heavy operation, so a large turn never *starts* under-provisioned. The existing pre-invoke gate (`spawn-orchestrator.sh supervisor-gate`) is coarse — it gates process **relaunch** on `paused_until`; it does not gate individual operations inside a live orchestrator.

The expensive Claude turns inside one `/deliver-task` cycle are: claim, the orchestrator's per-task verify turn, and (if enabled) co-review. A worst case is a big verification turn on an oversized diff.

## Task

- **Own the reserve here.** Introduce a conservative fixed `reserve` (headroom floor, default ~15%) **and** a `--reserve <pct>` override **in this task** — [[cao_usage_task_4]] later replaces the fixed floor with a measured value, so nothing in this task forward-references task 4 (that would be circular: task 4 `is_blocked_by` this task).
- Add a **pre-dispatch reserve check** to the run loop (`SKILL.md` "Run phase") **before every Claude-consuming delivery step — `claim`, the verify turn, AND co-review** (the context above names all three as heavy; gating only claim+verify would still let a costly co-review start below reserve): query `scripts/claude-usage.sh --session-status`, and if `headroom (100 - percent) < reserve`, trigger the **existing** near-cap pause path (`run-budget.md` "Near-cap → pause + relaunch past reset") — checkpoint-exit with `paused_until`, do **not** start the step.
- Document this as a distinct hook point in `run-budget.md` "Rate-window check": it now runs both **pre-invoke** (this task) and **post-task** (existing), sharing one `claude-usage.sh` call per cycle where possible (cache the reading within a cycle; re-read across cycles).
- Preserve the failure-closed contract: a `claude-usage.sh` non-zero exit still falls back to the time/dispatch proxy, never blindly proceeds.
- Keep the error backstop authoritative: this is a *predictive* gate; an actual 429 during the turn is still classified by `supervisor-check`/`classify-exit` (state that explicitly, per the Codex review — the usage query is advisory).

## Acceptance Criteria

**Code-enforced**
- `run-budget.md` "Rate-window check" documents two hook points (pre-invoke + post-task) and states the usage query is advisory, the exit classifier authoritative.
- The pre-dispatch check reuses the existing near-cap pause path (no second pause implementation).
- A fixed `reserve` default + `--reserve <pct>` override exist and are documented **in this task** (no dependency on task 4).
- The gate fires before co-review as well as before claim and verify (covered by a test with co-review enabled and headroom below reserve).

**User-run**
- With headroom deliberately set below the reserve (e.g. a low `--reserve` override), a run checkpoints and exits **before** claiming the next task, writing `paused_until`, rather than starting and dying mid-turn.
