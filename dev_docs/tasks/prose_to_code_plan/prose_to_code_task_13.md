---
title: Share one kanban-classify.py across the linear, gh-issue and jira list handlers
priority: medium
size: 3
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/linear-list.md:28-42
  - commands/handlers/gh-issue.md:117-129
  - commands/handlers/jira.md:115-127
  - commands/list-tasks.md
  - commands/handlers/assets/_labels.py
is_blocked_by: [prose_to_code_task_14]
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - cross-handler
---

Plan: [[prose_to_code_plan]] · Index row 16.

## Context

Three handlers carry a seven-section table mapping (state type or category,
labels) → kanban section with a precedence order (blocked > needs_review >
in_progress > ready > needs_refinement). The tables are close, not identical:
Linear's `needs_review` depends on linked-PR data the payload may lack. The
first step is to diff them and decide whether the differences are tracker
facts (keep, in a field map) or drift (fix).

## Task

1. Diff the three tables and write the result into this task before coding;
   if a difference is drift, fix the prose in the same PR.
2. Add `commands/handlers/assets/kanban-classify.py`: input, JSON rows of
   `{id, category, labels[], has_open_pr?}` plus `--tracker
   linear|gh-issue|jira` selecting a field map; output, the rows grouped by
   section in the fixed render order, sorted within section by the tracker's
   priority rule, importing `_linear_rank.py` from task 14 for the Linear
   ordering (this task is blocked on it so the None-last rule has one home).
3. Tests: each tracker's map, the blocked-over-needs_review tie, an issue
   matching no rule.
4. Rewrite the three list sections to map fields in prose and call the helper
   for classification and order.

## Acceptance Criteria

- Code-enforced: `just check` passes; the tie case is pinned per tracker.
- Code-enforced: the precedence sentence appears once, in the helper's header,
  and the three handlers link to it.
- User-run: `/list-tasks` on a Linear repo and on this repo (gh-issue) render
  the same sections as before the change.
