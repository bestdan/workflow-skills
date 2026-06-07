---
title: Build the jira promote handler (dynamic workflow-status transitions)
priority: medium
size: 5
status: new
created: 2026-06-07
tracker_id: PRE-112
tracker_url: https://linear.app/prethinkio/issue/PRE-112/build-the-jira-promote-handler-dynamic-workflow-status-transitions
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/promote-tasks.md
  - commands/handlers/jira-promote.md
  - commands/handlers/jira.md
  - commands/handlers/jira-config.md
  - commands/handlers/linear-promote.md
  - commands/task-config.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]]. The promote dispatch spine already exists upstream.

## Context

`/promote-tasks` step 0 already resolves the handler; the `jira` branch currently **stops** ("promotion not supported"). This task flips it to dispatch, modeled on the shipped `commands/handlers/linear-promote.md`.

Jira differs from gh-issue/linear: there is no Jira `## List` flow or label vocabulary established yet, and **Jira workflow statuses are project-configurable** — there is no fixed "Ready" status to assume. So jira promote drives a **workflow-status transition** (not a label), and the handler must resolve the available transitions dynamically. This is why this task is size 5 while gh-issue promote is size 3.

## Task

1. **Flip the dispatch** in `commands/promote-tasks.md` step 0: `jira` → dispatch to `commands/handlers/jira-promote.md` (mirror the `linear` branch). Add the Atlassian MCP tool names to `allowed-tools`.
2. **Config.** Extend the `jira:` block (and `commands/handlers/jira-config.md`) with optional `ready_status` / `refinement_status` keys (e.g. `Selected for Development` / `Needs Refinement`). If unset, the handler resolves the available transitions and prompts via `AskUserQuestion`, then notes the chosen statuses for the user to persist.
3. **Write `commands/handlers/jira-promote.md`**, modeled on `linear-promote.md`:
   - Preflight reachability exactly as `jira.md` step 1 (`getAccessibleAtlassianResources`, site match).
   - Query candidates in the project's initial/new status via `searchJiraIssuesUsingJql` (e.g. `project = "<project>" AND statusCategory = "To Do" ORDER BY updated DESC`), pulling `summary`, `description`, `status`, `priority`.
   - Score each against the shared confidence check (the field-mapped gate from `linear-promote.md` step 6), reading the description.
   - **Resolve transitions dynamically** — confirm the exact Atlassian MCP tool names at implementation (expected `getJiraIssueTransitions` read + `transitionJiraIssue` write under the `mcp__claude_ai_Atlassian__*` / `mcp__atlassian__*` prefixes used elsewhere). Map `ready_status`/`refinement_status` to their transition ids per item (ids vary per workflow).
   - Apply: HIGH → transition to `ready_status`; LOW → transition to `refinement_status` + a comment naming the failed check. Honor `dry-run`.
   - Report in the `linear-promote.md` step 8 shape, keyed by Jira key.
4. **Flip the matrix cell** `promote × jira` to `yes` in `commands/task-config.md`.

## Acceptance Criteria

**Code-enforced**
- `commands/promote-tasks.md` dispatches `jira` to `jira-promote.md`.
- `jira-promote.md` resolves transitions dynamically (no hard-coded status ids), scores via the shared confidence check, and honors `dry-run`.
- `ready_status`/`refinement_status` config keys are documented; absence triggers the prompt path.
- The `promote × jira` matrix cell reads `yes`.
- `just check` passes.

**User-run**
- `/promote-tasks` with `handler: jira` transitions a well-formed item to the ready status and an underspecified one to the refinement status (+ comment).
- `/promote-tasks dry-run` mutates nothing.
