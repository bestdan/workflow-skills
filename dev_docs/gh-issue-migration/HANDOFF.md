# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-13, after #511 merged. #512 (`linear-import.py --apply`) is next, and
the import plan it executes already exists on this machine — see "#512 is next".** Read
this first, then
[`gh_migration_plan.md`](gh_migration_plan.md) (the epic) and
[`2026-08-24-requirements-and-evidence.md`](2026-08-24-requirements-and-evidence.md)
(the measured record).

## The launcher prompt is these three lines

Everything a fresh session needs is in this file, which is the point: the pointer
that starts it stays small enough to paste and throw away. Do **not** write the
briefing out as a second document — three `.dev_docs/task_N_handoff.md` files had
accumulated by task 6, each an untracked copy of plan content telling a new session
to begin already-merged work, none with any git history to recover from. If you find
yourself wanting a longer pointer, the missing content belongs **here** instead.

```
Pick up https://github.com/bestdan/workflow-skills/issues/512

Read dev_docs/gh-issue-migration/HANDOFF.md first — it is NOT on main, only on the
unmerged draft PR #441. Get it with:
  git show origin/bestdan/gh-issue-migration:dev_docs/gh-issue-migration/HANDOFF.md
```

Swap the issue number as the chain advances (#512 → #513 → #514 → #515 → #516) and
the pointer keeps working, because which task is next is a fact this file carries
rather than one the prompt has to.

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

**Decision records are the exception — they go on `main`**, beside
`dev_docs/decisions/2026-08-24-routine-claim-channel.md`, because runtime prose under
`commands/` links to them. The 2026-09-05 record below shipped on `main` in PR #490, not
here; this branch's links to it resolve once that merges.

## Where things stand

**This repo has been on `gh-issue` since 2026-09-07.** Any older copy of this file that
says otherwise is wrong; if you planned around that sentence, stop and re-read here.

**Phase 3 is complete.** Tasks 4–8 and 14–17 are done. Task 12 is unclaimed and has no
task file; it gates nothing anyone is waiting on.

**The switch already happened, ahead of this plan's own sequencing.**
`dev_docs/tasks/.task-config.yml` has read `handler: gh-issue` since
[PR #503](https://github.com/bestdan/workflow-skills/pull/503) merged 2026-09-07. The
plan said to sequence task 13 first; that did not happen, and the consequence is live —
see "The switch happened without task 13" below. **Do not plan as though the flip is
still ahead of you.**

**Phase 4 task 9 is broken down into seven issues under GitHub milestone 1** (filed
2026-09-08) — [#510](https://github.com/bestdan/workflow-skills/issues/510)
(export) → [#511](https://github.com/bestdan/workflow-skills/issues/511) (plan) →
[#512](https://github.com/bestdan/workflow-skills/issues/512) (apply) →
[#513](https://github.com/bestdan/workflow-skills/issues/513) (link) →
[#514](https://github.com/bestdan/workflow-skills/issues/514) (verify) →
[#515](https://github.com/bestdan/workflow-skills/issues/515) (Linear side) →
[#516](https://github.com/bestdan/workflow-skills/issues/516) (close out).
**The milestone's own description is the crosswalk contract** — the Linear-to-GitHub
table nothing else in this repo states. Read it there, not from a copy.

**#510 and #511 are complete**, both merged to `main`:
`commands/handlers/assets/linear-export.py` as `d57d51c`
([PR #590](https://github.com/bestdan/workflow-skills/pull/590)), and
`commands/handlers/assets/linear-import.py` — `--plan` and `--show`, plus the test pair
— as `32f8523` in v2.49.0
([PR #597](https://github.com/bestdan/workflow-skills/pull/597)).

**Two files on this machine are the input to everything downstream, and neither has a
remote.** Both sit in `$HOME/src/linear-export/`:

- `2026-09-13-prethink.json` — the export, 3.3 MB, sha256
  `60276d3c65fb8334a74b84ea65ba880816532137fcc5dba5a513ea3723ecc4b3`, **810 issues, 557
  archived**, 462 with comments, 110 with relations, across 40 projects. It is the only
  record of those keys once the Linear originals are cancelled.
- `2026-09-13-import-plan.json` — the import plan #512 executes, 451 KB, sha256
  `b080ff8585da0e10ee7ae2402858b215a55e4badbcd60323642a3a3536d4644e`.

**Treat the two differently: the plan is regenerable, the export is not.** Rebuilding
the plan from the export takes seconds and the invocation is in `linear-import.py`'s
header, so a lost or stale plan costs nothing. A lost export is the provenance hole this
whole phase exists to avoid.

**Read an entry back rather than grepping the JSON.** `--show` prints one plan entry
beside its Linear original, which is how the crosswalk is checked by a person — the
plan is 125 entries and the question asked of it is never "does it parse":

```
python3 commands/handlers/assets/linear-import.py --show PRE-746 \
    --plan-file "$HOME/src/linear-export/2026-09-13-import-plan.json" \
    --export "$HOME/src/linear-export/2026-09-13-prethink.json"
```

Two things it deliberately shows: relation **direction** (`-> blocks X` is this issue
blocking X, `<- blocks X` is X blocking it, and only the second becomes a `blocked_by`
edge), and the footer sliced at the **last** `---`, because a Linear description may
carry its own rule.

**Read the 810 against `PRE-835`, not against 781.** The export spans `PRE-5 … PRE-835`
with 21 numbers missing to deleted issues, and that top identifier is what shows the
pagination reached the end — it matches the highest key observed independently on
2026-09-12. **An identifier is not an index**; nothing downstream may compute a count
from a key, or treat a gap as a lost issue.

## #512 is next, and the plan file settles most of its arguments

`--apply` creates or reopens the issues, writes labels and milestones, and posts the
consolidated comment. It reads `2026-09-13-import-plan.json` and **must not re-derive
the selection or the crosswalk from the export** — the enumeration and the mapping are
the plan's, argued in `linear-import.py`'s header, and re-deriving them is how the
"project name is a lying proxy" bug gets reintroduced.

**What the plan holds, measured 2026-09-13 by the real run.** These are the numbers
`--apply` must reproduce, and a mismatch is a defect rather than drift:

| Fact                      | Value                                                       |
| ------------------------- | ----------------------------------------------------------- |
| entries                   | **125** (117 `create`, 8 `reopen`)                          |
| status rungs              | 32 untriaged, 54 needs_refinement, 38 ready, 1 needs_review |
| milestones to create      | 5, holding 18 / 16 / 5 / 1 / 1; **84 entries get none**     |
| native `blocked_by` edges | 27                                                          |
| sub-issue links           | 15                                                          |
| entries carrying comments | 62                                                          |
| assignees to write        | 1 (`PRE-416`, the only `started` issue, `@me`)              |

**Nothing falls off the selection edge.** Zero blockers and zero parents point outside the
selected set, so no edge or sub-issue link is dropped, and no Linear label lands without a
crosswalk row. Those three categories are printed even when empty, deliberately — a
category that only appears when non-empty cannot be read as reassurance.

**`status:3_started` appears zero times, and that is correct.** Exactly one live issue is
`started` and it sits in `In Review`, so it maps to `status:4_needs_review`. Related trap:
55 live issues carry `human-approval-requested` but only **54** get
`status:1_needs_refinement`, because the crosswalk reads that label only on `backlog`. The
counts are not meant to reconcile.

**The eight reopen targets were verified CLOSED on 2026-09-13**: #284, #288, #289, #295,
#296, #297, #299, #302. That is a live-state check, not a fact about the file — if anyone
reopens one by hand, regenerating the plan refuses (an open original means two live homes
already exist), and `--apply` must not paper over it.

**Three refusals guard the reopen decision, and `--apply` inherits rather than repeats
them.** Re-implementing any of them is how they drift apart:

- An original that is **OPEN** means two live homes already exist.
- An original that **cannot be read** is unverified, which is not the same as absent — a
  404 is not downgraded to `create`, because a permissions problem looks identical.
- **Two Linear issues claiming one number** refuses. It was the only one of the three
  that fails silently: both entries are legal alone, and the loss shows up as a Linear
  key with no GitHub home after the import has run.

A marker's **repository identity comes from its url, never its title.** `GitHub #288
(migrated)` carries no repo, so the attachment path checks `attachment.url` against
`--repo` exactly as the description-footer path does; an attachment naming another repo
leaves the issue to be created fresh, and one disagreeing with its own url refuses.

**Expect `PRE-416` to arrive needing a sweep, not a fix.** It lands
`status:4_needs_review` with its PR #487 already merged, which is a faithful migration —
Linear still has it In Review. `/sweep-for-complete` is the verb that notices the merged
PR and closes it. Do not "correct" it during the import.

**`papercut` is provisioned on this repo; `blocked` is not.** No live issue carries
`blocked`, so nothing needs it today — but `--apply` must not assume a carried label
exists. The four managed namespaces are all provisioned (task 17).

**Two deviations from #511's task file, both to avoid a silent failure:**

1. **The migrated-from footer is matched anywhere in the description, and through
   Linear's markdown link wrapping** (`Migrated from [https://…/issues/288](<…>)`,
   followed by a sizing-flag block). The issue spelled it as a description that _ends
   with_ a bare URL; that form matches **none** of the eight real candidates. A missed
   candidate is a duplicate GitHub issue, not a loud failure. The attachment marker
   caught all eight independently, so the footer detector is the belt to that braces.
2. **A blocker outside the selection is footnoted `Blockers not migrated:`, never
   `Blocked by:`.** The issue said to note it "in the footer instead", and `Blocked by:`
   is the spelling `/reoptimize-tasks` reads as a dependency claim — see the footer rule
   below. These blockers have no edge and never can, because the issue they name is not
   being imported.

**`duplicate` relations keep no direction, and that was decided rather than overlooked.**
A reviewer argued they should: an outgoing duplicate means this issue duplicates the
target, an inverse one means the target duplicates this canonical issue, and
`linear-relations.py` populates its `duplicate_of` field from outgoing relations only for
exactly that reason. It was declined on three grounds — the crosswalk puts
`related`/`similar`/`duplicate` on one footer line, so a split amends the contract and
belongs in the milestone description first; the footer is prose rather than a machine-read
edge, unlike that consumed field; and the export contains **zero** duplicate relations, so
it is handling for a path that cannot fire. **If a later export ever carries one, this is
the decision to revisit**, starting with the milestone table.

Also worth knowing rather than rediscovering: the export contains **zero**
`<issue id=… href=…>` mentions, so the rewrite `linear-import.py` performs for them is
insurance, not load-bearing. And `PRE-746` is the only oversized entry (`est:8`); it
already carries its own `/break-down-task` note in the body, added during the 2026-08-08
migration out of GitHub.

## What will bite you

### `op` is dead over SSH, and there is a second Linear credential that is not

**Measured 2026-09-13** while running #510's export from an SSH session on the Mac mini.
This matters for [#515](https://github.com/bestdan/workflow-skills/issues/515), the
Linear-side write — **#512, #513 and #514 need no Linear credential at all**, since they
read the plan file and write only GitHub.

**`op` cannot work here, and the error says which kind of cannot.** The key in
`dev_docs/tasks/.task-config.local.yml` is an `op://Private/…` ref, and that read returns
**`authorization timeout`** — 1Password's biometric prompt has no desktop to surface on.
Do not read this as `promptError`, which means no session yet and is fixed by
`op signin`; **`authorization timeout` means the session lives on another machine and
`op signin` will never help.** `op account list` here reports no accounts configured at
all, so the CLI is desktop-integrated only.

**`~/.config/linear/client-credentials` is the way through.** It holds an OAuth app's
client id and secret, and Linear's `client_credentials` grant against
`https://api.linear.app/oauth/token` returns a working token at scope `read`. Contrary
to the obvious worry, that app-actor token is **not** scope-limited in practice — it read
all 810 issues including the 557 archived ones. Two things to know before using it: it is
a **`Bearer`** token, not a personal `lin_api_…` key (`linear-export.py:auth_header()`
handles both, and nothing else in `commands/handlers/assets/` does), and the token is a
plaintext credential wherever you park it, so delete it after.

> **Two credentials are still live on this machine, and their fate is still undecided.**
> `~/.linear-key` holds the OAuth token in plaintext (mode 600), and
> `~/src/linear-token.py` is the throwaway that mints it. They were kept in case #511/#512
> wanted the same token; **#511 did not, and #512 will not** — the remaining Linear read is
> #515's. The exposure is unchanged: the token is full-account, and any coder backend with
> filesystem access can read it. Deleting both is now the cheap option, since re-minting
> takes seconds and the client credentials persist. **Needs the operator.**

**The trap that outlives both:** `scripts/check.sh:148` excludes `scripts/test-*-live.sh`
from the gate outright, and each live harness exits **0** with a warning when no key
resolves — by design, since a Linear personal key is a full-account bearer token that
must never reach CI secrets. So a whole task can be written, tested, and merged green
while the thing it exists to do has never happened once. **A green gate is not evidence
for any criterion whose discharge is a live run.** Say "it skipped" when it skipped; the
warning is the only signal, and it scrolls past.

Practical note: an approval-based resolver is invalidated between resolves, so a harness
that probes and then runs the script raises one dialog per resolve. And a
worktree-isolated session refuses `$(cat …)` in a command, so bridging a key from a file
means the `api_key` rung of `.task-config.local.yml`, not a command substitution —
remove it again afterwards.

### A cloud session gives a gh-issue batch neither the plugin nor a usable `gh`

**Measured 2026-09-05**, full evidence in
[`2026-09-05-cloud-session-plugin-and-proxy.md`](../decisions/2026-09-05-cloud-session-plugin-and-proxy.md)
(on `main` via PR #490). Probed against `bestdan/dotfiles`, deliberately not this repo.

- **A committed `.claude/settings.json` does NOT install the plugin.** The declaration
  (`extraKnownMarketplaces` + `enabledPlugins`) was present and correct on the cloned
  HEAD; `claude plugin list` reported none installed and `$CLAUDE_PLUGIN_ROOT` was
  empty. The documentation says otherwise. The documentation is wrong here.
  **The two halves failed differently, which narrows the fix**: after a manual
  `claude plugin marketplace add`, the next session start showed a **`Scope: project`**
  entry, which can only come from the committed file. So `enabledPlugins` is honoured
  once a marketplace exists; it is `extraKnownMarketplaces` that a cold session did not
  act on. Observed sequence, not a diagnosed cause — no second cold session was run.
- **`gh` is installed and uncredentialed.** `GH_TOKEN=proxy-injected`, `gh auth status`
  fails, and every `gh api` call 403s — the **read** as well as the write, so it is not
  a method restriction.
- **The GitHub MCP connector is still the credentialed channel**, and it **can** do the
  label write (`mcp__github__issue_write`, full-set replacement, same as REST). Same
  channel the 2026-08-24 routine probe found.
- **Nothing is blocked by network or proxy scoping.** `claude plugin marketplace add`
  plus `claude plugin install`, run **inside** the session, worked — cloning the
  unattached public marketplace repo over plain HTTPS.

Two things this deliberately does **not** settle, and the record says so: whether the
`gh` 403 is proxy policy or a missing Claude GitHub App connection on this account
(untried), and whether a cloud-environment **setup script** installing the plugin at
session start would close the gap (plausible, unmeasured, and a per-environment setting
rather than something a repo can commit).

Consequences for the plan:

- **`gh-issue.remote_batch` stays `false`; the capability matrix stays `opt`.** Task
  16's acceptance criterion said flip it to `yes`. Do not — the premise that would have
  justified flipping is now measured false rather than merely unprobed, which is a
  stronger reason for the same default. **This deviation is now settled, not pending.**
- **The handler owes an MCP branch for its label writes.** It is the only route to a
  working dispatched session, since `gh` does not work there. It still owes the same
  `labels.yml` validate-then-replace rule.
- **`claim-lock.md` no longer claims a dispatched session "usually can" acquire the
  ref.** PR #490 amended that sentence; the instruction (take the election) is unchanged.

### What task 16 shipped that you will trip over

- **A dispatched session claims on the comment election, never the ref lock**, and it
  runs **two `git ls-remote` probes** the election itself does not contain — one before
  the board write, one with the post-sleep re-list. Those probes are the only way a batch
  session and a local ref-lock session detect each other; the elections cannot see each
  other's primitive. `claim-lock.md` carries its own entry condition for this.
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
- **Anything that derives a bound from a bounded query must check the query can see far
  enough to answer.** A `wip_limit` above `count_wip`'s `--limit` (default 100) used to
  read a truncated page as an under-limit count and manufacture slack. Found by the PR's
  bot reviewer, not by the diff-only reviewer that passed it eighteen times.
- **`--project` is `linear` only.**
- **Asset calls take `$CLAUDE_PLUGIN_ROOT`.** `CONTRIBUTING.md` mandates it and most
  handlers follow it; `gh-issue-claim.md` still spells its calls repo-relative, which
  resolves only inside this plugin's own repo. That deviation is pre-existing and was
  deliberately left alone — but anything **new** must use the mandated form, and anything
  inlined into a dispatch prompt must be rewritten to it. Note the probe found
  `$CLAUDE_PLUGIN_ROOT` **empty** in a cloud session, so the mandated form does not
  rescue a dispatched session by itself.

### The rest, unchanged and still true

**The vocabulary migration is finished.** Every gh-issue verb speaks `labels.yml`; there
is no bridge left. An old spelling (`auto-eligible`, `priority:*`) is a defect, not a
migration in progress. The one exception is the import: `auto-eligible` and
`human-approval-requested` are **Linear** labels the crosswalk reads and consumes, and
they exist on this repo only because the 2026-09-07 papercut transfer created them.

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
[`2026-08-24-routine-claim-channel.md`](../decisions/2026-08-24-routine-claim-channel.md),
[`2026-09-05-cloud-session-plugin-and-proxy.md`](../decisions/2026-09-05-cloud-session-plugin-and-proxy.md)
and §10b of the requirements record. **All are dated snapshots — read them as what was
true then.**

**Channels.** A routine's credentialed channel is the GitHub MCP connector, which has
**no dependency-edge tool**, and it can acquire the claim ref
(`mcp__github__create_branch`) but **cannot release** it. A cloud session's credentialed
channel is the same connector, for a different reason — `gh` is present there but its
token does not work. A **GitHub Actions runner** is a third channel and is the one that
is not credential-starved: measured 2026-09-02 by task 6
([PR #447](https://github.com/bestdan/workflow-skills/pull/447)), a runner has `gh` and
its ambient `GITHUB_TOKEN` PATCHes labels with `permissions: issues: write`. **Do not
read "unattended" as "cloud routine"** — a runner is unattended too. The token is
repo-scoped, so a repo whose `gh-issue.repo` points elsewhere must not run the task-6
backstop.

**Writes.** All four bear directly on #512 and #513.

- A label write **replaces** the whole set and **auto-creates** unknown names. Hence
  validate-then-replace, always, before any network call. True on the REST path and on
  the MCP connector alike.
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

Both write facts are silent when wrong. This file has been wrong three times by
asserting unattended GitHub behaviour from documentation — twice about routines, once
about cloud sessions. Probe it.

## Open blockers, and who owns them

- **`/auto-pilot` does not support `gh-issue`** (task 13) — **postponed 2026-09-02,
  reaffirmed by the owner 2026-09-12**, because `/auto-pilot` is under active development
  with a new harness. It stops outright rather than degrading. **The cost is no longer
  hypothetical**: the config flipped on 09-07 without it, so this repo is out of unattended
  auto-pilot today. It still gates a like-for-like **task 10**; it does not gate task 9,
  which is import work needing no auto-pilot at all.
- **The handler owes an MCP branch for its label writes**, reusing `labels.yml` for the
  same validate-then-replace rule. The probe promoted this from "for any channel without
  `gh`" to **the** prerequisite for a working dispatched session. **No task owns it.**
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
  costs a sandbox escape. **#512 is a few hundred such writes — budget for the escape
  rather than discovering it mid-batch.** Outside this repo; needs the operator.
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
  `repo-pr-execute.md` all still say `--remote`. Cosmetic, unowned, one sweep. The probe
  measured a further wrinkle worth folding in: **`--cloud` refuses `--print`**, so a
  cloud session cannot be created non-interactively; `claude -p "<msg>" --cloud <id>`
  only queues into one that already exists.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic.
- Two non-migration follow-ups live in Linear: **PRE-822** (`reopened` unhandled by the
  task-6 backstop) and **PRE-823** (does a runner's token reach the dependency endpoints).
  PRE-823 blocks nothing in this plan.

## The switch happened without task 13

`dev_docs/tasks/.task-config.yml` reads `handler: gh-issue`, `labels: []`, and has since
[PR #503](https://github.com/bestdan/workflow-skills/pull/503) merged **2026-09-07**. It
was not done to this plan's schedule: 28 papercut issues were transferring in from
`bestdan/dotfiles` and would have been invisible to `/list-tasks`, `/promote-tasks` and
`/do-tasks` while the config still read `linear`. Seven labels the transfer needed were
created directly on the repo, outside `labels.yml`'s four managed namespaces.

**`labels: []` is deliberate and must stay.** `gh-issue.labels` is an AND filter on
`/list-tasks`, not a stamp on new issues; every issue in this repo is a task issue, so any
marker would hide part of the board. The cost is asymmetric and known: `/reconcile-tasks`
has `--all`, `/archive-tasks` has no equivalent and will refuse
([#504](https://github.com/bestdan/workflow-skills/issues/504)).

**What this costs, right now.** Task 13 is still postponed — confirmed by the owner
2026-09-12, and verified unstarted: `skills/auto-pilot/SKILL.md:135` still stops outright
on any handler but `linear`/`repo-pr`. So **this repo is currently outside unattended
auto-pilot**, and has been since 09-07. That was a predicted consequence, not a surprise;
it is recorded here because it is now a live operating condition rather than a future
risk, and because it is invisible from the config file alone. Every handler verb still
works in a foreground session.

**What it costs task 10.** The pilot evaluation asks "keep, extend, or revert", against
`finplan` as a Linear control. The pilot repo has lost unattended operation and the control
has not, so that comparison is no longer like-for-like. Either restore parity (task 13)
before running the gate, or run it attended and **state the asymmetry in the verdict**.
Do not let the gate silently score the handler down for a gap that is auto-pilot's.

## The two boards are parked, not drifting

The newest workflow-skills issue in Linear is PRE-823, created **2026-09-03** — four days
before the switch. Nothing has been filed there since, so the import is not racing a
moving source. For scale on the other side, the GitHub board held 46 open issues at the
2026-09-08 count; #512 adds 117 to it.

### Acceptance criteria still owed

Each needs a repo on the `gh-issue` handler. **Two qualify** — `bestdan/dotfiles` since
before this plan, and `bestdan/workflow-skills` itself since 2026-09-07 — so anything
testing only **handler dispatch** can run in either against a real board. What neither can
stand in for is a **migrated** backlog: dotfiles was never on Linear, and this repo's
Linear issues have not been imported yet, so no board anywhere carries the imported
issues, old-vocabulary labels or `Blocked by:` footers the migration criteria are about.
That half waits on #512.

- **Task 16's dispatch half is unrunnable, not merely blocked.** `/do-tasks --all`
  dispatching bounded sessions needs a repo that can legitimately set
  `remote_batch: true`, and the probe says none can today. Retire the criterion or
  rewrite it against whatever closes the plugin gap. The **degrade** path is testable now
  on `bestdan/dotfiles`: `--all` with the default config must claim exactly one issue in
  the foreground and say `remote batch disabled — claiming one issue`.
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
  `workflow-skills` backlog, spot-checking three edges in the UI. Needs #512 and #513.
- **Task 15** — its user-run check.

None of these is a defect. Task 7's was run and **found** task 17's defect, which is that
check earning its keep: the row was sitting on a blocked-looking list when it was
runnable, and free.
