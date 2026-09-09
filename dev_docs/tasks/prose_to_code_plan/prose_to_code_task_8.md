---
title: Add spawn-orchestrator.sh reserve-gate for the usage_delta bookkeeping
priority: high
size: 5
impact: 5
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - skills/auto-pilot/references/run-budget.md:64-133
  - skills/auto-pilot/references/run-state.md:135-158
  - skills/deliver-task/SKILL.md:60-101
  - scripts/spawn-orchestrator.sh
  - scripts/claude-usage.sh
  - scripts/lib/spawn-orchestrator-test-prelude.sh
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - auto-pilot
---

Plan: [[prose_to_code_plan]] · Index row 5.

## Context

The reserve gate is specified twice (`run-budget.md`, `run-state.md`) and
executed by hand at four `/deliver-task` boundaries: compare the new
`reset_epoch` to the stored baseline; append a delta only when epochs match and
the delta is non-negative; cap `usage_deltas` at 20; clear the baseline on a
negative delta or a failed read; once five same-window samples exist,
`reserve = max(fixed_floor, max(usage_deltas) * 1.25)`; `headroom = 100 -
percent`; pause when `headroom < reserve`. No script computes any of it
(`rg usage_delta scripts/` is empty). `claude-usage.sh` reads the window;
`supervisor-gate` already gates on `paused_until`.

`RUN.md` is written by one orchestrator process at a time by design, so a
read-modify-write with an atomic replace is the assumed model; see the plan's
open question if the boundaries can overlap.

## Task

1. Add `spawn-orchestrator.sh reserve-gate --run-md <path> --percent <p>
   --reset-epoch <e> [--floor <n>] [--samples <n>]` (also accept `--read-failed`
   for the fail-closed branch). It rewrites the `usage_delta_baseline` /
   `usage_deltas` frontmatter fields in place, prints
   `RESERVE: reserve=<n> headroom=<n> verdict=proceed|pause samples=<n>`, and
   exits 0/1 for proceed/pause, 2 on malformed input. Atomic replace via temp
   file + `mv`, matching how the other subcommands write `RUN.md`.
2. Tests in a new `scripts/test-spawn-orchestrator-reserve.sh` using the shared
   prelude: epoch mismatch discards, negative delta clears, cap at 20,
   below-five-samples uses the floor, the pause verdict, read-failed records
   nothing.
3. Rewrite the four boundaries in `deliver-task/SKILL.md` to one call each, and
   collapse the two reference specifications to a pointer at the subcommand's
   header comment (one home for the formula).

## Acceptance Criteria

- Code-enforced: `just check` passes; the six cases are pinned.
- Code-enforced: `rg -n 'usage_delta' skills/` finds only pointers to the
  subcommand, not the update rules.
- User-run: a detached auto-pilot run's `RUN.md` shows `usage_deltas` growing
  by at most one entry per task boundary.
