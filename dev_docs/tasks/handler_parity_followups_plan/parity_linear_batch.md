---
title: Lift Linear /do-tasks --all to true batch execution; establish the tracker-batch subroutine
priority: low
size: 5
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/do-tasks.md
  - commands/handlers/linear-claim.md
  - commands/handlers/repo-pr-execute.md
  - commands/task-config.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]]. Foundation for the Phase 3 batch cells.

## Context

`/do-tasks --all` / `-n N` is true batch only for the file store (`repo-pr`), whose remote fan-out now lives in `commands/handlers/repo-pr-execute.md` (the old `commands/process-tasks.md` was removed). For Linear, `commands/do-tasks.md` section 3 says batch *execution* degrades to a single foreground claim — **but `--claim-only` already batches** (`do-tasks.md` line ~189: "`--all` / `-n N --claim-only` may claim several issues at once, bounded by the pre-claim WIP gate"). So the missing piece is **batch execution**, not batch claiming.

This task closes that gap and, in doing so, defines the **reusable tracker-batch subroutine** that gh-issue (task 6) and jira (task 7) reuse. The push-race guard is the atomic `auto-claimed` claim already in `linear-claim.md`, not file-store branch determinism.

## Task

1. **Add true batch execution for Linear** in `commands/do-tasks.md` section 3. When `--all`/`-n N` (without `--claim-only`) and `handler: linear`:
   - Select dependency-ready unclaimed issues from the `unstarted`/`Todo` state (the `linear-claim.md` "Find candidates" filters + rank). "Dependency-ready" for Linear = all native `blockedBy` relationships resolved (blockers in a `completed`-type state).
   - Apply the WIP slack check (`wip_limit - current_wip`) from the section-3 pre-claim gate. Select the top `min(N, slack)`.
   - Dispatch **one remote session per issue** (one issue per session, like `repo-pr-execute.md`'s fan-out), each running `linear-claim.md` end-to-end and claiming atomically. Bare `/do-tasks` stays a single foreground claim; `--claim-only` keeps its existing batch-claim behavior.
   - Report dispatched vs held (WIP / `-n N` ceiling), matching the file-path report.
2. **Document the tracker-batch subroutine** — factor selection + WIP-slack + per-issue-remote-dispatch + atomic-claim into a clearly delimited section tasks 6–7 can reference ("follow the tracker-batch subroutine, substituting the gh-issue/jira candidate query and claim").
3. **Flip the matrix cell** `process — batch × linear` to `yes` in `commands/task-config.md`.

## Acceptance Criteria

**Code-enforced**
- `commands/do-tasks.md` no longer degrades Linear `--all`/`-n N` execution to a single claim; it dispatches up to `min(N, wip_slack)` remote single-claim sessions, while preserving the existing `--claim-only` batch-claim and single-foreground default.
- The Linear dependency-ready definition (native `blockedBy` resolved) is stated, and a reusable tracker-batch subroutine is documented.
- The `process — batch × linear` matrix cell reads `yes`.
- `just check` passes.

**User-run**
- `/do-tasks --all` with `handler: linear` and several ready unblocked issues dispatches multiple remote sessions bounded by `wip_limit`, each claiming a distinct issue atomically; held issues are reported with the reason.
- Bare `/do-tasks` still does a single foreground claim; `/do-tasks --all --claim-only` still batch-reserves.
