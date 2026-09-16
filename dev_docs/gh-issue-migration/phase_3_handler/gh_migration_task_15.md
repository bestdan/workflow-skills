---
title: Create native dependency edges on the write side
priority: high
size: 3
status: done
created: 2026-08-31
completed: 2026-09-02
tracker_id: "bestdan/workflow-skills#444"
source_branch: bestdan/gh-issue-migration
parent: gh_migration
related_files:
  - commands/push-plan.md
  - commands/handlers/gh-issue.md
  - commands/task-config.md
  - commands/handlers/gh-issue-reoptimize.md
tags: [handler, dependencies]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Create native dependency edges on the write side

## Context

**The read side migrated and the write side did not, so dependency-blocking is inert.**
Task 5 taught `/list-tasks` to read blocked-ness from GitHub's native graph
(`gh-issue.md:121`, via `repos/<repo>/issues/<n>/dependencies/blocked_by`), and task 4
taught `/do-tasks` to drop dependency-blocked candidates the same way
(`gh-issue-ready.py --issue N`). Neither is wrong. But **nothing in the repo creates an
edge**: `push-plan.md` writes a `Blocked by: #<n>` prose footer into the issue body, and
there is no `POST` to the dependencies endpoint anywhere in `commands/` or `skills/`.

So every issue the loop itself files has an empty `blocked_by` list, and both checks pass
everything. They are correct code answering a question nobody populated the data for.
A human adding an edge in the GitHub UI is currently the only way either check fires.

Three files still assert the platform lacks the feature, which is how the gap survived:

- `commands/handlers/gh-issue-reoptimize.md` — 7 places, including its opening paragraph
- `commands/task-config.md:33` — the reoptimize row's justification
- `commands/push-plan.md:282` — the reason it writes a footer instead

That claim was true when written and is now false. **Task 8's context records the
measurement**: `POST` to `/issues/{n}/dependencies/blocked_by` creates a real edge that
reads back, confirmed on a personal-account repo. Do not re-derive it, and do not treat
the three stale assertions as evidence against it.

## Scope, against task 8

Task 8 owns **reoptimize**: applying dependency _edits_ natively and migrating existing
footers. This task owns the **origin** of an edge — `/push-plan`, which is what files a
plan's issues in the first place — plus retiring the platform claim wherever it is
asserted. Task 8 can then be about reoptimize's own logic rather than about whether the
edge exists. Either order works; doing this one first is what makes the two shipped read
paths do anything.

## Task

- Have `/push-plan`'s gh-issue path create a native `blocked_by` edge per dependency
  instead of (or alongside, if the footer is still wanted for human readability) the
  body-footer line. Mind `push-plan.md:91`'s note that v1 never updates an issue after
  creation — an edge needs the blocker's issue number, so the edges are a second pass
  after all issues exist, in dependency order.
- Retire the "no native dependency edge" claim in the three files above. Where a file
  keeps the footer deliberately, say that it is a human-readable echo of the edge, not a
  substitute for one.
- Decide and record whether the footer stays. Two readers already exist (`gh-issue.md`,
  `gh-issue-reoptimize.md`); task 8 proposes dropping it entirely.

## Acceptance Criteria

**Code-enforced**

- A test asserts `/push-plan` on a plan with a dependency issues a `POST` to
  `dependencies/blocked_by`, not only a body write
- A test asserts the edges are created after every issue exists, so a blocker's number is
  always resolvable
- `rg 'no native dependency edge'` returns nothing

**User-run**

- Push a two-task plan with one dependency to a scratch repo; confirm the edge shows in
  the GitHub UI, then confirm `/list-tasks` reports the dependent as blocked and
  `/do-tasks` refuses to claim it
