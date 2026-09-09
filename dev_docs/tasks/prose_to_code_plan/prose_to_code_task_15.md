---
title: Graduate the prose-to-code round-2 decisions and delete the plan folder
priority: low
size: 1
impact: 2
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - dev_docs/deterministic-code-opportunity.md
  - dev_docs/2026-09-09-prose-to-code-index.md
  - dev_docs/tasks/prose_to_code_plan/
is_blocked_by: [prose_to_code_task_1, prose_to_code_task_2, prose_to_code_task_3, prose_to_code_task_6, prose_to_code_task_7, prose_to_code_task_8, prose_to_code_task_9, prose_to_code_task_10, prose_to_code_task_11, prose_to_code_task_12, prose_to_code_task_13, prose_to_code_task_14]
parent: prose_to_code
expires: 2026-10-09
tags:
  - cleanup
---

Plan: [[prose_to_code_plan]] · Plan lifecycle.

## Context

`dev_docs/tasks/` holds live scaffolding only. The durable record of this
round belongs beside the first audit's, and the index rows that shipped need
their PR links so the snapshot says what happened to them.

## Task

1. Add a "Round 2 — delivered" section to
   `dev_docs/deterministic-code-opportunity.md`: the shipped script
   interfaces (one table row each, mirroring the 2026-07-21 section), and the
   load-bearing gotchas discovered while building them.
2. In `dev_docs/2026-09-09-prose-to-code-index.md`, add the PR link to each
   shipped row's Evidence or Note cell. Do not rewrite the rows.
3. Delete `dev_docs/tasks/prose_to_code_plan/` (on the gh-issue handler
   `/push-plan` may already have removed it; verify before deleting).

## Acceptance Criteria

- Code-enforced: `just check` passes.
- User-run: `eza dev_docs/tasks/` shows no `prose_to_code_plan/`.
