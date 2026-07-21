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

Uses the run-shim implementation selected by [[elite_stage0_task_4]]. Waits only on the agent identity and the kill sheet per §7a — it may run concurrently with the viability canary, though a falsified canary stops this probe at its next safe checkpoint.

**Max-usage constraint (§0a):** the spike's real-Claude allowance belongs to probe 1. The real shim→Claude transition evidence comes from probe 1's authorized minimal invocation ([[elite_stage0_task_3]] records it); this probe's destructive race matrix uses a **harmless long-running exec surrogate** in place of `claude` — the process-identity mechanics under test don't depend on the binary being Claude.

## Task

In a disposable directory with a throwaway tmux socket, per the kill sheet:

- Start a trivial shim, then the exec surrogate, through the fixed-wrapper shape (`tmux new-session` → shim → `setsid` → `execve`); measure PID/PPID/PGID/SID/executable continuity end to end. Cross-check the happy-path topology against the real shim→Claude observation recorded by probe 1.
- From a separate (maintainer-side) observer, attempt the §4.1 binding: candidate pane PID from tmux → independent process-table validation against expected topology.
- Inject the fault matrix: pane death **before** observation; launcher death mid-launch; a replacement pane appearing with the same name; stop races (kill the observed target while a look-alike survives).
- For each fault, record whether the observer can distinguish "my incarnation" from an impostor or a successor, and what evidence discriminates.
- Close against the kill sheet.

## Acceptance Criteria

- **User-run:** measurement rows for the happy path and every injected fault; an explicit verdict on whether pane identity + process-table validation suffices as the §4.1 binding, or the redirect is taken; fixtures checked in and re-runnable.
- The result names the concrete binding procedure (or its impossibility) that the measured revision ([[elite_stage0_task_8]]) will encode.
