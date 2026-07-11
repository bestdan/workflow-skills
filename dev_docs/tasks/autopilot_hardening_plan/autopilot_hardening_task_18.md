---
title: "stacked PRs: automate the post-merge restack — a human must never hand-rebase a stack under squash-merge"
priority: 1
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/run-state.md
  - commands/deliver-task.md
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, stacked-prs, merge, p1]
---

[[autopilot_hardening_plan]]

## Context

Finding **#25** — hit for real during detached run #2, and it **silently closed a P1
PR**.

**What happened.** The human merged **#169** (task_1). GitHub's
`delete_branch_on_merge` deleted `auto-pilot/hardening-task_1`. **#172** (task_4) was
based on that branch — so its base ref vanished and GitHub **closed it**
(`base_ref_deleted` and `closed`, same second). A P1 PR — the out-of-jail verify
broker — was gone, and nothing announced it. It was noticed only because a status
check happened to diff the open-PR list against `RUN.md`.

**Two distinct failure modes, and only one is about branch deletion:**

1. **Auto-close on base deletion.** Fixed by `delete_branch_on_merge=false`, but that
   is a *repo setting a human must remember to set* — not a property of the tool. A
   fresh repo, or anyone who re-enables the GitHub default, gets the same failure.
2. **Squash-merge orphans the child's history.** A squash collapses the parent into a
   **new SHA** on `main`. The child branch still carries the parent's **original**
   commits, which are no longer ancestors of `main`. Consequences:
   - Reopening/retargeting the child naively makes its diff **re-propose the parent's
     entire changeset** on top of itself.
   - A child left pointing at its parent's *branch* (as **#171** → `task_3` still is)
     merges into **that branch, not `main`** — so the work **never reaches `main`** and
     the PR looks perfectly healthy while doing nothing. This is the quiet version, and
     it is worse than the loud one.

**Why a doc does not fix this.** The obvious response is "write merge-bottom-up
guidance into the reference" (run #1's finding #14 said exactly that, and it is still
unfixed). But the correct action is a **four-command git incantation** — `fetch`,
`rebase --onto origin/main <parent_tip>`, `push --force-with-lease`, `gh pr edit
--base main` — that must be run **per child, in dependency order, at the right moment
(after the parent merges), under a squash-merge**. Expecting a human to execute that
reliably at review time is the same "rule with no enforcement" pattern that produced
#22, #23, and #24. **A rule a human must remember is not a control.**

Every input this needs is already known: `RUN.md` records the stack (`base`,
`base_sha`), and GitHub records which parents merged. It is fully mechanizable.

## Task

- Add a **restack** operation (e.g. `scripts/spawn-orchestrator.sh restack`, or a
  `/auto-pilot restack` entry point) that, for a given run:
  1. Reads the stack from `RUN.md` (`base` / `base_sha` per task).
  2. For each task whose **parent's PR has merged**, and whose PR still targets the
     parent's branch: `git rebase --onto origin/main <parent_base_sha> <child_branch>`
     (dropping the parent's now-squashed commits), `push --force-with-lease`, and
     `gh pr edit <child#> --base main`.
  3. Processes children in **dependency order**, and is **idempotent** — safe to run
     repeatedly; a child already based on `main` is a no-op.
  4. **Fails closed on a conflict**: a rebase that does not apply cleanly must stop and
     report, never force-push a broken branch. Record it and let a human resolve.
- **Detect the orphaned-child condition and alarm on it** (ties to task 16): a PR whose
  base is a **merged-or-deleted branch**, or whose base is any branch other than the
  run's `base_branch` after its parent merged, is a **defect**. Surface it in
  `REPORT.md` and via the alarm channel — do not wait for a human to diff the PR list
  by hand, which is the only reason #172 was caught at all.
- **Emit the exact restack commands in `REPORT.md`**, per child, copy-pasteable — so
  even without running the automation the human is one paste from correct, rather than
  reconstructing a rebase from prose.
- State the **repo-setting dependency** explicitly in the pre-flight (task 5/17
  territory): check `delete_branch_on_merge` and **warn** when it is `true` with a
  stacked run, because that setting turns a recoverable restack into a closed PR.
- Update `run-state.md` on the human-merge interaction: the existing freeze/`base_sha`
  park rule guards the **orchestrator** moving a base; it does **not** guard a **human
  merging** one. Say so, and point at the restack as the remedy (run #1's finding #14,
  finally closed).

## Acceptance Criteria

**Code-enforced:**
- A test builds a squash-merged parent + a stacked child and asserts `restack` rebases
  the child onto `main`, drops the parent's squashed commits (the child's diff contains
  **only** the child's files), force-pushes, and retargets the PR base to `main`.
- A test asserts idempotency (a second `restack` is a no-op) and fail-closed behavior
  (a conflicting rebase reports and does **not** push).
- A test asserts the orphaned-child detector flags a PR whose base is a merged/deleted
  branch.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Merge a parent PR in a stacked run, then run the restack: the child PR ends up
  `base=main`, mergeable, with a diff containing only its own changes — with no manual
  `rebase --onto` anywhere, and no PR silently closed or silently targeting a branch
  instead of `main`.
