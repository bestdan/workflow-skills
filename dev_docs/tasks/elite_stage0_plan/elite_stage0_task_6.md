---
title: "probe 3: real alert walking skeleton — kill/wedge a launchd heartbeat, device notified within 10 minutes"
priority: urgent
size: 3
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: elite_stage0_task_2
parent: elite_stage0
tags: [e-lite, spike, stage-0, probe, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

Design §7a priority 3 + §5.1. Key assumption: the inherent unattended promise — external detection and notification — works without the full registry or launcher. Falsification redirect: stop unattended work; change launch context, watcher primitive, cadence, or delivery provider and rerun this probe. Runs in parallel with the viability canary (needs only the kill sheet, not the agent identity — it can run as the maintainer).

The push provider is chosen in the kill sheet ([[elite_stage0_task_2]]); this probe registers the real device against that provider.

## Task

Per the kill sheet:

- Stand up a trivial launchd-hosted heartbeat process (touches a file per tick) and a minimal watcher launchd job at the real cadence (`StartInterval` 120s, single short-lived pass, staleness threshold 6 min). Install both plists as per-user LaunchAgents in the maintainer's `~/Library/LaunchAgents/`; both jobs run as the maintainer uid (this probe needs no agent identity).
- Wire the watcher's alert path to the kill-sheet-selected push provider and the real device. The watcher reads the provider token/topic from an untracked local file at a documented path (e.g. `~/.autopilot-spike-push`, mode 0600); the checked-in script references only that path.
- Drill 1 — **kill**: `kill -9` the heartbeat process; measure wall-clock time to notification on the device.
- Drill 2 — **wedge**: leave the process alive but stop its heartbeat (SIGSTOP or an intentional hang); measure time to notification.
- Drill 3 — **launchd lifecycle** (§0a's empirical fact "launchd behavior across crash, sleep, and reboot"): crash the launchd-hosted process and observe relaunch behavior (`KeepAlive` semantics); sleep the host past a watcher tick and record what fires on wake; reboot and record whether/when the per-user jobs return without a GUI login. Record each observation — this is the only Stage-0 probe that owns the sleep/reboot questions.
- Record provider-side vs device-side receipt ("provider accepted ≠ you received" — §5.1); note any delivery lag or drop.
- Close per the plan's probe close protocol: pass requires device notification within the 10-minute SLO for drills 1 and 2; drill 3 is recorded observational evidence, not SLO-gated.

## Acceptance Criteria

- **User-run:** all three drills executed with timestamps (kill/wedge time, watcher detection time, device receipt time; relaunch/wake/reboot observations for drill 3), recorded in the probe's row in `dev_docs/elite-spike/measurements.md`.
- Probe closed as exactly one of `confirmed`/`falsified`/`inconclusive` against the kill sheet's pass threshold and inconclusive condition (§0a — no unfinished state).
- Launchd plists + watcher script checked in under `dev_docs/elite-spike/fixtures/probe3/`; a single documented command re-executes drill 1 end-to-end; no provider tokens, topic names, user keys, or device identifiers in checked-in evidence (plan sanitization checklist).
- Provider matches the kill sheet's recorded choice.
