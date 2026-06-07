---
title: Build gh-issue batch (/do-tasks --all) on the tracker-batch subroutine
priority: low
size: 3
status: new
created: 2026-06-07
source_branch: bestdan/handler-parity-followups
related_files:
  - commands/do-tasks.md
  - commands/handlers/gh-issue-claim.md
  - commands/task-config.md
is_blocked_by:
  - parity_gh_issue_execute
  - parity_linear_batch
expires: 2026-09-07
tags:
  - task-loop
  - handlers
  - parity
---

> Part of [[handler_parity_followups_plan]]. Blocked by [[parity_gh_issue_execute]] and [[parity_linear_batch]].

## Context

With gh-issue single-execute (`gh-issue-claim.md`, task 3) and the reusable tracker-batch subroutine (task 5) in place, gh-issue batch is a thin reuse: substitute the gh-issue candidate query and atomic claim into the subroutine. Without single-execute there is nothing to batch, hence the dependency.

## Task

1. **Wire gh-issue into the tracker-batch subroutine** in `commands/do-tasks.md`: when `--all`/`-n N` and `handler: gh-issue`, follow the task-5 subroutine, substituting:
   - **Candidate query** — `gh issue list --label auto-eligible --search "no:assignee"`, filtered to dependency-ready issues. **Decide the dependency representation** (see the plan's open question): the captured body already carries a `Blocked by:` footer (rendered as `#142, #143` by `/push-plan`) — parse it, or adopt the `blocked` label, or GitHub native issue relationships. Pick one, document it, treat an issue as ready only when its blockers are closed.
   - **Atomic claim** — the assignee + `auto-eligible`→`auto-claimed` guard from `gh-issue-claim.md`.
   - Each selected issue → its own remote session running `gh-issue-claim.md`.
2. Apply the same `wip_limit` slack and dispatched/held reporting as the file path and the Linear batch.
3. **Flip the matrix cell** `process — batch × gh-issue` to `yes` in `commands/task-config.md`.

## Acceptance Criteria

**Code-enforced**
- `commands/do-tasks.md` routes gh-issue `--all`/`-n N` through the tracker-batch subroutine with a gh-issue candidate query and the `gh-issue-claim.md` atomic claim.
- Dependency-ready selection for gh-issue is defined and documented.
- The `process — batch × gh-issue` matrix cell reads `yes`.
- `just check` passes.

**User-run**
- `/do-tasks --all` with `handler: gh-issue` dispatches multiple remote sessions for distinct `auto-eligible` unblocked issues, bounded by `wip_limit`; blocked/held issues are reported with the reason.
