---
description: Render todo files as a kanban board grouped by status column
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Glob, Grep, Read
argument-hint: [filter: new|needs_refinement|ready|in_progress|blocked|needs_review|expired|all]
---

# List Todos

Render todos in `dev_docs/todos/` as a kanban board, one section per column.

## Steps

### 1. Find todo files

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/todos" -name '*.md' -type f 2>/dev/null
```

If the directory doesn't exist or is empty, report "No todos found in this repo."

### 2. Parse and filter

For each file, parse the YAML frontmatter to extract: `title`, `priority`, `status`, `created`, `expires`, `tags`, `is_blocked_by`, `human_approval_requested`.

Check for expired todos: if `expires` < today and `status` is not `done`, mark as expired.

Also compute whether the todo is currently dependency-blocked: if `is_blocked_by` is set and a todo file with that slug still exists anywhere under `dev_docs/todos/**/*.md` with a status other than `done`, treat this todo as waiting on that dependency. This is distinct from `status: blocked`, which means someone tried to process the todo and hit a problem.

If `$ARGUMENTS` is provided, filter to that status (or `expired`). Default: show all columns.

### 3. Display as kanban board

Print one section per column in this fixed order, omitting empty columns:

`new` → `needs_refinement` → `ready` → `in_progress` → `blocked` → `needs_review` → `done`

Within each section, sort by priority (urgent > high > medium > low), then age (oldest first). Render each card as a single line:

```
## ready (2)

- [high] PRE-12 Fix broken import in utils.ts — created 2026-03-20, expires 2026-04-19  [cleanup]
- [low]  Remove stale foobar alias — waiting on fix-broken-import  [cleanup, zsh]

## needs_refinement (1)

- [medium] Add missing test for parser — human-approval-requested  [tests]

## in_progress (1)

- [high] Migrate config loader — claimed on bestdan/migrate-config
```

Annotations to surface inline when present: `human-approval-requested`, `waiting on <slug>`, `expired`, `claimed on <branch>`.

Finish with a summary line:

```
8 cards (1 new, 1 needs_refinement, 2 ready, 1 in_progress, 0 blocked, 2 needs_review, 1 done; 1 expired, 1 waiting on dependencies)
```
