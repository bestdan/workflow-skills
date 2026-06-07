---
title: Add /list-tasks support for the jira handler
priority: low
size: 2
status: in_progress
created: 2026-06-07
source_branch: claude/wonderful-bardeen-SW7wW
related_files:
  - commands/list-tasks.md
  - commands/handlers/jira.md
  - commands/handlers/gh-issue.md
  - commands/handlers/linear-list.md
tags:
  - task-loop
  - handlers
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Carved out of the original task_9, whose
> gh-issue half shipped on `claude/wonderful-bardeen-SW7wW`; this is the jira half.

## Context

`/list-tasks` (`commands/list-tasks.md` step 1) dispatches to the configured
handler, but `jira` has no `## List` section, so it stops with "does not yet
support /list-tasks." Jira is a single JQL query away from a board. The gh-issue
`## List` section (`commands/handlers/gh-issue.md`) and `linear-list.md` are the
templates for the kanban-section rendering and the kanban-mapping reversal —
follow their shape (preflight → query → group → render → summary → filter →
empty), don't re-specify the card line format.

- jira: `searchJiraIssuesUsingJql` over the configured project/labels; map Jira
  `statusCategory` (`new` / `indeterminate` / `done`) onto the kanban sections,
  using the same status-label vocabulary the other handlers reuse where Jira
  labels are present.

## Task

1. Add a `## List` section to `commands/handlers/jira.md`: query the configured
   project's issues via JQL (honoring `jira.project` and `jira.labels`), group
   into the seven kanban sections by `statusCategory` + (optionally) labels,
   render the same vertical-section layout and summary line as
   `commands/list-tasks.md` step 4, and honor the `$ARGUMENTS` status filter.
2. Reuse the rendering conventions documented in `commands/list-tasks.md` step 4
   and the gh-issue/Linear `## List` sections — reference them, don't restate the
   card line format.
3. Update the dispatcher note in `commands/list-tasks.md` step 1 so `jira` no
   longer falls into the "not supported" branch (the note currently flags jira as
   the remaining follow-up).
4. Empty backlog reports "No tasks found" rather than erroring.

## Acceptance Criteria

- **Code-enforced:** `just check` passes; `commands/handlers/jira.md` has a
  `## List` section.
- **User-run:** With `handler: jira`, `/list-tasks` renders the project's issues
  mapped from `statusCategory` into kanban sections; `/list-tasks ready` filters
  to one section. Empty backlog reports "No tasks found" rather than erroring.
