---
title: Reconciler row 3 must check the rung it looks for is provisioned
priority: medium
size: 1
status: new
created: 2026-09-04
source_branch: bestdan/gh-issue-migration
parent: gh_migration
is_blocked_by: []
related_files:
  - commands/handlers/assets/gh-issue-reconcile.py
  - commands/handlers/gh-issue-reconcile.md
tags: [handler, reconciler, defect]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Reconciler row 3 must check the rung it looks for is provisioned

## Context

**Defect from task 7**, found 2026-09-04 by running task 7's own user-run
acceptance criterion — the `/reconcile-tasks` dispatch check — against
`bestdan/dotfiles`, the one repo already on `handler: gh-issue`.

Row 3 flags an issue closed although it was never labelled
`status:4_needs_review`, by reading the issue's `labeled` events. It never asks
whether that label **exists on the repo**. Where it was never provisioned, no
issue can ever have carried it, so **every** closed issue in the window is a
hit: the run reported **50 of 50**, with total confidence and no signal.

That is the same failure shape this migration keeps meeting — a check
confidently answering a question nobody populated the data for. Task 15's
dependency reads passed everything for the same reason, and task 8's footer
findings would have.

## The prose is also wrong, and that matters more than the count

`gh-issue-reconcile.md` step 2 justifies the label scope by claiming it prevents
exactly this:

> every issue in the repo that is _not_ part of the task loop … is missing both
> rungs and never reached review, so it is a row-2 **and** a row-3 hit.
> Unscoped, the report is mostly issues that were never wrong.

**That claim is false as written.** The 50 issues were correctly scoped by the
configured `task-add` label and still all hit. The scope separates loop issues
from strangers; it does nothing about a rung the repo never provisioned. Fixing
the code without fixing this paragraph leaves the next reader believing the
scope already covers it.

## Task

Give row 3 a preflight: if `status:4_needs_review` is absent from the repo's
labels, the row's premise is void — report **that**, once, instead of one
finding per closed issue. Read the rung from the vocabulary rather than
hardcoding the name, the way `gh-issue-ready.py`'s `ready_label()` does, so a
rename in `labels.yml` fails loudly instead of matching nothing.

Then correct step 2's paragraph to say what the scope does and does not do.

Consider whether row 2 needs the same guard. It flags an open issue missing a
`status:` or `auto:` rung — on an under-provisioned repo the missing rungs may
be unassignable rather than unassigned, which is a different finding. Decide it
rather than inheriting it; the answer may legitimately be no, since row 2 is
flag-only and a human reads it.

**Do not add a fourth row.** Task 7's table is deliberately closed, and this is a
correctness fix to an existing row, not a new rule.

## Acceptance Criteria

**Code-enforced**

- A test asserts that with the rung absent from the repo's labels, row 3 reports
  the provisioning gap and returns **no** per-issue findings
- A test asserts that with the rung present, row 3's existing behaviour is
  unchanged
- The rung name is derived from `labels.yml`, and a test asserts a renamed value
  fails loudly rather than matching nothing

**User-run**

- Re-run the dispatch check against `bestdan/dotfiles` and confirm row 3 reports
  the gap rather than 50 findings. Note that
  [bestdan/dotfiles#675](https://github.com/bestdan/dotfiles/issues/675) may have
  provisioned the labels by then — if so, the fix needs a repo that is still
  under-provisioned, or a fixture, to exercise at all.
