---
title: "stacked PRs: automate the post-merge restack — a human must never hand-rebase a stack under squash-merge"
priority: urgent
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

## Human review moves the parent — that is the process, not an anomaly

Confirmed with the human during run #2: *"there is always my review in between hand-off
and merging. Files being changed should be an expected part of the process."* This is
load-bearing and corrects two assumptions:

- **The `base_sha` freeze rule models the wrong actor.** `run-state.md`'s stacked-PR
  rule parks a child when the parent's tip moves — it exists to catch the
  **orchestrator** moving a base. But a **human reviewer** moves the parent *every
  time*, by design. Applied to human review, the rule would park every stacked child on
  every run. The restack must therefore **expect** the parent's merged content to differ
  from the frozen `base_sha`, and rebase onto the **merged, reviewed** parent — never
  treat the divergence as an error.
- **A restacked child's review is STALE, and that is the real risk.** The child was
  co-reviewed against the **pre-review** parent. In run #2, task_2 gained two security
  fixes *during human review* (`close out-of-jail launch escape opened by toolchain
  exec`, `deny osascript exec`) — and task_3, which edits the same files, was reviewed
  against a version of `render_profile` that predated them. A `git rebase` that applies
  **cleanly** proves nothing here: the child's approval refers to code that no longer
  exists, and a clean auto-merge can silently drop or contradict a fix the reviewer just
  added. (Checked by hand this run: the fixes survived. That check must not be manual.)

So the restack is not just a git operation — it is a **re-verification trigger**.

## CORRECTION (2026-07-11) — two things this spec got wrong, found while implementing it (PR #184)

- **The restack orphans grandchildren — the very failure it exists to fix.**
  Force-pushing a restacked child **rewrites** it, so a grandchild still carrying
  that child's old commits is orphaned by the restack itself. A fixed-point pass
  over the table *detects* the chain but does the wrong thing with it: a
  grandchild must be **cascaded onto its parent's new tip**, and its PR base must
  **stay the parent's branch** — retargeting it to `main` would re-propose the
  parent's entire changeset. The spec's "process children in dependency order"
  is necessary but not sufficient.
- **The rebase moves the caller's `HEAD`.** `git rebase --onto <base> <sha>
  <branch>` **checks out `<branch>`**, so a restack run against the run worktree
  parks its `HEAD` on a task branch — reintroducing **finding #23**, the exact bug
  task 13 exists to prevent. Every rebase must run in a **throwaway detached
  worktree**, removed on all exit paths (success, conflict-abort, push rejection),
  with the caller's `HEAD` asserted unchanged on exit and a dirty caller worktree
  fail-closed before anything is touched.

**Shipped state (PR #184):** the *mechanism* — rebase, force-push with an explicit
lease, PR retarget, cascade, orphan detection, fail-closed on conflict — is
implemented and test-enforced. The **re-verification** below (re-run verify against
the new base; diff-audit the child against the parent's post-hand-off review
commits; flag the child's co-review as stale) is a **specified contract with no
enforcement** — `restack` writes the requirement into `REPORT.md` so a human sees
it, but nothing checks that it happened. That is the plan's own "a rule with no
enforcement is a comment" pattern, reproduced inside the fix for it. Wiring it into
the run loop / `/deliver-task` is **task 21**.

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
- **Re-verify every restacked child — a clean rebase is not evidence of correctness.**
  After rebasing onto the merged parent, the child must:
  1. **Re-run the verify command** against the new base (its previous green was against
     the old one).
  2. **Diff-audit against the parent's review changes**: assert that no line added by the
     parent's post-hand-off review commits is removed or contradicted by the child. In
     run #2 this was the difference between shipping and silently reverting a
     sandbox-escape fix, and it was caught only by a human reading all 6 deleted lines.
  3. **Flag the child's co-review as stale** in `REPORT.md` when the parent changed
     during review, so the human knows the child's approval predates the parent's current
     content — and re-run co-review on the child if the parent's review touched the same
     files.
- **Fix the freeze rule's actor confusion** in `run-state.md`: state that `base_sha`
  guards the **orchestrator** moving a base (→ park), while a **human merging/reviewing**
  a parent is expected and routes to **restack + re-verify**, never to a park.
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
