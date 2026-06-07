---
title: Implement vetted-plan push to the configured tracker
priority: medium
size: 3
status: ready
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - skills/plan-with-docs/SKILL.md
  - commands/add-task.md
  - commands/handlers/linear-add.md
  - commands/handlers/jira.md
  - commands/handlers/gh-issue.md
is_blocked_by: task_13
tags:
  - task-loop
  - planning
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_13]].

## Context

The design spike settles the trigger, mapping, and idempotency for pushing a vetted local plan to the configured tracker. This task implements the recommended approach. **Do not start until the spike is done** — its output (recommended trigger/command, mapping rules, idempotency mechanism, and any required new frontmatter field) defines this task's concrete steps and may even re-split it.

Provisional shape (subject to the spike): a push action that, when the handler is non-`repo-pr`, walks the vetted plan's `task_N.md` files and creates one tracker issue per task via the existing handler add-flow, translating `is_blocked_by` to native blocker relationships and recording created ids back locally for idempotency. Local-first: nothing pushes automatically on plan write; the user triggers it after vetting.

## Task

1. Implement the trigger/command recommended by the spike (e.g. `/push-plan <name>` or a `plan-with-docs` push step).
2. Reuse the handler add-flows (`linear-add.md`, `jira.md`, `gh-issue.md`) to create issues — do not duplicate create logic.
3. Translate plan structure per the spike's mapping (overview→epic/project, task→issue, `is_blocked_by`→native relationship where supported).
4. Implement the spike's idempotency mechanism so re-running doesn't duplicate issues.
5. Update `skills/plan-with-docs/SKILL.md` to document the local-first → vet → push flow and that plans still draft locally by default.

## Acceptance Criteria

- **Code-enforced:** `just check` passes; any new command has valid frontmatter and is reflected in the README count.
- **User-run:** With `handler: linear` and a vetted plan, the push action creates one Linear issue per task with blocker relationships matching the plan's `is_blocked_by`; running it again does not create duplicates. With `handler: repo-pr`, the push is a no-op (plans already live as files) and says so. Plans still draft to local files by default — nothing is pushed without the explicit trigger.
