---
title: Automate the needs_review transition, its reverse, and an Action backstop
priority: medium
size: 3
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_5
related_files:
  - .github/workflows/
  - commands/handlers/gh-issue-claim.md
tags: [automation, actions, lifecycle]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Automate the needs_review transition, its reverse, and an Action backstop

## Context

On GitHub the review gate is **pre-merge**: the open PR _is_ the `needs_review`
state, and merging is the act of accepting it. `/auto-pilot` never merges
unattended, so a merge is always a human act — which is why merge is a sound
completion signal and closing keywords are used rather than banned.

Note what actually enforces this: **nothing**.
`required_approving_review_count` is 0 on `workflow-skills`, and cannot usefully
be raised because GitHub forbids approving your own PR. The gate holds because
the user presses merge, not because the platform requires it.

## Task

Primary path: the agent that opens the PR sets `status:4_needs_review` in the
same step — `/deliver-task` already writes to the issue there, so this is one
more field, and it works identically locally and in a routine.

Backstop: a GitHub Action that parses the issue number from the branch and sets
the label, catching PRs opened outside the loop. Trigger on **`ready_for_review`,
not `opened`** — a draft PR is not `needs_review`, and the house convention makes
`gregan_finances` PRs always draft.

Reverse transition: a PR closed without merging returns the issue to
`status:3_started`, or it sits in `needs_review` with no open PR forever.

`workflow-skills` is public, so Actions minutes are free.

## Acceptance Criteria

**Code-enforced**

- A test asserts the branch parser extracts the issue number from both prefixes
- The workflow triggers on `ready_for_review` and `closed`, not `opened`
- A test asserts a closed-unmerged PR returns the issue to `status:3_started`

**User-run**

- Open a draft PR on a real issue; confirm the issue does NOT move to needs_review until the PR is marked ready
- Close a PR without merging; confirm the issue returns to started
