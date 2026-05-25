---
description: List all todo files in the current repo with their status, priority, and dependency blockers
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Glob, Grep, Read
argument-hint: [filter: unclaimed|claimed|blocked|expired|all]
---

# List Todos

Show a summary of all todo files in `dev_docs/todos/`.

## Steps

### 1. Find todo files

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/todos" -name '*.md' -type f 2>/dev/null
```

If the directory doesn't exist or is empty, report "No todos found in this repo."

### 2. Parse and filter

For each file, parse the YAML frontmatter to extract: `title`, `priority`, `status`, `created`, `expires`, `tags`, `is_blocked_by`.

Check for expired todos: if `expires` < today and `status` is `unclaimed`, mark as expired.

Also compute whether the todo is currently dependency-blocked: if `is_blocked_by` is set and a todo file with that slug still exists anywhere under `dev_docs/todos/**/*.md`, treat this todo as waiting on that dependency. This is distinct from `status: blocked`, which means someone tried to process the todo and hit a problem.

If `$ARGUMENTS` is provided, filter to that status. Default: show all.

### 3. Display

Format as a table sorted by dependency readiness (ready before dependency-blocked), then priority (high first), then age (oldest first):

```
| Status    | Priority | Blocked By        | Title                         | Created    | Expires    | Tags          |
| --------- | -------- | ----------------- | ----------------------------- | ---------- | ---------- | ------------- |
| unclaimed | high     |                   | Fix broken import in utils.ts | 2026-03-20 | 2026-04-19 | cleanup       |
| unclaimed | low      | fix-broken-import | Remove stale foobar alias     | 2026-03-23 | 2026-04-22 | cleanup, zsh  |
| blocked   | medium   |                   | Add missing test for parser   | 2026-03-15 | 2026-04-14 | tests         |
```

For dependency-blocked todos, either show the blocker slug in `Blocked By` or annotate the status cell as `unclaimed (waiting)` if the table formatter is cramped. Do not rewrite the stored `status`.

Include a summary line: "3 todos (2 unclaimed, 1 blocked, 0 expired, 1 waiting on dependencies)"
