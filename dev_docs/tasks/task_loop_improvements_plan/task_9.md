---
title: Add /list-tasks support for gh-issue and jira handlers
priority: low
size: 3
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/list-tasks.md
  - commands/handlers/gh-issue.md
  - commands/handlers/jira.md
  - commands/handlers/linear-list.md
tags:
  - task-loop
  - handlers
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]].

## Context

`/list-tasks` (`commands/list-tasks.md` step 1) dispatches to the handler, but `gh-issue` and `jira` have no `## List` section, so it stops with "does not yet support /list-tasks." Both are a single query away from a board. Use `commands/handlers/linear-list.md` as the template for the kanban-section rendering and the kanban-mapping reversal.

- gh-issue: `gh issue list --json number,title,labels,assignees,state,createdAt` (+ the configured repo/labels); map open/closed and `task-loop`-style labels onto the kanban sections.
- jira: `searchJiraIssuesUsingJql` over the configured project/labels; map Jira statusCategory (`new`/`indeterminate`/`done`) onto the sections.

## Task

1. Add a `## List` section to `commands/handlers/gh-issue.md`: query issues (honoring `gh-issue.repo`/`labels`), group into the seven kanban sections by state+label, render the same vertical-section layout and summary line as `list-tasks` step 4, honor the `$ARGUMENTS` status filter.
2. Add a `## List` section to `commands/handlers/jira.md`: same, via JQL on the configured project; map statusCategory + (optionally) the configured labels.
3. Reuse the rendering conventions documented in `commands/list-tasks.md` step 4 / `linear-list.md`; don't re-specify the card line format — reference it.
4. Update the dispatcher note in `commands/list-tasks.md` step 1 so gh-issue/jira no longer fall into the "not supported" branch.

## Acceptance Criteria

- **Code-enforced:** `just check` passes; both handler files have a `## List` section.
- **User-run:** With `handler: gh-issue`, `/list-tasks` renders the repo's issues grouped into kanban sections; `/list-tasks ready` filters to one section. With `handler: jira`, `/list-tasks` renders the project's issues mapped from statusCategory. Empty backlog reports "No tasks found" rather than erroring.
