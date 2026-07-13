---
title: Audit the full reachability set — which subcommands can actually be ported
priority: high
size: 3
status: done
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
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
loudly — it further shrinks what the port can achieve, and further raises the stakes of the runtime decision in task 2, which follows immediately.

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

## Outcome

`scripts/audit-orchestrator-reachability.py` walks the call graph, both generated launchd jobs,
and the skill docs, then computes the **transitive** constrained closure (constraint is inherited
through in-process calls). The 29-row table is in `dev_docs/orchestrator-python-port.md`.

**Tier B: 17 subcommands, 2,618 handler lines. Tier A: 12, 1,422.** The audit found more
constrained surface than either hand-drawn boundary, and the answer to the card's own question is
yes, loudly:

- **Eight subcommands moved into Tier B** that no prior list had: `teardown`, `verify-request`,
  `verify-await`, `verify-broker`, `assert-run-head`, `alarm`, `alarm-request`, `report-tick`.
- **A fourth entry point exists**: `write_verify_broker` generates a **second launchd job**
  (`:5062`) whose script calls `verify-broker` under the same pinned PATH. Reading only
  `write_launch` missed it.
- **`teardown` is constrained purely by in-process call** — the supervisor's halt path
  (`_supervisor_halt`, `supervisor_check`, `supervisor_gate`) calls its bash function directly.
  It reads like a human cleanup command.
- **`assert-run-head` runs every loop iteration from inside the jail** (`run-state.md:359`).
- **`alarm-clear` moved *out* of Tier B** — every call site is `--resume`, which is attended.

Two things this changes downstream, both recorded in the plan: the constrained tier is **1.8x**
the portable one and holds the entire unattended runtime (task 2 decides against those numbers,
not the old ~1,000), and a Tier A **generator** emits scripts that call Tier B **callees**, so no
generator port can "finish" its callee.

**Follow-ups written down, not fixed here** (per the plan's no-scope-creep rule):

- `exit-reason` is **not read-only** — it upserts three RUN.md fields, makes a git commit, and
  creates/removes the done sentinel (`:2559-2590`). A stdout/rc golden corpus would capture none
  of that. Whatever card ports it needs acceptance criteria for the stateful contract.
- The tier boundary has **no test**. If a future change teaches the run loop to call an
  already-ported Tier A subcommand, it breaks in the jail at runtime and nothing catches it.
  Task 3 should gate: no subcommand on the `PORTED` list may appear in the audit's Tier B.
