---
title: Fix the stale promote×linear matrix cell and keep the capability matrix in sync
priority: low
size: 1
status: new
created: 2026-06-07
tracker_id: PRE-114
tracker_url: https://linear.app/prethinkio/issue/PRE-114/fix-the-stale-promotelinear-matrix-cell-and-keep-the-capability-matrix
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/task-config.md
  - commands/promote-tasks.md
  - commands/handlers/linear-promote.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]].

## Context

The capability matrix **already exists** in `commands/task-config.md` ("Handler capability matrix") and is the single source of truth that `/task-config` step 5 reads to tell users which verbs a handler supports. It does **not** need to be created.

It has a **stale cell**: `promote × linear` shows `no`, but `commands/handlers/linear-promote.md` is built and `/promote-tasks` step 0 dispatches to it. This is a pre-existing bug independent of the rest of this plan — fix it now.

The other parity tasks each flip their own cell as they land, so this task is the standing consistency check rather than a bulk update.

## Task

1. **Fix the stale cell now:** set `promote × linear` to `yes` in the `commands/task-config.md` matrix, and update the prose in step 5 (the `linear` bullet currently says "Not supported: /promote-tasks") to reflect that Linear promote is supported.
2. **Verify the possible second stale cell:** `commands/handlers/gh-issue.md` has a full `## List` section and `commands/list-tasks.md` dispatches `gh-issue` to a handler `## List` section. Check whether gh-issue list is actually wired and, if so, correct `list × gh-issue` from `no` to `yes`. (If it stops short, leave it `no` and note why.)
3. **Final sync:** once the parity build tasks have landed, confirm every matrix cell matches an actual dispatch path + handler file (don't mark a cell `yes` on the strength of this plan alone — check the merged code).

## Acceptance Criteria

**Code-enforced**
- `promote × linear` reads `yes` and the `/task-config` step 5 prose matches.
- `list × gh-issue` reflects the actual wiring (corrected or justified).
- Every `yes` cell has a real dispatch path + handler file.
- `just check` passes.

**User-run**
- Running `/task-config` and choosing `linear` reports `/promote-tasks` as supported.
