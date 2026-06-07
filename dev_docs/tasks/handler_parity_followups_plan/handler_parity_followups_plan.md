---
title: Handler parity follow-ups
type: epic
status: new
created: 2026-06-07
tracker_id: ebbc284b-a1c1-4cb3-96e0-914e210a79a2
tracker_url: https://linear.app/prethinkio/project/workflow-skills-handler-parity-follow-ups-f09d72e59cbb
source_branch: bestdan/handler-parity-followups
tags:
  - task-loop
  - handlers
  - parity
---

# Handler parity follow-ups

Bring every task-loop verb (`/promote-tasks`, `/do-tasks` single, `/do-tasks` batch) to **parity across all handlers** (`repo-pr`, `linear`, `gh-issue`, `jira`). The matrix is jagged: `repo-pr` runs the full loop, `linear` now has promote + single-execute, and `gh-issue`/`jira` are capture/list destinations with no promotion or execution. None of these gaps are *fundamental* — they are implementation gaps. This plan closes them.

> **Rebased 2026-06-07.** Since this plan was first drafted, a large batch of task-loop work landed on `main`. Two originally-planned tasks are now **done upstream** and have been dropped: the `/promote-tasks` handler-dispatch spine (now `commands/promote-tasks.md` step 0) and the Linear promote handler (`commands/handlers/linear-promote.md`). The remaining tasks are revised to build on that baseline rather than re-create it.

## Current baseline (what already exists on `main`)

- `/promote-tasks` resolves the handler (step 0): `repo-pr` → file path; `linear` → `commands/handlers/linear-promote.md` (built); `gh-issue`/`jira` → **stop** ("promotion not supported").
- `/do-tasks` resolves the handler (section 1): `repo-pr` → `commands/handlers/repo-pr-execute.md`; `linear` → `commands/handlers/linear-claim.md`; `gh-issue`/`jira` → **stop**. It also has a **claim/execute split** (`--claim-only` / `--no-claim`) and **size-gate auto-routing**. On Linear, `--claim-only` **already batches**; only batch *execution* degrades to single.
- A **shared status-label vocabulary** is already defined and reused by Linear and the gh-issue List view: `auto-eligible` (= ready), `human-approval-requested` (= needs_refinement), `auto-claimed` (= in_progress/claimed), `needs-review`, `blocked`. "new" = an open issue carrying none of these. New handler work **reuses these labels** — do not invent a parallel `task:*` scheme.
- The **capability matrix lives in `commands/task-config.md`** ("Handler capability matrix") and is the single source of truth (step 5 reads it). It currently has a **stale cell**: `promote × linear` shows `no` but `linear-promote.md` is built and dispatched. (Fixed by task 8.)
- `commands/claim-task.md` and `commands/process-tasks.md` have been **removed** — `commands/do-tasks.md` + `commands/handlers/repo-pr-execute.md` are the authoritative file-path references now. Earlier drafts of these tasks pointed at the deleted files; references are corrected below.

## Scope

**In scope** — the cells still missing:

| handler  | promote (`/promote-tasks`) | execute single (`/do-tasks`) | batch (`/do-tasks --all`) |
| -------- | -------------------------- | ---------------------------- | ------------------------- |
| repo-pr  | ✅                         | ✅                           | ✅                        |
| linear   | ✅ (built upstream)        | ✅                           | **build** (task 5)        |
| gh-issue | **build** (task 1)         | **build** (task 3)           | **build** (task 6)        |
| jira     | **build** (task 2)         | **build** (task 4)           | **build** (task 7)        |

**Non-goals**

- `/list-tasks` for gh-issue/jira — the gh-issue handler already has a `## List` section; list is not one of the three verbs this plan targets.
- Reworking the `repo-pr` file-path verbs — they are the reference implementation.

## Approach

Mirror the **established handler-dispatch pattern** — now with real upstream templates to copy: `commands/handlers/linear-promote.md` (promote) and `commands/handlers/linear-claim.md` (execute). The work is to (a) flip the gh-issue/jira "stop" branches in `/promote-tasks` and `/do-tasks` to dispatch, (b) write the missing per-handler files modeled on their Linear siblings, reusing the existing label vocabulary, and (c) keep the capability matrix in `task-config.md` in sync (each task flips its own cell).

Three ordered phases, cleanest first, coupled per-handler so each verb consumes the "ready" state the prior one sets (`promote → execute → batch`), encoded via `is_blocked_by`:

1. **Promote** — flip the dispatch stop; add a `<handler>-promote.md` modeled on `linear-promote.md`. (gh-issue uses the label vocab; jira uses workflow-status transitions.)
2. **Execute (single)** — a `<handler>-claim.md` modeled on `linear-claim.md`, honoring the new `--claim-only`/`--no-claim` split.
3. **Batch** — lift Linear from batch-claim-only to true batch *execution* first (establishing the reusable tracker-batch subroutine), then have gh-issue/jira batch reuse it.

## Tasks

**Phase 1 — Promote**

1. [[parity_gh_issue_promote]] — flip the gh-issue promote stop; add `gh-issue-promote.md` (label transitions, modeled on `linear-promote.md`).
2. [[parity_jira_promote]] — flip the jira promote stop; add `jira-promote.md` (dynamic workflow-status transitions).

**Phase 2 — Execute (single)**

3. [[parity_gh_issue_execute]] — `gh-issue-claim.md` + route in `/do-tasks`, honoring `--claim-only`/`--no-claim`.
4. [[parity_jira_execute]] — `jira-claim.md` + route in `/do-tasks`.

**Phase 3 — Batch**

5. [[parity_linear_batch]] — lift Linear from batch-claim-only to true batch *execution*; establish the reusable tracker-batch subroutine.
6. [[parity_gh_issue_batch]] — gh-issue batch reusing the subroutine.
7. [[parity_jira_batch]] — jira batch reusing the subroutine.

**Wrap-up**

8. [[parity_capability_matrix]] — fix the stale `promote × linear` cell now; keep the matrix in sync as the cells above land.

## Open questions

- **gh-issue dependency representation.** The file store encodes `is_blocked_by` in frontmatter. For a GitHub issue, where does a dependency live — the body's `Blocked by:` footer (which `gh-issue.md` already renders as `#142, #143` for `/push-plan`), a `blocked` label, or GitHub native issue relationships? Task 6 needs this decided.
- **Jira transition tool names.** Tasks 2/4 assume `getJiraIssueTransitions` / `transitionJiraIssue`-style MCP tools; confirm exact names at implementation.
- **Possible second stale matrix cell.** `gh-issue.md` has a full `## List` section but the matrix shows `list × gh-issue = no`. Task 8 should verify whether gh-issue list is actually wired into `/list-tasks` and correct the cell if so (out of this plan's three verbs, but it's a matrix-accuracy issue).
