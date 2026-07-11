---
title: "observability: status subcommand + done-sentinel; neutralize jail-incompatible Stop hooks"
priority: 3
size: 3
status: done # merged as PR #176 (detached run #2, 2026-07-11)
created: 2026-07-10
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
  - skills/auto-pilot/references/launch-runtime.md
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, observability, p5]
---

[[autopilot_hardening_plan]]

## Context

Findings **P5 #17, #18**.

- **#17 — live status is hand-rolled.** The orchestrator log is stream-json only;
  producing each 10-min status update required ad-hoc `python -c` to extract phase
  table + last event. `REPORT.md` is good for outcomes but lags per-event, and
  "is it done?" requires polling `ps` + parsing `RUN.md`.
- **#18 — Stop-hook noise.** A project `Stop` hook fired `stop-hook-error` on every
  turn under the jail (not jail-compatible). Benign (final result `success`) but it
  clutters the log and could mask a real hook failure.

New `status` subcommand touches a **different function** than tasks 1/2/3, so it
merges cleanly with them; note the rebase order in the PR.

## Task

- Add `spawn-orchestrator.sh status --label <label>`: print, in one call, the
  run-level `status:` from `RUN.md`, the per-task phase table, the last meaningful
  log event, PID liveness (via the recorded handle: PID + start-time match), and
  the `--until` deadline. Parseable final line, mirroring `preflight-freshness.sh`.
- Emit a **done-sentinel** at clean loop termination so a watcher can detect
  completion without polling `ps` + parsing `RUN.md`. Tie it to the existing
  teardown step. **Unify it with the launchd relaunch sentinel** — the `PathState`
  file the supervisor already gates `KeepAlive`/relaunch on (`launch-runtime.md`
  "Spawn mechanics") — so completion is **one** mechanism, not two files that can
  disagree (a `DONE` marker that says finished while a relaunch sentinel still says
  wake would be a bug).
- **Neutralize jail-incompatible `Stop` hooks** for the detached run: either run
  the orchestrator with a minimal/hook-scrubbed settings set, or document how the
  launch settings disable the offending Stop hook. Record the choice in
  `launch-runtime.md` "Logs / observability".

## Acceptance Criteria

**Code-enforced:**
- `scripts/test-spawn-orchestrator.sh`: `status --label` on a synthetic run dir
  prints the phase table + a `STATUS:` verdict line and reports PID-not-live when
  the handle points at a dead/mismatched PID; the done-sentinel appears after a
  simulated clean termination.
- `bash scripts/check.sh` green.

**User-run:**
- During a real detached run, `spawn-orchestrator.sh status --label
  com.autopilot.<run>` gives an at-a-glance snapshot without hand-written parsing,
  and the orchestrator log no longer shows `stop-hook-error` every turn.
