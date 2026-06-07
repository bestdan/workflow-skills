---
title: Build jira batch (/do-tasks --all) on the tracker-batch subroutine
priority: low
size: 3
status: new
created: 2026-06-07
tracker_id: PRE-118
tracker_url: https://linear.app/prethinkio/issue/PRE-118/build-jira-batch-do-tasks-all-on-the-tracker-batch-subroutine
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/do-tasks.md
  - commands/handlers/jira-claim.md
  - commands/task-config.md
is_blocked_by:
  - parity_jira_execute
  - parity_linear_batch
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]]. Blocked by [[parity_jira_execute]] and [[parity_linear_batch]].

## Context

With jira single-execute (`jira-claim.md`, task 4) and the reusable tracker-batch subroutine (task 5) in place, jira batch is a thin reuse: substitute the jira candidate query and atomic claim into the subroutine. Jira's native `is blocked by` issue links give a clean dependency-ready signal (unlike gh-issue, this is not an open question for Jira).

## Task

1. **Wire jira into the tracker-batch subroutine** in `commands/do-tasks.md`: when `--all`/`-n N` and `handler: jira`, follow the task-5 subroutine, substituting:
   - **Candidate query** — `searchJiraIssuesUsingJql` for ready-status, unassigned items, dependency-ready = all `is blocked by` linked issues resolved (Done-category). Express via JQL where possible or filter after fetching link data.
   - **Atomic claim** — the assignee + status-transition guard from `jira-claim.md`.
   - Each selected item → its own remote session running `jira-claim.md`.
2. Apply the same `wip_limit` slack and dispatched/held reporting as the other batch paths.
3. **Flip the matrix cell** `process — batch × jira` to `yes` in `commands/task-config.md`.

## Acceptance Criteria

**Code-enforced**
- `commands/do-tasks.md` routes jira `--all`/`-n N` through the tracker-batch subroutine with a jira candidate query and the `jira-claim.md` atomic claim.
- Dependency-ready selection uses Jira native blocker links.
- The `process — batch × jira` matrix cell reads `yes`.
- `just check` passes.

**User-run**
- `/do-tasks --all` with `handler: jira` dispatches multiple remote sessions for distinct ready, unblocked items, bounded by `wip_limit`; blocked/held items are reported with the reason.
