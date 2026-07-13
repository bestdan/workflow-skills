---
title: Audit the full reachability set — which subcommands can actually be ported
priority: high
size: 3
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_1
related_files:
  - scripts/spawn-orchestrator.sh:4233 # status_report -> status (in-process)
  - scripts/spawn-orchestrator.sh:3195 # supervisor_check -> classify_exit (in-process)
  - scripts/spawn-orchestrator.sh:711 # the Seatbelt process-exec allowlist
  - scripts/spawn-orchestrator.sh:2559 # exit-reason's mutations (RUN.md, commit, sentinel)
  - skills/auto-pilot/SKILL.md:305 # the jailed agent's loop: doctor, exit-reason, heartbeat
tags: [orchestrator, python, port, audit, launchd, seatbelt]
---

← [[orch_py_plan]]

## Context

> **This task replaces "Port the read-only reporters: status, classify-exit, exit-reason."**
> Co-review of PR #205 (Copilot and codex, independently) established that **all three of those
> are constrained**, and that the plan's original Tier A/B split was wrong. Porting them as
> planned would have **broken the supervisor wake loop**. The port must not continue past the
> renderers until the real reachability set is known — that is now this task's job.

The original plan assumed one constraint (launchd's pinned PATH) and one seam (the CLI). Both
assumptions were incomplete. Three ways a subcommand is reachable from a context that cannot
freely resolve a Python interpreter:

1. **The generated launch script**, under launchd's pinned minimal PATH.
2. **In-process bash function calls** — these bypass the CLI entirely. Confirmed:
   `status_report` → `status` (`:4233`), `supervisor_check` → `classify_exit` (`:3195`).
   Deleting a ported function's bash body **breaks its in-process caller outright**.
3. **The jailed run-phase agent**, which invokes the orchestrator from *inside* `sandbox-exec`
   and is therefore bound by the profile's `(allow process-exec` allowlist (`:711-724`).
   Confirmed: `doctor` runs **every loop iteration**, `exit-reason` on every termination, plus
   `heartbeat` and `alarm-clear` (`skills/auto-pilot/SKILL.md`).

Note also that **`exit-reason` is not read-only** — it upserts three RUN.md fields, makes a git
commit, and creates/removes the done sentinel (`:2559-2590`). Any future port of it needs
acceptance criteria covering that stateful contract; a stdout/stderr/rc golden corpus would
capture none of it. Record this on whatever card eventually ports it.

## Task

Produce the reachability map that should have preceded this plan. For **every one of the 29
subcommands**, determine and record:

- **Is it invoked by the generated launch script?** (Read the `write_launch` printf body.)
- **Is its bash function called in-process by any other function?** Walk the call graph — do
  **not** rely on the CLI dispatch table, which is exactly the mistake that produced the
  original boundary. `rg '\b<fn_name>\b'` across the file, minus its own definition and dispatch
  entry.
- **Is it invoked by the jailed agent?** (`rg 'spawn-orchestrator\.sh [a-z-]+' skills/auto-pilot/`.)
- **Therefore: Tier A (free to port) or Tier B (gated on the interpreter decision)?**

Write the result as a table in `dev_docs/orchestrator-python-port.md`, replacing the current
tier lists, and update `orch_py_plan.md` plus any affected task cards to match.

If the audit finds **additional** constrained subcommands beyond the seven already known
(`status`, `classify_exit`, `exit_reason`, `doctor`, and the supervisor/heartbeat set), say so
loudly — it further shrinks what the port can achieve before the interpreter question is
settled, and may justify moving task 8 to the front of the plan.

## Acceptance Criteria

**Code-enforced:**

- A complete **29-row** table exists in `dev_docs/orchestrator-python-port.md`: one row per
  subcommand, with all three reachability answers and the resulting tier.
- Every row is backed by a `file:line` citation. Nothing is asserted from memory — the original
  boundary was wrong precisely because it was reasoned about rather than traced.
- A checked-in script (or documented command) reproduces the in-process call-graph walk, so the
  audit can be re-run after a future refactor instead of trusted forever.
- `orch_py_plan.md` and every task card agree with the table; no stale tier claim survives
  anywhere in the plan.

**User-run:**

- Sanity-check the Tier A set against `smoke-confinement.sh` and a real `launch --dry-run`:
  nothing in Tier A may appear in the generated launch script, in the jailed agent's
  instructions, or as an in-process callee of any Tier B function.
