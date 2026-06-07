---
title: Build the gh-issue single-execute path (gh-issue-claim.md)
priority: medium
size: 5
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/do-tasks.md
  - commands/handlers/gh-issue-claim.md
  - commands/handlers/gh-issue.md
  - commands/handlers/linear-claim.md
  - commands/task-config.md
is_blocked_by: parity_gh_issue_promote
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]]. Blocked by [[parity_gh_issue_promote]] (needs the `auto-eligible`/ready state).

## Context

`/do-tasks` (`commands/do-tasks.md` section 1) currently **stops** for `handler: gh-issue` ("execution not supported"). The template is **`commands/handlers/linear-claim.md`** (the shipped tracker claim/execute flow). Note the consolidation that landed on `main`: `commands/claim-task.md` and `commands/process-tasks.md` were **removed** — `do-tasks.md` is the dispatcher and `repo-pr-execute.md` holds the file-path mechanics.

`/do-tasks` now also has a **claim/execute split** (`--claim-only` / `--no-claim`) and **size-gate auto-routing**; the gh-issue handler must fit that shape. Reuse the existing label vocabulary: claim = assign `@me` + add `auto-claimed`; on PR open swap to `needs-review`. GitHub's `gh issue develop` gives a native issue-linked branch (the analogue of Linear's verbatim `branchName`), and `Closes #<n>` in the PR body is the completion signal.

## Task

1. **Write `commands/handlers/gh-issue-claim.md`** mirroring the phases of `linear-claim.md`:
   - **Find candidates** — `gh issue list --label auto-eligible --search "no:assignee" --state open --json number,title,body,labels,assignees [--repo <repo>]`. Drop anything labeled `auto-claimed`/`human-approval-requested`/`blocked`. Rank by a `priority:*` label if present, else issue age (oldest first).
   - **Judge feasibility** — ranked order, stop at the first finishable, comment the skip reason on each rejected one.
   - **Claim** — `gh issue edit <n> --add-assignee @me --add-label auto-claimed --remove-label auto-eligible`; re-read; on a race (assigned elsewhere / already `auto-claimed`) fall back to the next candidate. This is the non-file push-race guard.
   - **Branch + execute** — `gh issue develop <n> --base <base>`, check out the branch it reports verbatim, do the work, run `just check`.
   - **PR** — `gh pr create` with `[#<n>]` in the title and `Closes #<n>` in the body; post the PR URL as an issue comment.
   - **Move to review** — swap `auto-claimed` → `needs-review`. Never close the issue manually — merge via `Closes #<n>` is the only completion signal (mirror the Linear hard rule).
   - **Bail** — remove `auto-claimed`, add `human-approval-requested`, unassign, comment what tripped the bail.
2. **Honor the claim/execute split** (per `do-tasks.md`): `--claim-only` runs the claim step and stops (the `auto-claimed` label + assignee is the reservation marker); `--no-claim <#n>` resumes an already-claimed issue (check out its linked branch via `gh issue develop --list` / the PR head, guard that it's claimed by this caller).
3. **Pre-claim WIP gate** mirroring the Linear path in `do-tasks.md` section 3: count open `auto-claimed` issues against `wip_limit` (default 3); decline at the limit.
4. **Route in `commands/do-tasks.md`** section 1 — change the `jira | gh-issue → stop` branch so `gh-issue` dispatches to `gh-issue-claim.md` (foreground single, like Linear). Batch *execution* degrades to single until task 6; `--claim-only` is batchable. Leave `jira` stopping until task 4.
5. **Update** the gh-issue "create-only" coverage note and **flip the matrix cell** `do — single × gh-issue` to `yes` in `commands/task-config.md`.

## Acceptance Criteria

**Code-enforced**
- `commands/handlers/gh-issue-claim.md` mirrors the `linear-claim.md` phases incl. an assignee+label atomic-claim race guard, and supports `--claim-only`/`--no-claim`.
- `commands/do-tasks.md` routes `gh-issue` to it (single/foreground); the WIP gate is enforced; the issue closes on merge via `Closes #<n>`.
- The `do — single × gh-issue` matrix cell reads `yes`.
- `just check` passes.

**User-run**
- With `handler: gh-issue`, `/do-tasks` claims an `auto-eligible` issue, opens a PR with `Closes #<n>`, and swaps the label to `needs-review`.
- A second concurrent claim hits the race guard and falls back.
- `/do-tasks --claim-only` reserves without opening a review PR; `/do-tasks --no-claim #n` resumes it.
- At the WIP limit, `/do-tasks` declines with the limit and in-flight count.
