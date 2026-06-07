---
title: Extend task frontmatter schema (assignee, impact, parent, list is_blocked_by)
priority: high
size: 3
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - skills/task/SKILL.md
  - skills/plan-with-docs/SKILL.md
  - scripts/validate.py
tags:
  - task-loop
  - schema
  - foundation
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]].

## Context

The canonical task format is defined in `skills/task/SKILL.md` ("Task file format" + "Field reference" table, ~lines 111–166). Several downstream improvements need new fields. This task adds the *schema and validation* only — no behavior consumes the fields yet (later tasks do). Doing all field additions in one PR avoids repeated merge conflicts on the same field-reference table.

New/changed fields:

- `assignee` (optional) — human or agent accountable. Mirrors gh-issue/Linear assignee so the file handler reaches parity.
- `impact` (optional) — Fibonacci `1`/`2`/`3`/`5` value estimate, mirroring `size`. Used later for value/effort ranking.
- `parent` (optional) — slug of an epic this task belongs to (see epic task). Distinct from `is_blocked_by` (ordering) — `parent` is grouping.
- `is_blocked_by` — change from a single slug to **scalar-or-list**: accept either `fix-import` or `[fix-import, other-task]`. A single string stays valid for back-compat.

`scripts/validate.py` enforces frontmatter shape and is dev/CI-only (see `CONTRIBUTING.md`). It must accept the new optional fields and the list form of `is_blocked_by` without breaking existing tasks.

## Task

1. In `skills/task/SKILL.md`:
   - Add `assignee`, `impact`, `parent` rows to the Field reference table (mark optional; document the Fibonacci scale for `impact`, pointing at the existing **Task size** section).
   - Update the `is_blocked_by` row to document scalar-or-list and note "ready only when ALL blockers resolve" (semantics implemented in the multi-blocker task).
   - Update the example frontmatter block to show `impact` and a list `is_blocked_by`.
2. In `scripts/validate.py`: allow the three new optional keys; accept `is_blocked_by` as string OR list of strings; keep rejecting unknown keys if it currently does.
3. In `skills/plan-with-docs/SKILL.md` step 5 frontmatter bullet: mention the new optional fields are available (don't require them).
4. Run `just check` — must stay green.

## Acceptance Criteria

- **Code-enforced:** `just check` passes. `scripts/validate.py` accepts a task file with `assignee`, `impact: 3`, `parent: some-epic`, and `is_blocked_by: [a, b]`; still rejects a malformed file (e.g. `impact: 7`).
- **User-run:** Field reference table in `skills/task/SKILL.md` renders with the three new rows and the updated `is_blocked_by` description.
- No behavior change in any command yet — only schema + validation.
