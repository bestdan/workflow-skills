# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-13, after #513 wrote the graph. The board is now complete —
125 issues, 27 edges, 15 sub-issue links — and
[#514](https://github.com/bestdan/workflow-skills/issues/514) (verify it, independently)
is next; see "#514 is next".** Read this first, then
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
Pick up https://github.com/bestdan/workflow-skills/issues/514

Read dev_docs/gh-issue-migration/HANDOFF.md first — it is NOT on main, only on the
unmerged draft PR #441. Get it with:
  git show origin/bestdan/gh-issue-migration:dev_docs/gh-issue-migration/HANDOFF.md
```

Swap the issue number as the chain advances (#514 → #515 → #516) and
the pointer keeps working, because which task is next is a fact this file carries
rather than one the prompt has to.

## Redraft this file when you finish — read this before you start

**Finishing a task includes rewriting this file for the agent who picks up the next
one.** Rewrite, not append. Land it in the same unit of work as the task — a follow-up
is how it got skipped before. The plan docs live on this branch (draft PR #441) while a
task's own code goes to a fresh branch off `main`, so "the same PR" is not literally
available; land both before you call the task done.

A patched file grows a paragraph per task, keeps every superseded sentence in place, and
becomes a changelog: a record of what was done, in the order it was done, which is the
one thing the next agent does not need. **They need what is true now and what will bite
them.** Git history and the PR record already hold the narrative, and hold it better.

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
ephemeral — paste it and let it go.

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
`commands/` links to them.

## Where things stand

**This repo has been on `gh-issue` since 2026-09-07.** Any older copy of this file that
says otherwise is wrong; if you planned around that sentence, stop and re-read here.

**Phase 3 is complete.** Tasks 4–8 and 14–17 are done. Task 12 is unclaimed and has no
task file; it gates nothing anyone is waiting on.

**The switch already happened, ahead of this plan's own sequencing** — see "The switch
happened without task 13" below. **Do not plan as though the flip is still ahead of you.**

**Phase 4 task 9 is seven issues under GitHub milestone 1** (filed 2026-09-08):
[#510](https://github.com/bestdan/workflow-skills/issues/510) (export) →
[#511](https://github.com/bestdan/workflow-skills/issues/511) (plan) →
[#512](https://github.com/bestdan/workflow-skills/issues/512) (apply) →
[#513](https://github.com/bestdan/workflow-skills/issues/513) (link) →
[#514](https://github.com/bestdan/workflow-skills/issues/514) (verify) →
[#515](https://github.com/bestdan/workflow-skills/issues/515) (Linear side) →
[#516](https://github.com/bestdan/workflow-skills/issues/516) (close out).
**The milestone's own description is the crosswalk contract** — the Linear-to-GitHub
table nothing else in this repo states. Read it there, not from a copy.

**#510 through #513 are complete.** The assets are
`commands/handlers/assets/linear-export.py` and `linear-import.py` (`--plan`, `--show`,
`--apply`, `--link`), all on `main` as of v2.54.0.

**THE MIGRATION HAS RUN AND THE GITHUB BOARD IS THE LIVE BOARD.** 125 Linear issues
landed on 2026-09-13: **117 created as #617–#733** and **8 reopened** (#284, #288, #289,
#295, #296, #297, #299, #302), each with its labels, milestone, provenance footer and
comment transcript, plus the one assignee. Then `--link` wrote the graph: **27
`blocked_by` edges, 15 sub-issue links** across two parents (#701 holds 7, #702 holds 8),
and `#number` cross-references in 33 bodies. The board went from 46 open issues to 177
(176 after #723 was completed), sectioning 33 / 83 / 56 / 0 / 2 across the five rungs.

**Every claim above was verified by reading the board, not the runs' own logs** — 125
distinct numbers all open, every planned label, milestone and assignee present, 62 of 62
transcripts posted, all 27 edges and all 15 links live. The mapping records what a run
believes; agreement between two copies of one claim is not evidence, so check the board
when you need to know. **That is also exactly why #514 exists** — see "#514 is next".

**Three files on this machine are the input to everything downstream, and none has a
remote.** All three sit in `$HOME/src/linear-export/`:

- `2026-09-13-prethink.json` — the export, 3.3 MB, sha256
  `60276d3c65fb8334a74b84ea65ba880816532137fcc5dba5a513ea3723ecc4b3`, **810 issues, 557
  archived**, 462 with comments, 110 with relations, across 40 projects. It is the only
  record of those keys once the Linear originals are cancelled.
- `2026-09-13-import-plan.json` — the plan, 451 KB, sha256
  `b080ff8585da0e10ee7ae2402858b215a55e4badbcd60323642a3a3536d4644e`. It holds the
  `blocked_by` and `parent` fields #513 reads.
- `2026-09-13-mapping.json` — what `--apply` wrote. **This is the Linear-key-to-GitHub-number
  crosswalk**, one record per key with its `number`, `url`, `action`, `resolution`,
  `milestone` and `phase`. #513 and #514 both need it, and nothing else on this machine
  holds it.

**The plan is NO LONGER regenerable, and the previous revision of this file said it was.**
That sentence was true until `--apply` ran. Regenerating the plan re-runs the
create-versus-reopen decision, and the eight reopen targets are now **OPEN** — which that
decision refuses, correctly, as "two live homes already exist". So a regenerated plan
would abort rather than reproduce this one. **Treat all three files as provenance now.**
If a plan must be rebuilt for some later purpose, that is a new argument to make
explicitly, not a `--force` away.

**Read the 810 against `PRE-835`, not against 781.** The export spans `PRE-5 … PRE-835`
with 21 numbers missing to deleted issues, and that top identifier is what shows the
pagination reached the end. **An identifier is not an index**; nothing downstream may
compute a count from a key, or treat a gap as a lost issue.

## #514 is next, and it is the first task whose job is to DISBELIEVE the others

#514 writes `linear-verify.py`: a field-by-field and edge-set check of the board against
the plan. Everything before it verified its own work, which is the weakness it exists to
cover — #512 and #513 each checked what they had just written, using the same join, the
same files and the same assumptions. #514's value is in reading the board **independently**
and disagreeing.

**The state it is checking, all of it landed and spot-verified:**

| Fact                      | Value                                                       |
| ------------------------- | ----------------------------------------------------------- |
| entries                   | **125** (117 created as #617–#733, 8 reopened)              |
| status rungs              | 32 untriaged, 54 needs_refinement, 38 ready, 1 needs_review |
| milestones                | 5, holding 18 / 16 / 5 / 1 / 1; **84 entries get none**     |
| native `blocked_by` edges | **27**, all present                                         |
| sub-issue links           | **15**, across 2 parents (#701 holds 7, #702 holds 8)       |
| entries carrying comments | 62, all posted                                              |
| assignees written         | 1 (`PRE-416` → #723, since completed)                       |
| bodies with `#n` rewrites | 33 of 125                                                   |

**Do not let #514 re-derive the join — that is the one shortcut that voids it.** The
numbers come from the mapping and the shapes from the plan, which is exactly what #512 and
#513 did; a verifier that repeats their arithmetic can only confirm their arithmetic. Read
the live issue and compare. The things worth checking are the ones no earlier pass could
have caught: a field that landed differently from what was sent, a label a human has since
changed, an edge present on the board that the plan does not contain (the reverse direction
of what #513 checked), and a body whose rewrite mangled something.

**Four things #514 will see and must not report as drift**, each verified deliberate. The
first is the one that will bite a field-by-field body comparison hardest:

- **33 of 125 live bodies no longer equal the plan's `body` field, by design.** `--link`
  rewrote migrated cross-references to `#number` after `--apply` wrote the body, so the
  plan holds the pre-link text and the board holds the post-link text. A verifier that
  diffs the two reports 33 false mismatches. Compare the plan body **as `--link` would
  rewrite it** — `rewrite_body(plan_body, numbers)` is importable and idempotent — or
  exclude the body from the equality check and verify the footer separately.

- **A reopened issue carries two provenance footers.** The eight were migrated OUT of
  GitHub in August, so each body already ended with
  `Migrated from [https://github.com/…/issues/288]`, and `--apply` appended the inbound
  `Migrated from Linear PRE-746 (…)`. It is a round trip and reads as one.
- **A reopened issue keeps unmanaged labels its GitHub original already had.** `papercut`
  on #288 is not in the plan's `carried_labels`; `gh-issue-state.py` carries forward
  everything outside the four managed namespaces, by design.
- **Bodies carry no `Blocked by:` footer.** The edges are native and #513 deliberately did
  not echo them. Whether they should be echoed is a live question — `/push-plan` writes
  that footer and `/reoptimize-tasks` reads it as an echo of an edge — but the rule is
  "write the edge, then echo it", and the edge now exists, so an echo is legal for the
  first time. Decide it; do not assume the absence is a bug.

**`--link` skips what is not imported yet rather than refusing, so a bare `--link` is
also a cheap read-only audit.** It reports `N of M plan entries in the mapping`, lists any
key with no mapping record under `NOT YET IMPORTED`, and prints the skipped edges and
sub-issue links by name — all without `--apply`, and with the existing-link reads done
either way so the preview's counts equal the real run's. The only refusal left about
mapping state is a mapping **file** that does not exist. An earlier revision refused on
any absent or not-yet-`done` key; that was wrong twice over, and both halves are worth
knowing before writing #514's verifier:

- It made the skip-and-list path this task requires unreachable dead code.
- Its stated reason — "an edge written to a number a rerun might replace" — described a
  state the apply path cannot produce. `number` is written in exactly one `record()` call,
  at phase `created`, and `record()` is an `update` that never clears it. **A recorded
  number is stable whatever the record's phase**, which is the fact #514 should rely on
  rather than re-deriving.

**A partial mapping is the cheapest way to exercise the skip paths against real tooling,
and it needs no writes.** Copy the mapping, drop a key that is both a blocker and a
parent, and run `--link` with no `--apply`: predicting the counts from the plan and
comparing is what proved the real `gh-issue-deps.py` accepts a shortened `--edge` batch.
Measured 2026-09-13 dropping `PRE-554` (blocks five) and `PRE-545` (parent of seven):
22 of 27 edges and 8 of 15 links linkable, 5 and 7 skipped by name, exit 0.

**`--link` is re-runnable, so #514 can use it as a repair.** All three of its passes are
check-then-write, so running it again writes only what is genuinely missing. It has no
progress file and needs none.

**What the runs cost, so #514 can budget.** `--apply`'s 125 entries at `--sleep 2` took
about nine minutes and hit **zero** rate limits across roughly 400 writes; `--link`'s 74
writes took about three. The secondary limit was never the binding constraint at that pace
— but it was also never reached, so do not read that as licence to drop the throttle.

**`PRE-416` landed at `status:4_needs_review` (#723) with its PR #487 already merged, and
that is faithful** — Linear still has it In Review. It is not a defect of the import and
must not be "corrected" during one.

**But `/sweep-for-complete` will NOT close it, and earlier revisions of this file said it
would.** That command is **unsupported on `gh-issue`** and refuses by design: GitHub
normally closes an issue natively from `Closes #<n>` in the PR body, so the sweep has
nothing to add. Here the native path could not fire either — #723 did not exist when #487
merged on 2026-09-08, and #487's body cites the Linear key, not a GitHub number. **Every
issue the import carried is in this position**, so nothing auto-closes any of them; #723 is
simply the only one whose work is already done.

Close it with the state helper, not a bare `gh issue close`:

```
python3 commands/handlers/assets/gh-issue-state.py --repo bestdan/workflow-skills \
    --issue 723 --labels "prio:3,est:2" --done --apply
```

`--done` closes the issue and asserts no `status:`/`auto:` rung, which is the point: a bare
`gh issue close` leaves live rungs on a closed issue, and that is one of the two label
invariants with **no reconciler rule** (below). `prio:`/`est:` are not rungs and are carried
through deliberately. v2.51.0 ([#613](https://github.com/bestdan/workflow-skills/issues/613))
strips rungs at merge time, so this is pre-feature residue rather than a recurring class.

**A reopened issue carries TWO provenance footers, and that is faithful.** #288 and the
other seven were migrated OUT of GitHub in August, so each body already ended with
`Migrated from [https://github.com/…/issues/288]`; `--apply` appended the inbound
`Migrated from Linear PRE-746 (…)`. It is a round trip and it reads as one. Do not
"tidy" it — and note that the body-marker search `--apply` uses to recover a lost create
matches only the **inbound** spelling, so the two do not collide.

**`papercut` survives on a reopened issue without being in the plan's `carried_labels`.**
`gh-issue-state.py` carries forward every label outside the four managed namespaces, so a
label the GitHub original already had is preserved. A diff of "plan labels versus live
labels" that flags this as drift is reading the wrong contract.

## What will bite you

### `gh --milestone` takes a TITLE, not a number

**Measured 2026-09-13 against gh 2.98.0**, and it cost the first live `--apply` run its
first create: `gh issue create --milestone 8` exits 1 with
`could not add to milestone '8': '8' not found`. Both `gh issue create -m` and
`gh issue edit -m` are documented "by name" and look the title up.

`commands/push-plan.md` §5.3 said to pass the resolved **number**, "so a title with
shell-unsafe characters or a later rename can't break it". That reason does not survive
contact with the flag, and it does not apply to a caller passing argv entries anyway.
**The sentence is amended where it lives** (PR #616), with the measurement beside it. The
number is still resolved and still needed — for reuse-before-create and for the record —
it is just not what `gh` accepts at the create.

### `op` is dead over SSH, and there is a second Linear credential that is not

**Measured 2026-09-13** while running #510's export from an SSH session on the Mac mini.
This matters for [#515](https://github.com/bestdan/workflow-skills/issues/515), the
Linear-side write — **#513 and #514 need no Linear credential at all**, since they read
the plan and mapping files and write only GitHub.

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
> `~/src/linear-token.py` is the throwaway that mints it. They were kept in case
> #511/#512 wanted the same token; **neither did** — the remaining Linear read is #515's.
> The exposure is unchanged: the token is full-account, and any coder backend with
> filesystem access can read it. Deleting both is the cheap option, since re-minting takes
> seconds and the client credentials persist. **Needs the operator.**

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
  label write (`mcp__github__issue_write`, full-set replacement, same as REST).
- **Nothing is blocked by network or proxy scoping.** `claude plugin marketplace add`
  plus `claude plugin install`, run **inside** the session, worked.

Two things this deliberately does **not** settle, and the record says so: whether the
`gh` 403 is proxy policy or a missing Claude GitHub App connection on this account
(untried), and whether a cloud-environment **setup script** installing the plugin at
session start would close the gap (plausible, unmeasured, and a per-environment setting
rather than something a repo can commit).

Consequences for the plan:

- **`gh-issue.remote_batch` stays `false`; the capability matrix stays `opt`.** Task
  16's acceptance criterion said flip it to `yes`. Do not — the premise that would have
  justified flipping is now measured false rather than merely unprobed, which is a
  stronger reason for the same default. **This deviation is settled, not pending.**
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
  discharged because it is **provably** redundant. **Dependency readiness is re-run in the
  session** before claiming: a blocker can reopen between selection and claim, and no
  arithmetic makes that recheck redundant. The distinction is the point — "the dispatcher
  just asked" is not a reason to skip a gate; only provable redundancy is.
- **Anything that derives a bound from a bounded query must check the query can see far
  enough to answer.** A `wip_limit` above `count_wip`'s `--limit` (default 100) used to
  read a truncated page as an under-limit count and manufacture slack.
- **`--project` is `linear` only.**
- **Asset calls take `$CLAUDE_PLUGIN_ROOT`.** `CONTRIBUTING.md` mandates it and most
  handlers follow it; `gh-issue-claim.md` still spells its calls repo-relative, which
  resolves only inside this plugin's own repo. That deviation is pre-existing and was
  deliberately left alone — but anything **new** must use the mandated form. Note the
  probe found `$CLAUDE_PLUGIN_ROOT` **empty** in a cloud session, so the mandated form
  does not rescue a dispatched session by itself.

### The rest, unchanged and still true

**The vocabulary migration is finished.** Every gh-issue verb speaks `labels.yml`; there
is no bridge left. An old spelling (`auto-eligible`, `priority:*`) is a defect, not a
migration in progress. The one exception was the import: `auto-eligible` and
`human-approval-requested` are **Linear** labels the crosswalk reads and consumes, and
they exist on this repo only because the 2026-09-07 papercut transfer created them. With
the import done, nothing reads them any more — **retiring them is #516's business**, and
they are on the board today.

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
  its group has any member provisioned; only an entirely empty group voids it.

**`papercut` is provisioned on this repo; `blocked` is NOT.** Confirmed again 2026-09-13
by `--apply`'s own pre-flight, which reads the board's labels and refuses before any write
on a carried label that is absent. The plan needed only `papercut`, so nothing tripped —
but the four managed namespaces being complete (task 17) says nothing about the carried
ones.

**The `Blocked by:` footer is an echo of a native edge, never a dependency in itself.**
Nothing may read a footer to decide blocked-ness, and nothing may write a footer for a
dependency with no edge: write the edge, then echo it. **The rule's precondition is now
satisfied and the decision is open.** The 27 edges exist, so an echo is legal for the first
time; #513 wrote the edges and did **not** echo them, because the task did not ask. The
imported bodies therefore carry no `Blocked by:` line at all, and carry
`Blockers not migrated:` for blockers outside the selection — deliberately not spelled
`Blocked by:`, because that spelling is what `/reoptimize-tasks` reads as a dependency
claim, and those blockers have no edge and never can. **Do not read the absence as a bug**;
`/push-plan` does write the footer, so the two verbs are inconsistent today, and closing
that is a real decision for #514 or #516 rather than an oversight to patch.

**A scope this handler cannot honour, plus a write, is a refusal.** No initiative
dimension and no project dimension. `/reconcile-tasks` stops on `--project` **with
`--apply`** and continues at the default label scope without it; `/reoptimize-tasks`
stops on an `initiative` scope outright; `/do-tasks` refuses `--project` on this handler.

**`duplicate` relations keep no direction, and that was decided rather than overlooked.**
A reviewer argued they should, since `linear-relations.py` populates `duplicate_of` from
outgoing relations only. Declined on three grounds — the crosswalk puts
`related`/`similar`/`duplicate` on one footer line, so a split amends the contract and
belongs in the milestone description first; the footer is prose rather than a machine-read
edge; and the export contains **zero** duplicate relations. **If a later export ever
carries one, this is the decision to revisit**, starting with the milestone table.

Also worth knowing rather than rediscovering: the export contains **zero**
`<issue id=… href=…>` mentions, so the rewrite `linear-import.py` performs for them is
insurance, not load-bearing. And `PRE-746` is the only oversized entry (`est:8`); it
already carries its own `/break-down-task` note in the body.

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

**Writes.** All four bear directly on #513.

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
- **The sub-issue endpoint works on this repo**, measured 2026-09-13 by #513 — the call
  this plan had carried as unmeasured since task 2, and the reason the `Parent: #n` footer
  fallback was never needed. `POST repos/{owner}/{repo}/issues/{n}/sub_issues` takes
  `{"sub_issue_id": <database id>}`, the same database-id trap the dependency POST has.
  Two things about what comes back. **The response is the PARENT issue**, not the child and
  not the link, so it cannot confirm which child attached — only a re-read can. And **a
  repeat POST is 422, not idempotent**, with a message that conflates two different
  conditions: `Issue may not contain duplicate sub-issues and Sub issue may only have one
  parent`. So a 422 must never be read as "already linked" — a child parented somewhere
  else fails identically, and treating that as success records a link that does not exist.
  The parent's own `sub_issues` list is the only thing that separates them.
- **`gh issue create -m` / `gh issue edit -m` take the milestone by TITLE**, measured
  2026-09-13 against gh 2.98.0 after the number cost `--apply` its first create:
  `--milestone 8` exits 1 with `could not add to milestone '8': '8' not found`.
  `commands/push-plan.md` §5.3 said to pass the resolved number and is amended.

**Reads**, measured 2026-09-03 by task 7
([PR #464](https://github.com/bestdan/workflow-skills/pull/464)).

- `repos/{owner}/{repo}/issues/{n}/events` carries the same `labeled` stream as
  `timeline`, without `cross-referenced` and comment entries. `events` is the cheaper one.
- **`gh issue list` orders by creation date descending, not by close date.** A `--limit`
  window over closed issues holds the most recently _created_ ones, so a long-lived issue
  closed yesterday can sit outside it. GitHub search has no `sort:closed`;
  `--search "sort:updated-desc"` is the nearest proxy and `updated` moves on a post-close
  comment.
- **Issue search is eventually consistent**, measured 2026-09-13 while designing
  `--apply`'s lost-create recovery. A just-created issue may not be findable by a body
  search yet, which is exactly where a per-issue "did I already create this?" check would
  be least reliable — so that search runs once per run, before the first write, where the
  writes it is looking for are from an earlier run and long since indexed.

**Provisioning**, measured 2026-09-04 by task 17
([PR #479](https://github.com/bestdan/workflow-skills/pull/479)).

- `gh-label-sync.py` is idempotent and **dry-run by default**, so
  `python3 …/gh-label-sync.py --repo <repo>` prints a board's gap for one `gh label list`.
- **There is no longer a live under-provisioned board.**
  [dotfiles#675](https://github.com/bestdan/dotfiles/issues/675) provisioned all 12
  missing labels on `bestdan/dotfiles`. **Anything testing under-provisioned behaviour now
  needs a fixture** — task 17's hermetic tests are that fixture and are the model to copy.
- **Provisioning a rung moves the row-3 noise rather than ending it.** Every issue that
  closed before the rung existed has no `labeled` event for it, so those are the
  pre-rollout **backlog**. Answering it needs a rollout boundary — per-repo state this plan
  would then own. **Do not file this as a task-17 defect**; the defect (a row answering
  confidently from a void premise) shipped fixed in **v2.21.1**.
- **`gh-label-sync.py`'s `existing_labels()` refuses above 500 labels rather than
  truncating.** Anything reading a repo's labels should go through that helper — `--apply`
  does, by rebinding that module's `run_gh` to its own seam rather than copying the cap.

Both write facts are silent when wrong. This file has been wrong three times by
asserting unattended GitHub behaviour from documentation — twice about routines, once
about cloud sessions. Probe it.

## Open blockers, and who owns them

- **`/auto-pilot` does not support `gh-issue`** (task 13) — **postponed 2026-09-02,
  reaffirmed by the owner 2026-09-12**, because `/auto-pilot` is under active development
  with a new harness. It stops outright rather than degrading. **The cost is no longer
  hypothetical**: the config flipped on 09-07 without it, so this repo is out of unattended
  auto-pilot today. It still gates a like-for-like **task 10**; it does not gate the rest
  of task 9, which needs no auto-pilot at all.
- **The handler owes an MCP branch for its label writes**, reusing `labels.yml` for the
  same validate-then-replace rule. The probe promoted this from "for any channel without
  `gh`" to **the** prerequisite for a working dispatched session. **No task owns it.**
- **A crashed claim strands its issue, and nothing sweeps it.** A session that claims and
  dies before opening a PR leaves the issue assigned and on `status:3_started`; the
  candidate query excludes it on **both** counts, so no later run picks it up. Identical on
  the ref path and the election path, so it is the handler's failure mode rather than
  batch's, but a batch multiplies the exposure by N. Recovery today is a human
  `gh issue edit`. **No task owns this**, and task 12's sweep as described covers refs,
  not board markers. **The exposure just grew**: the board went from 46 open issues to
  ~170.
- **`sandbox-network-guard` blocks non-GET `gh api`.** Confirmed as a workaround, not a
  fix: every local write costs a sandbox escape. #512's batch was a few hundred such
  writes under one escape per invocation, which was fine — but it is friction every task
  here pays. Outside this repo; needs the operator.
- **`state_reason` on the close path is unowned.** `gh-issue-state.py --done` writes
  `state: closed` and nothing else, so a completed issue and an abandoned one are
  indistinguishable afterwards. It costs more than it did: task 8's stale-versus-satisfied
  split reads `state_reason`. **#516 will want it** — closing 125 Linear issues as
  "migrated" is exactly the distinction `state_reason` carries.
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
  cloud session cannot be created non-interactively.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic.
- Two non-migration follow-ups live in Linear: **PRE-822** (`reopened` unhandled by the
  task-6 backstop) and **PRE-823** (does a runner's token reach the dependency endpoints).
  Both are now **also** GitHub issues, since the import carried them. **PRE-823 is worth
  reading before #513** — it asks exactly the question #513's edge writes will answer in
  passing.

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
([#504](https://github.com/bestdan/workflow-skills/issues/504)). **That cost just got
bigger** — the board now holds ~170 open issues rather than 46.

**What this costs, right now.** Task 13 is still postponed — confirmed by the owner
2026-09-12, and verified unstarted: `skills/auto-pilot/SKILL.md:135` still stops outright
on any handler but `linear`/`repo-pr`. So **this repo is currently outside unattended
auto-pilot**, and has been since 09-07. Every handler verb still works in a foreground
session.

**What it costs task 10.** The pilot evaluation asks "keep, extend, or revert", against
`finplan` as a Linear control. The pilot repo has lost unattended operation and the control
has not, so that comparison is no longer like-for-like. Either restore parity (task 13)
before running the gate, or run it attended and **state the asymmetry in the verdict**.
Do not let the gate silently score the handler down for a gap that is auto-pilot's.

## The two boards are no longer symmetric

The newest workflow-skills issue in Linear is PRE-823, created **2026-09-03**, so the
import was not racing a moving source. **As of 2026-09-13 the GitHub board is the live
one and Linear holds 125 issues that have been copied out of it but not yet closed.** That
is a deliberate intermediate state, not drift: closing the Linear side is
[#515](https://github.com/bestdan/workflow-skills/issues/515), and it is the only step
left that needs a Linear credential. Until it runs, **both boards show the same work as
open** — anyone reading Linear for status will read it wrong.

### Acceptance criteria still owed

Two repos are on the `gh-issue` handler — `bestdan/dotfiles` and `bestdan/workflow-skills`
— so anything testing **handler dispatch** can run in either. **Everything that needed a
migrated backlog is now runnable**, which nothing was before 2026-09-13: this repo's board
carries the imported issues, the consumed Linear labels, the footers AND, since #513, the
dependency graph. No criterion here is waiting on migration work any more.

- **Task 8's migrated-backlog half — runnable NOW, and nothing blocks it.**
  `/reoptimize-tasks` against the migrated `workflow-skills` backlog, spot-checking three
  edges in the UI. The 27 edges and 15 sub-issue links are live; #701 and #702 are the two
  richest subjects, and the PRE-554/PRE-555 chain (#692 → #691 → #686) is the clearest
  dependency to eyeball.
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
- **Task 15** — its user-run check.

None of these is a defect. Task 7's was run and **found** task 17's defect, which is that
check earning its keep.
