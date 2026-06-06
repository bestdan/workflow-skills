---
description: Configure where /add-task delivers tasks (repo PR, GitHub issue, Jira, or Linear)
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(mkdir *), Read, Write, Glob, AskUserQuestion, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__getVisibleJiraProjects, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__getVisibleJiraProjects, mcp__atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Linear__list_teams, mcp__claude_ai_Linear__list_projects, mcp__linear__list_teams, mcp__linear__list_projects
argument-hint: [repo-pr | gh-issue | jira | linear]
---

# Task Config

Set up the destination handler for `/add-task` in this repo. Writes `dev_docs/tasks/.task-config.yml`, which `/add-task` reads to decide where a captured task lands. The file is **repo-committed and shared by the team** — everyone in the repo files to the same destination.

This command is a thin dispatcher. The per-handler setup logic (preflight checks, prompts, config block shape) lives in `commands/handlers/<handler>-config.md`. The runtime delivery logic for each handler lives in `commands/handlers/<handler>.md`.

## Steps

### 1. Show current config

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

If it exists, show the user what's currently configured so they see what they're changing. If not, say there's no config yet (so `/add-task` currently defaults to `repo-pr`).

### 2. Choose the handler

If `$ARGUMENTS` names a handler (`repo-pr`, `gh-issue`, `jira`, or `linear`), use it. Otherwise ask via `AskUserQuestion` (header: "Destination") which one they want:

- **`repo-pr`** (recommended, first) — commit the task as a markdown file via PR (the default; works with `/process-tasks` and `/list-tasks`)
- **`gh-issue`** — create a GitHub Issue
- **`jira`** — create a Jira work item under an epic
- **`linear`** — create a Linear issue under a team (optionally attached to a project)

### 3. Dispatch to the per-handler setup file

Read `commands/handlers/<handler>-config.md` and follow it. The handler-config file:

- Runs all prerequisite checks (auth, MCP connectivity) and stops with guidance on failure — **do not fall back to writing a config that can't deliver**.
- Prompts for any handler-specific fields, validating values against the source of truth (real projects, real epics, etc.) rather than accepting free-text.
- Returns the config block to be written.

If the per-handler file dispatches to `mcp-setup-offer.md` (the shared MCP install subroutine for `jira` and `linear`), follow that and stop — the user needs to restart Claude Code before the new tools are available, and there's nothing to retry inline.

If the relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/<handler>-config.md`) and Read the result.

### 4. Write the config

Ensure the directory exists and write the file:

```bash
mkdir -p "$(git rev-parse --show-toplevel)/dev_docs/tasks"
```

Write the config block returned by the per-handler file to `dev_docs/tasks/.task-config.yml`. Examples of the shape each handler returns:

```yaml
# repo-pr
handler: repo-pr
```

```yaml
# gh-issue
handler: gh-issue
gh-issue:
  repo: owner/name
  labels: [follow-up]
  assignees: []
```

```yaml
# jira
handler: jira
jira:
  site: mycompany.atlassian.net
  project: PLAT
  issue_type: Task
  default_epic: PLAT-100
  labels: []
```

```yaml
# linear
handler: linear
linear:
  team: PreThink # team NAME (as shown in Linear) or UUID id — never the team key like "PRE"
  default_project: null
  default_priority: 3
```

Omit optional keys the user didn't set (the per-handler file already handles this in what it returns).

### 5. Confirm

Tell the user:

- Which handler is now configured and where the file lives.
- That the file is repo-committed and shared — they should **commit it** so teammates pick up the same destination.
- They can now run `/add-task`, or re-run `/task-config` to switch handlers.
