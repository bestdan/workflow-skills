---
title: Export Linear and import the active workflow-skills issues
priority: high
size: 5
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_8
related_files:
  - commands/handlers/linear-common.md
tags: [migration, data]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Export Linear and import the active workflow-skills issues

## Context

781 issues exist; 513 are archived and may be discarded, **but export first**.
Those Linear keys are baked into branch names and commit messages already in git
history (e.g. `bestdan/ss-earnings-record-task-6`), so deleting without an export
turns a free snapshot into a permanent provenance hole. One GraphQL call with the
API key `linear-archive.md` already holds.

Only `workflow-skills` migrates in this plan — 84 backlog issues plus its share
of the active set. `finplan` stays on Linear as the control.

**Labels must be provisioned before any issue lands** (task 2), or the state is
silently lost on arrival.

## Task

1. Export **all 781** issues to a date-named JSON file kept outside the plan
   folder — identifiers, titles, bodies, comments, labels, estimates,
   priorities, relations, sub-issue links, attachment URLs.
2. Import the active `workflow-skills` issues to GitHub: map status/priority/
   estimate to labels via the task 3 helper, recreate `blocks` edges natively,
   recreate sub-issue links.
3. Map Linear projects to GitHub milestones — half of all reads are
   project-scoped, so the grouping dimension must survive.
4. Record a Linear-key → GitHub-number mapping in the export file so historical
   branch names stay traceable.

Do **not** delete anything in Linear until task 10 decides.

## Acceptance Criteria

**Code-enforced**

- The export file contains all 781 issues including archived, with comments and relations
- A verification script asserts every imported issue has exactly one `status:` and one `auto:` label
- A verification script asserts the dependency edge count matches the source

**User-run**

- Spot-check five imported issues in the GitHub UI against their Linear originals — labels, blockers, milestone, comments
- Confirm the export file is stored outside `dev_docs/tasks/` and is not gitignored away by accident
