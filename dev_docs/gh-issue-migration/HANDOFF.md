# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-03, after task 6.** Read this first, then
[`gh_migration_plan.md`](gh_migration_plan.md) (the epic) and
[`2026-08-24-requirements-and-evidence.md`](2026-08-24-requirements-and-evidence.md)
(the measured record).

## Redraft this file when you finish — read this before you start

**Finishing a task includes rewriting this file for the agent who picks up the next
one.** Rewrite, not append. Do it in the same PR as the work — a follow-up is how it
got skipped before.

The distinction is the whole rule. Patching adds your paragraph and leaves the previous
five in place, and the file becomes a changelog: a record of what was done, in the order
it was done, which is the one thing the next agent does not need. **They need what is
true now and what will bite them.** Git history and the PR record already hold the
narrative, and hold it better.

So each time: read this file as if you were the next agent, and rewrite what you find.

**What survives a redraft** — anything still operative:

- Which task is next, and what blocks it.
- Facts that were **measured**, that later tasks rest on. Keep the fact and a link to
  where it was measured; never copy the evidence, which rots with no invalidation.
- Conventions and limitations that will trip the next agent.
- Open questions, and who owns them.

**What goes** — anything whose job is finished:

- Per-task narratives of completed work. "Task N — PR #x, merged `<sha>`" belongs in the
  plan's task list, once, not here.
- Reasoning that has since been settled, or superseded by a measurement.
- Anything the next agent would read and then have to work out no longer applies.

**Two things to carry across that are easy to lose in a rewrite:**

1. **What you settled that OTHER tasks rest on.** The test: did you measure something
   that makes a sentence elsewhere in this plan false? If so, amend that sentence where
   it lives, not only here. Task 6 measured a third credentialed channel and falsified
   an inference inside task 8's `Constraint` — no amount of detail in task 6's own entry
   would have surfaced that.
2. **Where you deviated from the task file, and why.** Task 6's trigger list contradicted
   its own acceptance criterion, for a reason that only became visible mid-work. An
   undocumented deviation reads as a mistake later.

Also retire the finished task in the two places that drift independently of this file:
its own `status:` frontmatter (`new` → `done`) and its plan-list entry. And anything you
learned that is not a task goes to the tracker, not here — task 6 produced PRE-822 and
PRE-823 that way.

**Do not leave an untracked launcher doc behind.** The pointer that starts a session is
ephemeral — paste it and let it go. Three `.dev_docs/task_N_handoff.md` files had
accumulated by task 6, each a stale copy of plan content telling a fresh session to
begin already-merged work, none with any git history to recover from.

If your redraft looks like the previous version with a paragraph added, you patched it.

## Where this lives, and why it is not on main

This plan used to live under `dev_docs/tasks/gh_migration_plan/`, which `.gitignore:31`
excludes — so it existed in exactly one git worktree, uncommitted, with no history and no
remote. Tearing that worktree down would have destroyed it. It is now committed under
`dev_docs/gh-issue-migration/`, per the `.gitignore` comment block's own advice: "Prefer
graduating durable wisdom to a top-level `dev_docs/<name>.md` (never ignored) over keeping
it here."

It is still on **draft PR #441**, not on `main`. Do the migration's own work in a fresh
worktree off `main`; edit these plan docs on this branch.

## Where things stand

**Task 7 is next** — reconciler rules for the label invariants. Its blocker (task 5) is
done.

Phase 1–2 and tasks 4, 5, 6, 14, 15 are done; the plan's task list carries each one's PR
and merge sha. Tasks 7, 8, 12 are unclaimed. **Phase 4 is held** by task 13's
postponement — see the blockers below.

## What will bite you

**One verb is still on the old vocabulary: `reoptimize`.** Tasks 4 and 5 moved add /
list / promote / complete / claim onto the namespaced names in `labels.yml`, and task 4
deleted the bridges that let both spellings coexist. `gh-issue-reoptimize.md` was in
neither scope and still reads `auto-eligible` / `auto-claimed` / `priority:*`, so **on a
migrated board it classifies every issue as `new`**. It carries a scope note saying so;
migrating it belongs with task 8.

| Pre-migration                | The provisioned vocabulary |
| ---------------------------- | -------------------------- |
| `auto-eligible`              | `auto:eligible`            |
| `human-approval-requested`   | `auto:human-review-needed` |
| `auto-claimed`               | `status:3_started`         |
| `needs-review`               | `status:4_needs_review`    |
| `priority:urgent\|high\|...` | `prio:0`–`prio:3`          |

**Three open PRs edit files this plan owns**, against the old vocabulary — and they all
apply almost cleanly, which is what makes them dangerous. Read the epic's **In-flight PRs
against files this plan owns** section before touching `gh-issue-claim.md` or
`gh-issue-promote.md`. One of them (#426) was misled by a since-retired claim into
building a body-footer parser; its **Dependency-ready selection** section needs deleting,
not reconciling.

## Do not re-derive these — they were measured, not read

Full evidence in
[`dev_docs/decisions/2026-08-24-routine-claim-channel.md`](../decisions/2026-08-24-routine-claim-channel.md)
(committed) and §10b of the requirements record.

**Three credentialed channels, not two.**

- A cloud routine has **no `gh`** and **no credential on raw HTTP**. The GitHub MCP
  connector is its credentialed channel.
- A **GitHub Actions runner** is the third. Measured 2026-09-02 by task 6
  ([PR #447](https://github.com/bestdan/workflow-skills/pull/447)) — a runner has `gh`,
  and its ambient `GITHUB_TOKEN`, with `permissions: issues: write` declared, PATCHes an
  issue's labels. No PAT, no extra secret. **Do not read "unattended" as "cloud routine"
  anywhere in this plan**; a runner is unattended too and is not channel-starved.
  - Same REST path local `gh` uses, so the label-write rule below is one mechanism, not a
    third to re-measure.
  - **Unmeasured:** whether that token reaches the dependency / sub-issue endpoints.
    Task 8's `Constraint` turns on exactly this — tracked as **PRE-823**.
  - The token is repo-scoped, so a runner writes only its **own** repo's issues. A repo
    whose `gh-issue.repo` points elsewhere must not run the task-6 backstop.
- A routine **can** acquire the claim ref (`mcp__github__create_branch`, create-only) but
  **cannot release** it — no delete-ref tool, and `git push --delete` 403s. Routines stay
  on the comment election; task 12 gates flipping that.
- The connector has **no dependency-edge tool**.

**Writes.**

- A label write **replaces** the whole set and **auto-creates** unknown names. Hence
  validate-then-replace, always, before any network call.
- The dependency POST body carries **`issue_id`, a database id**, not the issue number.
- `blocked_by` is **paginated** — read it with `--paginate --slurp`.

Both write facts are silent when wrong. This file was wrong twice by asserting routine
behaviour from documentation. Probe it.

## Open blockers, and who owns them

- **`/auto-pilot` does not support `gh-issue`** (task 13) — **postponed 2026-09-02**,
  because `/auto-pilot` is under active development with a new harness and teaching it a
  fifth handler against a moving target is rework. It stops outright rather than
  degrading. **This holds Phase 4**, task 10's pilot included.
- **`sandbox-network-guard` blocks non-GET `gh api`.** Until an allowlist entry exists in
  the operator's dotfiles, `gh-issue-state.py`'s PATCH cannot run locally.
  **Outside this repo; needs the operator.**
- **`gh-issue-reoptimize.md` has not migrated** — see above. Owned by task 8.
- **`gh-issue-state.py` shells out to `gh api`.** A cloud routine has no `gh`, so the loop
  still owes an MCP branch reusing `labels.yml` for the same validate-then-replace rule.
- **`state_reason` on the close path is unowned.** `gh-issue-state.py --done` writes
  `state: closed` and nothing else, so a completed issue and an abandoned one are
  indistinguishable afterwards — GitHub's `state_reason` (`completed` / `not_planned`)
  is never set. No task covers it. Recorded here because it previously survived only in
  an untracked launcher doc and was one deletion away from being lost.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic. Every
  other task has one under a `phase_*/` directory.
- Two non-migration follow-ups live in Linear: **PRE-822** (`reopened` unhandled by the
  task-6 backstop) and **PRE-823** (the dependency-endpoint probe above).

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`. Nothing has switched, and the
switch waits on the auto-pilot harness — switching before then ends unattended operation
rather than degrading it. That is a postponement, not a dead end: every handler verb works
in a foreground session today, so the repo could switch and be driven by hand deliberately.

Two acceptance criteria are consequently **unmet, and neither is a defect** — both need a
repo actually on the `gh-issue` handler. Run them when one exists:

- **Task 4** — two `/do-tasks` sessions against the same ready issue, confirming exactly
  one proceeds.
- **Task 15** — its user-run check.
