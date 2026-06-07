---
title: Model epics with parent field and rollup status in /list-tasks
priority: medium
size: 3
status: ready
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - skills/task/SKILL.md
  - commands/list-tasks.md
  - skills/plan-with-docs/SKILL.md
is_blocked_by: task_1
tags:
  - task-loop
  - epics
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_1]].

## Context

Tasks are flat; `is_blocked_by` gives ordering but no grouping/rollup. A `plan-with-docs` `_plan/` directory is already a de-facto epic. Per the planning decision, we make epics a **first-class file** (not just a directory name): an epic file carries its own frontmatter so it can hold a title, status, and owner and drive a real rollup. Tasks join an epic via the `parent` field (from the schema task).

Epic file shape (`type: epic` distinguishes it from a task):

```yaml
---
type: epic
title: Task Loop Improvements
status: active   # active | done | abandoned
owner: dan
created: 2026-06-07
---
```

The `plan-with-docs` overview file (`<name>_plan.md`) becomes this epic file (gains the epic frontmatter). **Critical:** `/promote-tasks` and `/do-tasks`/`/process-tasks` scan `status: new` task files — they must **skip `type: epic` files** so the epic is never scored or executed as a task. Today the overview has no frontmatter and is skipped implicitly; adding frontmatter means the skip must become explicit.

## Task

1. `skills/task/SKILL.md`: document the epic file format (`type: epic` + frontmatter above) and epic membership — a task belongs to an epic via `parent: <epic-slug>` (the epic file's slug) and/or by living under `<epic>_plan/`. Define rollup = counts of member tasks by status (plus PR-derived done via the existing `task-loop` label query).
2. **Skip guard:** update `commands/promote-tasks.md` (and the `repo-pr` scan in `/do-tasks`/`/process-tasks`) to ignore any file with `type: epic` — never score or execute it.
3. `commands/list-tasks.md`: add an "Epics" rollup section at the top of the board (before `new`), one line per epic file: `<title> (<slug>): <done>/<total> done (<in_progress> in progress, <blocked> blocked) — owner @<owner>, <status>`. Omit when no epic files present.
4. `skills/plan-with-docs/SKILL.md` step 4/5: write the overview as the epic file (add `type: epic` frontmatter) and set `parent: <name>` on each generated `task_N.md`.
5. `scripts/validate.py`: in the `dev_docs/tasks/**/*.md` scan added in task 1, recognize `type: epic` files and validate them against the epic frontmatter shape (title / status / owner) rather than the task shape — and keep them excluded from task-specific checks (no `size`/`status: new` requirement).
6. Honor the existing `$ARGUMENTS` filter — epics section shows under `all` and the default view, not when filtering to a single status.

## Acceptance Criteria

- **Code-enforced:** `just check` passes; `scripts/validate.py` accepts a `type: epic` file; `/promote-tasks` does not score `type: epic` files.
- **User-run:** With this plan present (its overview carrying `type: epic`), `/list-tasks` shows an Epics line like `Task Loop Improvements (task_loop_improvements): 0/14 done — owner @dan, active`. Marking a member task done updates the count. A standalone task with `parent: task_loop_improvements` is counted in the rollup. `/promote-tasks` leaves the epic file untouched.
