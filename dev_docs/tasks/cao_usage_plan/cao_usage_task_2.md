---
title: Auto-pilot "less-Claude" run profile — CAO coder + co-review/verify knobs
priority: high
size: 3
status: new
created: 2026-07-15
source_branch: worktree-bestdan+cao-usage-adapter
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/run-state.md
  - skills/auto-pilot/references/resume.md
  - skills/deliver-task/SKILL.md
  - skills/co-review/SKILL.md
is_blocked_by: cao_usage_task_1
parent: cao_usage
tags: [auto-pilot, cao, less-claude]
---

Part of [[cao_usage_plan]].

## Context

With the CAO coder available ([[cao_usage_task_1]]), a CAO run is stock auto-pilot with three run-level settings that (a) route implementation to CAO and (b) cut the orchestrator's own Claude spend — Fable's two knobs. No loop or delivery changes; `/deliver-task` runs unchanged, just parameterized.

Auto-pilot resolves the coder set and reviewer set once at Launch (`SKILL.md` Launch step 3) via `select-coder` / `.co-review.yml`, records them in `RUN.md`, and passes them down. The run loop calls `/deliver-task` per task; `/deliver-task` runs `orchestrate-coders` (coder) then non-interactive co-review then hand-off.

## Task

- Add a launch-time **"less-Claude" run profile** (e.g. `--profile less-claude` or discrete flags) that sets, and records in `RUN.md` (document fields in `run-state.md`):
  - **Coder** = the CAO **named** coders from task 1 (not a single `cao` entry). Set `orchestrate-coders`' `default_coder` to a CAO entry and map each task's `select-coder` result to the matching named coder (`codex:*`→`cao-codex`, `agy:*`→`cao-agy`) — model is baked per named entry, so there is no per-task model placeholder. **Constrain `select-coder`'s candidate set for a CAO run to the CAO fleet (`codex`/`agy`)** so it never returns `opus`/`devin`, which have no CAO coder and would otherwise force a re-dispatch loop or park.
  - **Co-review** = off (default for this profile), or dialed to a single cheap pass (knob #1 — the bulk of the savings). If `/deliver-task` exposes no "skip co-review" control today, **adding one is part of this task**; build on co-review's existing `--non-interactive` + reviewer-set selection (`skills/co-review/SKILL.md`).
  - **Cheaper-tier diff-judgment** (knob #2). Mechanics matter: `/deliver-task`'s *verify* is the task's **shell `verify_command`**, not an LLM turn — so the Claude spend to cut is the **orchestrator reading and judging the resulting diff** (accept / iterate / park). Delegate that diff-judgment to a bounded **cheaper-tier subagent** (Sonnet/Haiku) with a checklist, keeping Opus only for park/escalate decisions. This needs a real hook — a model override for the diff-judgment step in `/deliver-task` — **not** just a recorded flag; specify where that hook lives.
- Thread all three through Launch → `RUN.md` → run loop → `--resume` (resume reads them back; no new reconciliation rows — these don't change task phases).
- Keep the pre-flight fail-closed: the less-claude profile requires the CAO coder prerequisites from task 1 (cao/cao-server present + running); missing → block launch.
- Leave the default (non-CAO) auto-pilot behavior **untouched** when the profile isn't set.

## Acceptance Criteria

**Code-enforced**
- `run-state.md` documents the run-profile fields (named CAO coder, co-review mode, diff-judgment tier) with defaults.
- A default auto-pilot launch is byte-for-byte unchanged in behavior (the profile is opt-in).
- The less-claude profile records the CAO coder mapping, co-review off, and the diff-judgment tier in `RUN.md`, and `--resume` re-reads them.
- The diff-judgment step **actually runs on the cheaper tier** (asserted, e.g. the spawned judgment subagent's model), not merely recorded as a setting.
- The pre-flight blocks launch when `cao`/`cao-server` are absent (fail-closed); on `--resume`, `cao-server` presence is re-verified via the capability-join re-check.

**User-run**
- A flat plan dir launched with the less-claude profile drives each task through `/deliver-task` where implementation runs on a CAO worker, co-review is skipped, and the orchestrator's diff-judgment runs on Sonnet/Haiku — with per-task Opus usage visibly lower than a stock run (compare `usage_deltas`, task 4).
- **One full pause→exit→relaunch→resume cycle under the CAO profile** completes: the run checkpoints on low reserve, exits, the timer relaunches past reset, the resume re-verifies `cao-server`, and the next task dispatches to a CAO worker — proving the reused supervisor path works unchanged for CAO dispatch.
