---
title: Warn about handler capability gaps at /task-config
priority: medium
size: 2
status: ready
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/task-config.md
  - commands/handlers/gh-issue-config.md
  - commands/handlers/jira-config.md
  - commands/handlers/linear-config.md
  - commands/handlers/repo-pr-config.md
tags:
  - task-loop
  - handlers
  - ux
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]].

## Context

Handler feature parity is jagged: configuring `jira` gives `/add-task` only (no list/promote/claim/process); `gh-issue` is capture + (after task 9) list; `linear` lacks process(batch) but gains promote after task 8; `repo-pr` is the only full-loop handler. Today `/task-config` (`commands/task-config.md`) writes the config silently, so a user adopts a handler without knowing they've opted out of most of the loop.

This task surfaces a capability matrix at config time. It should reflect reality _after_ the parity tasks land, so keep the matrix in one place that's easy to update.

## Task

1. Add a capability matrix to `commands/task-config.md` (verbs × handlers: add / list / promote / claim or do / process or do). Source of truth — referenced, not duplicated per handler.
2. In step 5 ("Confirm"), after writing the config, print the selected handler's supported and unsupported verbs explicitly, e.g.: "`jira` supports: /add-task. Not supported: /list-tasks, /promote-tasks, /do-tasks. You can still manage these in Jira directly."
3. Cross-check the matrix against the per-handler files so it doesn't drift (note in `CONTRIBUTING.md` "adding a handler" if such a checklist exists, else add a one-liner).

## Acceptance Criteria

- **Code-enforced:** `just check` passes; the matrix in `commands/task-config.md` matches what the handler files actually implement.
- **User-run:** Running `/task-config jira` prints the capability warning naming the unsupported verbs before finishing. `/task-config repo-pr` reports full-loop support.
