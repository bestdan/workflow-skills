---
title: Rename handler content, part 2 (jira and linear handlers)
priority: medium
size: 3
status: done
created: 2026-06-06
source_branch: bestdan/refactor/task-vocabulary
source_pr: 18
related_files:
  - commands/handlers/jira.md
  - commands/handlers/jira-config.md
  - commands/handlers/linear-common.md
  - commands/handlers/linear-add.md
  - commands/handlers/linear-list.md
  - commands/handlers/linear-claim.md
  - commands/handlers/linear-config.md
is_blocked_by: task_vocabulary_normalization_task_3
expires: 2026-07-06
tags:
  - refactor
  - handlers
---

← [[task_vocabulary_normalization_plan]]

# Task 4 — Rename handler content, part 2

Convert "todo" → "task" wording in the jira and linear handlers.

## Context

- Same as Task 3: filenames stay; content only. These are the external-tracker handlers.
- Linear is a five-file family sharing `linear-common.md`; keep terminology consistent across all five so the per-verb files (`linear-add`, `linear-list`, `linear-claim`) agree with the common file.
- Several of these files use "step" as a _procedural_ word — leave those; only rename the unit-of-work noun and the loop nouns/paths/commands.

## Task

Content rename (same substitutions as Task 3) in:

- `commands/handlers/jira.md`
- `commands/handlers/jira-config.md`
- `commands/handlers/linear-common.md`
- `commands/handlers/linear-add.md`
- `commands/handlers/linear-list.md`
- `commands/handlers/linear-claim.md`
- `commands/handlers/linear-config.md`

## Acceptance Criteria

### Code-enforced

- `rg -n '\btodos?\b|todo-loop|\.todo-config|/(add|claim|promote|process|list)-todo' commands/handlers/jira*.md commands/handlers/linear*.md` → no matches.
- `dprint check` passes on the seven files.
- `uv run scripts/validate.py` → OK.

### User-run

- None.
