---
title: Rename the six slash commands to task vocabulary
priority: medium
size: 3
status: done
created: 2026-06-06
source_branch: bestdan/refactor/task-vocabulary
source_pr: 18
related_files:
  - commands/add-task.md
  - commands/claim-task.md
  - commands/promote-tasks.md
  - commands/process-tasks.md
  - commands/list-tasks.md
  - commands/task-config.md
is_blocked_by: task_vocabulary_normalization_task_1
expires: 2026-07-06
tags:
  - refactor
  - commands
---

← [[task_vocabulary_normalization_plan]]

# Task 2 — Rename the six slash commands

Rename the todo-loop slash commands to task vocabulary and rewrite their bodies.

## Context

- Slash command names derive from filenames in `commands/*.md`, so renaming a command = `git mv` of its file.
- Number scheme (from design): singular for one-card ops, plural for board/batch ops.
- These commands reference `skills/task/SKILL.md` (renamed in Task 1) and the handler files by path. Handler **filenames** do not change in this refactor (only their content, Tasks 3–4), so `commands/handlers/<handler>.md` path references stay valid.
- `scripts/validate.py:105` validates only `commands/*.md` frontmatter (description, allowed-tools shape) — counts are unchanged, so this is safe.
- `commands/process-todo.md` has the heaviest "todo" usage (46) and embeds remote-prompt instructions; rename carefully there.

## Task

File renames (`git mv`):

- `commands/add-todo.md` → `commands/add-task.md`
- `commands/claim-todo.md` → `commands/claim-task.md`
- `commands/promote-todos.md` → `commands/promote-tasks.md`
- `commands/process-todo.md` → `commands/process-tasks.md`
- `commands/list-todos.md` → `commands/list-tasks.md`
- `commands/todo-config.md` → `commands/task-config.md`

In each file, rewrite content: `todo`/`todos` → `task`/`tasks`; all `/…-todo[s]` command references → new names; `dev_docs/todos/` → `dev_docs/tasks/`; `.todo-config.yml` → `.task-config.yml`; branch `todo/<slug>` → `task/<slug>`; label `todo-loop` → `task-loop`; references to `skills/todo/SKILL.md` → `skills/task/SKILL.md`. In `add-task.md` and `process-tasks.md`, update the embedded remote-session prompt text too.

In `add-task.md` (and any scanning command), add the Task 1 **Legacy migration** preflight: one line — "Before scanning, if `dev_docs/todos/` exists, run the Legacy migration prompt from `skills/task/SKILL.md`."

## Acceptance Criteria

### Code-enforced

- `uv run scripts/validate.py` → OK.
- `ls commands/*-todo*.md commands/todo-config.md 2>/dev/null` → no matches (old files gone).
- `rg -n '\btodos?\b|/(add|claim|promote|process|list)-todo' commands/*.md` → only matches are the legacy `dev_docs/todos/` string inside the migration-preflight lines.
- `dprint check 'commands/*.md'` passes.

### User-run

- Confirm the new command names render as expected (`/add-task`, `/claim-task`, `/promote-tasks`, `/process-tasks`, `/list-tasks`, `/task-config`).
