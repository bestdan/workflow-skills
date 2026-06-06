---
description: Score new tasks against the confidence check and promote them to ready or needs_refinement
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Glob, Grep, Read, Edit
argument-hint: "[filter: dry-run|apply] (default apply)"
---

# Promote Tasks

Scan `dev_docs/tasks/**/*.md` for tasks in `status: new`, score each against the confidence check, and flip status accordingly. This is the auto-promotion stage of the kanban flow — it never touches tasks past `new`. Humans own demotions from `ready`.

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Steps

### 1. Find candidates

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/tasks" -name '*.md' -type f 2>/dev/null
```

Filter to files with `status: new` in their YAML frontmatter. Report and exit if none.

### 2. Score each candidate

For each candidate, run the **confidence check** from `skills/task/SKILL.md`:

**HIGH (→ `ready`)** requires ALL of:

- `title`, `priority`, `size`, `created`, `source_branch`, `expires` present
- `size` is one of `1` / `2` / `3` / `5` (see **Task size** — `> 5` means the task should be split into sub-tasks)
- `related_files` has ≥ 1 entry, OR `tags` includes `scope: research`
- Body has a `## Acceptance Criteria` section with ≥ 1 bullet
- Body has no `## Open Questions` or `## TBD` section with non-empty content (an empty heading is fine)
- `priority` ≠ `urgent`
- `human_approval_requested` is unset or false
- Title and body together do not contain any scope red-flag keyword: `refactor`, `migrate`, `redesign`, `rewrite`, `overhaul` (case-insensitive, whole word) suggesting the task exceeds size `5`

**LOW (→ `needs_refinement`, set `human_approval_requested: true`)** if any HIGH condition fails.

Note: `is_blocked_by` is intentionally not part of the promotion check — `/process-tasks` filters dependency-blocked cards at runtime, and re-checking here would permanently strand otherwise-ready cards in `needs_refinement` (the promoter only scans `status: new`).

### 3. Apply

If `$ARGUMENTS` is `dry-run`, print the proposed transitions and exit without writing.

Otherwise, for each scored candidate, use `Edit` to update the YAML frontmatter in place:

- HIGH: set `status: ready`
- LOW: set `status: needs_refinement`, set `human_approval_requested: true` (add the field if missing). Append a one-line `# promoter:` comment to the frontmatter naming which check failed (e.g., `# promoter: missing acceptance_criteria`) so the human can fix quickly.

Do not touch any other fields. Do not move the file. Do not stage or commit — the next git operation (manual or `/process-tasks`) will pick up the changes.

### 4. Report

Print a summary table:

```
Promoted 4 of 6 candidates:
  ready (3):
    - remove-stale-foobar-alias
    - fix-broken-import
    - bump-eslint-config
  needs_refinement (1):
    - migrate-auth-service  (red-flag keyword: migrate)
  skipped (2, already past new):
    - <slug>
    - <slug>
```
