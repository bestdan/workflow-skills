---
title: Fix the --no-claim resume branch name task 4 missed
priority: high
size: 1
status: done
created: 2026-08-31
source_branch: bestdan/gh-issue-migration
parent: gh_migration
related_files:
  - commands/do-tasks.md
tags: [handler, claiming, defect]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Fix the `--no-claim` resume branch name task 4 missed

## Context

**This is a defect shipped by task 4 (PR #442), not new work.** That PR renamed the
gh-issue claim branch from `task/<n>` to `<branch_prefix>task-<n>` and updated
`commands/do-tasks.md`'s `--claim-only` bullet and its `--no-claim` guard label. It did
not update the `--no-claim` **resume** bullet three items further down.

`commands/do-tasks.md:131-133` still reads:

> - `gh-issue`: check out the handler's deterministic claim branch `task/<n>`
>   (`git fetch origin && git switch task/<n>`), which the claim pushed as its lock;
>   create it (`git switch -c task/<n> origin/<base>`) only when the claim ran on the
>   degraded election path.

The claim never creates `task/<n>` any more. So a `/do-tasks --no-claim <#n>` run fetches
a branch that does not exist, reads that as the degraded-election case, and creates a
**second** branch under the old name — off the lock, off the work, and invisible to the
pre-flight probe that looks for the real one.

Co-review caught the two sibling references in the same file and missed this one, which
is worth remembering: the three references are far enough apart that a reader checking
"did do-tasks get updated?" sees a yes at line 92 and stops.

The adjacent **jira** bullets (`do-tasks.md:137-139`) are correct as they stand — jira's
branch is still `task/<KEY>`. Do not sweep them up.

## Task

Point the gh-issue `--no-claim` resume bullet at `<branch_prefix>task-<n>`, matching
`commands/handlers/gh-issue-claim.md`'s own `--no-claim` section, and have it resolve the
name through `gh-issue-claim.py branch-name` rather than spelling a literal.

While in the file, grep it for any other gh-issue reference to the old branch name or the
old label vocabulary — this defect exists because a partial sweep read as a complete one.

## Acceptance Criteria

**Code-enforced**

- `rg 'task/<n>' commands/do-tasks.md` returns nothing (the jira `task/<KEY>` references
  survive and are expected)

**User-run**

- Read the `--no-claim` section end to end and confirm the gh-issue and jira bullets each
  name their own handler's branch

## Outcome — PR #443

The sweep found **two** stale references, not one: the `--no-claim` resume bullet named in
this file, and section 4's summary of what `gh-issue-claim.md` holds, which also still
called move-to-review a "label swap" rather than a rung move.

The fix is the resolver, not the string. The bullet now calls
`python3 commands/handlers/assets/gh-issue-claim.py branch-name` instead of spelling a
literal, because `gh-issue.branch_prefix` is per-repo and any hardcoded name is right in
one repo and wrong in the next — the class of mistake that caused the defect.

Co-review then caught the fix's own version of the same failure: the resolver was first
written as a bare `gh-issue-claim.py …`, with no interpreter and no path. The script is not
on `PATH`, so an agent following the line would get `command not found` and fall straight
back to spelling the branch by hand — the exact behaviour the instruction forbids.

jira's `task/<KEY>` references are untouched, as scoped.
