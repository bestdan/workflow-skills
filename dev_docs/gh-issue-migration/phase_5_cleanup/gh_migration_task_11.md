---
title: Graduate durable decisions to dev_docs and delete the plan folder
priority: low
size: 2
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_10
related_files:
  - dev_docs/
tags: [cleanup, docs]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Graduate durable decisions to dev_docs and delete the plan folder

## Context

The plan folder is temporary scaffolding. The durable material is the
architecture and the reasoning behind it — particularly the parts that are
non-obvious and were established empirically.

## Task

Write `dev_docs/gh_issue_task_loop.md` carrying:

- The label schema and the three invariants.
- **Validate-then-PATCH**, and why neither half works alone (the two spike
  failures).
- Why the claim lock is ref creation and not assignee or `git push` — including
  the measured `Everything up-to-date` exit-0 trap.
- Merge-as-completion, and the fact that the review gate is convention rather
  than enforcement.
- Label provisioning as a migration prerequisite, because transfer drops
  unprovisioned labels.
- Which Linear commands were retired and which remain while any repo is on
  Linear.

Then delete `dev_docs/tasks/gh_migration_plan/`.

**Only retire the Linear handler and its four Linear-only commands once no repo
is still on Linear.** If `finplan` remains, they stay.

## Acceptance Criteria

**Code-enforced**

- `dev_docs/gh_issue_task_loop.md` exists and covers all six points
- `dev_docs/tasks/gh_migration_plan/` no longer exists
- No Linear command is deleted while any repo's `.task-config.yml` still says `handler: linear`

**User-run**

- Read the graduated doc cold and confirm it explains the design without reference to the plan folder
