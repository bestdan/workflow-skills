---
title: "Graduate Probe 5b's durable wisdom and delete the plan scaffolding"
priority: low
size: 1
status: new
created: 2026-07-27
expires: 2026-08-26
source_branch: bestdan/autopilot-e-lite-design
parent: probe5b
is_blocked_by: probe5b_task_6
related_files:
  - dev_docs/tasks/probe5b_plan/
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
tags: [spike, probe5b, cleanup]
---

Plan: [[probe5b_plan]]

## Context

`dev_docs/tasks/<name>_plan/` is temporary scaffolding, not documentation. Once
the probe is classified, the durable wisdom belongs in the design doc and the
fixture's own probe document — not in a task folder.

Probe 5b's durable output is small and already has homes:

- The **breaker-gap inventory** — the finding that of 5b's three named breaker
  families none had out-of-process enforcement — belongs in §7a row 5b and, if
  it contradicts them, in Risk #1 / Decision #5. It is the most reusable thing
  this probe produced and must not die with the scaffolding.
- The **kill sheet, legs and evidence** stay in
  `dev_docs/elite-spike/fixtures/runaway/` as checked-in evidence, per rule 4.

Note what does **not** get deleted: `dev_docs/tasks/probe5-incident-evidence/`
outlives the spike and is untouched by this task. The fixture tree also stays —
Probe 5's fixture is precedent: torn down operationally, retained as evidence.

## Task

- Confirm the breaker-gap finding is recorded in the design doc (task 6), not
  only in this folder.
- **Commit the fixture tree** — rule 4 requires the fixture command/test,
  sanitized evidence, result and decision to be checked in, and no other task
  owns that. `dev_docs/elite-spike/fixtures/` is not gitignored (unlike
  `dev_docs/tasks/`), so this is just a commit — but it must happen before the
  plan folder is deleted.
- Delete `dev_docs/tasks/probe5b_plan/`.
- Do **not** delete `dev_docs/elite-spike/fixtures/runaway/` or
  `dev_docs/tasks/probe5-incident-evidence/`.
- If the fixture left any operational residue — scratch rundirs, run-state
  branches, stray processes — remove it and record the teardown in
  `probe5b-runaway.md`, matching Probe 5's Part E teardown record.

## Acceptance Criteria

**Code-enforced:**

- `dev_docs/tasks/probe5b_plan/` no longer exists.
- `dev_docs/elite-spike/fixtures/runaway/` and
  `dev_docs/tasks/probe5-incident-evidence/` are intact; `git status` shows no
  deletions under either.
- `scripts/check.sh` passes.

**User-run:**

- No scratch rundir, run-state branch, or surrogate process from the probe
  survives; the teardown is recorded in `probe5b-runaway.md`.
