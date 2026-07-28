---
title: "spike: write the kill sheet (probe 0) — falsifier, thresholds, caps, and redirects for every Stage-0 probe"
priority: urgent
size: 3
status: done
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

Design §7a priority 0 (dev_docs/auto-pilot-e-lite-design-2026-07-21.md). Rule 1 plus row 0: before writing any fixture, write the falsifier, pass threshold, `inconclusive` condition, evidence required, time cap, dependent work, and redirect for each probe. Probe tasks cite this sheet as their start gate — none starts without its row. A result is useful only if it changes what gets built next. The kill sheet is the contract every subsequent probe closes against — `confirmed` / `falsified` / `inconclusive`, no fourth state, no automatic extension (§0a).

## Task

Create the spike evidence directory `dev_docs/elite-spike/` (per the plan's binding evidence layout) with `kill-sheet.md` covering:

- **Probe 1 — dedicated-user viability canary** ([[elite_stage0_task_3]])
- **Probe 2 — tmux/process-binding spike** ([[elite_stage0_task_5]])
- **Probe 3 — real alert walking skeleton** ([[elite_stage0_task_6]])
- **Opportunistic — `setsid→execve` topology** ([[elite_stage0_task_4]])
- **Opportunistic — Max-window coherence** ([[elite_stage0_task_7]])

For each: falsifier, pass threshold, `inconclusive` condition, evidence required, time cap (default half-day per §7a rule 3), dependent work it blocks, and the named redirect (copy the redirect column from §7a's table, sharpened to be executable). Each probe's "evidence required" field cites the plan's binding sanitization checklist **in full** (§7a rule 4 plus the plan's additional redactions — account password, push-provider tokens/topics/user keys/device identifiers, hostnames, home paths), never a subset. Also include a **probe-0 self-row** carrying the same seven fields as every other row, with: falsifier = a probe row that cannot yield a binary confirmed/falsified call; redirect = tighten the decision rule before any implementation work (§7a row 0). Record the tranche-1 packing decision: which probes run in the first working day, per the dependency graph (canary ∥ alert skeleton first; topology before process-binding). Record the **push-provider choice** (ntfy vs Pushover) with rationale — probe 3 executes against whichever this sheet names.

Include a `measurements.md` skeleton (probe → fixture command/test → sanitized evidence link → non-secret environment metadata → result → decision) that later probe tasks fill in.

## Acceptance Criteria

- **Code-enforced:** `dprint check` passes on the new markdown.
- Kill sheet exists with all seven fields for all five probes, plus the probe-0 self-row (same seven fields).
- Each probe row's redirect quotes or cites its §7a table-row redirect (sharpening permitted, substitution not).
- `measurements.md` skeleton exists with the six columns above.
- Push provider (ntfy or Pushover) named with a one-paragraph rationale.
- Tranche-1 packing recorded.
- Reviewed and merged as a PR (docs-only; the spike contract permits checked-in evidence scaffolding).
