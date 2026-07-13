---
title: Port doctor — the 659-line run diagnostic
priority: medium
size: 5
status: needs_refinement
human_approval_requested: true
# promoter: scope exceeds size 5 — porting a 659-line function is >300 lines of diff; split (or decide the delete-vs-port question as its own card)
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_5
related_files:
  - scripts/spawn-orchestrator.sh:282 # doctor docs
tags: [orchestrator, python, port]
---

← [[orch_py_plan]]

## Context

`doctor` is **659 lines** — the single largest function in the file, a cheap read-only run
diagnostic generalizing findings #22/#23. It is the clearest single instance of the thesis:
659 lines of branching, state-gathering, and formatted reporting, written without
associative arrays.

**Before porting, answer the plan's open question 3: is `doctor` actually used?** It is a
diagnostic; diagnostics accrete. Check whether any skill, the auto-pilot orchestrator, or the
supervisor invokes it, and whether you have ever run it by hand. Porting it is roughly a day
of careful work. **Deleting it is ten minutes.** If it is dead or near-dead, propose deletion
in this PR instead of a port — that is a better outcome, and the plan explicitly sanctions it.

If it stays, it is a good port candidate precisely because it is read-only: a defect degrades
a report, it does not breach a jail or corrupt a run.

## Task

**First**, determine usage (grep the skills, the orchestrator, the launch script; ask the
user). Then either:

**(a) Delete it** — remove `doctor`, its dispatch entry, its docs block, and its harness
coverage. Note the deletion in the PR body with the evidence it was unused. Stop here.

**(b) Port it** — implement in `scripts/orchestrator/doctor.py`:

- Reproduce output byte-for-byte. Preserve every check, its ordering, and its verdict text.
- Preserve the read-only guarantee: `doctor` must not mutate run state. Assert this in a test
  (snapshot the run dir before and after; require it unchanged).
- Preserve the fail-closed posture on undetermined signals.
- Add to `PORTED`; delete the bash function.

## Acceptance Criteria

**Code-enforced:**

- If deleted: `just check` green, harness passes with the `doctor` coverage removed, and no
  caller anywhere references the subcommand (grep clean).
- If ported: golden corpus reproduces byte-for-byte across the diagnostic's branches
  (healthy run, halted run, missing state, stale heartbeat); a test asserts the run directory
  is byte-identical before and after a `doctor` invocation; harness passes unchanged.
- `just check` green either way.

**User-run:**

- Run `doctor` against a real (or archived) auto-pilot run directory and confirm the report
  is identical to the bash version's.
