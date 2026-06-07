---
title: Rename handler content, part 1 (repo-pr, gh-issue, mcp-setup-offer)
priority: medium
size: 2
status: done
created: 2026-06-06
source_branch: bestdan/refactor/task-vocabulary
source_pr: 18
related_files:
  - commands/handlers/repo-pr.md
  - commands/handlers/repo-pr-config.md
  - commands/handlers/gh-issue.md
  - commands/handlers/gh-issue-config.md
  - commands/handlers/mcp-setup-offer.md
is_blocked_by: task_vocabulary_normalization_task_2
expires: 2026-07-06
tags:
  - refactor
  - handlers
---

← [[task_vocabulary_normalization_plan]]

# Task 3 — Rename handler content, part 1

Convert "todo" → "task" wording in the repo-pr, gh-issue, and mcp-setup-offer handlers.

## Context

- `commands/handlers/*.md` are reference procedures bundled into the task skill (see `scripts/validate.py:103` comment — that comment is fixed in Task 6). They have no frontmatter and are skipped by the validator, so changes here are purely content/consistency.
- Handler **filenames stay the same** — only internal wording changes. This keeps command→handler path references (from Task 2) valid.
- `repo-pr.md` is the default handler and the heaviest (`todo` ×30); it owns the file-based task store under `dev_docs/tasks/`.

## Task

Content rename (`todo`/`todos` → `task`/`tasks`; `dev_docs/todos/` → `dev_docs/tasks/`; `.todo-config.yml` → `.task-config.yml`; branch `todo/<slug>` → `task/<slug>`; label `todo-loop` → `task-loop`; command refs to new names) in:

- `commands/handlers/repo-pr.md`
- `commands/handlers/repo-pr-config.md`
- `commands/handlers/gh-issue.md`
- `commands/handlers/gh-issue-config.md`
- `commands/handlers/mcp-setup-offer.md`

## Acceptance Criteria

### Code-enforced

- `rg -n '\btodos?\b|todo-loop|\.todo-config' commands/handlers/repo-pr.md commands/handlers/repo-pr-config.md commands/handlers/gh-issue.md commands/handlers/gh-issue-config.md commands/handlers/mcp-setup-offer.md` → no matches.
- `dprint check` passes on the five files.
- `uv run scripts/validate.py` → OK (handlers are not validated, but confirms nothing else regressed).

### User-run

- None.
