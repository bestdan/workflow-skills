---
title: Remove /process-tasks and /claim-task in favor of /do-tasks
priority: medium
size: 2
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/process-tasks.md
  - commands/claim-task.md
  - commands/do-tasks.md
  - skills/task/SKILL.md
  - README.md
is_blocked_by:
  - task_10
  - task_11
tags:
  - task-loop
  - do-tasks
  - docs
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_10]] and [[task_11]].

## Context

`/do-tasks` now covers both the file path and the tracker path, so `/process-tasks` and `/claim-task` are redundant. Per the planning decision we **remove them immediately** (no alias/deprecation period) and make `/do-tasks` the sole execute verb. This is the breaking-change task; it must land only after both `/do-tasks` halves exist (tasks 10 and 11).

Flag equivalences to fold into `/do-tasks` so no capability is lost: `/process-tasks [args]` ≡ `/do-tasks [args]` (remote/file default); `/claim-task [id]` ≡ `/do-tasks [id]` (tracker).

## Task

1. Delete `commands/process-tasks.md` and `commands/claim-task.md` (`git rm`). Confirm `/do-tasks` covers every flag they had (`--all`, `--local`, `<slug>`/`<id>`); add any missing flag to `/do-tasks` first.
2. `skills/task/SKILL.md`: replace the "Execute: /process-tasks vs /claim-task" comparison with an "Execute: /do-tasks" section (flag matrix: single vs `--all`, `--remote` vs `--local`, per-handler support). Remove remaining references to the deleted commands across the SKILL (lifecycle, kanban notes, race-conditions section).
3. Grep the whole repo for `/process-tasks` and `/claim-task` and update every reference (handlers, README, plan-with-docs, other skills) to `/do-tasks`. The `repo-pr` defer message in old docs ("use `/process-tasks --local`") becomes "`/do-tasks --local`".
4. `README.md`: update the Task-loop table — `/do-tasks` as the execute row, the two old commands removed; fix the "N skills, M commands, K subagent" count (now one fewer command).
5. Run `just check` — the validator enforces the README count and frontmatter, and will catch dangling references if any.

## Acceptance Criteria

- **Code-enforced:** `just check` passes (README count matches the reduced command set; no broken frontmatter). A repo-wide grep for `/process-tasks` and `/claim-task` returns no stray references outside this plan's own historical files.
- **User-run:** `commands/process-tasks.md` and `commands/claim-task.md` no longer exist. Every workflow they supported is reachable through `/do-tasks` with the documented flags. SKILL.md and README present `/do-tasks` as the only execute verb.
