---
type: epic
title: CAO usage-aware orchestrator — pause-before-limit, resume-on-reset
status: active
owner: Daniel Egan
created: 2026-07-15
---

## Goal

Let a **Claude orchestrator** advance a flat list of coding tasks by dispatching each to a **CAO worker** (Codex / Antigravity, via `cao-run`) instead of coding itself, and — because the orchestrator is the *only* Claude-quota consumer in this setup — **pause before it hits Claude's 5-hour rate window and auto-resume once the window resets**, unattended and overnight.

The pause/resume half is **not new**: it reuses auto-pilot's existing `scripts/claude-usage.sh` + `scripts/spawn-orchestrator.sh` (checkpoint-exit → external timer → shell gate) verbatim. This plan adds only (a) a CAO dispatch mode for the per-task action and (b) four reserve-gate hardening tweaks surfaced by the Codex (gpt-5.6-sol) design review.

## Scope / non-goals

**In scope**
- A `--dispatch cao-run` mode for the auto-pilot run loop: per task, resolve a coder via `select-coder`, run `cao-run` in an isolated worktree, harvest + verify the diff, hand off.
- Reusing the **plan adapter** as the flat-list task source (a plan dir with no `is_blocked_by` edges *is* a flat list — no new adapter).
- Four run-budget hardening tweaks: pre-invoke reserve check, reserve-sized threshold, `reset_epoch` validation, resume grace.

**Non-goals**
- Any usage tracking for Codex/Antigravity — the user does not care about worker-side limits. Only **Claude** (the orchestrator) is budgeted.
- Rebuilding pause/resume/backoff/crash-recovery — reused from auto-pilot unchanged.
- A brand-new top-level skill. This **extends** `skills/auto-pilot/`.
- CAO-side co-review by default (keeps per-task Claude spend low); opt-in only.

## Approach

**Depth 1 — ride the existing supervisor AND the existing delivery skill; couple narrowly.** (Resolved: Codex + Fable both picked this over a lean bespoke loop — see Resolved decisions.) Nothing about pause/resume or delivery is rebuilt. The auto-pilot supervisor kernel (`spawn-orchestrator.sh` gate/relaunch/classify + `claude-usage.sh`), the run-state machine, and `/deliver-task`/`orchestrate-coders` are all reused. The changes are configuration + knobs, not a new execution loop:

1. **Task source** = a `plan-with-docs` dir routed through the **existing plan adapter** (`skills/auto-pilot/references/adapters.md` "plan adapter"). "Flat list" = no `is_blocked_by`. No new adapter code. (Resolved: reuse, decision #2.)
2. **Coder backend** = register the CAO launcher (`cao-run`) as `orchestrate-coders` **custom `command:` coders** — one *named* entry per (backend, model), since the custom-command contract has no model placeholder — so `/deliver-task` → `orchestrate-coders` dispatches the implement step to a CAO worker (Codex/Antigravity). `/deliver-task` is otherwise unchanged.
3. **"Less Claude" knobs** (Fable) — where the actual savings live: (a) **co-review off / dialed** for CAO-routed tasks — the bulk of per-task Claude spend; (b) the orchestrator's **diff-judgment on a cheaper tier** (Sonnet/Haiku subagent, bounded checklist) — note the shell `verify_command` isn't an LLM turn, so the spend to cut is the orchestrator's accept/iterate/park read of the diff — keeping Opus only for park/escalate. The orchestrator still judges every diff (Resolved: decision #3) — that's the one thing kept.

`cao-server` (the local CAO session daemon) is a **runtime prerequisite**, not a managed component: checked at launch and re-verified on each resume via auto-pilot's capability-join re-check (fail-closed), never auto-restarted — the same posture as the existing auth/base-freshness prerequisites.

**Why not Depth 2 (a lean `cao-run` loop bypassing `/deliver-task`).** Per-task Claude spend is dominated by implementation (offloaded to CAO in *both* depths) and co-review (a knob in Depth 1). Depth 2 only shaves the small claim/PR/hand-off bookkeeping sliver — at the cost of the largest, riskiest new component (a duplicate delivery/recovery loop a solo maintainer must keep in sync). Reconsider only on real per-task cost data.

The four hardening tweaks are additive to `run-budget.md` and benefit **all** of auto-pilot, not just CAO.

## Tasks

1. [[cao_usage_task_1]] — CAO coder backend for `orchestrate-coders`: named custom `command:` wrappers mapping `{SPEC}`/`{WORKTREE}` → a CAO worker via `cao-run`.
2. [[cao_usage_task_2]] — Auto-pilot "less-Claude" run profile: select the CAO named coder + co-review off/dialed + cheaper-tier diff-judgment, threaded launch → `RUN.md` → resume.
3. [[cao_usage_task_3]] — Pre-invoke reserve check: gate **before** each Claude-heavy turn (claim / verify / co-review), not only after each task (hardening #1); owns the fixed reserve + `--reserve`.
4. [[cao_usage_task_4]] — Reserve-sized near-cap threshold + per-task usage instrumentation (window-tagged), replacing the fixed % (hardening #2).
5. [[cao_usage_task_5]] — `reset_epoch` validation + resume grace for clock skew, in `claude-usage.sh` / the pause writer (hardening #3 + #4).
6. [[cao_usage_task_6]] — Graduate-then-delete: migrate durable design into `dev_docs/cao_usage.md`, delete the plan scaffolding.

## Resolved decisions

1. **Dispatch depth → Depth 1** (CAO as an `orchestrate-coders` custom coder; `/deliver-task` unchanged). Both Codex and Fable picked it: "less Claude" is captured by offloading implementation (both depths) + disabling co-review (a Depth-1 knob), so Depth 2's bespoke loop would only shave cheap bookkeeping at high code/maintenance risk.
2. **Flat-list source → reuse the plan adapter** (a `plan-with-docs` dir, no `is_blocked_by`). Zero new adapter code.
3. **Verification → the orchestrator judges** each diff (on a cheaper tier per Fable's knob #2). The one delivery step kept on Claude — it's the only way to distinguish a real success from a stalled/empty worker.
4. **Threshold → conservative fixed reserve first** (owned by task 3), instrument per-task deltas, graduate to the measured reserve later (task 4).

## Open questions

- None blocking. Revisit Depth 2 only if real per-task Claude cost data (task 4's instrumentation) shows Depth 1 + knobs misses the target.
