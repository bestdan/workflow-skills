---
title: "--park-limit is unreachable in production (never plumbed through write_launch)"
priority: medium
size: 1
status: new
created: 2026-07-12
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
is_blocked_by: [autopilot_hardening_task_16]
parent: autopilot_hardening
tags: [auto-pilot, supervisor, dead-flag, p2]
---

[[autopilot_hardening_plan]]

## Context

Found co-reviewing task 16 (#191). Both `supervisor-check` and `supervisor-scan` parse `--park-limit <n>` (the park-storm threshold), but `write_launch` never emits it into the generated wrapper. So **every production wake uses the default 3**, and the flag is reachable only by invoking the subcommand by hand.

Small, but it is a knob the operator believes they have and do not. Either wire it or delete it — a flag that exists in the parser and nowhere else is a lie in the `--help` output.

## Task

- Add a `write-launch --park-limit <n>` passthrough that emits the flag into the generated wrapper's `supervisor-scan` / `supervisor-check` invocations.
- Thread it from the launch path (`/auto-pilot`'s spawn) so an operator can actually set it.
- If a knob is not wanted, remove the parser arms instead and hard-code the default — but do not leave it half-wired.

## Acceptance Criteria

- A wrapper generated with `--park-limit N` observably passes `N` to both subcommands (asserted against the **real generated wrapper**, not the source text).
- The default remains 3 when the flag is omitted.
- `bash scripts/check.sh` green.
