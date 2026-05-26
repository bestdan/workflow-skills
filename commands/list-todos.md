---
description: Render todos as a kanban board grouped into vertical sections by status — dispatches to the configured handler (repo-pr files, or external trackers like Linear)
allowed-tools: Bash(git *), Bash(gh *), Bash(find *), Bash(grep *), Bash(cat *), Glob, Grep, Read, AskUserQuestion, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__claude_ai_Linear__list_issues, mcp__claude_ai_Linear__list_workflow_states, mcp__linear__list_teams, mcp__linear__list_projects, mcp__linear__list_issues, mcp__linear__list_workflow_states, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql
argument-hint: [filter: new|needs_refinement|ready|in_progress|blocked|needs_review|expired|all]
---

# List Todos

Render todos in `dev_docs/todos/` as a vertical kanban view — one section per status, stacked top to bottom. Text UIs don't render side-by-side columns reliably, so the kanban "columns" are presented as sequential sections instead.

## Steps

### 1. Resolve the handler

Read `dev_docs/todos/.todo-config.yml`:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/todos/.todo-config.yml" 2>/dev/null
```

- File absent, or no `handler:` key → `repo-pr` (default). Continue to step 2 below (file-based path).
- `handler: repo-pr` → continue to step 2 below (file-based path).
- `handler: gh-issue | jira` → **dispatch to the handler.** Read `commands/handlers/<handler>.md` and follow its `## List` section, passing `$ARGUMENTS` (the optional status filter) through. The handler owns all tracker-specific querying and renders the same vertical-section kanban layout described in step 4. Skip steps 2–4 of this file. If the handler file has no `## List` section yet, stop with: "The `<handler>` handler does not yet support /list-todos. View your kanban directly in `<handler>` (e.g. your Jira board, or `gh issue list`)."

- `handler: linear` → **dispatch to the Linear handler.** Read `commands/handlers/linear-common.md` (shared config/preflight/kanban mapping) and `commands/handlers/linear-list.md` (the list flow), passing `$ARGUMENTS` through. Skip steps 2–4 of this file.

  If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/<handler>.md` or `**/commands/handlers/linear-*.md`) and Read the result. Do not read handler files for handlers other than the resolved one.

- Any other (unknown) value → stop with: "Unknown todo handler `<value>` in dev_docs/todos/.todo-config.yml. Run /todo-config to fix it."

### 2. Find todo files

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/todos" -name '*.md' -type f 2>/dev/null
```

If the directory doesn't exist or is empty, report "No todos found in this repo."

### 3. Parse and filter

For each file, parse the YAML frontmatter to extract: `title`, `priority`, `status`, `created`, `expires`, `tags`, `is_blocked_by`, `human_approval_requested`.

Check for expired todos: if `expires` < today and `status` is not `done`, mark as expired.

Also compute whether the todo is currently dependency-blocked: if `is_blocked_by` is set and a todo file with that slug still exists anywhere under `dev_docs/todos/**/*.md` with a status other than `done`, treat this todo as waiting on that dependency. This is distinct from `status: blocked`, which means someone tried to process the todo and hit a problem.

If `$ARGUMENTS` is provided, filter to that status (or `expired`). Default: show every section that has cards.

### 4. Display as stacked sections

Print one section per status in this fixed order, top to bottom, omitting empty sections:

`new` → `needs_refinement` → `ready` → `in_progress` → `blocked` → `needs_review` → `done`

The first five sections come from todo files (status field). `needs_review` and `done` are **PR-derived** — the file is deleted when `/process-todo` opens the PR, so there is no file to source those statuses from. Populate them by running, in parallel with the file scan:

```bash
gh pr list --label todo-loop --state open  --json number,title,headRefName,updatedAt   # → needs_review
gh pr list --label todo-loop --state merged --limit 10 --json number,title,mergedAt    # → done (recent)
```

Skip those two `gh` calls (and the two sections) if `gh` is unavailable or unauthenticated.

Within each section, sort by priority (urgent > high > medium > low), then age (oldest first). Render each card as a single line. Separate sections with a horizontal rule (`---`) so they're clearly distinct in a terminal:

```
## ready (2)

- [high] Fix broken import in utils.ts — created 2026-03-20, expires 2026-04-19  [cleanup]
- [low]  Remove stale foobar alias — waiting on fix-broken-import  [cleanup, zsh]

---

## needs_refinement (1)

- [medium] Add missing test for parser — human-approval-requested  [tests]

---

## in_progress (1)

- [high] Migrate config loader — claimed on bestdan/migrate-config
```

Annotations to surface inline when present: `human-approval-requested`, `waiting on <slug>`, `expired`, `claimed on <branch>`.

Finish with a summary line:

```
8 cards (1 new, 1 needs_refinement, 2 ready, 1 in_progress, 0 blocked, 2 needs_review, 1 done; 1 expired, 1 waiting on dependencies)
```
