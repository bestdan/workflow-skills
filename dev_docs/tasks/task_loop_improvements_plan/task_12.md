---
title: Remove /process-tasks and /claim-task in favor of /do-tasks
priority: medium
size: 3
status: ready
created: 2026-06-07
source_branch: claude/keen-tesla-pgLI4
related_files:
  - commands/process-tasks.md
  - commands/claim-task.md
  - commands/do-tasks.md
  - skills/task/SKILL.md
  - README.md
is_blocked_by:
  - task_10
  - task_11
tags:
  - task-loop
  - do-tasks
  - docs
expires: 2026-07-07
---

> Part of [[task_loop_improvements_plan]]. Depends on [[task_10]] and [[task_11]].

## Context

`/do-tasks` now covers both the file path and the tracker path, so `/process-tasks` and `/claim-task` are redundant. Per the planning decision we **remove them immediately** (no alias/deprecation period) and make `/do-tasks` the sole execute verb. This is the breaking-change task; it must land only after both `/do-tasks` halves exist (tasks 10 and 11).

Flag equivalences to fold into `/do-tasks` so no capability is lost: `/process-tasks [args]` ≡ `/do-tasks [args]` (remote/file default); `/claim-task [id]` ≡ `/do-tasks [id]` (tracker).

## Task

1. **Relocate before deleting — these files hold content nothing else does.** `/do-tasks` currently _references_ `commands/process-tasks.md` and `commands/claim-task.md` for orchestration that does **not** live in the surviving handler docs (`commands/handlers/*`). Before `git rm`, move that content into a survivor so nothing is lost:
   - **File path:** `do-tasks.md` §2 defers to `process-tasks.md` for the scan/rank/multi-blocker/WIP/remote-dispatch-prompt/`--local` mechanics. Inline those into `do-tasks.md` §2 (or a new `commands/handlers/repo-pr-execute.md`) so §2 stands alone.
   - **Tracker path:** `do-tasks.md` §3 cites "`/claim-task` step 4" (the feasibility-judgment criteria — concrete-outcome / files-identifiable / <1hr / needs-a-product-call questions) and "`/claim-task` step 9" (the success/bail report format). `commands/handlers/linear-claim.md` does **not** contain these — it stops at find-candidates/claim/move-to-review/bail mechanics. Move the feasibility criteria and report format into `do-tasks.md` §3 (or into `linear-claim.md`) so the step pointers can be dropped, not just renamed.
2. Delete `commands/process-tasks.md` and `commands/claim-task.md` (`git rm`). Confirm `/do-tasks` covers every flag they had (`--all`, `--local`, `<slug>`/`<id>`); add any missing flag to `/do-tasks` first.
3. `skills/task/SKILL.md`: replace the "Execute: /process-tasks vs /claim-task" comparison with an "Execute: /do-tasks" section (flag matrix: single vs `--all`, `--remote` vs `--local`, per-handler support). Remove remaining references to the deleted commands across the SKILL (lifecycle, kanban notes, race-conditions section).
4. Grep the whole repo for `/process-tasks` and `/claim-task` and rewire every reference (handlers, README, plan-with-docs, other skills, **including `do-tasks.md` §§2–4**) to `/do-tasks`. This is a **rewire, not a blind find-replace**: a bare `/claim-task`→`/do-tasks` swap turns "`/claim-task` step 4/9" into dangling pointers to nonexistent `/do-tasks` steps. After step 1's relocation, drop those step citations and the "like/exactly as `/claim-task`" asides entirely; keep only references that point at surviving files (`linear-claim.md`, `linear-common.md`). The `repo-pr` defer message in old docs ("use `/process-tasks --local`") becomes "`/do-tasks --local`".
5. `README.md`: update the Task-loop table — `/do-tasks` as the execute row, the two old commands removed; fix the "N skills, M commands, K subagent" count (now one fewer command).
6. Run `just check` — the validator enforces the README count and frontmatter, and will catch dangling references if any.

## Acceptance Criteria

- **Code-enforced:** `just check` passes (README count matches the reduced command set; no broken frontmatter). A repo-wide grep for `/process-tasks` and `/claim-task` returns no stray references outside this plan's own historical files.
- **No content lost:** the orchestration that only lived in the deleted files now lives in a survivor — `do-tasks.md` §2 stands alone for the file path (scan/rank/WIP/dispatch/`--local`), and the feasibility-judgment criteria + report format are present in `do-tasks.md` §3 (or `linear-claim.md`). `do-tasks.md` contains **no** "`/claim-task` step N" or "`/process-tasks` step N" pointers — only references to surviving files.
- **User-run:** `commands/process-tasks.md` and `commands/claim-task.md` no longer exist. Every workflow they supported is reachable through `/do-tasks` with the documented flags. SKILL.md and README present `/do-tasks` as the only execute verb.
