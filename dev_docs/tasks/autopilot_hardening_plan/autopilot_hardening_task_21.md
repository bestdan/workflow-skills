---
title: "restack: enforce the re-verification trigger — a clean rebase is not evidence of correctness"
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
is_blocked_by: [autopilot_hardening_task_18, autopilot_hardening_task_16]
parent: autopilot_hardening
tags: [auto-pilot, stacked-prs, co-review, p1]
---

[[autopilot_hardening_plan]]

## Context

Task 18 shipped the restack **mechanism** (rebase onto the merged parent,
force-push with an explicit lease, retarget the PR, cascade grandchildren onto the
rewritten parent, detect orphans, fail closed on conflict). It did **not** ship the
part the finding actually cared about.

**A clean rebase proves nothing.** The child was co-reviewed against the
**pre-review** parent. In run #2, task_2 gained two security fixes *during human
review* (`close out-of-jail launch escape opened by toolchain exec`, `deny
osascript exec`) — and task_3, which edits the same files, had been reviewed
against a `render_profile` that predated them. A `git rebase` that applies
**cleanly** can silently drop or contradict a fix the reviewer just added. That the
fixes survived was established **by a human reading all 6 deleted lines by hand**.

Task 18 currently writes the re-verification requirement into `REPORT.md` so a
human sees it, and `run-state.md` specifies it — but **nothing checks that it
happened**. This is the plan's own indictment, reproduced inside the fix for it:
*writing the rule down is not the same as enforcing it*. Same shape as #22 (the
breaker exists but counts the wrong thing), #23 (worktree isolation described,
never asserted), and #24 (the no-interactive-prompt rule declared, never probed).

## Task

Make the restack's re-verification an **enforced** step, not a `REPORT.md` note:

- **Re-run the verify command** against the restacked child's new base. Its
  previous green was against the old one and does not carry over. A failing
  re-verify **parks** the child and alarms (task 16) — it never silently keeps a
  PR that no longer builds on `main`.
- **Re-run the PR's own stated acceptance criterion** — the "How to evaluate" /
  user-run criterion the PR body declares, the one phrased as *"a 'no' here
  invalidates the change."* **The suite is not this.** Amended 2026-07-12 after
  co-reviewing #188–#191: the suite re-runs for free and was **green on both
  branches that carried merge blockers**, because in each case the harness diverged
  from production at exactly the point the invariant was about (a test bypassing the
  gate the real wrapper runs first; a fixture pre-baking a cell production leaves
  empty). Task 16's own criterion — *"confirm that within one supervisor interval
  you get a notification instead of silence"* — would have caught its blocker
  immediately, and was not re-run after the rebase that broke it. **The author wrote
  the exact check that would have found the bug and did not re-run it.** A criterion
  that cannot be automated must at minimum be *surfaced as an unchecked box that
  blocks hand-off*, not silently assumed to still hold.
- **Diff-audit against the parent's post-hand-off review commits.** Compute the
  parent's review delta — the commits added between hand-off and merge — from its
  two endpoints: the hand-off tip is the child's recorded `base_sha`, but the
  **final pre-merge tip is not stored and may not be a local ref** (a
  squash-merge can delete the parent branch, per task 18's finding #25). Obtain it
  from GitHub — `refs/pull/<parent#>/head` (fetchable even after the branch is
  deleted) or the API (`gh pr view <parent#> --json` for the head/merge SHA) —
  never assume "the SHAs are already local." Then assert, **mechanically**, that no
  line the review delta *added* is **removed or modified** in the child's
  post-rebase tree (a line-level diff over the delta's touched files, accounting for
  legitimate child edits and renames). This line-level check is the mechanizable
  half and is precisely what the human did by hand in run #2; a violation is a
  **defect**, not a warning. Deeper **semantic** contradiction — a reviewed line
  that survives textually but is negated or overridden elsewhere — is **not**
  mechanically decidable and is delegated to the mandatory co-review re-run below,
  not claimed as part of this check.
- **Flag the child's co-review as STALE and re-run it** when the parent's review
  touched files the child also touches. The overlap is computable from the two
  diffs; `/co-review --non-interactive` already exists to re-run.
- Wire the above into the run loop / `/deliver-task` iterate step so the restack is
  a **re-verification trigger**, not just a git operation, and so a restacked child
  cannot reach hand-off carrying an approval that refers to code that no longer
  exists.
- **Define the phase transition out of `handed-off`.** A restacked child is already
  `handed-off`, which `run-state.md` defines as a **terminal** (success) phase —
  PR linked, tracker at `needs_review`, PR frozen. Re-verifying it therefore
  requires a legal transition *back* into an in-flight phase (e.g. `iterating`),
  a matching tracker rollback (`needs_review` → `started`), and a new row in
  `run-state.md`'s **Write order** / **Crash reconciliation** table so a crash
  mid-rollback can't leave the tracker at `needs_review` while the run files say
  `iterating` (or vice versa). This task must **amend `run-state.md`** so
  `handed-off` is no longer strictly terminal, rather than silently breaking an
  invariant stated in its own related file.

## Acceptance Criteria

**Code-enforced:**

- A test builds a parent that gains a review commit **after** hand-off, plus a child
  that edits the same file, and asserts the restack (a) re-runs verify against the
  new base, (b) **fails** when the child's rebase drops a line the parent's review
  commit added — even though the rebase itself applied cleanly — and (c) marks the
  child's co-review stale when the file sets overlap.
- A test asserts that when the stale condition is set, `/co-review
  --non-interactive` is actually **invoked and completes** on the restacked child,
  and that **hand-off stays blocked** until the refreshed review passes — a
  stale-marker-only implementation that never re-runs review must **fail** this
  test.
- A test asserts a re-verify failure **parks** the child rather than leaving an
  apparently-healthy PR, **and** that it fires the **task 16 alarm channel**
  (ALARM sentinel / `REPORT.md` top-line) — parking without alarming, or a
  `REPORT.md`-only notice, must fail (the exact regression this task exists to
  prevent).
- `bash scripts/check.sh` green.

**User-run:**

- Merge a parent whose review changed files a child also touches, run the restack,
  and confirm the child is re-verified, its stale co-review re-run, and any dropped
  review line surfaced as a defect — with no human reading deleted lines by hand.
