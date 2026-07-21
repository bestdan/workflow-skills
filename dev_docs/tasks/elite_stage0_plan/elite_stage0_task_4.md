---
title: "probe: capture `setsid(2) → execve` topology — select the run-shim implementation"
priority: high
size: 2
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: [elite_stage0_task_1, elite_stage0_task_2]
parent: elite_stage0
tags: [e-lite, spike, stage-0, probe, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

Design §7a "two cheap probes" + §4.1. The run shim either calls `setsid(2)`/`execve(2)` itself or uses a pinned maintainer-owned helper — the measurement spike selects which. This probe captures the PID/PGID/SID/executable transition **before** the broader tmux race matrix, and its result feeds probe 2 ([[elite_stage0_task_5]]). It does not by itself establish control-plane identity.

Also gathers the Stage-0 incarnation-identity evidence (§4.1): is `{pid, ps lstart, pgid, sid, executable}` stable and discriminating on this macOS version, or is a stronger identity needed? Second-granularity `lstart` is not a kernel attestation — measure what actually distinguishes PID reuse and the shim-to-Claude transition.

## Task

In a disposable directory as `agent`, per the kill sheet:

- Write a trivial shim that records its own `{pid, pgid, sid, starttime}`, calls `setsid(2)`, then `execve`s a long-running child; capture the identity tuple at each step from an outside observer (`ps`, `proc_pidinfo` if needed).
- Repeat with the pinned-helper variant (e.g. `/usr/bin/setsid`-equivalent — macOS lacks one, so document what the helper would be) and compare.
- Measure PID/PGID/SID/executable continuity across the `execve`; determine what an external observer can verify about "this Claude process is the one my shim started."
- Test discriminability: kill the child, force PID reuse pressure, and check whether the tuple distinguishes the incarnations.
- Close against the kill sheet and record the run-shim selection decision.

## Acceptance Criteria

- **User-run:** measurement table row with the observed tuples per step; a written selection (shim calls `setsid` itself vs pinned helper) with rationale; a statement of the strongest stable incarnation identity available, or `falsified`/`inconclusive` with the fail-closed consequence for §4.1 named.
- Fixture script checked in and re-runnable.
