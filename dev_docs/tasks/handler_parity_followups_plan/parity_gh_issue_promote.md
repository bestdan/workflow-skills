---
title: Decide gh-issue promote support or record it as a non-goal
priority: low
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/task-config.md
  - commands/handlers/gh-issue.md
  - commands/promote-tasks.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]].

## Context

Matrix cell: **gh-issue × promote** (`/promote-tasks`).

`/promote-tasks` scores `status: new` task files against the confidence check and flips them to `ready` or `needs_refinement`. It is file-only today. The planned parity work (the Linear promote task) only adds a Linear promote path; it does not touch gh-issue. So after that work lands, gh-issue still has no promote verb — issues are created already-actionable and GitHub has no equivalent "new vs ready" gate that `/promote-tasks` could drive.

This may be a **deliberate non-goal**: GitHub Issues manage triage through labels/projects in their own UI, and a repo-side promotion scorer may not map cleanly onto that model.

## Task

1. Decide whether `/promote-tasks` should support the gh-issue handler at all.
2. If **build**: define what "promote" means for a GitHub Issue (e.g. a label transition `task:new` → `task:ready` / `task:needs-refinement` driven by the same confidence check), and what would be involved in `commands/promote-tasks.md` + `commands/handlers/gh-issue.md`.
3. If **non-goal**: record it explicitly so the jagged cell is intentional — mark the cell in the capability matrix in `commands/task-config.md` as a non-goal (not merely "unsupported") and note the rationale in `commands/handlers/gh-issue.md`.

## Acceptance Criteria

- The decision is recorded: either a gh-issue promote path is implemented (matrix updated to supported), or the capability matrix in `commands/task-config.md` and `commands/handlers/gh-issue.md` explicitly mark gh-issue promote as a deliberate non-goal with rationale.
- `just check` passes.
