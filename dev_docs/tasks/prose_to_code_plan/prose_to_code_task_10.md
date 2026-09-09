---
title: Add preflight.sh --scout-run-md to check task backends against the host
priority: medium
size: 2
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - skills/auto-pilot/references/launch-preflight.md:167-184
  - skills/auto-pilot/references/launch-preflight.md:83-88
  - skills/auto-pilot/references/resume.md:105-110
  - scripts/preflight.sh
  - scripts/probe-coders.sh
is_blocked_by: [prose_to_code_task_8]
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - auto-pilot
---

Plan: [[prose_to_code_plan]] · Index row 7.

## Context

Launch step 6 asks the agent to take each task's routed backend and confirm it
exists in the environment fingerprint; a task routed to an absent backend
blocks launch. The named failure is a `codex` task in a `claude-web` run with
no `codex` binary. The CAO gate (`command -v cao/cao-run/cao-server`,
`nc -z localhost 9889`, `cao_coder_mapping` vs fleet) is restated at launch
and at resume. `preflight.sh` has no scout logic.

## Task

1. Add `--scout-run-md <path>` to `scripts/preflight.sh`: read each task's
   backend from `RUN.md`, probe `command -v` per distinct backend, run the CAO
   gate when any task routes to `cao`, and print `SCOUT VERDICT: go` or one
   `BLOCKS LAUNCH: <task> -> <backend> (missing)` line per gap, exit 1 on any
   gap. Mirror the `PREFLIGHT VERDICT` line format.
2. Test in `scripts/test-shell.sh`'s style with a PATH fixture: all present,
   one missing, CAO port closed.
3. Rewrite launch step 6 and the two CAO passages to one call each.

## Acceptance Criteria

- Code-enforced: `just check` passes; the missing-backend case exits 1 with the
  task named.
- Code-enforced: `resume.md` and `launch-preflight.md` share one invocation,
  not two copies of the probe list.
