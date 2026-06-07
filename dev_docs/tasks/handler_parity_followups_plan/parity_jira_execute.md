---
title: Build the jira single-execute path (jira-claim.md)
priority: medium
size: 5
status: new
created: 2026-06-07
tracker_id: PRE-116
tracker_url: https://linear.app/prethinkio/issue/PRE-116/build-the-jira-single-execute-path-jira-claimmd
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/do-tasks.md
  - commands/handlers/jira-claim.md
  - commands/handlers/jira.md
  - commands/handlers/linear-claim.md
  - commands/task-config.md
is_blocked_by: parity_jira_promote
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]]. Blocked by [[parity_jira_promote]] (needs the ready workflow status).

## Context

`/do-tasks` stops for `handler: jira` today. The template is **`commands/handlers/linear-claim.md`**, implemented over the Atlassian MCP. (`claim-task.md`/`process-tasks.md` were removed on `main`; `do-tasks.md` + `repo-pr-execute.md`/`linear-claim.md` are the references now.) The handler must fit the `--claim-only`/`--no-claim` split `/do-tasks` now exposes.

Mapping the Linear claim phases onto Jira:
- **Candidates** — `searchJiraIssuesUsingJql`: items in the `ready_status` (from task 2), unassigned.
- **Atomic claim** — assign self + transition to an In Progress status; race guard = re-read assignee/status, fall back if taken.
- **Branch** — Jira has no native branch primitive, so the handler publishes a deterministic name, `task/<KEY>`.
- **Completion** — PR title `[<KEY>]`; Jira closure happens via the Jira↔GitHub integration / smart commits on merge, **not** a manual transition. Mirror the Linear hard rule: never transition the item to a Done-category status from this handler.

## Task

1. **Write `commands/handlers/jira-claim.md`** mirroring `linear-claim.md` over the Atlassian MCP:
   - **Find candidates** — `searchJiraIssuesUsingJql` for `project = "<project>" AND status = "<ready_status>" AND assignee IS EMPTY ORDER BY priority DESC, updated ASC`, pulling `summary`, `description`, `priority`, `status`, `assignee`.
   - **Judge feasibility** — ranked order, stop at the first finishable, comment skip reasons.
   - **Claim** — assign self + transition to In Progress (resolve transition ids dynamically, as in task 2); re-read; fall back on a race.
   - **Branch + execute** — branch `task/<KEY>`, do the work, run `just check`.
   - **PR** — `gh pr create` with `[<KEY>]` in the title and the item URL / `<KEY>` in the body; post the PR URL as a Jira comment.
   - **Move to review** — transition to an In Review status if the workflow has one; else stay In Progress. Never transition to a Done-category status.
   - **Bail** — transition back to the ready/backlog status, request human approval (label or comment), unassign, comment the bail reason.
2. **Honor the claim/execute split**: `--claim-only` runs the claim and stops (assignee + In Progress status is the reservation marker); `--no-claim <KEY>` resumes an item already claimed by this caller (check out `task/<KEY>`, guard on assignee/status).
3. **Pre-claim WIP gate** mirroring Linear: count in-flight items (In Progress / In Review category) for the project against `wip_limit`; decline at the limit.
4. **Route in `commands/do-tasks.md`** section 1 — `jira` dispatches to `jira-claim.md` (foreground single). Batch execution degrades to single until task 7; `--claim-only` batchable. Add the Atlassian MCP tool names to `do-tasks.md` `allowed-tools`.
5. **Flip the matrix cell** `do — single × jira` to `yes` in `commands/task-config.md`.

## Acceptance Criteria

**Code-enforced**
- `commands/handlers/jira-claim.md` mirrors the `linear-claim.md` phases incl. an assignee+status atomic-claim race guard and `--claim-only`/`--no-claim`, and never transitions to a Done-category status.
- `commands/do-tasks.md` routes `jira` to it (single/foreground), WIP gate enforced.
- The `do — single × jira` matrix cell reads `yes`.
- `just check` passes.

**User-run**
- With `handler: jira`, `/do-tasks` claims a ready-status unassigned item, opens a PR referencing `<KEY>`, and transitions it to In Progress (then In Review on PR open if available).
- A concurrent claim hits the race guard and falls back.
