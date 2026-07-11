---
title: "restack: enforce the re-verification trigger — a clean rebase is not evidence of correctness"
priority: urgent
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - skills/auto-pilot/SKILL.md
  - skills/auto-pilot/references/run-state.md
  - commands/deliver-task.md
  - scripts/spawn-orchestrator.sh
is_blocked_by: autopilot_hardening_task_18
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
- **Diff-audit against the parent's post-hand-off review commits.** Compute the
  parent's review delta (the commits added between hand-off and merge) and assert
  no line it added is removed or contradicted by the child. This is mechanizable —
  the SHAs are known — and it is precisely the check a human did by hand in run #2.
  A violation is a **defect**, not a warning.
- **Flag the child's co-review as STALE and re-run it** when the parent's review
  touched files the child also touches. The overlap is computable from the two
  diffs; `/co-review --non-interactive` already exists to re-run.
- Wire the above into the run loop / `/deliver-task` iterate step so the restack is
  a **re-verification trigger**, not just a git operation, and so a restacked child
  cannot reach hand-off carrying an approval that refers to code that no longer
  exists.

## Acceptance Criteria

**Code-enforced:**

- A test builds a parent that gains a review commit **after** hand-off, plus a child
  that edits the same file, and asserts the restack (a) re-runs verify against the
  new base, (b) **fails** when the child's rebase drops a line the parent's review
  commit added — even though the rebase itself applied cleanly — and (c) marks the
  child's co-review stale when the file sets overlap.
- A test asserts a re-verify failure **parks** the child rather than leaving an
  apparently-healthy PR.
- `bash scripts/check.sh` green.

**User-run:**

- Merge a parent whose review changed files a child also touches, run the restack,
  and confirm the child is re-verified, its stale co-review re-run, and any dropped
  review line surfaced as a defect — with no human reading deleted lines by hand.
