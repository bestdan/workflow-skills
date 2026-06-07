---
title: Size-gate /do-tasks auto-routing (headless small, human-claim big)
priority: medium
size: 3
status: in_progress
created: 2026-06-07
source_branch: task/task_10
related_files:
  - commands/do-tasks.md
  - commands/handlers/repo-pr-config.md
  - skills/task/SKILL.md
is_blocked_by: task_15
tags:
  - task-loop
  - do-tasks
  - routing
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_15]].

## Context

The intended operating model is **both**: a headless runner drains small tasks autonomously, while bigger tasks stay human-in-the-loop. Today that triage is manual — a person decides what the loop may auto-execute. The `size` field (1/2/3/5) plus the `--claim-only` flag from [[task_15]] make it automatic: a single `/do-tasks --all` pass can self-route by size.

This turns the two manual flags into a policy: the loop executes what it should and parks the rest on a human's plate, reserved but not started, without anyone hand-sorting the backlog.

## Task

1. Add an optional `auto_execute_max_size` key to the repo-pr handler config (`.task-config.yml`, documented in `commands/handlers/repo-pr-config.md`). Default: `2` (auto-do size 1–2; reserve 3+). Absent ⇒ default.
2. `commands/do-tasks.md`: in `--all` / `-n N` selection, after ranking and the WIP gate, split the batch by `size`:
   - `size <= auto_execute_max_size` → claim + execute (the normal path).
   - `size > auto_execute_max_size` → treat as `--claim-only`: reserve it (per [[task_15]]) and do **not** execute.
3. Report the two groups distinctly: executed/dispatched vs. `reserved for human (size N > auto_execute_max_size)`.
4. Single-task mode (`/do-tasks` / `/do-tasks <slug>`) is **not** size-gated — an explicit pick is an explicit instruction to do it. The gate applies only to the batch (`--all` / `-n N`) auto-routing path.
5. Explicit flags override the gate: `--claim-only` reserves regardless of size; an explicit run on one big task still executes it.
6. Document the routing rule and the config knob in the `/do-tasks` section of `skills/task/SKILL.md`.

## Acceptance Criteria

- **Code-enforced:** `just check` passes.
- **User-run:** With `auto_execute_max_size: 2` and a mixed backlog, `/do-tasks --all` executes the size 1–2 tasks and reserves the size 3+ ones (`--claim-only` semantics), reporting the two groups separately. `/do-tasks <big-slug>` still executes despite exceeding the threshold. Removing the config key falls back to the default of 2.
