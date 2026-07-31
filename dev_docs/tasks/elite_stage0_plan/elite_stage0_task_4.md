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

In the probe workspace `/Users/agent/spike/setsid-topology/` (plan's probe-workspace convention) as `agent` (interactive shell via `sudo -u agent -i`, per the plan's agent-access note; the launchd context is out of scope for this probe), per the kill sheet (`dev_docs/elite-spike/kill-sheet.md`). Time cap: half a working day (§7a rule 3); at the cap, stop and classify.

- Write a trivial shim that records its own `{pid, ps lstart, pgid, sid, executable}`, calls `setsid(2)`, then `execve`s a long-running child. The transitions complete in microseconds, so the shim must pause at each stage (sleep N seconds or wait on a fifo/signal after start, after `setsid(2)`, and in the exec'd child) so an outside observer running as the maintainer uid can capture the identity tuple per step (`ps`, `proc_pidinfo` if needed) — mirroring the launcher's cross-uid vantage.
- Repeat with a pinned-helper surrogate: macOS ships no `setsid(1)`, so build a ~10-line maintainer-owned wrapper (compiled C or script) that calls `setsid(2)` then `execve(2)`s its argv — built by the maintainer, world-readable/executable, placed in the probe workspace (never under `/usr/local/autopilot`); run the same measurement through it and compare tuples.
- Measure PID/PGID/SID/executable continuity across the `execve`; determine what an external observer can verify about "this Claude process is the one my shim started."
- Test discriminability: kill the child, then spawn short-lived processes in a loop until the kernel PID counter wraps past the dead child's PID (verify a new process now holds it), and check whether the tuple distinguishes the two incarnations.
- Close per the plan's probe close protocol and record the run-shim selection decision.

## Acceptance Criteria

- **User-run:** probe closed with exactly one of `confirmed`/`falsified`/`inconclusive` against its kill-sheet row; measurement row appended to `dev_docs/elite-spike/measurements.md` with the observed tuples per step; a written selection (shim calls `setsid` itself vs pinned helper) with rationale; a statement of the strongest stable incarnation identity available.
- Redirects are explicit, not softened: self-`setsid` failing selects the pinned-helper variant; **both** variants failing (or no stable incarnation identity found) blocks probe 2 and Stage 2 and takes §7a probe 2's redirect verbatim — abandon pane identity as authority; evaluate a directly held maintainer guardian/process handle or a different session host before designing leases — recorded in the measurement row.
- Fixture checked in at `dev_docs/elite-spike/fixtures/setsid-topology/` (raw `ps` captures sanitized per the plan checklist; macOS version/build recorded as environment metadata); running its single documented command from a clean checkout (per the plan's reproducibility convention) reproduces a tuple table of the same shape.
