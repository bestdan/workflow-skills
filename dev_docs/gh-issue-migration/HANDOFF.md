# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-04 after task 8.** Read this first, then
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

**Two available tasks, 16 and 17.** Both unblocked, neither blocking the other. Tasks
4–8, 14 and 15 are done; 12 is unclaimed but gates only the routine claim default; 13 is
postponed, which **holds all of Phase 4** with it.

**Take 17 first if you want a short one** — it is size 1, it is a defect in shipped work
rather than new surface, and it is the reason `/reconcile-tasks` output cannot currently
be read on an under-provisioned board.

- **Task 16** — batch execution (`/do-tasks --all`), absorbed from Linear PRE-117
  (cancelled as superseded). Read the closed PR #426 before starting: its §4 batch
  machinery is reusable and its five defects are the list of what not to repeat. Task 16
  carries the fetch command for its code. **Do not rebase #426** — it applies almost
  cleanly and is almost all wrong.
- **Task 17** — reconciler row 3 hits every closed issue on a repo where
  `status:4_needs_review` was never provisioned. Fix the row **and** the step-2 paragraph
  that claims the label scope already prevents it; a false explanation costs more than
  the bug, because it stops the next reader looking.
- **Task 12** — stale claim-ref sweep. Unclaimed, no task file. Gates flipping the
  routine claim default, nothing else.

## What will bite you

**The vocabulary migration is finished.** Task 8 moved the last verb;
`gh-issue-reoptimize.md` was the file still reading `auto-eligible` / `auto-claimed` /
`priority:*`, which made it classify every issue on a migrated board as `new`. Every
gh-issue verb now speaks `labels.yml`. There is no bridge left anywhere — task 4 deleted
the last one. If you find an old spelling, it is a defect, not a migration in progress.

**"Carrying a rung" means carrying one `labels.yml` defines** — never merely a label
whose name starts with `status:` or `auto:`. The same holds for `prio:` and `est:`.
`gh-issue-state.py`'s `validate()` has always read it that way, task 7's reconciler does,
and task 8's `gh-issue-graph.py` does. The prefix reading is the trap: a hand-typed
`status:blocked` or `prio:urgent` satisfies it, so the issue reads as healthy while being
in a state nothing can act on — or gets ranked by an order nothing defines. Anything new
that asks "does this issue have a rung?" must ask the vocabulary.

**The `Blocked by:` footer is an echo of a native edge, never a dependency in itself.**
Settled by task 8 for every path at once, which is the part that matters — the rule was
previously true on `/push-plan` and false on `/reoptimize-tasks`, and one path writing
unbacked footers is what made every footer unreadable as evidence. Two consequences:
- Nothing may read a footer to decide blocked-ness. `gh-issue-ready.py`, `/list-tasks`,
  `/do-tasks` and now `/reoptimize-tasks` all read the edge.
- Nothing may write a footer for a dependency with no edge. Write the edge, then echo it.

The footer was **kept rather than dropped**, against task 8's own acceptance criterion,
which that task file explicitly anticipated. The deciding argument is that `/push-plan`
and `/reoptimize-tasks` must not disagree about what a footer means — and note that
argument **does not turn on PRE-823**: if a runner can read the graph the footer is
redundant but harmless, and if it cannot the footer is the only unattended signal, so
consistency decides it either way. Do not treat PRE-823 as blocking a footer decision
again.

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
  `A blocked_by B` when `B blocked_by A` exists returns **422** ("this dependency would
  create a cycle where the target is already blocked by the source"); the same probe
  built `A -> B -> C -> A` with no complaint. Measured 2026-09-04. Two consequences:
  never read GitHub's guard as a guarantee that the graph is acyclic — which is why
  cycle detection reads the real graph — and a batch edge write must survive a per-edge
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
  indistinguishable afterwards. No task covers it. It now costs more than it did: task
  8's stale-versus-satisfied split reads `state_reason` to decide whether an edge blocks
  forever or is already met, and every issue this loop closes reports the same reason.
- **A partially provisioned repo makes the audits lie, and nothing detects it.**
  `gh-label-sync.py` is idempotent and **dry-run by default**, so
  `python3 …/gh-label-sync.py --repo <repo>` prints the gap for free — run it before
  trusting any audit's output on a board, and before reading a pilot result. The
  instance that exposed this is **task 17** (reconciler row 3), now a plan task rather
  than a loose blocker; the class is wider than that one row, so it stays here.
  As of 2026-09-04 `bestdan/dotfiles` is missing a double-figure share of the vocabulary
  and is **half-migrated** besides — 110 of its 254 issues carry the old and new
  vocabularies at once. That inventory is live state and will drift, so **do not trust
  this sentence, run the dry run.** Both are filed on that repo's own tracker
  ([#675](https://github.com/bestdan/dotfiles/issues/675) provisions,
  [#676](https://github.com/bestdan/dotfiles/issues/676) migrates, natively linked) —
  they are that repo's work, not this plan's.
- **Two label invariants have no reconciler rule.** Task 7's rule table is deliberately
  **closed**, so these were left out rather than becoming rows four and five:
  - `at most one prio:` / `at most one est:` — a duplicate stays invisible until the next
    write, which then refuses.
  - a **closed** issue still carrying live `status:`/`auto:` rungs. Reachable with a bare
    `gh issue close`, which is what a human reaches for.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic.
- Two non-migration follow-ups live in Linear: **PRE-822** (`reopened` unhandled by the
  task-6 backstop) and **PRE-823** (does a runner's token reach the dependency
  endpoints). PRE-823 no longer blocks anything in this plan — see the footer note above.

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`. Nothing has switched, and the
switch waits on the auto-pilot harness — switching before then ends unattended operation
rather than degrading it. That is a postponement, not a dead end: every handler verb works
in a foreground session today, so the repo could switch and be driven by hand deliberately.

Four acceptance criteria were consequently unmet, **and none is a defect** — task 7's is
now run (below), leaving three. Each needs a
repo on the `gh-issue` handler — but read that carefully, because it is **not** the same
as needing this repo to switch: **`bestdan/dotfiles` is already `handler: gh-issue`**
(its `dev_docs/tasks/.task-config.yml`, `repo: bestdan/dotfiles`, `labels: [task-add]`).
Anything testing only **handler dispatch** can run there today, against a real board,
with no migration and no config change. What dotfiles cannot stand in for is a
**migrated** backlog: it was never on Linear, so it carries none of the imported issues,
old-vocabulary labels or `Blocked by:` footers the migration criteria are about. Sort
each criterion by which of the two it actually needs:

- **Task 7 — RUN 2026-09-04 against `bestdan/dotfiles`. Dispatch passes.** Config →
  `gh-issue` → `gh-issue-reconcile.md` → the script → its report; every step followable
  as written, and the handler's own arguments (label scope from config, `--project`
  unsupported, dry-run default) behaved as documented.
  **One gap left, and it is small:** the session's cwd was `workflow-skills`, so the
  config was resolved against `$HOME/src/dotfiles` explicitly rather than through
  `git rev-parse --show-toplevel`. Everything downstream of that one line was the real
  path. A session launched **in** a gh-issue repo closes it.
  **It also found a defect** — row 3's unprovisioned-rung blind spot, in the blocker
  list below. That is the check earning its keep: it was sitting on this list looking
  blocked when it was runnable, and free.
- **Task 4 — dispatch, and runnable on `bestdan/dotfiles` today.** Two `/do-tasks`
  sessions against the same ready issue, confirming exactly one proceeds. Needs an issue
  at `status:2_ready` on that board and two concurrent sessions; the racing is the point,
  so a serial run proves nothing.
- **Task 8** — `/reoptimize-tasks` against the migrated `workflow-skills` backlog,
  spot-checking three edges in the GitHub UI. Needs task 9, so it waits on Phase 4.
  Only the **command dispatch** is untested: the scripts under it were exercised against
  live issues on `bestdan/dotfiles` — edges created, a 3-cycle detected through transitive
  backfill, an edge deleted and confirmed gone on readback, stale/satisfied classified off
  real `state_reason`.
  - **Run the dispatch half early, on `bestdan/dotfiles`, once a release ships.** That
    repo is already `handler: gh-issue`, so it needs no migration and no config change —
    the only thing blocking it is the version. **Do not run it before the release:** a
    slash command dispatches to the **installed** plugin under
    `~/.claude/plugins/cache/workflow-skills/workflow-skills/<version>/`, which is a real
    directory rather than a symlink to a checkout, so running it today exercises the old
    report-only prose and returns a green result that says nothing about the change. Once
    the version carrying [PR #478](https://github.com/bestdan/workflow-skills/pull/478) is
    installed, this costs minutes and covers what the live probe could not: that the
    command routes to the handler and its steps are followable as written. Expect zero
    dependency findings — dotfiles has no edges — so read it as a dispatch check, not a
    coverage one.
- **Task 15** — its user-run check.
