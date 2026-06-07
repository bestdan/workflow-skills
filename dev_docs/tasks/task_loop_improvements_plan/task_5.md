---
title: Add WIP limit to batch dispatch in /process-tasks --all
priority: high
size: 2
status: new
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/process-tasks.md
  - commands/handlers/repo-pr-config.md
  - skills/task/SKILL.md
tags:
  - task-loop
  - wip
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]].

## Context

`/process-tasks --all` (`commands/process-tasks.md` step 2 + step 4) dispatches every dependency-ready task in parallel — unbounded. This floods the real bottleneck (human PR review / the `needs_review` column). Kanban's defining constraint is a WIP limit; add one.

The cap counts work already in flight: tasks in `in_progress` plus open `task-loop` PRs (the `needs_review` signal queried in `commands/list-tasks.md`). Default cap 3, configurable via `.task-config.yml` (the repo-pr handler config). This task lands against today's `/process-tasks`; the later `/do-tasks` merge inherits it.

## Task

1. Define a `wip_limit` config key (default 3) — document it in `commands/handlers/repo-pr-config.md` and `skills/task/SKILL.md`.
2. `commands/process-tasks.md`: before dispatching under `--all`, count current WIP = (task files with `status: in_progress`) + (open PRs with label `task-loop`, via `gh pr list --label task-loop --state open`). Dispatch only up to `wip_limit - current_wip` tasks, highest-ranked first; report the rest as "held (WIP limit N reached)".
3. If `gh` is unavailable, count only `in_progress` files and note the count may undercount open PRs.
4. Single-task mode (`/process-tasks` / `/process-tasks <slug>`) is unaffected — the cap only gates `--all`.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** With `wip_limit: 2`, one task already `in_progress`, and three `ready` tasks, `/process-tasks --all` dispatches exactly one and lists the other two as held. With no WIP and `wip_limit: 3`, it dispatches at most 3.
