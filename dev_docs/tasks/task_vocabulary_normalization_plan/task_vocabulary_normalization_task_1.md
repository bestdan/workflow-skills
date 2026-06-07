---
title: Rename the task skill (source of truth); add Task size + Legacy migration
priority: medium
size: 3
status: done
created: 2026-06-06
source_branch: bestdan/refactor/task-vocabulary
source_pr: 18
related_files:
  - skills/task/SKILL.md
  - scripts/validate.py
expires: 2026-07-06
tags:
  - refactor
  - vocabulary
---

← [[task_vocabulary_normalization_plan]]

# Task 1 — Rename the task skill (source of truth)

Rename `skills/todo/` → `skills/task/`, convert all "todo" vocabulary to "task", and add the canonical **Task size** and **Legacy migration** sections.

## Context

- `skills/todo/SKILL.md` (213 lines) is the definitional home for the task file format, the seven-column kanban, the confidence check, branch naming, and lifecycle. Everything else references it (e.g. `commands/promote-todos.md:23` → "run the confidence check from `skills/todo/SKILL.md`").
- `scripts/validate.py:96` enforces `frontmatter name == directory name`, so the directory move and the `name:` change must happen together.
- `scripts/validate.py:99` caps skill body at 500 lines; current 213 + the two new sections stays well under.
- This PR is intentionally the **first** because it defines the new vocabulary that later PRs reference. Other files still say `todo` after this PR — that is expected and resolved in Tasks 2–6.

## Task

- `git mv skills/todo skills/task`.
- In `skills/task/SKILL.md` frontmatter: `name: todo` → `name: task`; rewrite the `description` to drop "todo" (e.g. "Capture and process follow-up work as tasks…").
- Body rename pass (whole file): `todo`/`todos` → `task`/`tasks` for the unit-of-work noun and all command names (`/add-task`, `/promote-tasks`, `/process-tasks`, `/claim-task`, `/list-tasks`, `/task-config`); paths `dev_docs/todos/` → `dev_docs/tasks/`; config `.todo-config.yml` → `.task-config.yml`; branch namespaces `todo/<slug>` / `todo/add/<slug>` → `task/<slug>` / `task/add/<slug>`; PR label `todo-loop` → `task-loop`; section title "Todo file format" → "Task file format"; "Todo Loop" → "Task Loop".
- Add a **## Task size** section (canonical; referenced by the confidence check and by plan-with-docs):

  > **Task size:** one task = one PR. Target ≤ ~300 lines of diff across ≤ ~5 files. If a task would exceed that, split it into multiple tasks and chain them with `is_blocked_by`.

  Then make the confidence-check scope red-flag point at it ("…keywords suggesting the task exceeds the **Task size** budget above").
- Add a **## Legacy migration** section (canonical one-time-prompt procedure, referenced by Tasks 2 & 5):

  > If a command finds a legacy `dev_docs/todos/` (task store) or `dev_docs/todo/` (plans) directory, pause before proceeding and prompt once: _"Found legacy `dev_docs/todos/`. Migrate to `dev_docs/tasks/`? [migrate / skip once]"_. On **migrate**: `git mv` the directory to `dev_docs/tasks/`, rename `.todo-config.yml` → `.task-config.yml` if present, and report what moved. Leave in-flight branches (`todo/<slug>`) and the `todo-loop` PR label untouched — they are historical. On **skip once**: proceed without migrating and do not re-prompt this invocation.

## Acceptance Criteria

### Code-enforced

- `uv run scripts/validate.py` → OK (confirms `name: task` matches `skills/task/` and body ≤ 500 lines).
- `rg -n '\btodos?\b' skills/task/SKILL.md` returns only matches inside the **Legacy migration** section (the legacy path strings) — no other `todo` usage remains.
- `dprint check skills/task/SKILL.md` passes.

### User-run

- Skim `skills/task/SKILL.md` to confirm the kanban table, lifecycle diagram, and field reference read coherently with "task" and that **Task size** / **Legacy migration** are clear.
