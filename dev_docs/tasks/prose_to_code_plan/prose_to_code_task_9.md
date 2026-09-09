---
title: Generalise gh-issue-claim.py acquire for the shared claim-lock prose
priority: medium
size: 2
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - commands/handlers/claim-lock.md:35-93
  - commands/handlers/jira-claim.md
  - commands/handlers/assets/gh-issue-claim.py:195-229
  - scripts/test_gh_issue_claim.py
is_blocked_by: [prose_to_code_task_1]
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
---

Plan: [[prose_to_code_plan]] · Index row 6.

## Context

The create-only ref acquire (POST `git/refs`, 201 won / 422 lost / other →
fall back to election) is implemented and tested in
`gh-issue-claim.py cmd_acquire`, with the measured plain-push defect in its
docstring. `claim-lock.md` and `jira-claim.md` still hand-walk the same
sequence because the asset is named for gh-issue and takes an issue number,
not a branch. Two sources of truth for a race-critical primitive.

## Task

1. Add a tracker-neutral entry point to `gh-issue-claim.py`:
   `acquire-ref --repo <o/n> --branch <name> --base-sha <sha>` sharing
   `cmd_acquire`'s body and exit codes (0 won / 3 lost / 4 other). Keep the
   existing `acquire` subcommand unchanged.
2. Extend `scripts/test_gh_issue_claim.py` with the new entry point (same
   stubbed `gh` seam).
3. Rewrite `claim-lock.md` "acquire" to call it and describe the exit codes;
   point `jira-claim.md`'s claim step at the same call with the Jira branch
   name.

## Acceptance Criteria

- Code-enforced: `just check` passes; the 422 case is pinned for the new
  entry point.
- Code-enforced: `claim-lock.md` no longer spells out the POST/422 dispatch.
