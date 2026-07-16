---
title: Reserve-sized near-cap threshold + per-task usage instrumentation (hardening #2)
priority: medium
size: 3
status: new
created: 2026-07-15
source_branch: worktree-bestdan+cao-usage-adapter
related_files:
  - skills/auto-pilot/references/run-budget.md
  - skills/auto-pilot/references/run-state.md
  - scripts/claude-usage.sh
is_blocked_by: cao_usage_task_3
parent: cao_usage
tags: [auto-pilot, run-budget, hardening]
---

Part of [[cao_usage_plan]].

## Context

The near-cap pause currently fires on a fixed threshold (owned by [[cao_usage_task_3]]). Codex #2: a fixed `85%` is only a starting approximation — the real policy is a **reserve** sized to the worst-case next orchestrator turn plus a margin: *"start another Claude turn only if remaining quota exceeds worst-case turn cost plus uncertainty margin."* Sizing needs data, so this task both defines the reserve formula and instruments the per-cycle consumption that tunes it.

## Task

- Define `reserve` in `run-budget.md`: pause when `100 - percent < reserve`, where `reserve = max(fixed_floor, observed_worst_task_delta * safety)`. The **conservative fixed floor** (~15%) + `--reserve` override already ship in task 3; this task adds the measured term.
- **Instrument** per-task Claude consumption: at each post-task rate-window read, record the delta in `percent` consumed since the previous read into `RUN.md` (a small rolling record — e.g. `usage_deltas: [..]`, capped length), documented in `run-state.md`. This is the data the reserve's `observed_worst_task_delta` reads back — no external metrics store.
- **Window-tag every sample.** `percent` is per-window and resets to ~0 at `resets_at`, so a delta straddling a reset is negative/meaningless and would corrupt the reserve. Record each sample with its `reset_epoch` (already computed for the pause path / [[cao_usage_task_5]]) and **only compute a delta between two reads in the same window**; discard (don't record) a cross-window delta, and reset the baseline when the window changes or a reading is invalid.
- Compute `observed_worst_task_delta` as a high-percentile (e.g. p90/max) of the recorded **in-window** deltas; until at least **N = 5** samples exist, fall back to the fixed floor. Document the formula and `N` in `run-budget.md`.
- Note the worker's Codex/Antigravity usage is explicitly **excluded** — only the orchestrator's Claude deltas are budgeted (matches the user's constraint).

## Acceptance Criteria

**Code-enforced**
- `run-budget.md` documents the reserve formula and the fixed-floor default + `--reserve` override.
- `run-state.md` documents the `usage_deltas` rolling record (capped length, `reset_epoch` tag, purpose).
- The reserve falls back to the fixed floor when fewer than **N = 5** in-window samples exist.
- A delta that straddles a window reset is discarded, not recorded (covered by a `--from-file` fixture feeding two readings across a reset).

**User-run**
- After a multi-task run, `RUN.md` shows recorded `usage_deltas`; a subsequent cycle's pause decision uses the observed worst delta, visibly larger than the fixed floor when tasks were heavy.
