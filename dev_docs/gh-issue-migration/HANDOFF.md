# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-04 after task 16.** Read this first, then
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
its own `status:` frontmatter and its plan-list entry. And anything you learned that is
not a task goes to the tracker, not here.

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

**No task is available to start.** Tasks 4–8 and 14–17 are done, task 16 included
(merged `547f776`). Task 12 is unclaimed and has no task file. Task 13 is postponed,
which **holds all of Phase 4** — so the only unclaimed work in this plan is task 12,
and it gates nothing anyone is waiting on.

- **Task 16** — batch execution (`/do-tasks --all`) for gh-issue. Merged; see "What
  task 16 changed that you will trip over" below, because it moved a premise the rest of
  this plan rests on. **What it shipped is off by default**, so nothing observable
  changed for any user — the probe below is what turns it on.
- **Task 12** — stale claim-ref sweep. Unclaimed, no task file. It gates flipping the
  routine claim default, and it is now also the sweep that would let a dispatched batch
  session take the ref lock instead of the comment election.

**Three user-run checks are unblocked and cheap.** None is a task; all are acceptance
criteria sitting on already-merged work. See "Acceptance criteria still owed".

## What will bite you

### The cloud-session premise moved, and the new one is NOT probed

This is the most important thing on the page, and it is unresolved rather than settled.

`do-tasks.md` §3 asserted for months that a dispatched cloud VM "has no plugin
installed", and the 2026-08-24 decision record measured that a routine has **no `gh`**.
Anthropic's current documentation says otherwise on both counts, read 2026-09-04:

- A cloud session **does** install plugins the repo declares in a **committed
  `.claude/settings.json`** (`extraKnownMarketplaces` + `enabledPlugins`). Plugins
  enabled only in user settings do **not** travel.
- `gh` **is** preinstalled and proxy-authenticated (`GH_TOKEN` injected); the proxy
  scopes API access to the repositories attached to the session.

**Nothing here was probed.** Docs are not measurement, and this plan's own record exists
because routine behaviour was got wrong twice by reading documentation
([`2026-08-24-routine-claim-channel.md`](../decisions/2026-08-24-routine-claim-channel.md)
— "Probe it."). Treat the conflict as **open**, not as a correction that landed. Two
specific things stay unknown and are exactly what the probe must answer: whether the
marketplace clone survives the session's GitHub proxy scoping, and whether a
`PATCH /repos/{o}/{r}/issues/{n}` label write is permitted through it.

**The probe is owed, and it is what gates remote batch.** Declare this plugin in a
consumer repo's committed `.claude/settings.json`, run one cloud session, and record
whether `claude plugin list` shows it, `$CLAUDE_PLUGIN_ROOT/commands/handlers/assets/`
exists, `gh auth status` passes, and a label PATCH succeeds. **Do not run it against
`workflow-skills` itself** — the assets are in this repo's own checkout, so a green
result there proves nothing about a consumer repo. Write the answer as a dated file
beside the 2026-08-24 decision record.

### What task 16 changed that you will trip over

- **gh-issue batch is opt-in.** `gh-issue.remote_batch` defaults to **`false`** (Linear's
  defaults to `true`), and the capability matrix reads **`opt`**, not `yes`. This is a
  **deviation from task 16's own acceptance criterion**, which said flip it to `yes`; the
  reason is the unprobed premise above. Flipping the default is a one-line change once
  the probe lands.
- **A dispatched session claims on the comment election, never the ref lock**, and it
  runs **two `git ls-remote` probes** the election itself does not contain — one before
  the board write, one with the post-sleep re-list. Those probes are the only way a batch
  session and a local ref-lock session detect each other; the elections cannot see each
  other's primitive. `claim-lock.md` now carries its own entry condition for this, so it
  is not a rule that lives only in `do-tasks.md` §4.
- **The dispatcher discharges two gates, and deliberately not the third.** Candidate
  selection and the pre-claim WIP gate happen dispatcher-side; the session runs
  pre-flight, the claim, execute, PR, and the two label writes. The WIP gate is
  discharged because it is **provably** redundant — at most `slack` sessions each
  observe a count strictly under `wip_limit` however they interleave. **Dependency
  readiness is re-run in the session** before claiming: a blocker can reopen between
  selection and claim, and no arithmetic makes that recheck redundant. The distinction is
  the point — "the dispatcher just asked" is not a reason to skip a gate; only
  provable redundancy is. The cost of the one real discharge: `slack` is a one-instant,
  dispatcher-side bound rather than a guarantee, because nothing re-checks WIP after
  dispatch.
- **A `wip_limit` above the query cap used to manufacture slack.** `count_wip`'s
  `--limit` defaults to 100 and `wip_limit` is unbounded, so a larger limit read a
  truncated page as an under-limit count. Found by the PR's bot reviewer, not by the
  diff-only reviewer that passed it eighteen times. Anything new that derives a bound
  from a bounded query has the same shape: check the query can see far enough to answer.
- **`--project` is `linear` only.** The flag list in `do-tasks.md` said "tracker handler
  only", which included gh-issue and jira. It now names `linear`, matching what the
  handlers implement.
- **Asset calls take `$CLAUDE_PLUGIN_ROOT`.** `CONTRIBUTING.md` mandates it and most
  handlers follow it; `gh-issue-claim.md` still spells its calls repo-relative, which
  resolves only inside this plugin's own repo. That deviation is pre-existing and was
  deliberately left alone — but anything **new** must use the mandated form, and anything
  inlined into a dispatch prompt must be rewritten to it.

### The rest, unchanged and still true

**The vocabulary migration is finished.** Every gh-issue verb speaks `labels.yml`; there
is no bridge left. An old spelling (`auto-eligible`, `priority:*`) is a defect, not a
migration in progress.

**"Carrying a rung" means carrying one `labels.yml` defines** — never merely a label
whose name starts with `status:` or `auto:`. The prefix reading is the trap: a hand-typed
`status:blocked` satisfies it, so the issue reads as healthy while being in a state
nothing can act on. Anything that asks "does this issue have a rung?" must ask the
vocabulary.

**A check must also ask whether the label it looks for is PROVISIONED.** Label namespaces
are per-repo, so a rung the vocabulary defines may never have been created on the board —
and then the check's question is unanswerable, not answered "no". Two consequences:

- The **scope does not vouch for the labels.** A label scope separates loop issues from
  strangers; it says nothing about provisioning.
- **Guard by group, not by completeness.** A rung being _assignable_ still holds while
  its group has any member provisioned; only an entirely empty group voids it. Decide
  per check — a guard that is too wide silences a working row.

**The `Blocked by:` footer is an echo of a native edge, never a dependency in itself.**
Nothing may read a footer to decide blocked-ness, and nothing may write a footer for a
dependency with no edge: write the edge, then echo it. The footer was **kept** against
task 8's own acceptance criterion, because `/push-plan` and `/reoptimize-tasks` must not
disagree about what a footer means — and that argument does **not** turn on PRE-823.

**A scope this handler cannot honour, plus a write, is a refusal.** No initiative
dimension and no project dimension. `/reconcile-tasks` stops on `--project` **with
`--apply`** and continues at the default label scope without it; `/reoptimize-tasks`
stops on an `initiative` scope outright; `/do-tasks` refuses `--project` on this handler.
Continuing wider would answer a request to _narrow_ with a _wider_ run.

## Do not re-derive these — they were measured, not read

Full evidence in
[`2026-08-24-routine-claim-channel.md`](../decisions/2026-08-24-routine-claim-channel.md)
and §10b of the requirements record. **Both files are dated snapshots — read them as
what was true then**, and see the cloud-session section above for where that has been
called into question.

**Channels.** A routine's credentialed channel is the GitHub MCP connector, which has
**no dependency-edge tool**, and it can acquire the claim ref
(`mcp__github__create_branch`) but **cannot release** it. A **GitHub Actions runner** is a
third channel and is not credential-starved: measured 2026-09-02 by task 6
([PR #447](https://github.com/bestdan/workflow-skills/pull/447)), a runner has `gh` and
its ambient `GITHUB_TOKEN` PATCHes labels with `permissions: issues: write`. **Do not
read "unattended" as "cloud routine"** — a runner is unattended too. The token is
repo-scoped, so a repo whose `gh-issue.repo` points elsewhere must not run the task-6
backstop. The "a routine has no `gh`" half of this is what the docs now contradict.

**Writes.**

- A label write **replaces** the whole set and **auto-creates** unknown names. Hence
  validate-then-replace, always, before any network call.
- The dependency POST body carries **`issue_id`, a database id**, not the issue number.
  So does the removal DELETE, as its last path segment. Measured 2026-09-04 by task 8;
  the removal is idempotent.
- **GitHub refuses a directly reciprocal edge, and refuses nothing else.** `A blocked_by
  B` when `B blocked_by A` exists returns **422**; `A -> B -> C -> A` built with no
  complaint. Never read GitHub's guard as a guarantee of acyclicity, and a batch edge
  write must survive a per-edge refusal rather than aborting with earlier edges written.
- `blocked_by` is **paginated** — read it with `--paginate --slurp`. A bare read stops at
  30, and an invisible edge is a cycle that reads as absent.

**Reads**, measured 2026-09-03 by task 7
([PR #464](https://github.com/bestdan/workflow-skills/pull/464)).

- `repos/{owner}/{repo}/issues/{n}/events` carries the same `labeled` stream as
  `timeline`, without `cross-referenced` and comment entries. `events` is the cheaper one.
- **`gh issue list` orders by creation date descending, not by close date.** A `--limit`
  window over closed issues holds the most recently _created_ ones, so a long-lived issue
  closed yesterday can sit outside it. GitHub search has no `sort:closed`;
  `--search "sort:updated-desc"` is the nearest proxy and `updated` moves on a post-close
  comment.

**Provisioning**, measured 2026-09-04 by task 17
([PR #479](https://github.com/bestdan/workflow-skills/pull/479)).

- `gh-label-sync.py` is idempotent and **dry-run by default**, so
  `python3 …/gh-label-sync.py --repo <repo>` prints a board's gap for one `gh label list`.
- **There is no longer a live under-provisioned board.**
  [dotfiles#675](https://github.com/bestdan/dotfiles/issues/675) provisioned all 12
  missing labels on `bestdan/dotfiles`. **Anything testing under-provisioned behaviour now
  needs a fixture** — task 17's hermetic tests are that fixture and are the model to copy.
- **Provisioning a rung moves the row-3 noise rather than ending it.** After #675,
  `/reconcile-tasks` reports **50 of 50** closed issues again, now through a row running
  correctly. Every issue that closed before the rung existed has no `labeled` event for
  it, so those are the pre-rollout **backlog**. Answering it needs a rollout boundary —
  per-repo state this plan would then own. **Do not file this as a task-17 defect**; the
  defect (a row answering confidently from a void premise) shipped fixed in **v2.21.1**.
- **`gh-label-sync.py`'s `existing_labels()` refuses above 500 labels rather than
  truncating.** Anything reading a repo's labels should go through that helper.

Both write facts are silent when wrong. This file was wrong twice by asserting routine
behaviour from documentation. Probe it.

## Open blockers, and who owns them

- **`/auto-pilot` does not support `gh-issue`** (task 13) — **postponed 2026-09-02**,
  because `/auto-pilot` is under active development with a new harness. It stops outright
  rather than degrading. **This holds Phase 4**, task 10's pilot included.
- **The cloud-session probe is unowned** — see above. It gates the `remote_batch` default
  and, with it, whether gh-issue batch is real for anyone but this repo.
- **A crashed claim strands its issue, and nothing sweeps it.** A session that claims and
  dies before opening a PR leaves the issue assigned and on `status:3_started`; the
  candidate query excludes it on **both** counts, so no later run picks it up. This is
  identical on the ref path and the election path — a crashed **local** claim does it too
  — so it is the handler's failure mode, not batch's, but a batch multiplies the exposure
  by N. Recovery today is a human `gh issue edit`. **No task owns this**, and task 12's
  sweep as described covers refs, not board markers.
- **`sandbox-network-guard` blocks non-GET `gh api`.** Confirmed as a workaround, not a
  fix: an asset's `--apply` PATCHes fine **unsandboxed**, because the hook matches the
  `gh api` text and a python helper hides it. Friction, not a wall; every local write
  costs a sandbox escape. **Outside this repo; needs the operator.**
- **The handler still owes an MCP branch** reusing `labels.yml` for the same
  validate-then-replace rule, for any channel without `gh`. How large that gap is now
  depends on the probe above.
- **`state_reason` on the close path is unowned.** `gh-issue-state.py --done` writes
  `state: closed` and nothing else, so a completed issue and an abandoned one are
  indistinguishable afterwards. It costs more than it did: task 8's stale-versus-satisfied
  split reads `state_reason`.
- **The provisioning class is wider than the reconciler.** Task 17 guarded the three
  reconciler rows. Every other verb that asks whether an issue carries a rung inherits the
  same blind spot and is unaudited. Nothing detects that; there is no task.
- **Two label invariants have no reconciler rule.** Task 7's rule table is deliberately
  **closed** and task 17 kept it closed: at most one `prio:` / at most one `est:` (a
  duplicate stays invisible until the next write, which then refuses), and a **closed**
  issue still carrying live `status:`/`auto:` rungs (reachable with a bare
  `gh issue close`).
- **`skills/task/SKILL.md`'s flag annotations are stale.** `--all`, `-n N`, `--remote` and
  `--local` are still marked "(file path only)", which was already wrong for `linear`
  before task 16 and is now wrong for `gh-issue` too. Left alone as pre-existing.
- **`claude --remote` is a deprecated alias for `claude --cloud`.** §3, §4 and
  `repo-pr-execute.md` all still say `--remote`. Cosmetic, unowned, one sweep.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic.
- Two non-migration follow-ups live in Linear: **PRE-822** (`reopened` unhandled by the
  task-6 backstop) and **PRE-823** (does a runner's token reach the dependency endpoints).
  PRE-823 blocks nothing in this plan.

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`. Nothing has switched, and the
switch waits on the auto-pilot harness — switching before then ends unattended operation
rather than degrading it. That is a postponement, not a dead end: every handler verb works
in a foreground session today.

### Acceptance criteria still owed

Each needs a repo on the `gh-issue` handler — but that is **not** the same as needing this
repo to switch: **`bestdan/dotfiles` is already `handler: gh-issue`**. Anything testing
only **handler dispatch** can run there today against a real board. What dotfiles cannot
stand in for is a **migrated** backlog: it was never on Linear, so it carries none of the
imported issues, old-vocabulary labels or `Blocked by:` footers the migration criteria are
about.

- **Task 16's — needs the cloud-session probe first.** `/do-tasks --all` with
  `handler: gh-issue` dispatching bounded sessions cannot be exercised until a repo can
  legitimately set `remote_batch: true`. The **degrade** path is testable today on
  `bestdan/dotfiles`: `--all` with the default config must claim exactly one issue in the
  foreground and say `remote batch disabled — claiming one issue`.
- **Task 8's dispatch half — runnable NOW, and the cheapest thing here.**
  `/reoptimize-tasks` against `bestdan/dotfiles`. Confirm the **installed** plugin under
  `~/.claude/plugins/cache/workflow-skills/workflow-skills/<version>/` is v2.21.0 or later
  before reading the result — an older one exercises the old report-only prose and returns
  a green result that says nothing. Expect zero dependency findings (dotfiles has no
  edges); read it as a dispatch check, not a coverage one.
- **Task 4 — dispatch, runnable on `bestdan/dotfiles` today.** Two `/do-tasks` sessions
  against the same ready issue, confirming exactly one proceeds. Needs an issue at
  `status:2_ready` (which **is** provisioned there) and two concurrent sessions; the
  racing is the point, so a serial run proves nothing.
- **Task 8's migrated-backlog half** — `/reoptimize-tasks` against the migrated
  `workflow-skills` backlog, spot-checking three edges in the UI. Needs task 9, so it
  waits on Phase 4.
- **Task 15** — its user-run check.

None of these is a defect. Task 7's was run and **found** task 17's defect, which is that
check earning its keep: the row was sitting on a blocked-looking list when it was
runnable, and free.
