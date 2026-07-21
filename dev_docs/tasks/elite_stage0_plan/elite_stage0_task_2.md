---
title: "spike: write the kill sheet (probe 0) — falsifier, thresholds, caps, and redirects for every Stage-0 probe"
priority: urgent
size: 3
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by:
parent: elite_stage0
tags: [e-lite, spike, stage-0, kill-sheet]
---

Plan: [[elite_stage0_plan]]

## Context

Design §7a priority 0 (dev_docs/auto-pilot-e-lite-design-2026-07-21.md). Rule 1: before writing any fixture, write the falsifier, pass threshold, `inconclusive` condition, evidence required, time cap, dependent work, and redirect for each probe. A result is useful only if it changes what gets built next. The kill sheet is the contract every subsequent probe closes against — `confirmed` / `falsified` / `inconclusive`, no fourth state, no automatic extension (§0a).

## Task

Create the spike evidence directory (default `dev_docs/elite-spike/` — see plan open question) with `kill-sheet.md` covering:

- **Probe 1 — dedicated-user viability canary** ([[elite_stage0_task_3]])
- **Probe 2 — tmux/process-binding spike** ([[elite_stage0_task_5]])
- **Probe 3 — real alert walking skeleton** ([[elite_stage0_task_6]])
- **Opportunistic — `setsid→execve` topology** ([[elite_stage0_task_4]])
- **Opportunistic — Max-window coherence** ([[elite_stage0_task_7]])

For each: falsifier, pass threshold, `inconclusive` condition, evidence required, time cap (default half-day per §7a rule 3), dependent work it blocks, and the named redirect (copy the redirect column from §7a's table, sharpened to be executable). Also record the tranche-1 packing decision: which probes run in the first working day, per the dependency graph (canary ∥ alert skeleton first; topology before process-binding). Record the **push-provider choice** (ntfy vs Pushover) with rationale — probe 3 executes against whichever this sheet names.

Include a `measurements.md` skeleton (probe → result → evidence link → decision) that later probe tasks fill in.

## Acceptance Criteria

- **Code-enforced:** `dprint check` passes on the new markdown.
- Kill sheet exists with all seven fields for all five probes; no probe task may start without its row (this is the gate the probe tasks cite).
- Tranche-1 packing recorded.
- Reviewed and merged as a PR (docs-only; the spike contract permits checked-in evidence scaffolding).
