# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-03 after task 7; PR states re-checked 2026-09-04 after it merged.** Read this first, then
[`gh_migration_plan.md`](gh_migration_plan.md) (the epic) and
[`2026-08-24-requirements-and-evidence.md`](2026-08-24-requirements-and-evidence.md)
(the measured record).

## Redraft this file when you finish — read this before you start

**Finishing a task includes rewriting this file for the agent who picks up the next
one.** Rewrite, not append. Land it in the same unit of work as the task — a follow-up
is how it got skipped before. Note the plan docs live on this branch (draft PR #441)
while a task's own code goes to a fresh branch off `main`, so "the same PR" is not
literally available; land both before you call the task done.

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
   it lives, not only here.
2. **Where you deviated from the task file, and why.** An undocumented deviation reads
   as a mistake later.

Also retire the finished task in the two places that drift independently of this file:
its own `status:` frontmatter (`new` → `done`) and its plan-list entry. And anything you
learned that is not a task goes to the tracker, not here.

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

**Task 8 is next** — upgrade `reoptimize` from report-only to native dependency edges.
Its blocker (task 5) is done, so it is unblocked. Read its **Constraint** section before
touching the body footer: whether to keep writing it as a human-readable echo is still
open, and it is now that task's main judgment call rather than a detail.

Phase 1–2 and tasks 4, 5, 6, 7, 14, 15 are done; the plan's task list carries each one's
PR and merge sha. Tasks 12 and 13 are unclaimed. **Phase 4 is held** by task 13's
postponement — see the blockers below.

## What will bite you

**One verb is still on the old vocabulary: `reoptimize` — and it is yours.** Tasks 4 and
5 moved add / list / promote / complete / claim onto the namespaced names in `labels.yml`,
and task 4 deleted the bridges that let both spellings coexist.
`gh-issue-reoptimize.md` was in neither scope and still reads `auto-eligible` /
`auto-claimed` / `priority:*`, so **on a migrated board it classifies every issue as
`new`**. It carries a scope note saying so. Task 8 owns migrating it alongside the
dependency-edge work.

| Pre-migration                | The provisioned vocabulary |
| ---------------------------- | -------------------------- |
| `auto-eligible`              | `auto:eligible`            |
| `human-approval-requested`   | `auto:human-review-needed` |
| `auto-claimed`               | `status:3_started`         |
| `needs-review`               | `status:4_needs_review`    |
| `priority:urgent\|high\|...` | `prio:0`–`prio:3`          |

**"Carrying a rung" means carrying one `labels.yml` defines** — never merely a label
whose name starts with `status:` or `auto:`. `gh-issue-state.py`'s `validate()` has always
read it that way (it rejects any name outside the vocabulary outright), and task 7's
reconciler now does too. The prefix reading is the trap: a hand-typed `status:blocked`
satisfies it, so an issue carrying only that name reads as healthy while being in a state
nothing can act on. Anything new that asks "does this issue have a rung?" must ask the
vocabulary.

**A scope this handler cannot honour, plus a write, is a refusal.** `/reconcile-tasks`
passes `--project` through, and the gh-issue handler has no project dimension to honour
it with. Continuing repo-wide would answer a request to _narrow_ with a _wider_ run —
harmless while reading, and under `--apply` it writes outside the scope the user named.
So task 7's handler stops on `--project` **with `--apply`** and continues at the default
label scope without it, which is the same rule `gh-issue-archive.md` step 2 applies to a
missing label scope. Task 8 writes too; the shape recurs wherever a scope flag reaches a
handler that cannot implement it.

**One open PR still edits files this plan owns: #426.** Checked 2026-09-04 —
[#411](https://github.com/bestdan/workflow-skills/pull/411) and
[#432](https://github.com/bestdan/workflow-skills/pull/432) have since merged, and they
merged **reconciled**: `git grep` over `origin/main` finds no live old-vocabulary query
left in `gh-issue-promote.md` or `gh-issue-claim.md`, only two prose mentions of
`auto-eligible` as history. The epic's **In-flight PRs against files this plan owns**
table still describes all three as open; read it for #426 only.

#426 is the one that matters, and it is the dangerous one: it was misled by a
since-retired claim into building a body-footer parser, so its **Dependency-ready
selection** section needs **deleting, not reconciling**. It also still spells the old
vocabulary and `task/<n>`, so it applies almost cleanly and would ship a feature that
matches zero issues.

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

**Reads**, measured 2026-09-03 by task 7
([PR #464](https://github.com/bestdan/workflow-skills/pull/464)).

- `repos/{owner}/{repo}/issues/{n}/events` carries the same `labeled` stream as the
  `timeline` endpoint, without `cross-referenced` and comment entries. Either answers
  "was this label ever applied"; `events` is the cheaper one.
- **`gh issue list` orders by creation date descending, not by close date.** So a
  `--limit` window over closed issues holds the most recently _created_ ones, and a
  long-lived issue closed yesterday can sit outside it. GitHub search has no
  `sort:closed`; `--search "sort:updated-desc"` is the nearest proxy and does compose
  with `--label`, but `updated` moves on a post-close comment, so it answers a different
  question.

Both write facts are silent when wrong. This file was wrong twice by asserting routine
behaviour from documentation. Probe it.

## Open blockers, and who owns them

- **`/auto-pilot` does not support `gh-issue`** (task 13) — **postponed 2026-09-02**,
  because `/auto-pilot` is under active development with a new harness and teaching it a
  fifth handler against a moving target is rework. It stops outright rather than
  degrading. **This holds Phase 4**, task 10's pilot included.
- **`sandbox-network-guard` blocks non-GET `gh api`.** Task 7 confirmed the workaround,
  not a fix: `gh-issue-state.py --apply` PATCHed a live issue successfully when the call
  ran **unsandboxed**. So this is friction rather than a wall, and every local write
  still costs a sandbox escape until an allowlist entry exists.
  **Outside this repo; needs the operator.**
- **`gh-issue-reoptimize.md` has not migrated** — see above. Owned by task 8.
- **The whole handler is local-only: every asset shells out to `gh`.** A cloud routine has
  none, so the loop still owes an MCP branch reusing `labels.yml` for the same
  validate-then-replace rule. `gh-issue-reconcile.py` (task 7) inherits this and says so.
- **`state_reason` on the close path is unowned.** `gh-issue-state.py --done` writes
  `state: closed` and nothing else, so a completed issue and an abandoned one are
  indistinguishable afterwards — GitHub's `state_reason` (`completed` / `not_planned`) is
  never set. No task covers it. Task 7's rule 3 now _reads_ `state_reason` as context on a
  finding, which makes the gap cheaper to live with and no less real: every issue this
  loop closes reports the same reason.
- **Two label invariants have no reconciler rule.** Found while building task 7's three.
  Its rule table is deliberately **closed**, so these were left out rather than becoming
  rows four and five. Both are real and neither is covered anywhere else:
  - `at most one prio:` / `at most one est:` — a duplicate stays invisible until the next
    write, which then refuses.
  - a **closed** issue still carrying live `status:`/`auto:` rungs. Reachable with a bare
    `gh issue close`, which is what a human reaches for; only `gh-issue-state.py --done`
    clears them.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic. Every
  other task has one under a `phase_*/` directory.
- Two non-migration follow-ups live in Linear: **PRE-822** (`reopened` unhandled by the
  task-6 backstop) and **PRE-823** (the dependency-endpoint probe above).

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`. Nothing has switched, and the
switch waits on the auto-pilot harness — switching before then ends unattended operation
rather than degrading it. That is a postponement, not a dead end: every handler verb works
in a foreground session today, so the repo could switch and be driven by hand deliberately.

Three acceptance criteria are consequently **unmet, and none is a defect** — each needs a
repo actually on the `gh-issue` handler. Run them when one exists:

- **Task 4** — two `/do-tasks` sessions against the same ready issue, confirming exactly
  one proceeds.
- **Task 7** — `/reconcile-tasks` end to end through the command rather than the script.
  The script itself was verified against the live API (a hand-edited double-rung issue was
  reported, then repaired), so what is untested is only the handler dispatch.
- **Task 15** — its user-run check.
