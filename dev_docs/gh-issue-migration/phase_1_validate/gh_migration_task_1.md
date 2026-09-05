---
title: Probe whether POST /git/refs works inside a cloud routine
priority: high
size: 2
status: done
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
related_files:
  - commands/handlers/claim-lock.md
tags: [research, claim-lock, routines]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Probe whether POST /git/refs works inside a cloud routine

## Context

`claim-lock.md` locks on create-only ref creation: `POST /git/refs` returns
`201` for the winner and `422 Reference already exists` for everyone else. This
was **verified against the live GitHub API** and works for same-account racers —
which is the reported failure mode, agents racing for the same issue.

What is **not** known is whether it works inside a Claude Code cloud routine.
`claim-lock.md` currently asserts that web sessions are pinned to a
`claude/<session>` branch and cannot create `task/<KEY>`, degrading to a weaker
comment-token election. That assertion may be stale: current routines docs say
non-`claude/` pushes _are_ accepted unless the branch is protected, carries
another author's commits, or has someone else's open PR — and the claim is a
`gh api` call, not a `git push`, so the push-check may not apply at all.

This decides whether unattended agents get the real lock or the degraded
election. Nothing else in the plan depends on the answer, but the handler's
claim path branches on it.

## Task

Create a scratch private repo. Write a routine at claude.ai/code/routines whose
prompt runs, against that repo:

1. `POST /repos/<repo>/git/refs` creating `refs/heads/bestdan/task-1` — record
   the HTTP status.
2. The same call again — confirm `422 Reference already exists`.
3. `echo $GH_TOKEN` to record whether the proxy placeholder or a real token is
   in play.

Run it, read the transcript, and record the result. Then update
`commands/handlers/claim-lock.md`: either delete the stale branch-pinning
assertion and its fallback, or keep the fallback and correct the stated reason.

## Acceptance Criteria

**Code-enforced**

- `commands/handlers/claim-lock.md` no longer contains an unverified claim about web-session branch pinning — it states what was measured, with the date

**User-run**

- The routine run transcript shows `201` on the first ref creation and `422 Reference already exists` on the second, OR shows the specific error that blocks it
- Scratch repo deleted via the GitHub web UI (the `gh` token lacks `delete_repo` scope, and it should not be granted)
