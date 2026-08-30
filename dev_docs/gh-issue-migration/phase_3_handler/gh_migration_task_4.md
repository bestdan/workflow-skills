---
title: Build the claim lifecycle to linear-level depth
priority: high
size: 5
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_3
related_files:
  - commands/handlers/gh-issue-claim.md
  - commands/handlers/claim-lock.md
  - commands/handlers/linear-claim.md
tags: [handler, claiming, concurrency]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Build the claim lifecycle to linear-level depth

## Context

`gh-issue-claim.md` is ~16 KB against `linear-claim.md`'s ~42 KB. The atomic
primitive already exists in `claim-lock.md` and is verified; what is missing is
the lifecycle around it. The reported symptom — agents racing for the same issue
— is most likely this gap, not a missing mechanism.

Branch naming has to satisfy three constraints at once: `claim-lock.md` uses
`task/<KEY>`; the issue number must be in the branch name for PR↔issue
traceability; and the house rule requires a `bestdan/` prefix. **`bestdan/task-<N>`
satisfies all three and is deterministic** — which matters, because both racers
must compute the _same_ name. A title-derived slug does not guarantee that.

Routines push to `claude/`-prefixed branches, so any parser must read the issue
number from a fixed position after the prefix, not assume `bestdan/`.

## Task

Bring `gh-issue-claim.md` up to `linear-claim.md`'s depth:

- Acquire the lock **before** any work, per `claim-lock.md` — 201 proceed,
  422 skip to the next candidate, anything else is neither.
- Set the human-visible markers _after_ acquiring: assignee, and
  `status:3_started` via the task 3 helper. These display the claim; they do not
  decide it.
- Branch name `bestdan/task-<N>`; parser accepts any prefix.
- Honor `wip_limit` by counting `status:3_started`.
- Release path when a claim is abandoned.

## Acceptance Criteria

**Code-enforced**

- A test asserts the second of two concurrent claims is refused and does not mutate the issue
- A test asserts the branch-number parser handles both `bestdan/task-N` and `claude/task-N`
- A test asserts `wip_limit` counts `status:3_started` and blocks at the cap

**User-run**

- Run two `/do-tasks` sessions against the same ready issue; confirm exactly one proceeds and the other reports a lost claim
