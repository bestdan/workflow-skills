---
title: Add /archive-tasks — a handler-dispatched archive/prune of completed work items
priority: medium
size: 3
status: new
created: 2026-06-26
source_branch: bestdan/archive-completed-tasks
related_files:
  - commands/handlers/linear-common.md
  - commands/handlers/linear-config.md
  - commands/handlers/gh-issue.md
  - commands/handlers/gh-issue-config.md
  - commands/handlers/jira.md
  - commands/handlers/jira-config.md
  - commands/handlers/repo-pr.md
  - commands/handlers/repo-pr-config.md
  - commands/task-config.md
  - commands/do-tasks.md
  - commands/promote-tasks.md
  - commands/doctor.md
expires: 2026-09-24
tags:
  - task-loop
  - handlers
  - hygiene
---

## Context

The task loop **creates** work items but never **retires** them. Over a high-velocity month the configured tracker fills with completed/canceled items that still count as live records, and on some trackers that is a hard wall, not just clutter:

- **Linear free plan caps a workspace at 250 _active_ issues; archived issues are unlimited and excluded from the cap.** A workspace driving `/add-task` + `/do-tasks` automatically hit `253/250` in ~one month — 143 of those (57%) were `Done`. Issue creation then started failing with `Usage limit exceeded — you've exceeded the free issue limit for this workspace`, which silently breaks the whole loop (`/add-task` can't file, `/push-plan` can't push).
- **Linear's native team auto-archive** (Team Settings → Issue statuses & automations) is the zero-maintenance fix, but its shortest window can still be too long for a workspace completing ~5 issues/day, so it only keeps you break-even at the cap. A tighter, opt-in prune is needed as a backstop.
- The MCP currently wired for Linear (`linear-common.md`) exposes **no archive or delete mutation** — only `save_issue` (which can't archive) and `delete_*` for comments/attachments/status-updates. So programmatic archiving cannot go through the MCP; it needs the Linear GraphQL API directly.

This is **not Linear-specific** — every handler accumulates completed work and each has a natural "retire" operation. The task loop should expose one generic verb for it, dispatched per handler exactly like `/promote-tasks` and `/do-tasks`, rather than a one-off Linear script.

## Task

1. **Add a new command `commands/archive-tasks.md` (`/archive-tasks`).** Follow the established handler-dispatch pattern (resolve the handler from `dev_docs/tasks/.task-config.yml`, then read and follow `commands/handlers/<handler>-archive.md`). It must:
   - Accept an optional age threshold and a dry-run flag in `$ARGUMENTS`: `/archive-tasks [--older-than <N>d] [dry-run]`. When `--older-than` is omitted, fall back to a config default `archive_after` (days); when that's also unset, **dry-run by default and refuse to mutate** until a threshold is given (no surprise bulk archive).
   - Parse a bare `dry-run`, a bare `--older-than`, and both together — don't use a strict-equality arg check (see the parsing footgun fixed in `scope-promote-tasks-to-container.md`).
   - Only ever touch items in a **terminal state** (handler's completed/canceled/done equivalent) whose completion timestamp is older than the threshold. Never archive open/in-progress/in-review work.
   - Always print the candidate list first; in dry-run, stop there.

2. **Add a per-handler archive file for each handler.** Each defines the terminal-state query and the retire operation for that tracker:
   - **`commands/handlers/linear-archive.md`** — primary guidance: enable native team auto-archive (document the exact setting path) and note archived issues don't count toward the free 250-issue cap. Backstop for tighter-than-UI windows: query `completed`/`canceled` issues with `completedAt`/`canceledAt` < cutoff and call the GraphQL `issueArchive(id:, trash:false)` mutation per id (no bulk mutation exists; loop). **This path uses the Linear GraphQL API with a personal API key, NOT the MCP** (the MCP has no archive mutation) — store the key via 1Password/`op`, never in the repo, and document that in `linear-config.md`.
   - **`commands/handlers/repo-pr-archive.md`** — move `status: done` task `.md` files older than the threshold out of `dev_docs/tasks/` into `dev_docs/tasks/_archive/` (preserve, don't delete), so `/promote-tasks` and `/do-tasks` scans stay fast and bounded.
   - **`commands/handlers/gh-issue-archive.md`** — GitHub has no issue cap and no true archive; define the retire op as closing stale completed issues (and/or an `archived` label), and state plainly that this handler is hygiene-only with no cap pressure.
   - **`commands/handlers/jira-archive.md`** — transition terminal issues older than the threshold to an archived status where the project has one; note native Jira issue archival is a Premium feature and default to a documented no-op/close otherwise.

3. **Extend the config schema.** Add `archive_after` (days; optional) and any per-handler keys (e.g. Linear personal-API-key reference, repo-pr archive dir) to `dev_docs/tasks/.task-config.yml`. Document them in `commands/task-config.md` and each handler's `*-config.md`.

4. **Make it schedulable, handler-agnostically.** Document running `/archive-tasks --older-than <N>d` on a cadence via `/schedule` (cloud routine) or `/loop`. For the Linear GraphQL backstop specifically, note it can alternatively run as a standalone scheduled job (GitHub Action / cron) independent of an agent session, since it only needs the API key. Keep the scheduling guidance in `archive-tasks.md`, not buried in one handler.

5. **Wire into `/doctor`.** `commands/doctor.md` should report archive health: whether `archive_after` is set, whether the resolved handler has an archive file, and (Linear) whether native auto-archive is the assumed primary with the script as backstop.

## Acceptance Criteria

**Code-enforced**

- `commands/archive-tasks.md` exists, resolves the handler the same way `commands/do-tasks.md` does, and documents the `--older-than` / `dry-run` arg parsing plus the "no threshold ⇒ dry-run only" safety default.
- A `commands/handlers/<handler>-archive.md` exists for each of linear, repo-pr, gh-issue, jira, each scoping strictly to terminal-state items past the threshold.
- `linear-archive.md` documents the native auto-archive setting AND the GraphQL `issueArchive` backstop, explicitly notes the MCP has no archive mutation, and references the API key via `op`/1Password with no secret in the repo.
- New config keys are documented in `commands/task-config.md` and the per-handler `*-config.md` files.
- `commands/doctor.md` reports archive configuration health.
- `just check` passes.

**User-run**

- `/archive-tasks dry-run` lists terminal-state candidates older than the threshold and changes nothing.
- `/archive-tasks --older-than 14d` (with a tracker handler) archives only items completed/canceled >14 days ago and leaves open work untouched.
- On the `repo-pr` handler, completed task files older than the threshold move to `dev_docs/tasks/_archive/` and are no longer scanned by `/promote-tasks`.
- With no threshold and no `archive_after` configured, `/archive-tasks` refuses to mutate and explains how to set one.
