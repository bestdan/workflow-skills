---
description: List all todo files in the current repo with their status and priority
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

For each file, parse the YAML frontmatter to extract: `title`, `priority`, `status`, `created`, `expires`, `tags`.

Check for expired todos: if `expires` < today and `status` is `unclaimed`, mark as expired.

If `$ARGUMENTS` is provided, filter to that status. Default: show all.

### 3. Display

Format as a table sorted by priority (high first), then age (oldest first):

```
| Status    | Priority | Title                              | Created    | Expires    | Tags          |
| --------- | -------- | ---------------------------------- | ---------- | ---------- | ------------- |
| unclaimed | high     | Fix broken import in utils.ts      | 2026-03-20 | 2026-04-19 | cleanup       |
| unclaimed | low      | Remove stale foobar alias          | 2026-03-23 | 2026-04-22 | cleanup, zsh  |
| blocked   | medium   | Add missing test for parser        | 2026-03-15 | 2026-04-14 | tests         |
```

Include a summary line: "3 todos (2 unclaimed, 1 blocked, 0 expired)"
