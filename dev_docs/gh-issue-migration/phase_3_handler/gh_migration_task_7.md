---
title: Add reconciler rules for the label invariants
priority: medium
size: 3
status: done
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_5
related_files:
  - commands/reconcile-tasks.md
  - commands/handlers/
tags: [reconciler, invariants]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Add reconciler rules for the label invariants

## Context

The full-set PATCH makes a double-status-label state structurally impossible on
the happy path, so the reconciler is an **audit**, not a load-bearing repair.
It still matters: labels can be edited by hand in the web UI, and the user does
use GitHub's UI.

Rule 3 is the backstop for the one residual risk of merge-as-completion — a
stray or mistaken `Closes #123` closing an issue that never passed review.

## Task

Add three rules to the `gh-issue` reconciler, dry-run by default like the
existing sweeps:

1. An open issue with two or more `status:` labels → keep the highest number
   (`max()`, which is why the ladder is numbered), drop the rest.
2. An open issue missing a `status:` or `auto:` label → flag. Do not guess.
3. An issue closed while it never carried `status:4_needs_review` → flag as a
   possible stray closing keyword.

Rule 3 needs the issue's label history, from the timeline `labeled` events.

## Acceptance Criteria

**Code-enforced**

- A test asserts rule 1 keeps the highest-numbered status label
- A test asserts rule 2 flags rather than assigns a default
- A test asserts rule 3 detects an issue closed without ever being labelled needs_review
- All three rules are no-ops without `--apply`

**User-run**

- Hand-edit an issue in the GitHub UI to carry two status labels; confirm a dry run reports it and `--apply` repairs it
