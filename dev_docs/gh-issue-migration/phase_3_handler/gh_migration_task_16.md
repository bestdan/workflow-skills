---
title: Build gh-issue batch execution (/do-tasks --all) on the tracker-batch subroutine
priority: low
size: 3
status: new
created: 2026-09-04
source_branch: bestdan/gh-issue-migration
parent: gh_migration
is_blocked_by: gh_migration_task_4
related_files:
  - commands/do-tasks.md
  - commands/handlers/gh-issue-claim.md
  - commands/task-config.md
tags: [handler, batch]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Build gh-issue batch execution (/do-tasks --all) on the tracker-batch subroutine

## Context

**Absorbed from Linear PRE-117 on 2026-09-04**, which was cancelled as superseded and
points here. It was the last piece of `linear`-level parity still tracked outside this
epic, and it touches the two files this epic churns hardest — `do-tasks.md` and
`gh-issue-claim.md`. That is why it moved: its first attempt died of exactly that
collision, and building it outside the epic would set the collision up again.

`main` still advertises the gap. `commands/do-tasks.md:646` reads "Single by nature …
until the gh-issue batch task lands", and `commands/task-config.md`'s capability matrix
carries `process — batch × gh-issue` = `no`. This task retires both.

**Nothing here is blocked.** Task 4 settled the claim contract, task 15 the dependency
edges, and both are merged. It does not depend on task 8.

## What already exists — do not rewrite it

[PR #426](https://github.com/bestdan/workflow-skills/pull/426) is **closed** (3 commits,
tip `bc7bf0b`, conflicting with `main` when it was closed). Do not reopen or rebase it —
but do not ignore it either. Its **§4 batch machinery** is the valuable half and was
never the problem: remote dispatch, the WIP bound, the `gh auth` self-check, the
unattended declaration, and the report. Lift that.

Read it through the **pull ref**, not the branch:

```bash
git fetch origin refs/pull/426/head && git show FETCH_HEAD
```

`refs/pull/426/head` is `bc7bf0b` (verified 2026-09-04) and GitHub retains it for the
life of the PR, so it survives the head branch being pruned —
`dpegan/pre-117-build-gh-issue-batch-do-tasks-all-on-the-tracker-batch` may not be there
when you look.

Its five recorded defects are in the epic's **#426 in detail** section. Read them there;
the short form is that every one of them **fails silently**:

1. The candidate query asks for `auto-eligible` / `-label:auto-claimed`. Nothing writes
   those since task 4, so it matches **zero** issues and reports "no candidate".
2. The WIP count is re-derived in prose off those same dead labels, so the bound the
   whole mechanism exists to enforce reads zero.
3. It spells `task/<n>`. Task 4 moved the lock to `<branch_prefix>task-<n>`
   (`gh-issue-claim.md:9-12`; line 23 says a fixed `task/<n>` cannot satisfy the scheme),
   so a batch session creates a ref that misses the real lock — **every racer thinks it
   won**.
4. Its **Dependency-ready selection** section parses the `Blocked by: #<n>` body footer,
   on the premise that GitHub has no native blocking relationship. Task 15 retired that
   premise. **Delete the section; do not reconcile it.**
5. Its capability-matrix flip is directionally right but must land **last**.

## Task

Wire gh-issue into the tracker-batch subroutine in `commands/do-tasks.md`, so
`--all` / `-n N` under `handler: gh-issue` follows it. **Re-apply against the current
contract as a fresh edit** rather than porting #426's prose — that prose predates the
helpers below, and the epic has invalidated it once already.

Every deterministic part is already code, and the contract is the exit code:

- **Candidate query** — the migrated vocabulary (`status:2_ready` + `auto:eligible`,
  ranked `prio:0`…`prio:3`), as `gh-issue-promote.md` and the single path already do.
- **Dependency readiness** — `gh-issue-ready.py --issue N` per candidate. This is the
  settled answer to the question PRE-117 left open; there is no footer path.
- **WIP bound** — call `gh-issue-claim.py wip`. Its docstring exists to say a caller must
  not re-derive the count.
- **Branch name** — `gh-issue-claim.py branch-name --issue <n> [--prefix …]`. Never a
  literal.
- **Atomic claim** — `gh-issue-claim.py`, branching on its exit codes: **`0` won, `3`
  lost the race, `4` neither.** `4` is the case prose has repeatedly got wrong.

Then flip `process — batch × gh-issue` to `yes` in `commands/task-config.md` — **last**,
once the above works, or it advertises a feature that matches nothing.

## Note — dispatched sessions and the claim ref

Batch dispatches one remote session per selected issue, and a routine **can acquire the
claim ref but cannot release it** (no delete-ref tool; `git push --delete` 403s). A
crashed routine would strand a claim permanently. This is not blocking: `claim-lock.md`
keeps routines on the **self-healing comment election** for exactly this reason, and task
12 (the stale claim-ref sweep) is what gates flipping that default. Do not flip it here,
and do not put dispatched sessions on the ref lock.

## Acceptance Criteria

Inherited from PRE-117, whose criteria survived intact — only its stale candidate query
and its "decide the dependency representation" open question did not.

**Code-enforced**

- `commands/do-tasks.md` routes gh-issue `--all` / `-n N` through the tracker-batch
  subroutine, with the migrated candidate query and the `gh-issue-claim.py` claim
- Dependency-ready selection calls `gh-issue-ready.py`; no code path reads the body footer
- The WIP bound calls `gh-issue-claim.py wip` rather than counting labels in prose
- No literal `task/<n>` remains on the batch path
- `process — batch × gh-issue` reads `yes` in the capability matrix
- `just check` passes

**User-run**

- `/do-tasks --all` with `handler: gh-issue` dispatches sessions for distinct ready,
  unblocked issues, bounded by `wip_limit`, with blocked/held issues reported and their
  reason given. **Needs a repo actually on the `gh-issue` handler** — see the handoff's
  standing note on that.
