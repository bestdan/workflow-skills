---
description: Score new tasks against the confidence check and promote them to ready or needs_refinement
allowed-tools: Bash(git *), Bash(find *), Bash(grep *), Bash(cat *), Bash(gh *), Glob, Grep, Read, Edit, AskUserQuestion, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__claude_ai_Linear__list_issue_labels, mcp__claude_ai_Linear__create_issue_label, mcp__claude_ai_Linear__save_issue, mcp__claude_ai_Linear__save_comment, mcp__linear__list_teams, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__linear__list_issue_labels, mcp__linear__create_issue_label, mcp__linear__save_issue, mcp__linear__save_comment, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__getTransitionsForJiraIssue, mcp__claude_ai_Atlassian__transitionJiraIssue, mcp__claude_ai_Atlassian__addCommentToJiraIssue, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getTransitionsForJiraIssue, mcp__atlassian__transitionJiraIssue, mcp__atlassian__addCommentToJiraIssue
argument-hint: "[filter: dry-run|apply] (default apply)"
---

# Promote Tasks

Scan `dev_docs/tasks/**/*.md` for tasks in `status: new`, score each against the confidence check, and flip status accordingly. This is the auto-promotion stage of the kanban flow — it never touches tasks past `new`. Humans own demotions from `ready`.

When the configured handler is an external tracker, the same scoring runs against the tracker's backlog instead of files (see step 0).

> **Legacy migration preflight.** Before scanning, if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Steps

### 0. Resolve the handler

Read `dev_docs/tasks/.task-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- File absent, or no `handler:` key → `repo-pr` (default). Continue to step 1 below (file-based path).
- `handler: repo-pr` → continue to step 1 below (file-based path).
- `handler: linear` → **dispatch to the Linear handler.** Read `commands/handlers/linear-common.md` (shared config/preflight/kanban mapping) and `commands/handlers/linear-promote.md` (the promote flow), passing `$ARGUMENTS` (the optional `dry-run` filter) through. The handler owns the tracker-specific scoring and transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/linear-*.md`) and Read the result.

- `handler: gh-issue` → **dispatch to the gh-issue handler.** Read `commands/handlers/gh-issue-promote.md` (the promote flow; it cites the `## List` section of `commands/handlers/gh-issue.md` for the shared label vocabulary), passing `$ARGUMENTS` (the optional `dry-run` filter) through. The handler owns the gh-issue scoring and label transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/gh-issue-promote.md`) and Read the result.

- `handler: jira` → **dispatch to the jira handler.** Read `commands/handlers/jira-promote.md` (the promote flow; it cites `commands/handlers/jira.md` step 1 for the shared Atlassian MCP preflight and `commands/handlers/jira-config.md` for the `ready_status`/`refinement_status` config keys it requires), passing `$ARGUMENTS` (the optional `dry-run` filter) through. The handler owns the jira scoring and status transitions. Skip steps 1–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/jira-promote.md`) and Read the result.

- Any other (unknown) value → stop with: "Unknown task handler `<value>` in dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

### 1. Find candidates

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/tasks" -name '*.md' -type f 2>/dev/null
```

Skip any file with `type: epic` in its frontmatter — epic rollup files are never scored (see **Epics** in `skills/task/SKILL.md`). The tracker path applies the analogous skip: backlog issues with sub-issues (children) are treated as **parent rollups** and are never scored — see `commands/handlers/linear-promote.md` step 5. Then filter to files with `status: new` in their YAML frontmatter. Report and exit if none.

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
- **Scope fits size 5 (judgment, not keywords).** Assess whether the task's described scope plausibly fits within size `5` (~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`), weighing the stated `size`, the breadth of the `## Task` steps, and the `related_files` count together. A title containing a word like "migrate" or "refactor" is not itself disqualifying ("Migrate one constant to the new config key" is size `1`); a title like "Restructure the auth module" that implies multi-file rework is disqualifying. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-tasks`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) is how that split gets done: it slices the card into PR-sized components and replaces the original.

**LOW (→ `needs_refinement`, set `human_approval_requested: true`)** if any HIGH condition fails.

This scope gate is **model judgment, not a deterministic rule** — acceptable here because `/promote-tasks` is not a blocking CI gate; a misjudged card lands in `needs_refinement` for a human to confirm, never silently lost. Every other HIGH check above stays deterministic.

Note: `is_blocked_by` is intentionally not part of the promotion check — `/do-tasks` filters dependency-blocked cards at runtime, and re-checking here would permanently strand otherwise-ready cards in `needs_refinement` (the promoter only scans `status: new`).

### 3. Apply

If `$ARGUMENTS` is `dry-run`, print the proposed transitions and exit without writing.

Otherwise, for each scored candidate, use `Edit` to update the YAML frontmatter in place:

- HIGH: set `status: ready`
- LOW: set `status: needs_refinement`, set `human_approval_requested: true` (add the field if missing). Append a one-line `# promoter:` comment to the frontmatter naming which check failed (e.g., `# promoter: missing acceptance_criteria`) so the human can fix quickly.

Do not touch any other fields. Do not move the file. Do not stage or commit — the next git operation (manual or `/do-tasks`) will pick up the changes.

### 4. Report

Print a summary table:

```
Promoted 4 of 6 candidates:
  ready (3):
    - remove-stale-foobar-alias
    - fix-broken-import
    - bump-eslint-config
  needs_refinement (1):
    - restructure-auth-module  (scope exceeds size 5 — split into sub-tasks)
  skipped (2, already past new):
    - <slug>
    - <slug>
```
