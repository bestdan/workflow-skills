# Handoff — migrating the task loop from Linear to GitHub Issues

**Redrafted 2026-09-15. Phase 4 is CLOSED: task 9 delivered the migration and task 10
decided the pilot — the verdict is KEEP.** `workflow-skills` stays on `gh-issue`,
`finplan` stays on Linear, and nothing is scheduled to migrate next.

**Next is task 11, the last task in the plan, and it is now fully unblocked.** Its sixth
section — which Linear commands retire — was the one part waiting on task 10, and task 10
has answered it. Task 11 also owns the decision this branch has been drifting on for
weeks: **merge PR #441, close it, or graduate a curated subset.**

Read this first, then [`gh_migration_plan.md`](gh_migration_plan.md) (the epic) and
[`2026-08-24-requirements-and-evidence.md`](2026-08-24-requirements-and-evidence.md)
(the measured record).

**Three things task 10's verdict opened rather than closed** — all of them task 11's, none
of them decided:

1. **The migration assets are to be REMOVED** (owner, 2026-09-15). `linear-import.py`
   (~2,500 lines), `linear-verify.py`, `linear-successor.py`, `linear-export.py` and
   `_linear_auth.py` were kept only against the possibility that the gate said _extend_.
   It said _keep_, no future use is known, and **git history is the recovery path** — so
   the delete commit must name them explicitly. **Two checks first**, in task 11: the
   Linear _handler_ and its four commands stay (`finplan` is still on Linear), and each
   asset needs a grep to confirm nothing in the day-to-day path calls it. Note
   `linear-verify.py` is what produces the expected-failure contract below, so **run it
   one last time before deleting it** if you want that check ever again.
2. **Task 13 now has nothing standing behind it.** It used to gate task 10's fairness;
   task 10 closed without it. This repo has been outside unattended auto-pilot since
   2026-09-07 and **nothing is now scheduled to end that.**
3. **The `Blocked by:` footer's justification is GONE, but NOTHING follows from that
   about removing it.** Task 10 never revisited it; the question got answered anyway. A
   cloud **session** and a scheduled **routine** both read `blocked_by` over plain
   `curl`, so "an unattended agent cannot read the edge" is false in both environments.
   Four runtime claims were corrected in
   [PR #741](https://github.com/bestdan/workflow-skills/pull/741) and
   [#751](https://github.com/bestdan/workflow-skills/pull/751), both **merged 2026-09-17**.

   > **Correcting earlier drafts of this file, which said removal was "owned by #500".**
   > **#500 is CLOSED/COMPLETED and never owned it.** It was already delivered by
   > [#444](https://github.com/bestdan/workflow-skills/pull/444) (merged 2026-09-02,
   > v2.17.0) — five days before #500 was filed — and #500's closing note records that
   > the footer was **kept alongside the native edge on purpose**, as that issue's own
   > option 1. So `/push-plan` has written native edges since v2.17.0, the footer beside
   > them is a settled choice, and **no open issue owns removing it.** There was never
   > an open design fork here; an earlier draft claimed one. Anyone who wants the footer
   > gone is reopening a settled decision and should say so. See "The footer" below.

## The board today, and the one thing that is actually blocking

Measured 2026-09-17, on `main` at v2.58.1:

|                           |                             |
| ------------------------- | --------------------------- |
| open issues               | **177**                     |
| `status:2_ready`          | **74** (78 `auto:eligible`) |
| `status:3_started`        | **0** — nothing in flight   |
| stale `task/*` claim refs | **0** — the board is clean  |
| unattended capability     | **none**                    |

**74 ready issues, nothing running, and no automation that can touch them.** That is the
state to hold in your head. `/auto-pilot` still stops at `skills/auto-pilot/SKILL.md:135`
on any handler but `linear`/`repo-pr`, and `gh-issue.remote_batch` is `false`, so the only
working path is foreground `/do-tasks`, one issue at a time, with a human present.

**Do not mistake the probe work for progress on this.** Three probes in September
corrected six false claims and confirmed `claim-lock.md`, which was worth doing — but they
answered "what can a routine reach?", and the bottleneck is "what can drive a task?".
Nothing measured so far moves it.

**The three routes, and they are not equal:**

1. **Task 13 — `/auto-pilot` gh-issue support.** The only route that ends with the board
   working itself. Owner-deferred twice (09-02, 09-12) pending a new harness — but that
   harness is moving (five commits to `skills/auto-pilot/` + `spawn-orchestrator.sh` since
   09-01, latest #579), so **the reason for the deferral may have expired. Ask before
   assuming it still holds.**
2. **`remote_batch`.** Needs the plugin installed in the cloud environment — measured
   working 2026-09-07 via an environment **setup script**, which is per-environment
   config the owner sets and a repo cannot commit. Then the handler's `gh api` calls
   still need a `gh` that a routine does not have. Two gaps; bigger than task 13.
3. **Work the board in foreground.** No new capability, but 74 ready issues is a lot of
   available work and `/do-tasks` functions today.

**The prerequisite nobody owns.** A crashed claim strands its issue permanently (see
"Open blockers"). Today that is harmless — 0 started, 0 stale refs — and it stops being
harmless the moment anything unattended runs, scaling with the batch. Treat the sweep as a
**precondition** for routes 1 and 2, not a follow-up.

**Task 11 is bookkeeping, not capability.** It closes the plan — docs, asset removal, the
#441 decision — and gets nothing running. Fine to do; it should not jump the queue.

## The launcher prompt is these three lines

Everything a fresh session needs is in this file, which is the point: the pointer
that starts it stays small enough to paste and throw away. Do **not** write the
briefing out as a second document — three `.dev_docs/task_N_handoff.md` files had
accumulated by task 6, each an untracked copy of plan content telling a new session
to begin already-merged work, none with any git history to recover from. If you find
yourself wanting a longer pointer, the missing content belongs **here** instead.

```
Tasks 9 and 10 are done. Task 11 is next and is the last one.

Read dev_docs/gh-issue-migration/HANDOFF.md first — it is NOT on main, only on the
unmerged draft PR #441. Get it with:
  git show origin/bestdan/gh-issue-migration:dev_docs/gh-issue-migration/HANDOFF.md
```

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

This plan used to live under `dev_docs/tasks/gh_migration_plan/`, which `.gitignore:35`
excludes — so it existed in exactly one git worktree, uncommitted, with no history and no
remote. It is now committed under `dev_docs/gh-issue-migration/`, per the `.gitignore`
comment block's own advice: "Prefer graduating durable wisdom to a top-level
`dev_docs/<name>.md` (never ignored) over keeping it here."

It is still on **draft PR #441**, not on `main`. Do the migration's own work in a fresh
worktree off `main`; edit these plan docs on this branch.

**Decision records are the exception — they go on `main`**, beside
`dev_docs/decisions/2026-08-24-routine-claim-channel.md`, because runtime prose under
`commands/` links to them.

### The old copy is still on disk, untracked — do not read it and do not act on it

`dev_docs/tasks/gh_migration_plan/` **still exists** in the main checkout: 8 entries,
0 tracked, gitignored, including its own stale `HANDOFF.md` and `gh_migration_plan.md`.
Audited 2026-09-13. It is the pre-move original, superseded by the 18 tracked files here,
and it is exactly the hazard this plan has already been bitten by twice — an untracked
copy of plan content that a fresh session reads and acts on, with no git history to tell
it how old the content is. **The file you are reading is the live one; that one is not.**

Task 11 already names it for deletion and the instruction is still correct, so this is not
a new task. It is recorded here because it will mislead someone before task 11 runs.
Deleting it is unrecoverable (untracked, no history), so it wants an explicit go from the
owner rather than a tidy-up in passing.

### What goes and what stays at close-out

Four sets. **Task 10 decided two of them; task 11 owns the remaining two.**

| Files                                                            | Fate                                                                                                                      |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `dev_docs/tasks/gh_migration_plan/` (untracked scaffolding)      | **Delete** — task 11 says so, the path is still accurate, needs the owner's go                                            |
| `$HOME/src/linear-export/` (export, plan, mapping)               | **Keep forever.** #516 step 3 records their paths in `linear-common.md`. **No remote** — unowned exposure                 |
| `dev_docs/gh-issue-migration/` (these 18 tracked files, PR #441) | **UNDECIDED** — merge, close, or graduate a subset. See "Closing out PR #441"                                             |
| `linear-import.py`, `linear-verify.py`, `linear-successor.py`    | **REMOVE** (owner, 2026-09-15) — keep-not-extend spent their justification; git history is the recovery path. See task 11 |

**#516's own acceptance criterion names a folder that never existed.** It asks that
`dev_docs/tasks/linear_import_plan/` "no longer exists locally" — verified absent
2026-09-13, and absent because nothing ever created it. The criterion therefore passes
without anything happening, which reads as work done. The folder it should name is the
scaffolding above. Fix the criterion or ignore it knowingly; do not let it stand as
evidence.

## Where things stand

**This repo has been on `gh-issue` since 2026-09-07**, and as of 2026-09-15 that is a
decision rather than a pilot. Any older copy of this file that frames it as provisional,
or that says a revert is being held open, is wrong.

**Phases 1–4 are complete.** Tasks 4–10 and 14–17 are done. **Task 11 is the only one
left.** Task 12 is unclaimed, has no task file, and gates nothing anyone is waiting on.

**The migration ran on 2026-09-13 and both boards are settled.** 125 Linear issues landed
on GitHub — **117 created as #617–#733** and **8 reopened** (#284, #288, #289, #295, #296,
#297, #299, #302) — each with its labels, milestone, provenance footer and comment
transcript. `--link` wrote **27 `blocked_by` edges** and **15 sub-issue links** across two
parents (#701 holds 7, #702 holds 8) and rewrote `#number` cross-references into 33 bodies.
The board went from 46 open issues to 177 (176 after #723 was completed). On the Linear
side, all **123** writable originals now point at their GitHub successors and are
**archived** — the cap relief this whole exercise was for.

**The milestone 1 description is the crosswalk contract** — the Linear-to-GitHub table
nothing else in this repo states. Read it there, not from a copy. The assets are
`commands/handlers/assets/linear-export.py`, `linear-import.py` (`--plan`, `--show`,
`--apply`, `--link`), `linear-verify.py`, `linear-successor.py`, and the shared
`_linear_auth.py`. All of #510–#516 are closed and on `main`.

### `linear-verify.py` is the independent check, and it has a standing expected failure

Run it whenever you need to know rather than believe:

```
python3 commands/handlers/assets/linear-verify.py \
    --export  $HOME/src/linear-export/2026-09-13-prethink.json \
    --plan-file $HOME/src/linear-export/2026-09-13-import-plan.json \
    --mapping $HOME/src/linear-export/2026-09-13-mapping.json \
    --repo bestdan/workflow-skills
```

Read-only (every `gh` call is a GET, so it runs sandboxed), about three minutes for ~375
reads, exits 0 clean / 1 on a finding / 2 when it cannot read an input, with a `--json`
mode.

> **The pass condition is `1 check(s) failed: fields`, 20 issues, `body` only.** That is
> **not** a regression. The plan file holds each body as `--apply` wrote it; the board now
> holds that body plus the `Blocked by:` line #516 applied. The plan is provenance and
> must not be edited to make a check pass, so the divergence is permanent. The 20 are
> #679, #686–#691, #693–#698, #707–#710, #719, #720, #729. **A run reporting more than
> those 20, or a wrong field other than `body`, is a real finding.** The failing set and
> the edited set were compared programmatically and are identical.

**Three of its behaviours will meet you before its findings do**, and the first two are
refusals that fire before any network read:

- **It refuses a `--repo` that disagrees with the repo the plan and the mapping both
  record.** Issue numbers are not unique across repos, so a mapping pointed at the wrong
  board verifies real issues that happen to share the numbers and can report a clean pass.
  `--repo` is required and is deliberately not read out of the files — taking the board
  from the writer's own record would make the run an echo rather than a verification.
- **It refuses an export that does not carry every plan key**, naming them. Every
  expectation about edges, sub-issue links and transcripts is derived from the export, so
  a key it does not describe contributes nothing expected while fields and labels go on
  comparing — the run then comes back green off the wrong file.
- **A `blocked_by` edge or sub-issue link whose FAR end was never imported is a NOTE, not
  a failure.** `/reoptimize-tasks` can legitimately add an edge between an imported issue
  and a native one, and the export has no vocabulary for such a pair. Both ends mapped is
  still compared and still fails.

Three further deviations from #514's task file, all deliberate, all still governing what
the verifier reports:

1. **"its GitHub issue is open" is not checked as written.** #723 is legitimately closed,
   so a strict open check would make the "exits 0 against the real repo" criterion
   unsatisfiable. Closed **without** a rung is a NOTE (work completed since the import);
   closed **with** a rung is a FAILURE (the invariant break). Collapsing the two would
   hide the defect behind the benign case.
2. **Carried labels are compared as a subset, not an equality.** `gh-issue-state.py`
   carries forward every label outside the four managed namespaces, so all eight reopened
   issues keep a `papercut` label absent from the plan's `carried_labels` by design. A
   missing carried label fails; an extra one is a note on a `reopen` and a failure on a
   `create`. The managed set is still a strict equality.
3. **The edge and sub-issue comparisons are not against "every pair on the board."** The
   far end decides the tier: both ends mapped stays comparable and can fail, one end
   outside becomes a note.

### The three provenance files, and why they are now irreplaceable

**All three sit in `$HOME/src/linear-export/` and none has a remote.**

- `2026-09-13-prethink.json` — the export, 3.3 MB, sha256
  `60276d3c65fb8334a74b84ea65ba880816532137fcc5dba5a513ea3723ecc4b3`, **810 issues, 557
  archived**, 462 with comments, 110 with relations, across 40 projects.
- `2026-09-13-import-plan.json` — the plan, 451 KB, sha256
  `b080ff8585da0e10ee7ae2402858b215a55e4badbcd60323642a3a3536d4644e`. Holds the
  `blocked_by` and `parent` fields `--link` reads.
- `2026-09-13-mapping.json` — what `--apply` wrote. **The Linear-key-to-GitHub-number
  crosswalk**, one record per key with its `number`, `url`, `action`, `resolution`,
  `milestone` and `phase`. Nothing else on this machine holds it.

**The plan is NO LONGER regenerable**, and the originals are now archived as well as
cancelled, so the export is the only record of those keys. Regenerating the plan re-runs
the create-versus-reopen decision, and the eight reopen targets are now **OPEN** — which
that decision correctly refuses as "two live homes already exist". A regenerated plan
aborts rather than reproducing this one. **Treat all three as provenance.** Rebuilding one
is a fresh argument to make explicitly, not a `--force` away.

**Read the 810 against `PRE-835`, not against 781.** The export spans `PRE-5 … PRE-835`
with 21 numbers missing to deleted issues, and that top identifier is what shows the
pagination reached the end. **An identifier is not an index** — nothing downstream may
compute a count from a key, or treat a gap as a lost issue.

### Export-shape facts that outlive the migration

- **The two relation directions agree exactly.** 77 `blocks` pairs from `relations`, 77
  from `inverseRelations`, symmetric difference **zero**. `--plan` reads
  `inverseRelations` alone and is right to; `linear-verify.py` reads both and unions them,
  because a verifier reading the same half could only ever agree with the writer.
- **`children` is NOT interchangeable with `parent`.** `parent` yields 77 parent/child
  pairs; `children` yields 16, every one of them also in the first set. `children` is a
  strict subset and anything reading it alone silently loses most of the hierarchy.
- **The export contains zero `duplicate` relations and zero `<issue id=… href=…>`
  mentions.** The rewrite `linear-import.py` performs for the latter is insurance, not
  load-bearing. `duplicate` relations deliberately keep no direction — if a later export
  ever carries one, that is the decision to revisit, starting with the milestone table.

### Nothing auto-closes an imported issue

`/sweep-for-complete` is **unsupported on `gh-issue`** and refuses by design, because
GitHub normally closes an issue natively from `Closes #<n>` in the PR body. Here the
native path cannot fire either: an imported issue did not exist when its PR merged, and
those PR bodies cite the Linear key rather than a GitHub number. **Every issue the import
carried is in this position** — #723 was simply the only one whose work was already done,
and it now reads `CLOSED`/`COMPLETED` with `prio:3`, `est:2` and no rung, which is the
invariant `--done` asserts.

**A bare `gh issue close` is the wrong tool** for the rest when their time comes: it
leaves live rungs on a closed issue, one of the two label invariants with **no reconciler
rule**. Use `gh-issue-state.py --done`.

### The footer

**The `Blocked by:` echo is written — 27 edges over 20 issues**, the footer being an
aggregate line (#686 carries five blockers on one line, #679 three). `/reoptimize-tasks`
reported all 27 imported edges as `edge_only` and the footers were applied.

**The argument that decided it has since been measured FALSE.** It ran: the GitHub MCP
connector exposes no dependency-edge tool, and a cloud routine has no `gh`, so an
unattended agent could not read `blocked_by` in any form while it _could_ read issue
bodies. Both halves of that premise are true and the conclusion does not follow — the
connector and `gh` were never the only channels.

**Measured 2026-09-15 and 2026-09-16:** a cloud session and a scheduled routine each read
`blocked_by` over plain `curl`, `HTTP 200` with correct data, carrying nothing but the
literal `proxy-injected` placeholder — the egress proxy substitutes a credential. Four
runtime claims were corrected in
[PR #741](https://github.com/bestdan/workflow-skills/pull/741), merged 2026-09-17; the
evidence is in
[`2026-09-05-cloud-session-plugin-and-proxy.md`](../decisions/2026-09-05-cloud-session-plugin-and-proxy.md)
→ "2026-09-16: the same read, from a routine".

> **What survives for the footer, and what does not.** The unattended-reader argument is
> dead — do not re-derive it. What is left is that the footer is visible in the body where
> the dependency panel is easy to miss, and that a blocker held back by `--ready-only` has
> no issue to link to and can only be recorded as prose. Those are human-legibility
> reasons, and they are weaker than the one they replace.
>
> **The 27 footers on the board stay**, and so does the one `/push-plan` writes on every
> new plan. They cost nothing, and rewriting provenance to chase a decision is what this
> plan has repeatedly declined to do. **What does NOT follow is a change to `/push-plan`.**
> It has written the native edge _and_ the footer since v2.17.0 (#444), deliberately —
> see the correction at the top of this file. The measurement below removes one argument
> for the footer; it does not create a task.
>
> Note that imported bodies carry `Blockers not migrated:` for blockers outside the
> selection — deliberately not spelled `Blocked by:`, because those blockers have no edge
> and never can.

**Writes are a different story, and that asymmetry is the durable fact.** The same routine
was refused on `POST` and `DELETE` to `/git/refs` — `403 Write access to this GitHub API
path is not permitted through this proxy`, word for word what 2026-08-24 measured. So
reads are credentialed and writes are not, and `claim-lock.md`'s "a routine cannot
release" stands. Re-measured deliberately rather than inherited, precisely because the
read half of that same record had just fallen.

### Two import-selection facts worth not rediscovering

- **A reopened issue carries TWO provenance footers, and that is faithful.** The eight were
  migrated OUT of GitHub in August, so each body already ended with
  `Migrated from [https://github.com/…/issues/288]`, and `--apply` appended the inbound
  `Migrated from Linear PRE-746 (…)`. It is a round trip and reads as one. Do not "tidy" it
  — and note the body-marker search `--apply` uses to recover a lost create matches only the
  **inbound** spelling, so the two do not collide.
- **Two archived issues reached the import set: PRE-503 and PRE-504**, archived since
  2026-08-01, now #714 and #713. Linear serves an archived issue to `issue(id:)` and then
  refuses **every** mutation against it — `commentCreate` answers `Entity not found: Issue`,
  naming neither the issue nor the reason. **That is a finding about the selector, not the
  script**: #511's `backlog`/`unstarted`/`started` filter reads state type, which an
  archived issue keeps. Filed as **#737**. If that selector is re-run or reused it will do
  the same thing again.

### What the runs cost

`--apply`'s 125 entries at `--sleep 2` took about nine minutes and hit **zero** rate limits
across roughly 400 writes; `--link`'s 74 writes took about three minutes; `linear-successor.py`
wrote three mutations each against 123 issues with 0 failures. The secondary limit was
never the binding constraint at that pace — but it was never reached either, so do not
read that as licence to drop the throttle.

**`--link` is re-runnable and doubles as a cheap read-only audit.** All three passes are
check-then-write, so a bare `--link` with no `--apply` reports what is missing without
writing, and a real rerun writes only what is genuinely absent. **A recorded number is
stable whatever the record's phase** — `number` is written in exactly one `record()` call,
at phase `created`, by an `update` that never clears it — which is why both `--link` and
`linear-verify.py` join on the number and treat the phase as a separate question.

### Two defects that only surfaced by running it

- **One bad issue ended the whole pass.** `gql()` answers a GraphQL error with `sys.exit`,
  as every sibling's does, and nothing caught it — so the first apply died 19 issues in and
  took the remaining 105 with it, while the docstring two lines up claimed a failed write
  does not abort the others. Now caught per write. **Check the siblings before trusting
  this pattern elsewhere**: the catch is per call site, not in `gql()`.
- **The `Bearer` framing was missing.** `linear-successor.py` copied the bare
  `"Authorization": key` header from the six older assets, which is right only for a
  personal `lin_api_…` key. The rule now lives in `_linear_auth.py`, imported by
  `linear-export.py` and `linear-successor.py`; **the other five still hardcode the bare
  form** and will fail against an OAuth token.

**The per-write idempotence earned its keep unplanned.** The aborted first run resumed with
no special handling — the guards simply found the 106 outstanding. Design for the crash you
expect and you get the crash you did not.

### `_body_refs` cannot tell an issue from a PR

`/reoptimize-tasks`' dimension 2 produced nothing, and the reason matters before you trust
that field. All 279 prose candidates came back `weak`/`related` — zero blocking strength —
and **151 of them point at numbers that are not issues in this graph** (`#1`, `#2`,
`#3580`). `_body_refs.py` reads any `#<digits>` as an issue reference, but GitHub issues and
PRs share one number space, so a prose `#138` is as likely to be a PR. A ~54% false-positive
rate on this repo, harmless only because nothing acts on a weak candidate. Filed as **#738**.

## Closing out PR #441 — the decision task 11 owns

**State as of 2026-09-16.** The branch is **current with `main`** (merged, 0 behind, gate
passes), clean-merging, and carries **18 tracked plan files**. PR #441 is still a draft
titled "DO NOT MERGE — docs(gh-issue): migration plan, tracking only".

**Nothing blocks this any more.** Task 11 declared `is_blocked_by: gh_migration_task_10`,
and task 10 is done — so the whole of task 11 is available, including the sixth section
that was waiting on the verdict. The blocker record stays in the frontmatter as history;
it is satisfied, not live.

### The open decision

**Merge #441, or close it and leave the record on the branch.** Both are defensible; drift
is not, and drift is what has been happening.

- **Merge it.** The 18 files reach `main` permanently. Costs: the repo's own `AGENTS.md`
  calls plan folders ephemeral in-flight state and says durable wisdom graduates to a
  top-level `dev_docs/<name>.md` — so this puts scaffolding on `main` against the
  progressive-disclosure rule the repo is built on.
- **Close it.** No scaffolding on `main`. Costs: the history survives only as long as
  nobody deletes `origin/bestdan/gh-issue-migration`, and an abandoned branch is exactly
  what a later sweep removes. Closing the PR does **not** delete the branch — only a
  separate delete does.
- **Graduate a curated subset — consider this first.** Merge
  `dev_docs/gh_issue_task_loop.md` (task 11's own distillation) **and**
  `2026-08-24-requirements-and-evidence.md` — 43K of measured record that is durable by any
  reading and is the one file here nothing else can reconstruct — then close the PR without
  merging the per-task scaffolding. That buys permanence for what is durable and keeps the
  in-flight material off `main`, which is what the repo's rules actually ask for.

### What changed under this plan since the sections below were written

Anything you read below predates these unless it says otherwise:

- **2026-09-17 — the routine channel was re-measured and half of it was wrong.** A cloud
  session and a scheduled routine both **read** `blocked_by` over plain `curl`; both are
  **refused** on `POST`/`DELETE` to `/git/refs`. So reads are credentialed and writes are
  not. Four runtime claims corrected and `claim-lock.md` re-confirmed in
  [PR #741](https://github.com/bestdan/workflow-skills/pull/741), **merged**. The
  `Blocked by:` footer's justification is gone; the footers already on the board stay.
  **The `$GH_TOKEN` in those environments is the literal `proxy-injected` placeholder** —
  the proxy substitutes a credential, so the capability belongs to the proxy path and
  does not travel.

- **Task 10 is done — the verdict is KEEP**, decided 2026-09-15 on day 0 of its own clock
  rather than run to term. Task 9 is done; #510–#516 are all closed and on `main`.
- The **123 Linear originals are archived**, not merely cancelled. The revert path is gone
  and that was the point, not a side effect.
- The free-plan cap was **misdocumented for months** (it counts every non-archived issue,
  not `unstarted`+`started`); corrected in #739, and `linear-common.md` now owns the figure
  as a dated observation because Linear exposes no field to re-derive it.
- Three findings from this work are open and unfiled against any task here: **#737**
  (archived issues reached the import), **#738** (`_body_refs` can't tell an issue from a
  PR), **#740** (`active_issue_quota`'s domain hardcodes the cap).

## What will bite you

### Keep this branch merged with `main`, or read `commands/` from `main`

**This bit you before, on 2026-09-15: the branch was 155 commits behind `main`, and its
stale `commands/handlers/gh-issue-reoptimize.md` said GitHub Issues have _no native
dependency edge_** — the opposite of `main`'s, and of a board that had carried 27 real
edges since #513. It nearly drove #516's whole analysis, and the helper that file calls
(`gh-issue-deps.py`) did not exist here at all.

**Resolved 2026-09-16 by merging `main` in; the branch is current and the gate passes.**
The trap is only dormant, though — it returns the moment `main` moves on. Two ways to
stay safe, either is enough:

- merge `main` into this branch before you trust anything under `commands/` here, or
- read the plan from this branch and every `commands/`, `skills/` or `scripts/` file from
  `main`.

The plan files are the only thing this branch is authoritative about.

### `op` is dead over SSH, and there is a second Linear credential that is not

**Measured 2026-09-13** while running #510's export from an SSH session on the Mac mini,
and confirmed again on 2026-09-15 for #515's writes. No remaining step needs it.

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
all 810 issues including the 557 archived ones.

**The open question — whether that grant can WRITE — is answered: it can.** Measured
2026-09-15 at scope `read,write`: 123 issues took a `commentCreate`, an
`attachmentCreate` and an `issueUpdate` each, 0 failures. Two things that follow:

- **The comments are authored by the APP, not by you.** A `client_credentials` token
  carries an `app` actor. For a migration footer that is arguably better provenance, but
  it is not reversible per comment, so decide before a bulk write rather than after.
- It is a **`Bearer`** token, not a personal `lin_api_…` key. That framing now lives in
  `_linear_auth.py` (`linear-export.py` and `linear-successor.py` import it; **the other
  five assets still hardcode the bare header**). The token is a plaintext credential
  wherever you park it, so delete it after.

> **Two credentials are still live on this machine, and their fate is still undecided.**
> `~/.linear-key` holds the OAuth token in plaintext (mode 600), and
> `~/src/linear-token.py` is the throwaway that mints it. They were kept in case
> #511/#512 wanted the same token; **neither did, and neither did #513 or #514** — the
> remaining Linear read is #515's. The exposure is unchanged: the token is full-account,
> and any coder backend with filesystem access can read it. Deleting both is the cheap
> option, since re-minting takes seconds and the client credentials persist. **Needs the
> operator.**

**The trap that outlives both:** `scripts/check.sh:148` excludes `scripts/test-*-live.sh`
from the gate outright, and each live harness exits **0** with a warning when no key
resolves — by design, since a Linear personal key is a full-account bearer token that
must never reach CI secrets. So a whole task can be written, tested, and merged green
while the thing it exists to do has never happened once. **A green gate is not evidence
for any criterion whose discharge is a live run.** Say "it skipped" when it skipped; the
warning is the only signal, and it scrolls past.

**The same blind spot has a cheaper form, and #514 shipped with it briefly: a second
output path that nothing ever executes.** `linear-verify.py`'s `--json` mode passed a
green gate and a clean live run while never having been invoked once, in tests or by
hand — the rendered path was the only one exercised, and `--json` carries different
shapes (integer pairs, nested dicts) that a serialisation error would have caught only at
use. It works, now that it has been run and pinned by a test. **#515 had the same shape
and the rule held**: its `--json` summary and its `--cancel` gate are each a path the
default invocation never touches, and both were exercised live before the task closed.
Run every flag once before calling a task done.

Practical note: an approval-based resolver is invalidated between resolves, so a harness
that probes and then runs the script raises one dialog per resolve. And a
worktree-isolated session refuses `$(cat …)` in a command, so bridging a key from a file
means the `api_key` rung of `.task-config.local.yml`, not a command substitution —
remove it again afterwards.

### `gh --milestone` takes a TITLE, not a number

**Measured 2026-09-13 against gh 2.98.0**, and it cost the first live `--apply` run its
first create: `gh issue create --milestone 8` exits 1 with
`could not add to milestone '8': '8' not found`. Both `gh issue create -m` and
`gh issue edit -m` are documented "by name" and look the title up.

`commands/push-plan.md` **§5.4 step 4** said to pass the resolved **number**, "so a title
with shell-unsafe characters or a later rename can't break it". That reason does not
survive contact with the flag, and it does not apply to a caller passing argv entries
anyway. **The sentence is amended where it lives** (PR #616), with the measurement beside
it. (Earlier drafts of this file cited §5.3, which is "Order the tasks (topological)" and
says nothing about milestones — §5.2 resolves the number, §5.4 step 4 passes the title.) The
number is still resolved and still needed — for reuse-before-create and for the record —
it is just not what `gh` accepts at the create.

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
- **The handler owes an MCP branch for its label writes — but it is now one option, not
  the only route.** Measured 2026-09-17 (#751): a routine `PATCH`ed an issue's labels
  over plain `curl` and got **`HTTP 200`**. The proxy's write block is **path-scoped** —
  the ref path is refused, the issues path is not. Whichever route is built still owes
  the same `labels.yml` validate-then-replace rule. Two caveats the record carries: only
  a **no-op** PATCH was observed (a mutating one is expected to behave the same,
  expected not measured), and the handler's own writes go through `gh-issue-state.py`
  calling `gh api`, while `gh` is absent from a routine outright.
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
vocabulary. `linear-verify.py`'s cardinality check is the worked example, and
`test_a_hand_typed_rung_is_not_a_rung` is what fails if anyone rewrites it as a prefix
test.

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

**Channels.** ~~A routine's credentialed channel is the GitHub MCP connector~~ —
**amended 2026-09-16: raw HTTPS is a credentialed channel too, for READS.** A routine
reads the GitHub API over plain `curl` carrying the `proxy-injected` placeholder, because
the egress proxy substitutes a credential; a cloud session does the same. So the
connector is the credentialed channel for **writes**, not for everything. The connector
still has **no dependency-edge tool**, and it can acquire the claim ref
(`mcp__github__create_branch`) but **cannot release** it — re-measured 2026-09-16 on the
raw path as well (`POST`/`DELETE` on `/git/refs` → `403 … not permitted through this
proxy`), so that limit holds on every channel. A cloud session's credentialed
channel is the same connector, for a different reason — `gh` is present there but its
token does not work. A **GitHub Actions runner** is a third channel and is the one that
is not credential-starved: measured 2026-09-02 by task 6
([PR #447](https://github.com/bestdan/workflow-skills/pull/447)), a runner has `gh` and
its ambient `GITHUB_TOKEN` PATCHes labels with `permissions: issues: write`. **Do not
read "unattended" as "cloud routine"** — a runner is unattended too. The token is
repo-scoped, so a repo whose `gh-issue.repo` points elsewhere must not run the task-6
backstop.

**Writes.**

- A label write **replaces** the whole set and **auto-creates** unknown names. Hence
  validate-then-replace, always, before any network call. True on the REST path and on
  the MCP connector alike.
- The dependency POST body carries **`issue_id`, a database id**, not the issue number.
  So does the removal DELETE, as its last path segment. Measured 2026-09-04 by task 8;
  the removal is idempotent. `gh-issue-deps.py --remove-edge` / `--edge` is the
  round-trip, exercised deliberately on the live board by #514 (#691 blocked_by #692,
  removed and restored).
- **GitHub refuses a directly reciprocal edge, and refuses nothing else.** `A blocked_by
  B` when `B blocked_by A` exists returns **422**; `A -> B -> C -> A` built with no
  complaint. Never read GitHub's guard as a guarantee of acyclicity, and a batch edge
  write must survive a per-edge refusal rather than aborting with earlier edges written.
- `blocked_by` is **paginated** — read it with `--paginate --slurp`. A bare read stops at
  30, and an invisible edge is a cycle that reads as absent. The same trap bites a
  _verifier_ the other way round: an edge past page one would be reported as missing when
  it is present, which is the most expensive kind of wrong answer.
- **The sub-issue endpoint works on this repo**, measured 2026-09-13 by #513 — the call
  this plan had carried as unmeasured since task 2, and the reason the `Parent: #n` footer
  fallback was never needed, by `--link` or by `linear-verify.py`.
  `POST repos/{owner}/{repo}/issues/{n}/sub_issues` takes `{"sub_issue_id": <database
  id>}`, the same database-id trap the dependency POST has. Two things about what comes
  back. **The response is the PARENT issue**, not the child and not the link, so it cannot
  confirm which child attached — only a re-read can. And **a repeat POST is 422, not
  idempotent**, with a message that conflates two different conditions: `Issue may not
  contain duplicate sub-issues and Sub issue may only have one parent`. So a 422 must never
  be read as "already linked" — a child parented somewhere else fails identically, and
  treating that as success records a link that does not exist. The parent's own
  `sub_issues` list is the only thing that separates them.
- **`gh issue create -m` / `gh issue edit -m` take the milestone by TITLE**, measured
  2026-09-13 against gh 2.98.0 after the number cost `--apply` its first create:
  `--milestone 8` exits 1 with `could not add to milestone '8': '8' not found`.
  `commands/push-plan.md` §5.4 step 4 said to pass the resolved number and is amended.

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
- **Reading the whole imported board costs ~375 GETs and about three minutes**, measured
  2026-09-13 by `linear-verify.py` (three reads per issue: fields, `blocked_by`,
  `sub_issues`). Zero rate limiting; the authenticated hourly budget is 5,000.

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
  auto-pilot today. **And it no longer gates anything.** It used to gate a like-for-like
  task 10; task 10 closed 2026-09-15 having weighed that unfairness and decided anyway. So
  what remains is the bare cost with no schedule behind it — the shape of thing that
  drifts. ~~If the footer above is ever to have its unattended reader, this is the task
  that brings it.~~ **That reason is spent**: the footer's unattended reader turned out
  not to need task 13 at all, because a routine reads the edge directly. Task 13's cost is
  now purely that this repo has no unattended auto-pilot.
- **The handler owes an MCP branch for its label writes**, reusing `labels.yml` for the
  same validate-then-replace rule. **No task owns it.**

  > **ANSWERED 2026-09-17 — the issues path is writable.** An earlier draft of this file
  > posed this as the cheap unasked question; it has since been asked. A routine
  > `PATCH`ed an issue's labels and got **`HTTP 200`** (#751), while the ref path stays
  > `403`. The refusal's wording — "Write access to **this GitHub API path**" — was
  > path-scoped and meant it.
  >
  > **So the MCP branch is no longer the only conceivable route, and `remote_batch` is
  > NOT thereby unblocked.** That gate needs two things and the **plugin-install gap is
  > the binding one** — untouched by any of this. The handler's writes also go through
  > `gh-issue-state.py` calling `gh api`, and `gh` is absent from a routine. What changed
  > is that "no working write channel" stops being a second independent blocker and
  > becomes an implementation gap.
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
  here pays. `linear-verify.py` is the one asset that escapes this, being GETs only.
  Outside this repo; needs the operator.
- **`state_reason` on the close path is unowned.** `gh-issue-state.py --done` writes
  `state: closed` and nothing else, so a completed issue and an abandoned one are
  indistinguishable afterwards. It costs more than it did: task 8's stale-versus-satisfied
  split reads `state_reason`. **#516 will want it** — closing 125 Linear issues as
  "migrated" is exactly the distinction `state_reason` carries. (#723 happens to read
  `COMPLETED` because that is GitHub's default for a plain close, not because anything
  chose it.)
- **The provisioning class is wider than the reconciler.** Task 17 guarded the three
  reconciler rows. Every other verb that asks whether an issue carries a rung inherits the
  same blind spot and is unaudited. Nothing detects that; there is no task.
- **Two label invariants have no reconciler rule.** Task 7's rule table is deliberately
  **closed** and task 17 kept it closed: at most one `prio:` / at most one `est:` (a
  duplicate stays invisible until the next write, which then refuses), and a **closed**
  issue still carrying live `status:`/`auto:` rungs (reachable with a bare
  `gh issue close`). `linear-verify.py` checks both, but only over the 125 imported
  entries and only when someone runs it — that is a spot check, not a reconciler rule.
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
  Both are now **also** GitHub issues, since the import carried them.

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

**What it cost task 10 — recorded because the reasoning is the verdict's.** The gate asked
"keep, extend, or revert" against `finplan` as a Linear control, and the pilot repo had
lost unattended operation while the control had not. That asymmetry was not resolved; it
was **weighed and accepted**, and it is part of why waiting two more weeks was judged
worthless — the extra time would have measured the one dimension already known to be
unfair. The verdict is not a score of the handler against Linear. It is a judgement that
the migration's cap relief was worth more than the comparison.

## The two boards, and what is left owed

**The GitHub board is the live one and the Linear side is closed out.** As of 2026-09-15
the 123 writable originals are archived, each pointing at its GitHub successor by comment
and link attachment. A re-run of `linear-successor.py` reports 0 outstanding. The
intermediate state where both boards showed the same work as open is over — anyone reading
Linear for `workflow-skills` status now reads an archive, which is correct.

`finplan` remains on Linear and is no longer a control for anything; task 10 closed the
comparison it was serving.

### Acceptance criteria still owed

Two repos are on the `gh-issue` handler — `bestdan/dotfiles` and `bestdan/workflow-skills`
— so anything testing **handler dispatch** can run in either. **Nothing here is waiting on
migration work any more.**

- **Task 16's dispatch half is unrunnable, not merely blocked.** `/do-tasks --all`
  dispatching bounded sessions needs a repo that can legitimately set `remote_batch: true`,
  and the probe says none can today. Retire the criterion or rewrite it against whatever
  closes the plugin gap. The **degrade** path is testable now on `bestdan/dotfiles`: `--all`
  with the default config must claim exactly one issue in the foreground and say
  `remote batch disabled — claiming one issue`.
- **Task 8's dispatch half — runnable NOW, and the cheapest thing here.**
  `/reoptimize-tasks` against `bestdan/dotfiles`. Confirm the **installed** plugin under
  `~/.claude/plugins/cache/workflow-skills/workflow-skills/<version>/` is v2.21.0 or later
  before reading the result — an older one exercises the old report-only prose and returns
  a green result that says nothing. Expect zero dependency findings (dotfiles has no
  edges); read it as a dispatch check, not a coverage one.
- **Task 4 — dispatch, runnable on `bestdan/dotfiles` today.** Two `/do-tasks` sessions
  against the same ready issue, confirming exactly one proceeds. Needs an issue at
  `status:2_ready` (which **is** provisioned there) and two concurrent sessions; the racing
  is the point, so a serial run proves nothing.
- **Task 15** — its user-run check.

Task 8's migrated-backlog half was discharged 2026-09-15 by #516, and task 7's check was
run and **found** task 17's defect, which is that check earning its keep.
