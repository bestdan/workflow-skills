# Handoff — migrating the task loop from Linear to GitHub Issues

**Updated 2026-09-03.** Read this first, then
[`gh_migration_plan.md`](gh_migration_plan.md) (the epic) and
[`2026-08-24-requirements-and-evidence.md`](2026-08-24-requirements-and-evidence.md)
(the measured record).

## Your last commit updates this file — read this before you start

**Whoever finishes a task owns leaving this handoff true for the next one.** This is
part of the task, not tidying after it. Do it in the same PR as the work, not a
follow-up — a follow-up is how it got skipped before.

Task 6 is the worked example of the failure. It merged and left **three** places still
saying it was next, plus a launcher doc telling a fresh session to start it. The next
agent's first act would have been to redo a merged task. Each item below is one of those:

1. **Advance the pointer.** Change "**Task N is next**" in this file, and move the
   **NEXT.** marker in the plan's task list to the task that actually is.
2. **Retire the finished task in all three places** — they drift independently:
   its own file's `status:` frontmatter (`new` → `done`), its plan-list entry
   (strikethrough + **Done** — PR #n, merged `<sha>`), and this file's status section.
3. **Record what you settled that OTHER tasks rest on**, not just that you shipped.
   The test: did you measure something that makes a sentence elsewhere in this plan
   false? If so, amend that sentence where it lives and say what replaced it. Task 6
   measured a third credentialed channel and falsified an inference in task 8's
   `Constraint`, which no amount of detail in task 6's own entry would have surfaced.
   **Link to the PR; do not copy its evidence here** — a committed copy of run IDs and
   PR state rots with no invalidation.
4. **Say where you deviated from the task file, and why.** Task 6's trigger list
   contradicted its own acceptance criterion for a reason that only became visible
   during the work. A deviation nobody wrote down reads as a mistake later.
5. **Delete your launcher doc.** These live untracked under `.dev_docs/task_N_handoff.md`
   and say "paste this into a fresh session". Once the task ships, that instruction is
   actively harmful, and being untracked it has no history to recover — so it is only
   ever deleted deliberately. Three had accumulated by task 6.
6. **Anything you learned that is not a task** goes to the tracker, not here. Task 6
   produced PRE-822 and PRE-823 that way.

If you finish a task and this file needed no edit, you almost certainly missed item 3.

## Where this lives, and why it moved

This plan used to live under `dev_docs/tasks/gh_migration_plan/`, which
`.gitignore:31` excludes — so it existed in exactly one git worktree, uncommitted, with
no history and no remote. Tearing that worktree down would have destroyed it. It is now
committed under `dev_docs/gh-issue-migration/`, per the `.gitignore` comment block's own
advice: "Prefer graduating durable wisdom to a top-level `dev_docs/<name>.md` (never
ignored) over keeping it here."

## State: phase 1–2 and tasks 4, 5, 6, 14 and 15 are done

**PR #415 merged as `d7aa23a`.** It shipped:

- `commands/handlers/assets/labels.yml` — the 17-label vocabulary (`status:`, `auto:`,
  `prio:`, `est:`), provisioned live on `bestdan/workflow-skills`
- `commands/handlers/assets/_labels.py` — the stdlib-only vocabulary reader
- `commands/handlers/assets/gh-label-sync.py` — idempotent per-repo provisioning
- `commands/handlers/assets/gh-issue-state.py` — validate, then ONE full-set PATCH
  carrying labels and open/closed together
- `dev_docs/decisions/2026-08-24-routine-claim-channel.md` — the routine-channel evidence
- 24 hermetic tests

## The one thing that will trip you up

**One verb is still on the old vocabulary: `reoptimize`.** Tasks 4 and 5 moved add /
list / promote / complete / claim onto the namespaced names in `labels.yml`, and task 4
deleted the bridges that let both spellings coexist. `gh-issue-reoptimize.md` was not in
either scope and still reads `auto-eligible` / `auto-claimed` / `priority:*`, so **on a
migrated board it classifies every issue as `new`**. It carries a scope note saying so;
migrating it belongs with task 8, which is the other reoptimize work.

The rename, for reference:

| Pre-migration                | The provisioned vocabulary |
| ---------------------------- | -------------------------- |
| `auto-eligible`              | `auto:eligible`            |
| `human-approval-requested`   | `auto:human-review-needed` |
| `auto-claimed`               | `status:3_started`         |
| `needs-review`               | `status:4_needs_review`    |
| `priority:urgent\|high\|...` | `prio:0`–`prio:3`          |

**Task 5** — PR #439, merged `a4815d7` — moved `/add-task`, `/list-tasks`,
`/promote-tasks` and `/complete-task`, and added `gh-issue-ready.py` for
dependency-readiness. **Task 4** — PR #442 — moved `/do-tasks`'s claim lifecycle, added
`gh-issue-claim.py` for the parts two racing sessions must perform identically, taught
claim to consult native `blocked_by`, and removed task 5's bridges.

**Task 7 is next.** Task 14 — the `--no-claim` branch-name defect task 4 shipped — is
**done** (PR #443), and so are tasks 15 and 6.

**Task 6 — PR #447, merged `4261eb67`, shipped v2.18.0.** The `needs_review` transition
and its reverse are automated by `.github/workflows/gh-issue-pr-sync.yml` +
`commands/handlers/assets/gh-issue-pr-sync.py`. Pieces 1 and 2 only — piece 3 (the agent
writing the rung when it opens the PR) is still deferred behind the auto-pilot harness,
same reason task 13 is. Four things it settled that this plan depended on:

- **A runner is a third credentialed channel** — see the bullet under "Do not re-derive
  these". This is the finding with reach beyond task 6.
- **The trigger set is `opened` + `ready_for_review` + `closed`**, not the task file's
  `ready_for_review` + `closed`. A PR opened straight to non-draft emits no
  `ready_for_review`, so the task file's list left that case permanently uncaught. The
  job's `if:` gates on `draft` rather than the event name, which is what makes `opened`
  safe.
- **The two runs for one PR race**, and the fix is a `concurrency:` group keyed on the PR
  number with `cancel-in-progress: false`. Reproduced and fixed under A/B on live
  runners — mark a PR ready then close it seconds later and the close reads a stale rung,
  no-ops green, and strands the issue in `needs_review`.
- **The backstop assumes the tracker is the code's own repo.** `GITHUB_TOKEN` is
  repo-scoped, so a repo whose `gh-issue.repo` points elsewhere must not run it.

Two follow-ups are tracked in Linear rather than here, because neither is a migration
task: **PRE-822** (`reopened` is still unhandled) and **PRE-823** (probe whether a
runner's token reaches the dependency endpoints — the fact task 8's `Constraint` now
turns on).

**Task 15 — PR #444, merged `09aa71f`.** Dependency-blocking is no longer inert.
`commands/handlers/assets/gh-issue-deps.py` creates the native `blocked_by` edges and
`/push-plan` §5.5 calls it as a second pass after every issue exists, so the read paths
tasks 4 and 5 shipped now have data to read. Two facts it pinned, both silent when wrong:
the POST body carries **`issue_id`, a database id**, not the issue number, and
`blocked_by` is paginated, so the create-missing-only check reads it with
`--paginate --slurp`. It also retired the "no native dependency edge" claim in **four**
files — the three the task named plus `commands/reoptimize-tasks.md`.

**The footer stayed**, as a human-readable echo of the edge. The deciding reason turned up
late: a cloud routine can read an issue **body** but has no way to reach the edge, so
`Blocked by: #<n>` is the only blocked-ness signal available **to a routine** — a hint,
never the graph. That is narrower than it was first written: this said "unattended", which
task 6 falsified by measuring a runner that is unattended and has `gh`. Whether a runner
can read the edge is unmeasured (PRE-823). See task 8's **Constraint** section, which
carries the full amendment, before changing that.

The same audit produced the epic's **In-flight PRs against files this plan owns**
section. Read it before touching `gh-issue-claim.md` or `gh-issue-promote.md` — three
open PRs edit those files against the old vocabulary, and they all apply almost cleanly,
which is exactly what makes them dangerous.

One consequence still open: `gh-issue-state.py` shells out to `gh api`. A cloud routine
has no `gh`, so the loop still owes an MCP branch that reuses `labels.yml` for the same
validate-then-replace rule.

## Do not re-derive these — they were measured, not read

Full evidence in
[`dev_docs/decisions/2026-08-24-routine-claim-channel.md`](../decisions/2026-08-24-routine-claim-channel.md)
(committed) and §10b of the requirements record.

- A cloud routine has **no `gh`** and **no credential on raw HTTP**. The GitHub MCP
  connector is its credentialed channel.
- **There is a THIRD credentialed channel: a GitHub Actions runner.** Measured
  2026-09-02 by task 6 ([PR #447](https://github.com/bestdan/workflow-skills/pull/447)) —
  a runner has `gh`, and its ambient `GITHUB_TOKEN`, with `permissions: issues: write`
  declared on the workflow, PATCHes an issue's labels. No PAT, no extra secret. Do not
  read "unattended" as "cloud routine" anywhere in this plan; a runner is unattended too,
  and it is **not** channel-starved the way a routine is.
  - It is the same REST path local `gh` uses, so the label-write semantics below are the
    same mechanism rather than a third set to re-measure.
  - What is **not** measured: whether that token reaches the dependency / sub-issue
    endpoints. Task 8's constraint turns on exactly that — probe before relying on it.
  - The token is repo-scoped, so a runner can only write its **own** repo's issues.
- On the local-`gh` and connector channels a label write **replaces** the whole set and
  **auto-creates** unknown names. Hence validate-then-replace, always, before any network
  call.
- A routine **can** acquire the claim ref (`mcp__github__create_branch`, create-only) but
  **cannot release** it — no delete-ref tool, and `git push --delete` 403s. So routines
  stay on the comment election. Task 12 (the stale-ref sweep) gates flipping that.
- The connector has **no dependency-edge tool**, so task 8 is local-only.

This file was wrong twice by asserting routine behaviour from documentation. Probe it.

## Open blockers, and who owns them

- **`sandbox-network-guard` blocks non-GET `gh api`.** Until an allowlist entry exists in
  the operator's dotfiles, `gh-issue-state.py`'s PATCH cannot run locally — so task 5
  cannot test its own write path. **Outside this repo; needs the operator.**
- **`/auto-pilot` does not support `gh-issue`** (task 13) — **postponed 2026-09-02**,
  because `/auto-pilot` is under active development with a new harness and teaching it a
  fifth handler against a moving target is rework. It stops outright rather than
  degrading, so switching `.task-config.yml` to `gh-issue` takes this repo out of
  unattended operation until the harness lands and task 13 is done.
- **`gh-issue-reoptimize.md` has not migrated** — see above. Owned by task 8.
- ~~**Nothing creates a native dependency edge**~~ — **resolved by task 15** (PR #444).
  The stale claim it retired had already misled one open PR (#426) into building a
  body-footer parser; that PR still needs its **Dependency-ready selection** section
  deleted rather than reconciled (see the epic's in-flight section).
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic. Every
  other task has one under a `phase_*/` directory.

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`, with four Linear projects.
Nothing has switched yet, and **the switch now waits on the auto-pilot harness** — task 13
is postponed until it lands (see the open blockers above), and switching before then ends
unattended operation rather than degrading it.

That is a postponement, not a dead end. Every handler verb works in a foreground session
today, so the repo could switch and be driven by hand deliberately. What it cannot do is
switch and stay unattended.

A consequence worth naming: **task 4's acceptance criteria are not all met yet.** Its
code-enforced ones are (hermetic tests for the race, the branch parser and the WIP cap),
but its user-run one — two `/do-tasks` sessions against the same ready issue, confirming
exactly one proceeds — needs a repo actually on the `gh-issue` handler. Run it when one
is.
