---
title: Add /doctor config and schema health command
priority: medium
size: 5
status: ready
created: 2026-06-07
source_branch: task/task_10
related_files:
  - commands/task-config.md
  - skills/task/SKILL.md
  - scripts/validate.py
  - commands/handlers/repo-pr-config.md
is_blocked_by: task_1
tags:
  - task-loop
  - doctor
  - health
  - ux
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_1]] (schema additions give the drift check something to check).

## Context

The task loop heals legacy setups by running a migration as a side-effect of any command ("migrate once on contact" — `skills/task/SKILL.md`). That auto-heal is worth keeping, but it is the _implicit_ path and covers only one thing (the `dev_docs/todos/` → `dev_docs/tasks/` move). As this plan lands, setups can drift in more ways: a `.task-config.yml` with an unknown `handler:`, a tracker handler whose prerequisites (gh auth, MCP reachability) aren't met, task files missing newly-required fields from [[task_1]] (`impact`, `parent`, list-valued `is_blocked_by`), and accumulated cruft (expired tasks past `expires`, orphaned `task-loop` branches/labels).

A `/doctor` gives users one **explicit** "diagnose and fix my setup" entry point. It does **not** replace the migrate-on-contact preflight — that stays as the implicit auto-heal so a stale setup still works without knowing `/doctor` exists. Both reference the single migration procedure in `skills/task/SKILL.md` so the logic lives in one place.

## Task

1. Create `commands/doctor.md`: a read-only-by-default diagnostic that runs a set of checks, reports a pass/warn/fail line per check, and applies fixes only with an explicit `--fix` flag (argument-hint `[--fix]`).
2. Checks (each reports status + remediation hint):
   - **Config valid** — `.task-config.yml` parses and `handler:` is a known value (`repo-pr`/`gh-issue`/`jira`/`linear`); point unknowns at `/task-config`.
   - **Handler prerequisites** — for the configured handler, the things its own handler doc requires: `gh auth status` for repo-pr/gh-issue; MCP reachability for linear/jira.
   - **Legacy dirs** — `dev_docs/todos/` (tasks) or `dev_docs/todo/` (plans) present → delegate to the existing **Legacy migration** procedure in `skills/task/SKILL.md` (reference, don't restate). This is one check among many, not the command's reason to exist.
   - **Schema drift** — task files missing fields that [[task_1]] made required, or with out-of-range `size`/`impact`. Reuse the rules in `scripts/validate.py` rather than re-specifying them.
   - **Hygiene** — tasks past `expires` while non-terminal (candidates for pruning); open `task-loop` PRs / branches with no matching task file (orphans).
3. Without `--fix`: report only. With `--fix`: apply the safe, mechanical fixes (run the migration, prune expired, fill defaulted fields) and leave judgment calls (unknown handler, failing auth) as reported warnings.
4. Add `/doctor` to `skills/task/SKILL.md` and the README command table; bump the README component count (enforced by `scripts/validate.py`).

## Acceptance Criteria

- **Code-enforced:** `just check` passes; `commands/doctor.md` exists with valid frontmatter; README count updated.
- **User-run:** In a repo with a legacy `dev_docs/todos/` dir and a task file missing a required field, `/doctor` reports both as fails with remediation hints and changes nothing; `/doctor --fix` runs the migration and fills/flags the field. With a clean, current setup, every check reports pass.

> If the check set grows past the size-5 budget, split the hygiene checks into a follow-on task — keep the first PR to config + prerequisites + legacy + schema drift.
