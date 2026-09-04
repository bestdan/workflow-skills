# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-04 after task 17.** Read this first, then
[`gh_migration_plan.md`](gh_migration_plan.md) (the epic) and
[`2026-08-24-requirements-and-evidence.md`](2026-08-24-requirements-and-evidence.md)
(the measured record).

## Redraft this file when you finish — read this before you start

**Finishing a task includes rewriting this file for the agent who picks up the next
one.** Rewrite, not append. Land it in the same unit of work as the task — a follow-up
is how it got skipped before. The plan docs live on this branch (draft PR #441) while a
task's own code goes to a fresh branch off `main`, so "the same PR" is not literally
available; land both before you call the task done.

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
remote. It is now committed under `dev_docs/gh-issue-migration/`, per the `.gitignore`
comment block's own advice: "Prefer graduating durable wisdom to a top-level
`dev_docs/<name>.md` (never ignored) over keeping it here."

It is still on **draft PR #441**, not on `main`. Do the migration's own work in a fresh
worktree off `main`; edit these plan docs on this branch.

## Where things stand

**One available task, 16.** Tasks 4–8, 14, 15 and 17 are done. Task 12 is unclaimed but
gates only the routine claim default. Task 13 is postponed, which **holds all of
Phase 4** with it.

- **Task 16** — batch execution (`/do-tasks --all`), absorbed from Linear PRE-117
  (cancelled as superseded). Read the closed PR #426 before starting: its §4 batch
  machinery is reusable and its five defects are the list of what not to repeat. Task 16
  carries the fetch command for its code. **Do not rebase #426** — it applies almost
  cleanly and is almost all wrong.
- **Task 12** — stale claim-ref sweep. Unclaimed, no task file. Gates flipping the
  routine claim default, nothing else.

**Two user-run checks are now unblocked and cheap.** Neither is a task; both are
acceptance criteria sitting on already-merged work, and both run against
`bestdan/dotfiles` today. See "Acceptance criteria still owed" below.

## What will bite you

**The vocabulary migration is finished.** Every gh-issue verb speaks `labels.yml`; there
is no bridge left anywhere. If you find an old spelling (`auto-eligible`, `priority:*`),
it is a defect, not a migration in progress.

**"Carrying a rung" means carrying one `labels.yml` defines** — never merely a label
whose name starts with `status:` or `auto:`. The same holds for `prio:` and `est:`. The
prefix reading is the trap: a hand-typed `status:blocked` satisfies it, so the issue
reads as healthy while being in a state nothing can act on. Anything new that asks "does
this issue have a rung?" must ask the vocabulary.

**A check must also ask whether the label it looks for is PROVISIONED.** This is the
sharper twin of the rule above and it is new with task 17. Label namespaces are per-repo,
so a rung the vocabulary defines may never have been created on the board — and then the
check's question is unanswerable, not answered "no". Reconciler row 3 asked whether a
closed issue had ever carried `status:4_needs_review` and hit **50 of 50** correctly
scoped closed issues on a repo that never provisioned it. Two things follow, and the
second is the one that generalises:

- The **scope does not vouch for the labels.** A label scope separates loop issues from
  strangers; it says nothing about provisioning. `gh-issue-reconcile.md` step 2 asserted
  otherwise and was corrected by task 17 — a false explanation costs more than the bug,
  because it stops the next reader looking.
- **Guard by group, not by completeness.** Row 2's premise is that a rung was
  _assignable_ and nobody assigned it, which still holds while its group has any member
  provisioned. Only an entirely empty group voids it. Row 1 needs no guard at all,
  because it ranks labels the issue already carries. Decide this per check rather than
  applying one blanket guard; a guard that is too wide silences a working row.

**The `Blocked by:` footer is an echo of a native edge, never a dependency in itself.**
Settled by task 8 for every path at once, which is the part that matters. Two
consequences:

- Nothing may read a footer to decide blocked-ness. `gh-issue-ready.py`, `/list-tasks`,
  `/do-tasks` and `/reoptimize-tasks` all read the edge.
- Nothing may write a footer for a dependency with no edge. Write the edge, then echo it.

The footer was **kept rather than dropped**, against task 8's own acceptance criterion,
which that task file explicitly anticipated. The deciding argument is that `/push-plan`
and `/reoptimize-tasks` must not disagree about what a footer means — and that argument
**does not turn on PRE-823**: if a runner can read the graph the footer is redundant but
harmless, and if it cannot the footer is the only unattended signal. Do not treat
PRE-823 as blocking a footer decision again.

**A scope this handler cannot honour, plus a write, is a refusal.** The gh-issue handler
has no initiative dimension and no project dimension of its own. `/reconcile-tasks` stops
on `--project` **with `--apply`** and continues at the default label scope without it;
`/reoptimize-tasks` stops on an `initiative` scope outright, because that flow writes in
every mode. Continuing wider would answer a request to _narrow_ with a _wider_ run. The
shape recurs wherever a scope flag reaches a handler that cannot implement it.

## Do not re-derive these — they were measured, not read

Full evidence in
[`dev_docs/decisions/2026-08-24-routine-claim-channel.md`](../decisions/2026-08-24-routine-claim-channel.md)
(committed) and §10b of the requirements record.

**Three credentialed channels, not two.**

- A cloud routine has **no `gh`** and **no credential on raw HTTP**. The GitHub MCP
  connector is its credentialed channel, and it has **no dependency-edge tool**.
- A **GitHub Actions runner** is the third. Measured 2026-09-02 by task 6
  ([PR #447](https://github.com/bestdan/workflow-skills/pull/447)) — a runner has `gh`,
  and its ambient `GITHUB_TOKEN`, with `permissions: issues: write` declared, PATCHes an
  issue's labels. No PAT, no extra secret. **Do not read "unattended" as "cloud routine"
  anywhere in this plan**; a runner is unattended too and is not channel-starved.
  - Same REST path local `gh` uses, so the label-write rule below is one mechanism.
  - The token is repo-scoped, so a runner writes only its **own** repo's issues. A repo
    whose `gh-issue.repo` points elsewhere must not run the task-6 backstop.
- A routine **can** acquire the claim ref (`mcp__github__create_branch`, create-only) but
  **cannot release** it — no delete-ref tool, and `git push --delete` 403s. Routines stay
  on the comment election; task 12 gates flipping that.

**Writes.**

- A label write **replaces** the whole set and **auto-creates** unknown names. Hence
  validate-then-replace, always, before any network call.
- The dependency POST body carries **`issue_id`, a database id**, not the issue number.
  So does the **removal DELETE**, as its last path segment:
  `DELETE repos/{owner}/{repo}/issues/{n}/dependencies/blocked_by/{issue_id}`. Measured
  2026-09-04 by task 8 — the edge is gone on readback, and the removal is idempotent.
- **GitHub refuses a directly reciprocal edge, and refuses nothing else.** Creating
  `A blocked_by B` when `B blocked_by A` exists returns **422**; the same probe built
  `A -> B -> C -> A` with no complaint. Measured 2026-09-04. Never read GitHub's guard as
  a guarantee that the graph is acyclic, and a batch edge write must survive a per-edge
  refusal rather than aborting with earlier edges already written.
- `blocked_by` is **paginated** — read it with `--paginate --slurp`. A bare read stops at
  30, and an invisible edge is a cycle that reads as absent.

**Reads**, measured 2026-09-03 by task 7
([PR #464](https://github.com/bestdan/workflow-skills/pull/464)).

- `repos/{owner}/{repo}/issues/{n}/events` carries the same `labeled` stream as the
  `timeline` endpoint, without `cross-referenced` and comment entries. Either answers
  "was this label ever applied"; `events` is the cheaper one.
- **`gh issue list` orders by creation date descending, not by close date.** So a
  `--limit` window over closed issues holds the most recently _created_ ones, and a
  long-lived issue closed yesterday can sit outside it. GitHub search has no
  `sort:closed`; `--search "sort:updated-desc"` is the nearest proxy and does compose
  with `--label`, but `updated` moves on a post-close comment.

**Provisioning**, measured 2026-09-04 by task 17
([PR #479](https://github.com/bestdan/workflow-skills/pull/479)).

- `gh-label-sync.py` is idempotent and **dry-run by default**, so
  `python3 …/gh-label-sync.py --repo <repo>` prints a board's gap for free. One
  `gh label list` is the whole cost, which is why the reconciler now makes that call
  itself before any per-issue work.
- **`bestdan/dotfiles` is the only live under-provisioned board, and it is deliberately
  still under-provisioned.** It is missing 12 of the 17 vocabulary labels
  (`status:3_started`, `status:4_needs_review`, every `prio:` and every `est:`) and is
  half-migrated besides. That inventory is live state and will drift, so **do not trust
  this sentence, run the dry run.** Both are filed on that repo's own tracker
  ([#675](https://github.com/bestdan/dotfiles/issues/675) provisions,
  [#676](https://github.com/bestdan/dotfiles/issues/676) migrates, natively linked) —
  they are that repo's work, not this plan's. **When #675 lands, the reproduction is
  gone**: anything testing under-provisioned behaviour then needs a fixture. Task 17's
  hermetic tests are that fixture, and they are the model to copy.
- **Provisioning a rung moves the noise rather than ending it, so expect #675 to be
  followed by a burst of row-3 findings — that is not a regression.** Every issue that
  closed before the rung existed still has no `labeled` event for it, so the first
  reconciler run after #675 flags exactly the issues task 17 taught it to suppress.
  Answering that needs a rollout boundary — evidence the rung was assignable when each
  issue closed — which is per-repo state this plan would then own and keep in step. Task
  17 documented it in `gh-issue-reconcile.py`'s docstring and deliberately did not build
  it. **Read that first post-provisioning run as a backlog, not as drift**, and do not
  file it as a task-17 defect.
- **`gh-label-sync.py`'s `existing_labels()` now refuses above 500 labels rather than
  truncating.** Task 17 made the reconciler conclude ABSENCE from that list, and a
  silently truncated read would suppress findings under a confident VOID line. Both
  callers share the change, so the sync fails loudly on a repo it previously limped
  through. Anything new that reads a repo's labels should go through that helper rather
  than re-deriving the call.

Both write facts are silent when wrong. This file was wrong twice by asserting routine
behaviour from documentation. Probe it.

## Open blockers, and who owns them

- **`/auto-pilot` does not support `gh-issue`** (task 13) — **postponed 2026-09-02**,
  because `/auto-pilot` is under active development with a new harness. It stops outright
  rather than degrading. **This holds Phase 4**, task 10's pilot included.
- **`sandbox-network-guard` blocks non-GET `gh api`.** Task 7 confirmed the workaround,
  not a fix: an asset's `--apply` PATCHes fine when the call runs **unsandboxed**,
  because the hook matches the `gh api` text and a python helper hides it. So this is
  friction rather than a wall, and every local write costs a sandbox escape.
  **Outside this repo; needs the operator.**
- **The whole handler is local-only: every asset shells out to `gh`.** A cloud routine
  has none, so the loop still owes an MCP branch reusing `labels.yml` for the same
  validate-then-replace rule. `gh-issue-reconcile.py` and `gh-issue-graph.py` both
  inherit this and say so.
- **`state_reason` on the close path is unowned.** `gh-issue-state.py --done` writes
  `state: closed` and nothing else, so a completed issue and an abandoned one are
  indistinguishable afterwards. No task covers it. It costs more than it did: task 8's
  stale-versus-satisfied split reads `state_reason` to decide whether an edge blocks
  forever or is already met, and every issue this loop closes reports the same reason.
- **The provisioning class is wider than the reconciler.** Task 17 guarded the three
  reconciler rows, which is where it was measured. Every other verb that asks whether an
  issue carries a rung inherits the same blind spot and is unaudited for it. Nothing
  detects that; there is no task.
- **Two label invariants have no reconciler rule.** Task 7's rule table is deliberately
  **closed**, so these were left out rather than becoming rows four and five — and task
  17 kept it closed:
  - `at most one prio:` / `at most one est:` — a duplicate stays invisible until the next
    write, which then refuses.
  - a **closed** issue still carrying live `status:`/`auto:` rungs. Reachable with a bare
    `gh issue close`, which is what a human reaches for.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic.
- Two non-migration follow-ups live in Linear: **PRE-822** (`reopened` unhandled by the
  task-6 backstop) and **PRE-823** (does a runner's token reach the dependency
  endpoints). PRE-823 no longer blocks anything in this plan.

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`. Nothing has switched, and the
switch waits on the auto-pilot harness — switching before then ends unattended operation
rather than degrading it. That is a postponement, not a dead end: every handler verb works
in a foreground session today, so the repo could switch and be driven by hand deliberately.

### Acceptance criteria still owed

Each needs a repo on the `gh-issue` handler — but read that carefully, because it is
**not** the same as needing this repo to switch: **`bestdan/dotfiles` is already
`handler: gh-issue`** (its `dev_docs/tasks/.task-config.yml`, `repo: bestdan/dotfiles`,
`labels: [task-add]`). Anything testing only **handler dispatch** can run there today,
against a real board, with no migration and no config change. What dotfiles cannot stand
in for is a **migrated** backlog: it was never on Linear, so it carries none of the
imported issues, old-vocabulary labels or `Blocked by:` footers the migration criteria
are about.

- **Task 8's dispatch half — runnable NOW, and the cheapest thing on this list.**
  `/reoptimize-tasks` against `bestdan/dotfiles`. The only thing that blocked it was the
  version, and **v2.21.0 shipped 2026-09-04 carrying PR #478**. A slash command
  dispatches to the **installed** plugin under
  `~/.claude/plugins/cache/workflow-skills/workflow-skills/<version>/`, a real directory
  rather than a symlink to a checkout, so confirm the installed version is v2.21.0 or
  later before reading the result — running against an older one exercises the old
  report-only prose and returns a green result that says nothing. Expect zero dependency
  findings, since dotfiles has no edges; read it as a dispatch check, not a coverage one.
  The scripts underneath were already exercised against live issues.
- **Task 17's re-run — done, and this is the one to imitate.** Run 2026-09-04 against
  `bestdan/dotfiles` both before and after the fix: row 3 went from 50 findings to a
  single provisioning-gap report, and row 2's five real findings were unchanged. Keeping
  the _before_ run is what made the _after_ run mean anything.
- **Task 4 — dispatch, runnable on `bestdan/dotfiles` today.** Two `/do-tasks` sessions
  against the same ready issue, confirming exactly one proceeds. Needs an issue at
  `status:2_ready` on that board and two concurrent sessions; the racing is the point, so
  a serial run proves nothing. Note `status:2_ready` **is** provisioned there.
- **Task 8's migrated-backlog half** — `/reoptimize-tasks` against the migrated
  `workflow-skills` backlog, spot-checking three edges in the UI. Needs task 9, so it
  waits on Phase 4.
- **Task 15** — its user-run check.

None of these is a defect. Task 7's was run and **found** task 17's defect, which is that
check earning its keep: the row was sitting on a blocked-looking list when it was
runnable, and free.
