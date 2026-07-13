---
title: Port doctor — the 659-line run diagnostic (BLOCKED — Tier B, jail-invoked)
priority: medium
size: 5
status: needs_refinement
human_approval_requested: true
# promoter: scope exceeds size 5 (659-line function). ALSO re-scoped after PR #205 co-review: doctor is jail-invoked every loop, so it is Tier B and gated on task 8's interpreter decision.
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_8
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
> blocked on task 8 rather than on task 5.

`doctor` is **659 lines** — the single largest function in the file, a read-only run diagnostic.
Two things changed:

**1. It is Tier B, not Tier A.** The jailed run-phase agent invokes it from *inside*
`sandbox-exec`: *"Every iteration opens with the run doctor"* (`skills/auto-pilot/SKILL.md:338`;
HALT on `status: systemic`, exit 30 — the loop must not dispatch). Porting it to Python
therefore requires the interpreter to be **on the Seatbelt `process-exec` allowlist**
(`spawn-orchestrator.sh:711-724`) — which is exactly the decision task 8 makes. It cannot be
ported before then.

**2. The "just delete it" option is dead.** It rested on `doctor` possibly being unused. It is
not: it is called on **every single loop iteration** of every auto-pilot run, and it can halt
the run. Deleting it would also have contradicted the plan's own contracts — an unchanged
29-subcommand CLI, and "the harness passes unedited" (`orch_py_task_1.md`). A deletion is a
behavior change and would need to be its own scoped task; it is not an option here.

## Task

**Do not start this until task 8 has decided the interpreter question.**

- **If task 8 chooses (a) — freeze Tier B in bash:** this card is **closed as won't-do**.
  `doctor` stays in bash. Say so explicitly in `dev_docs/orchestrator.md` (task 9), with the
  reason (jail-invoked every loop), so no future reader re-opens it.
- **If task 8 chooses (b) or (c) — an interpreter reachable from the jail:** port `doctor` under
  those constraints:
  - Reproduce output byte-for-byte. Preserve every check, its ordering, its verdict text, and
    the **exit-30 HALT contract** the run loop depends on.
  - Preserve the read-only guarantee (`doctor` must not mutate run state) — assert it with a
    before/after snapshot of the run dir.
  - The interpreter must be on the rendered profile's exec allowlist, and `render-profile` must
    emit it. A `doctor` that cannot exec inside the jail halts every run on iteration one.
  - Given 659 lines, **split this card** before starting (it exceeds size 5 on its own).

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
