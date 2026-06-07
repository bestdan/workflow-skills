---
title: Build the gh-issue promote handler (gh-issue-promote.md)
priority: medium
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/promote-tasks.md
  - commands/handlers/gh-issue-promote.md
  - commands/handlers/gh-issue.md
  - commands/handlers/linear-promote.md
  - commands/task-config.md
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]]. The promote dispatch spine already exists upstream.

## Context

`/promote-tasks` already resolves the handler (`commands/promote-tasks.md` step 0): `linear` dispatches to `commands/handlers/linear-promote.md`, but `gh-issue` currently **stops** with "promotion not supported." This task flips that stop into a real promote flow.

The template is **`commands/handlers/linear-promote.md`** — a complete, shipped promote handler. Mirror its structure (preflight → resolve states/labels → query un-scored candidates → score against the confidence check → apply HIGH/LOW → report).

Reuse the **existing shared label vocabulary** (documented in the `## List` section of `commands/handlers/gh-issue.md` and in `linear-common.md`), do **not** invent `task:*` labels:
- "new" (un-scored) = an open issue carrying neither `auto-eligible` nor `human-approval-requested`.
- HIGH → add `auto-eligible` (the gh analogue of moving to `Todo`).
- LOW → add `human-approval-requested`.

Because "new" is just "open without a status label," no creation-time stamp is needed — the gh-issue create flow can stay as-is.

## Task

1. **Flip the dispatch** in `commands/promote-tasks.md` step 0: change the `gh-issue` branch from a stop to "dispatch to `commands/handlers/gh-issue-promote.md`," mirroring the `linear` branch. Add the needed `gh` tools to `allowed-tools` if not already present.
2. **Write `commands/handlers/gh-issue-promote.md`**, modeled on `linear-promote.md`:
   - Preflight `gh auth status` (same handling as the gh-issue create flow; stop on failure).
   - Query: `gh issue list --state open --json number,title,body,labels [--repo <repo>]`. Set aside (don't score) issues already carrying `auto-eligible` or `human-approval-requested` — keep them in a `skipped (already scored)` list, mirroring `linear-promote.md` step 5.
   - Score each against the confidence check (`skills/task/SKILL.md`), read against the issue **body** (which carries the captured task body + footer), using the same field-mapped gate `linear-promote.md` step 6 defines: title; a `size`/estimate signal ∈ {1,2,3,5}; acceptance-criteria content; no open-questions/TBD; not urgent; scope fits size 5.
   - Apply: HIGH → `gh issue edit <n> --add-label auto-eligible` (create the label if missing, `gh label create … 2>/dev/null`). LOW → `--add-label human-approval-requested` + `gh issue comment <n>` naming the failed check. Honor `dry-run` (print transitions, no edits).
   - Report in the same shape as `linear-promote.md` step 8, keyed by `#<number>`.
3. **Update the "Coverage note"** in the `## List` section of `commands/handlers/gh-issue.md`: it currently says gh-issue is "create-only … no gh-issue promote flow that sets status labels." Reflect that promote now sets `auto-eligible`/`human-approval-requested`.
4. **Flip the matrix cell** `promote × gh-issue` to `yes` in `commands/task-config.md`.

## Acceptance Criteria

**Code-enforced**
- `commands/promote-tasks.md` dispatches `gh-issue` to `gh-issue-promote.md` (no longer stops).
- `gh-issue-promote.md` reuses the existing label vocabulary, scores via the shared confidence check, and honors `dry-run`.
- The `promote × gh-issue` matrix cell reads `yes`; the gh-issue coverage note is updated.
- `just check` passes.

**User-run**
- `/promote-tasks` with `handler: gh-issue` adds `auto-eligible` to a well-formed open issue and `human-approval-requested` (+ comment) to an underspecified one, and skips already-labeled issues.
- `/promote-tasks dry-run` mutates nothing.
