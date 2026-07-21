---
title: "probe 2: tmux/process-binding spike — bind a launch to one live incarnation despite the agent-owned tmux server"
priority: urgent
size: 3
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: [elite_stage0_task_1, elite_stage0_task_2, elite_stage0_task_4]
parent: elite_stage0
tags: [e-lite, spike, stage-0, probe, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

Design §7a priority 2 + §4.1. Key assumption: the maintainer can uniquely bind a requested launch to one live incarnation and stop only that incarnation, despite the tmux server being agent-owned (its answers are claims, not facts). Falsification redirect: abandon pane identity as authority; evaluate a directly held maintainer guardian/process handle or a different session host **before** designing leases. Stage 2 cannot begin while this is unresolved.

Uses the run-shim implementation selected by [[elite_stage0_task_4]]. Waits on the agent identity, the kill sheet, and that run-shim selection (§7a orders topology before process-binding) — it may run concurrently with the viability canary, though a falsified canary stops this probe at its next safe checkpoint.

**Max-usage constraint (§0a):** the spike's real-Claude allowance belongs to probe 1. The real shim→Claude transition evidence comes from probe 1's authorized minimal invocation ([[elite_stage0_task_3]] records it); this probe's destructive race matrix uses a **harmless long-running exec surrogate** in place of `claude` — the process-identity mechanics under test don't depend on the binary being Claude.

## Task

In the probe workspace `/Users/agent/spike/probe2/` (plan's probe-workspace convention) with a throwaway tmux socket at `/Users/agent/spike/probe2/tmux.sock` (never a production socket path), per the kill sheet (`dev_docs/elite-spike/kill-sheet.md`). The uid split is the boundary under test: the tmux server, shim, and surrogate run as the `agent` user (interactive `sudo -u agent`, per the plan's agent-access note); the observer runs as the maintainer uid — running both sides as one user dissolves the boundary. Time cap: half a working day (§7a rule 3); at the cap, stop and classify.

- Start the run-shim implementation selected by [[elite_stage0_task_4]] (reused as-is), then the exec surrogate (e.g. `/bin/sleep 3600`, or any argv-distinctive long-running binary), through the fixed-wrapper shape (`tmux new-session` → shim → `setsid` → `execve`); measure PID/PPID/PGID/SID/executable continuity end to end. Cross-check the happy-path topology against the real shim→Claude observation recorded by probe 1.
- From a separate (maintainer-side) observer, attempt the §4.1 binding: candidate pane PID from tmux → independent process-table validation against expected topology.
- Inject the fault matrix: pane death **before** observation; launcher death mid-launch; a replacement pane appearing with the same name; stop races (kill the observed target while a look-alike survives).
- For each fault, record whether the observer can distinguish "my incarnation" from an impostor or a successor, and what evidence discriminates.
- Close per the plan's probe close protocol.

## Acceptance Criteria

- **User-run:** one summary row for this probe appended to `dev_docs/elite-spike/measurements.md` (per the plan's evidence layout), linking a per-scenario fault matrix at `dev_docs/elite-spike/fixtures/probe2/fault-matrix.md` (one row per scenario: fault injected, observer evidence gathered, discriminated yes/no, discriminating evidence), with raw captures under `dev_docs/elite-spike/fixtures/probe2/` sanitized per the plan checklist (macOS + tmux versions recorded as environment metadata).
- The probe closes with exactly one of `confirmed`/`falsified`/`inconclusive` against the kill sheet's pre-written falsifier, pass threshold, and inconclusive condition; `inconclusive` triggers §7a rule 6 (changed kill sheet, redirect, or defer). The classification carries an explicit verdict on whether pane identity + process-table validation suffices as the §4.1 binding, or the redirect is taken.
- Fixtures checked in; a single documented command reproduces the full happy-path + fault matrix from a clean checkout per the plan's reproducibility convention (tmux installed).
- The result names the concrete binding procedure (or its impossibility) that the measured revision ([[elite_stage0_task_8]]) will encode.
