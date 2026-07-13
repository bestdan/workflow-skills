---
title: Port doctor — the 661-line run repair tool (Tier B — unblocked by task 2)
priority: medium
size: 5
status: needs_refinement
human_approval_requested: true
# promoter: scope exceeds size 5 (661-line function) — split before starting. Tier B (jail-invoked every loop); UNBLOCKED by task 2 (2026-07-13): pinned stdlib Python >=3.11 reaches the jail.
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_7
related_files:
  - scripts/spawn-orchestrator.sh:282 # doctor docs
  - scripts/spawn-orchestrator.sh:711 # the Seatbelt process-exec allowlist
  - skills/auto-pilot/SKILL.md:338 # "Every iteration opens with the run doctor"
tags: [orchestrator, python, port, seatbelt, blocked]
---

← [[orch_py_plan]]

## Context

> **Re-scoped 2026-07-13 after co-review of PR #205.** This card originally sat in Tier A and
> offered "just delete it" as an option. **Both premises were wrong**, and the card is now
> blocked on task 2 (the runtime decision) rather than on the audit.

`doctor` is **661 lines** — the single largest function in the file. It is **not** a read-only
diagnostic (an earlier draft of this card said so): it parks tasks via `_set_task_phase` (writing
`RUN.md`), removes orphaned worktrees (`:6066`), and halts the supervisor (`_doctor_halt`,
`:5744`). It is the run's self-repair, and any port or deletion has to answer to that stateful
contract — a stdout/rc golden corpus would capture none of it.
Two things changed:

**1. It is Tier B, not Tier A.** The jailed run-phase agent invokes it from *inside*
`sandbox-exec`: *"Every iteration opens with the run doctor"* (`skills/auto-pilot/SKILL.md:338`;
HALT on `status: systemic`, exit 30 — the loop must not dispatch). Porting it to Python
therefore requires the interpreter to be **on the Seatbelt `process-exec` allowlist**
(`spawn-orchestrator.sh:711-724`) — which is exactly the decision task 2 makes. It cannot be
ported before then.

**2. The "just delete it" option is dead.** It rested on `doctor` possibly being unused. It is
not: it is called on **every single loop iteration** of every auto-pilot run, and it can halt
the run. Deleting it would also have contradicted the plan's own contracts — an unchanged
29-subcommand CLI, and "the harness passes unedited" (`orch_py_task_3.md`). A deletion is a
behavior change and would need to be its own scoped task; it is not an option here.

## Task

**Task 2 decided (2026-07-13): stdlib-only Python on a pinned, pre-flight-resolved absolute
interpreter (≥3.11).** This card is **unblocked** — `doctor` ports. It is *not* frozen in bash.
Port it under the three requirements task 2 imposes
([`decisions/script_language.md`](../../decisions/script_language.md) → "The constrained tier's
runtime"):

- **Pre-flight resolve + assert ≥3.11, fail-closed at launch.** A `doctor` that cannot exec
  inside the jail halts every run on iteration one — so this must fail in front of a human at
  launch, never at 3am.
- **Bake the resolved absolute interpreter path into the launch script and the profile's exec
  grant** — never a PATH lookup. `render-profile` must emit the grant.
- **Grant the interpreter *directory* as a subpath, not a version-stamped literal** — a
  `cpython-3.11.14-…` literal re-creates finding #3's version-drift trap.

Behavioural contract to preserve:

- Reproduce output byte-for-byte. Preserve every check, its ordering, its verdict text, and the
  **exit-30 HALT contract** the run loop depends on.
- **`doctor` is NOT read-only — do not assert that it is.** (An earlier draft of this card
  demanded a "read-only guarantee … assert it with a before/after snapshot of the run dir." That
  is false and would have failed the port outright.) It is the run's **self-repair**: it parks
  tasks via `_set_task_phase` (writing `RUN.md`), removes orphaned worktrees (`:6066`), files an
  `alarm-request` via `_doctor_halt` (`:5504`), and halts the supervisor. **Every mutation is part
  of the contract** and needs its own acceptance criterion — a stdout/rc golden corpus captures
  none of it.
  - Given 661 lines, **split this card** before starting (it exceeds size 5 on its own).

## Acceptance Criteria

**Code-enforced:**

- If closed as won't-do: `dev_docs/orchestrator.md` records `doctor` as permanently bash, with
  the jail-invocation reason and a `file:line` citation.
- If ported: golden corpus reproduces byte-for-byte across the diagnostic's branches; a test
  asserts the run directory is unchanged after a `doctor` run; the **exit-30 HALT path is
  explicitly tested**; `render-profile` emits the interpreter on the exec allowlist.
- Harness passes unchanged either way.
- `just check` green.

**User-run (mandatory if ported):**

- Run a **real auto-pilot loop** and confirm `doctor` executes inside the jail on every
  iteration and can still HALT the run. A green unit suite proves nothing here — the failure
  mode is "the jailed agent cannot exec the interpreter", which only appears in a real run.
