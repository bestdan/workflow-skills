---
type: epic
title: Migrate the task loop from Linear to GitHub Issues
status: active
owner: bestdan
created: 2026-08-24
---

# Migrate the task loop from Linear to GitHub Issues

## Goal

Move the task loop off Linear onto GitHub Issues, building the `gh-issue`
handler to `linear`-level depth. Piloted in `workflow-skills`; `finplan` stays on
Linear as a live control.

## Why

Linear fails on three counts, two of which have bespoke workarounds in this repo:

- **Auto-close defect.** A PR body linking _related_ issues causes Linear to
  close them. Auto-close is already disabled and it still fires.
  `/find-false-closures` exists solely to detect and repair this.
- **250-issue free cap** against a ~195 issues/month run rate — overrun roughly
  every five weeks by arithmetic. `linear-archive.md` exists solely to stay under
  it, and cannot use the MCP at all.
- **The web UI is unused.** Linear is functioning as a database with a REST
  front-end, driven entirely by agents.

GitHub Issues clears every hard requirement and is the only candidate that
**deletes** a failure class rather than porting it: closing is keyword-explicit,
so the auto-close defect cannot occur.

Full requirements, verified facts, the 8-test empirical spike and the competitor
falsification pass: `/private/tmp/claude-501/-Users-danielegan-src-finplan/b3fd6077-583d-45fb-a692-0196b2535252/scratchpad/2026-08-24-tracker-migration-grilling-outcome.md`

## Scope / non-goals

**In scope:** the `gh-issue` handler at `linear`-level depth; the label state
model; label provisioning; claim locking; `needs_review` automation; the
reconciler; batch execution (`/do-tasks --all`); migration of `workflow-skills`
issues.

> **Widened 2026-09-04 to add batch execution**, absorbing Linear PRE-117 as
> [task 16](phase_3_handler/gh_migration_task_16.md). Recorded here rather than done
> quietly, because scope that grows without a reason reads as creep later. The reason:
> batch touches `do-tasks.md` and `gh-issue-claim.md`, the two files this epic churns
> hardest, and its first attempt ([#426](https://github.com/bestdan/workflow-skills/pull/426))
> died of exactly that collision — built against a vocabulary and a dependency premise
> this plan replaced while the PR was open. Keeping it outside the epic would set the
> same collision up again, and "at `linear`-level depth" already implied it: `linear`
> has batch and `gh-issue` does not.

**Not in scope:**

- Migrating `finplan` or `aiutopilot`. `finplan` is the control.
- **Deleting the Linear handler or its four Linear-only commands.** They stay
  while any repo is still on Linear. "Deleted, not ported" means they are not
  rebuilt for `gh-issue` — not that they are removed now.
- Projects v2 as a store. A board may be added later as a read-only view.
- Image attachment migration (see open questions).

## Approach

Labels are the source of truth for status, priority and estimate; dependencies
and sub-issues use GitHub's native endpoints. The two spike failures set the
core constraint: `gh issue edit --add-label/--remove-label` is **not** atomic
(8 requests), so transitions must use a full-set `PATCH` — but raw REST
**auto-creates** unknown labels, so every write must validate against the
vocabulary first. **Validate, then PATCH.**

Main tradeoff considered and rejected: storing state in Projects v2 custom
fields. Proper modelling, but it makes every read a REST call plus a GraphQL
join, and `gh` covers it only partially.

## Tasks

**Phase 1 — validate**

1. [phase_1_validate/gh_migration_task_1.md](phase_1_validate/gh_migration_task_1.md) — Probe whether `POST /git/refs` works inside a cloud routine.

**Phase 2 — primitives — DONE, shipped in PR #415 (merged `d7aa23a`)**

2. ~~[phase_2_primitives/gh_migration_task_2.md](phase_2_primitives/gh_migration_task_2.md)~~ — `labels.yml` and an idempotent per-repo sync script. **Done.** The 17
   labels are provisioned on `bestdan/workflow-skills`; a second `--apply` is a no-op.
3. ~~[phase_2_primitives/gh_migration_task_3.md](phase_2_primitives/gh_migration_task_3.md)~~ — Atomic write helper. **Done**, but it grew: it is
   `commands/handlers/assets/gh-issue-state.py`, not `gh-label-write.py`, because it
   settles labels AND open/closed in one PATCH — under this schema a closed issue is
   exactly "no rungs", so the two are one fact. `--done` closes; live rungs on a closed
   issue need `--reopen`. Task 5 wired the callers; every status write now goes through it.

**Phase 3 — handler**

4. ~~[phase_3_handler/gh_migration_task_4.md](phase_3_handler/gh_migration_task_4.md)~~ — Claim lifecycle at `linear`-level depth. **Done** — PR #442.
   Claim writes `status:` rungs through `gh-issue-state.py`, and every bridge task 5 left
   is gone, in the same change. Three things it settled that the plan had not:
   - **The deterministic parts are code now.** `commands/handlers/assets/gh-issue-claim.py`
     owns the branch name, the branch→issue parser, the in-flight count and
     acquire/release. Its **exit codes are the contract** the handler branches on —
     `0` won, `3` lost the race, `4` neither. `4` is the case prose kept getting wrong.
   - **The branch is `<branch_prefix>task-<n>`, not `bestdan/task-<n>`.** The plan named a
     literal prefix; that is one owner's house rule inside a handler every installed user
     runs. It became the optional `gh-issue.branch_prefix` config key, empty by default
     (`task-142`). Deterministic across racers either way — both read one repo's config —
     and the parser ignores the prefix, so a routine's `claude/task-142` still resolves.
   - **Claim now consults native `blocked_by`.** `gh-issue-ready.py` gained a
     candidate-scoped `--issue N` mode and claim asks about exactly the candidates it
     ranked, rather than a second bounded query that could drop one silently.

   Co-review (codex + the reconciler) caught four things, all fixed in the same PR: the
   claim re-read narrowed the `status:` rung but not `auto:eligible`; the two-write board
   marker had no instruction for a failure between the writes; and `/do-tasks` plus
   `gh-issue-state.py`'s own docstring still described the pre-migration world.
5. ~~[phase_3_handler/gh_migration_task_5.md](phase_3_handler/gh_migration_task_5.md)~~ — State model across add / list / promote / do. **Done** — PR #439, merged `a4815d7`.
   Added `gh-issue-ready.py` for dependency-readiness. The **migration bridges** it left
   were removed by task 4.
6. ~~[phase_3_handler/gh_migration_task_6.md](phase_3_handler/gh_migration_task_6.md)~~ — `needs_review` transition, its reverse, and the Action backstop. **Done** — PR #447, merged `4261eb67`, shipped v2.18.0.
   **Pieces 1 and 2 only.** Piece 3 (the agent writing the rung as it opens the PR) is
   deferred behind the auto-pilot harness, same reason as task 13. It also **deviated from
   the task file's trigger list**, adding `opened` behind a `draft` guard — without it a
   PR opened straight to non-draft emits no `ready_for_review` and is never caught.
   Follow-ups live in Linear: PRE-822 (`reopened` unhandled), PRE-823 (does a runner's
   token reach the dependency endpoints — the fact task 8 now turns on).
7. ~~[phase_3_handler/gh_migration_task_7.md](phase_3_handler/gh_migration_task_7.md)~~ — Reconciler rules for the label invariants. **Done** — PR #464, merged `afd2d00`, shipped v2.19.0.
   `/reconcile-tasks` had answered "unsupported" for `gh-issue` on the grounds that GitHub
   closes issues natively on merge. That is true and it is a different question, so the
   handler now reconciles what it actually can drift in: the label state model, audited
   against `labels.yml` by `commands/handlers/assets/gh-issue-reconcile.py`. Three things
   it settled that the plan had not:
   - **"Has a rung" means a vocabulary rung.** Rule 2 first tested the bare `status:` /
     `auto:` prefix, which a hand-typed `status:blocked` satisfies — leaving that issue
     invisible to every rule at once, since rule 1 has no ladder position to rank it by
     either. Caught in co-review. The vocabulary reading is what `gh-issue-state.py`'s
     `validate()` always meant, and it now holds across the handler.
   - **Deviation from the task file: rule 3 reads `issues/{n}/events`, not `timeline`.**
     Measured — both carry the same `labeled` stream, and `timeline` adds
     `cross-referenced` and comment entries the rule has no use for.
   - **The `--limit` window is not what it looked like.** `gh issue list` orders by
     creation date, so a long-lived issue closed yesterday can fall outside it and go
     unaudited by rule 3. Documented rather than papered over; GitHub search has no
     `sort:closed`.

   Two invariants were deliberately **not** given rules, because the table is closed:
   duplicate `prio:`/`est:`, and a closed issue still carrying live rungs (reachable with
   a bare `gh issue close`). Both are recorded in HANDOFF.md.

   **Row 3 has a blind spot, found 2026-09-04 when task 7's own dispatch check was
   finally run** (against `bestdan/dotfiles`, since it needed only a gh-issue repo and
   not a migrated one). It asks whether an issue was ever labelled
   `status:4_needs_review` without first asking whether that label exists on the repo. On
   a partially provisioned board it does not, so no issue can have carried it and **every**
   closed issue is a hit — the run reported 50 of 50. This entry's claim that the three
   rules are sound holds for rows 1 and 2; row 3 is sound only where the vocabulary is
   fully provisioned, which nothing checks. Unowned, and outside task 8. See HANDOFF.md.
8. ~~[phase_3_handler/gh_migration_task_8.md](phase_3_handler/gh_migration_task_8.md)~~ — Upgrade `reoptimize` from report-only to native dependency edges. **Done** — PR #478.
   It also carried reoptimize's **vocabulary migration**, which was the last one: every
   gh-issue verb now speaks `labels.yml`, and no bridge remains anywhere. Three things it
   settled that the plan had not:
   - **The real defect was not the missing feature.** The flow wrote `Blocked by:` footer
     lines for dependencies it created no edge for, so a footer could record a proposal
     nobody applied — indistinguishable from one echoing a real edge. That is what made
     every footer in the repo unreadable as evidence.
   - **Deviation from the task file: the footer stays.** Its acceptance criterion "No
     `Blocked by:` footer-writing code path remains" is deliberately unmet, which the task
     file anticipated. `/push-plan` and `/reoptimize-tasks` must not disagree about what a
     footer means. The rule is now uniform instead: **the footer follows the edge, and a
     footer alone is never a dependency.**
   - **PRE-823 does not gate that decision, and the task file was wrong to say it did.**
     Whether an Actions runner reaches the dependency endpoints does not discriminate: if
     it can, the footer is redundant but harmless and `/push-plan` writes it anyway; if it
     cannot, the footer is the only unattended signal. Consistency decides it in both
     branches, so the probe was not run for this purpose.

   Its analysis half is `commands/handlers/assets/gh-issue-graph.py` (read-only), and
   `gh-issue-deps.py` gained `--remove-edge` for stale-link repair. Both were probed
   against live issues, which turned up a fact the plan had assumed the other way:
   **GitHub refuses a directly reciprocal edge (422) and refuses nothing else** — a
   3-cycle is created without complaint. So its guard cannot be read as acyclicity, and
   a batch write now survives a per-edge refusal instead of aborting half-applied.

**Phase 4 — migrate**

9. [phase_4_migrate/gh_migration_task_9.md](phase_4_migrate/gh_migration_task_9.md) — Export Linear, import the active `workflow-skills` issues.
   **Broken down 2026-09-08 into GitHub issues [#510](https://github.com/bestdan/workflow-skills/issues/510)–[#516](https://github.com/bestdan/workflow-skills/issues/516)**
   under milestone 1, a strict chain: export → plan → apply → link → verify → Linear-side
   close-out → retire. **Not blocked by task 13** — importing issues needs no auto-pilot.
   **#510 through #515 are done, on `main`, and both boards are settled.** All **125**
   selected issues landed on the GitHub board on 2026-09-13, 117 created and 8 reopened,
   with the edges and sub-issue links written by #513 and the whole board independently
   checked by #514. #515 then pointed the Linear originals at their successors and
   canceled them — **123 of 125**, the 2 exceptions being PRE-503 and PRE-504, archived
   since 2026-08-01 and therefore unwritable. The set is 125, not the "84" the task file
   opened with nor the "96 or 135" that replaced it — both predate an export, and the
   count lives in `$HOME/src/linear-export/2026-09-13-import-plan.json`.
   **DONE 2026-09-15.** [#516](https://github.com/bestdan/workflow-skills/issues/516)
   closed it out: `/reoptimize-tasks` at whole-repo scope found the graph already sound
   (0 cycles, 0 stale edges, 0 inversions) and reported the 27 imported edges as missing
   their `Blocked by:` body echo — applied as 27 edges over 20 issues, which settles the
   footer question the handoff had left open. Three edges were spot-checked against the
   export, 3/3. The second `linear-verify.py` run then passes every check except `fields`,
   which now reports `body` differences on exactly those 20 issues — a **deliberate,
   permanent divergence**, because the plan file is provenance and must not be edited to
   make a check pass. See HANDOFF.md → "What #516 ran" for the expected-failure contract.
10. [phase_4_migrate/gh_migration_task_10.md](phase_4_migrate/gh_migration_task_10.md) — Pilot evaluation gate: keep, extend, or revert.
    **DONE 2026-09-15 — the verdict is KEEP**, and the gate was closed on day 0 of its
    own two-week clock rather than run to term. `workflow-skills` stays on `gh-issue`,
    `finplan` stays on Linear, nothing is scheduled to migrate next. **Firmed up
    2026-09-18: `finplan` and `aiutopilot` stay on Linear by decision, not deferral —
    `workflow-skills` is the only repo that ever moved.**

    **Closing it early was the decision, not a shortcut past it.** Two weeks of waiting
    would have bought a comparison against a control that does not hold still — both
    boards keep resolving and creating issues, so the 09-29 verdict would have scored a
    stale snapshot — while the one dimension the time would have measured was already
    known to be unfair (this repo lost unattended auto-pilot on 09-07 and the control did
    not). Against that, waiting had a real price: it held the revert path open, which held
    the 123 migrated originals unarchived, which kept the workspace at ~253 against a
    250 cap **with Linear refusing to create new issues.** The migration exists to relieve
    that cap and the relief was being withheld to preserve an escape hatch already
    measured as impractical. Archiving ran the same day and took the workspace to ~130.

    **The one consequence that opens rather than closes, now also decided:** "keep, not
    extend" removed the justification for the migration assets, and the owner's call
    (2026-09-15) is to **remove them**. No future use is known and git history is the
    recovery path, so the deletion is cheap and reversible — which is what makes it
    defensible while `finplan`'s own migration stays a not-now rather than a never. `linear-import.py` (~2,500 lines),
    `linear-verify.py` and `linear-successor.py` were kept against the possibility that
    this gate said extend, and every installed plugin user carries them. Executed in
    [task 11](phase_5_cleanup/gh_migration_task_11.md), with the greps that must precede
    it. The Linear **handler** and
    its four Linear-only commands are a separate question and they stay — `finplan` is
    still on Linear, which is the rule task 11 states.

    Full reasoning, and the five diagnostic questions retired unanswered, are in the task
    file's Decision section.

**Phase 3 — handler (added by co-review of PR #415)**

12. Stale claim-ref sweep — find `task/<KEY>` refs with no open PR and no started
    issue, and delete them. **This is what gates flipping the routine claim default.**
    A routine can acquire the ref lock (`mcp__github__create_branch`, verified
    create-only) but cannot release it, so a crashed routine would deadlock an issue
    permanently. Until this sweep exists, `claim-lock.md` keeps routines on the
    self-healing comment election. Note `scripts/claim-scan.sh` and `/doctor` cover
    `repo-pr` claim PRs, not refs — this is new work, not a config change.

**Phase 3 — handler (added 2026-08-31, from the post-task-4 PR audit)**

14. ~~[phase_3_handler/gh_migration_task_14.md](phase_3_handler/gh_migration_task_14.md)~~ — **Defect from task 4.** `/do-tasks --no-claim`
    checked out `task/<n>`, a branch the claim no longer creates. **Done** — PR #443.
    The sweep the task file asked for found a **second** stale reference, in section 4's
    summary of what the handler holds; co-review found a third problem in the fix itself
    (the resolver was spelled with no interpreter and no path, so following it literally
    gets `command not found`). Both are the same lesson: a partial sweep reads as a
    complete one, and an unrunnable command sends a reader back to the literal the
    instruction exists to forbid.
15. ~~[phase_3_handler/gh_migration_task_15.md](phase_3_handler/gh_migration_task_15.md)~~ — Create native dependency edges on the write
    side. **Done** — PR #444, merged `09aa71f`. `gh-issue-deps.py` writes the edges and
    `/push-plan` §5.5 calls it as a second pass once every issue exists, which is what
    made the read paths tasks 4 and 5 shipped stop passing everything. The
    "no native dependency edge" claim was retired in **four** files, not the three this
    entry originally named — `commands/reoptimize-tasks.md` asserted it too.
    Three things it settled that the plan had not:
    - **The POST body wants a database id.** `issue_id` is the blocker's REST `id`, not
      its `#<number>`; passing the number links a different issue and reads back as a
      plausible edge. The helper resolves it.
    - **The footer stays, as a human-readable echo.** The deciding argument arrived after
      the review: a cloud routine can read an issue body but not the edge, so the footer
      is the only unattended blocked-ness signal — a hint, never the graph. **Task 8 then
      made that rule uniform** across both writing paths, and kept the footer for a
      second reason: the two paths must not disagree about what one means.
    - **Reoptimize's limit was a handler gap, not a platform one.** Task 8 closed it.

**Phase 3 — handler (added 2026-09-04, absorbing Linear PRE-117)**

16. ~~[phase_3_handler/gh_migration_task_16.md](phase_3_handler/gh_migration_task_16.md)~~ — Batch execution (`/do-tasks --all`) on the
    tracker-batch subroutine. **Done** — PR #482, merged `547f776`. Absorbed
    **PRE-117** and superseded the closed
    [#426](https://github.com/bestdan/workflow-skills/pull/426), which was rebuilt as a
    fresh edit rather than ported. Every deterministic value on the new path reads from a
    script whose output is the contract — the migrated candidate query, `gh-issue-ready.py`
    against the native `blocked_by` graph, `gh-issue-claim.py wip` (which gained a clamped
    `slack` field) and `branch-name`. Two things it settled that the plan had not:

    - **Deviation: the capability matrix reads `opt`, not `yes`.** The task file said flip
      it to `yes`. Remote dispatch needs the dispatched session to run a plugin script,
      which the documentation said reaches a cloud session from a committed
      `.claude/settings.json`. **Probed 2026-09-05 and it does not**
      ([`2026-09-05-cloud-session-plugin-and-proxy.md`](../decisions/2026-09-05-cloud-session-plugin-and-proxy.md),
      shipped on `main` in PR #490). The same session's `gh` is uncredentialed, reads and
      writes alike. So the deviation is **permanent, not pending**: `remote_batch` stays
      `false`, the matrix stays `opt`, and the criterion that said flip it needs
      retiring or rewriting against whatever closes the plugin gap.
    - **The "a cloud VM has no plugin / has no `gh`" premise held**, by a different
      mechanism than the 2026-08-24 routine probe found: a cloud session has the `gh`
      binary and a dead token, where a routine had no binary. The credentialed channel is
      the GitHub MCP connector in both, which makes the handler's unowned MCP branch the
      prerequisite for any working dispatched session.

    Also surfaced and **not** repaired: a crashed claim strands its issue at
    `status:3_started` with an assignee, invisible to the candidate query on both counts,
    on both lock paths. No task owns it.

**Phase 3 — handler (added 2026-09-04, from task 7's dispatch check)**

17. [phase_3_handler/gh_migration_task_17.md](phase_3_handler/gh_migration_task_17.md) — **DONE.**
    [PR #479](https://github.com/bestdan/workflow-skills/pull/479). Reconciler row 3 asked
    whether an issue ever carried `status:4_needs_review` without asking whether that
    label exists on the repo, so on an under-provisioned repo it hit every closed issue —
    measured at 50 of 50 against `bestdan/dotfiles`. Each row now checks its own premise
    against one `gh label list`; row 2 is guarded by group rather than completeness, and
    row 1 needs no guard. Step 2's claim that the label scope already prevented this is
    corrected.

**Phase 4 — migrate (added 2026-08-30)**

13. **Teach auto-pilot the `gh-issue` handler. POSTPONED 2026-09-02, reaffirmed by the
    owner 2026-09-12** — `/auto-pilot` is under active development with a new harness.
    Teaching it a fifth handler against a moving target buys rework, so this waits until
    that harness lands; resume by re-reading the three `SKILL.md` line references below,
    which will have moved.

    > **Amended 2026-09-12 — the conditional below is now past tense.** The repo switched
    > to `gh-issue` on 2026-09-07 ([#503](https://github.com/bestdan/workflow-skills/pull/503))
    > with this task still postponed, so `workflow-skills` **is** out of unattended
    > auto-pilot today. Verified: `skills/auto-pilot/SKILL.md:135` still refuses any
    > handler but `linear`/`repo-pr`. This is an accepted cost, not an unnoticed
    > regression — but it is a live one, so anything that reads "auto-pilot will stop
    > working if we switch" should be read as "auto-pilot is not running here".
    > **It holds no task at all now.** Task 9 was import work that needed no auto-pilot,
    > and task 10 — the one thing that did depend on this, for its like-for-like
    > comparison — closed 2026-09-15 without it.

    `/auto-pilot` today supports **linear and plan sources only**:
    `skills/auto-pilot/SKILL.md:135` stops outright on "any handler other than
    `linear`/`repo-pr`". Three pieces need doing:
    - **Source detection** (`SKILL.md:132-133`, re-checked 2026-09-12) — recognise a
      gh-issue source (a milestone, or a `status:`-label query) alongside
      `dev_docs/tasks/<name>_plan/` and a Linear project.
    - **Effective-handler mapping** (`SKILL.md:136-140`) — the current rule is plan ⇒
      `repo-pr`, linear ⇒ `linear`; add gh-issue ⇒ `gh-issue`.
    - **`/deliver-task` gh-issue path** (`SKILL.md:290-294`) — the handler is passed in
      rather than re-derived, so `/deliver-task` must accept and honour it.

    **This no longer gates anything — and that makes it worse, not better.** It used to
    gate task 10, on the argument that a keep/extend/revert verdict is unfair if the pilot
    repo silently lost unattended operation while the Linear control kept it. Task 10
    closed 2026-09-15 without waiting for it, having weighed that unfairness explicitly.
    So the sequencing argument is spent, and what remains is the bare cost: **this repo
    has been outside unattended auto-pilot since 2026-09-07 and nothing is now scheduled
    to end that.** It is an accepted cost with no gate behind it, which is exactly the
    shape of thing that drifts. Still postponed by the owner (2026-09-12) pending the new
    `/auto-pilot` harness.

    **Amended 2026-09-02 — a runner is a third credentialed channel.** Task 6
    ([PR #447](https://github.com/bestdan/workflow-skills/pull/447)) measured that a
    GitHub Actions runner has `gh` and can write issue labels with its ambient
    `GITHUB_TOKEN` under `permissions: issues: write`. That does not do this task's work
    — auto-pilot's refusal is a `SKILL.md` check, not a credential problem, so the three
    pieces above stand unchanged. It does change what "unattended" can mean for the
    gh-issue handler generally: some of what this plan assumed needed either a foreground
    session or the MCP connector can run in a workflow instead. Weigh that when the
    harness lands, rather than porting the two-channel assumption into the new design.

**Phase 5 — cleanup**

11. [phase_5_cleanup/gh_migration_task_11.md](phase_5_cleanup/gh_migration_task_11.md) — Graduate durable decisions to `dev_docs/`, delete the plan folder.
    **DONE 2026-09-17 — [PR #755](https://github.com/bestdan/workflow-skills/pull/755).**
    `dev_docs/gh_issue_task_loop.md` carries all six points; the
    requirements-and-evidence record graduated beside it as
    `dev_docs/decisions/2026-08-24-gh-issue-migration-requirements-and-evidence.md`. The
    five migration assets and their nine test files are deleted, each named in the delete
    commit so `log --diff-filter=D` is the recovery path. The untracked
    `dev_docs/tasks/gh_migration_plan/` is gone from the main checkout.

    **The #441 decision this task owned: graduate a curated subset.** Those two files reach
    `main`; the per-task scaffolding does not, and PR #441 is closed without merging. The
    record survives on `origin/bestdan/gh-issue-migration`.

    **Two things verified rather than inherited.** The task file says "four Linear-only
    commands"; there are **three** — `/sweep-for-complete`, `/find-false-closures` and
    `/sweep-for-archive` are the only ones that refuse on every other handler
    (`/reconcile-tasks` reads as the fourth but `gh-issue` supports it as `audit`). And
    `test_linear_gql_shape.py` asserts its glob finds **at least five** assets defining
    `gql()`: there were seven, two went, so it now sits exactly on its boundary — the next
    Linear asset removed breaks it.

    **This plan is complete.** Task 12 was never written and gates nothing; task 13 is the
    owner's, tracked outside this plan.

## In-flight PRs against files this plan owns

> **Re-checked 2026-09-04: only #426 is still open.** #432 and #411 merged, and merged
> reconciled — `git grep` over `origin/main` finds no live old-vocabulary query left in
> `gh-issue-promote.md` or `gh-issue-claim.md`. Their rows below are kept as the record of
> what was required, not as work outstanding. **#426 is the live one**, and it is the row
> that was always the most dangerous.

Three open PRs modify handler files this migration has since rewritten. They are tracked
in Linear, not here — **this section records only what the migration requires of each**,
which is the migration's own knowledge and lives nowhere else. Audited 2026-08-31 against
`main` at `3883d49`.

The shared hazard: **all three apply almost cleanly and are almost all wrong.** Each was
written against the pre-migration vocabulary, so `git` reports success while the prose
now instructs an agent to query labels nothing writes.

| PR                                                                                                 | Verdict                        | What must change before merge                                                                                                                                                                                                                                                                                                         |
| -------------------------------------------------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [#426](https://github.com/bestdan/workflow-skills/pull/426) — gh-issue batch `/do-tasks --all`     | rebase-then-**revise heavily** | Four things, below. Three of its four files apply clean, which is the trap.                                                                                                                                                                                                                                                           |
| [#432](https://github.com/bestdan/workflow-skills/pull/432) — paginate promote's candidate query   | rebase-then-revise             | Three of five hunks anchor on the deleted legacy-label query. The intent (`--limit 50` → `500`, pagination prose) is orthogonal to the vocabulary change — re-anchor it onto the current query and backstop paragraph. Its GraphQL rewrite applies as-is.                                                                             |
| [#411](https://github.com/bestdan/workflow-skills/pull/411) — warn against parallel tracker writes | rebase-then-revise             | Its anchor line is gone (task 5 restructured promote's step 5), and its warning names `gh issue edit` — the call this migration **banned** on that path. Reword to name the `gh-issue-state.py` PATCH. The concern itself is real and complementary: the script bounds one issue's transition, #411 bounds parallelism across issues. |

**#432 and #411 touch disjoint sections of `gh-issue-promote.md`**, so they can land in
either order once each is separately reconciled.

### #426 in detail — it would ship a silently dead feature

> **Closed 2026-09-04 as superseded.** #426 was parked to draft after a co-review found
> the defects listed below, then closed once
> [task 16](phase_3_handler/gh_migration_task_16.md) took over the work. It was
> conflicting with `main` at the time, and its tracker issue PRE-117 is cancelled. Its
> code stays reachable through the pull ref — see task 16, which carries the fetch
> command; that ref outlives the head branch. Its five-item verdict is preserved as a
> [parking comment on the PR](https://github.com/bestdan/workflow-skills/pull/426#issuecomment-5540130672).
> **Do not rebase it.** The work it was attempting is now
> [task 16](phase_3_handler/gh_migration_task_16.md), which builds against the current
> contract as a fresh edit; PRE-117 is cancelled as superseded and points there. This
> section survives as task 16's list of what not to repeat, not as a merge checklist —
> except its **§4 batch machinery**, which was never the problem and is worth lifting.
>
> Two items to add to the list below, both verified against `origin/main` by the
> reviewing session: the PR spells `task/<n>` throughout while task 4 moved the lock to
> `<branch_prefix>task-<n>` (`gh-issue-claim.md:9-12`, and line 23 says a fixed
> `task/<n>` cannot satisfy the scheme) — so a batch session creates a ref that misses
> the real lock and every racer thinks it won; and the PR re-derives the WIP count in
> prose rather than calling `gh-issue-claim.py wip`, whose docstring exists to say a
> caller must not re-derive it.

Merged after a mechanical rebase, gh-issue batch would report "no candidate" forever and
look healthy doing it:

- Its candidate query asks for `auto-eligible` / `-label:auto-claimed`. Nothing writes
  those now, so it matches **zero issues** — reported as no candidate, with no error.
- It re-derives the WIP count in prose from the same two dead labels instead of calling
  `gh-issue-claim.py wip`, so the bound the whole batch mechanism exists to enforce reads
  zero.
- It spells `task/<n>` throughout, so a batch session would create a ref that misses the
  real lock.
- It adds a **"Dependency-ready selection"** section that parses the `Blocked by: #<n>`
  body footer, on the stated premise that "GitHub Issues has no native blocking
  relationship this handler can query." That premise is false (task 8's context, and the
  live endpoint `gh-issue-ready.py` uses) — and task 15 has since retired the claim that
  produced it, so the repo no longer tells a reader what it told #426.
  **Delete that section rather than reconciling
  it** — task 4 already drops dependency-blocked candidates via
  `gh-issue-ready.py --issue N`, scoped exactly the way #426 wants.
- Its capability-matrix flip to `yes` is directionally right but must land **last**:
  until the four above are fixed it advertises a feature that no-ops.

Worth naming: #426 reached for the body footer because the repo told it to. It is the
same stale claim task 15 exists to retire, arrived at independently — which is the
argument for fixing the claim at its source rather than per-PR.

### Not interacting

[#436](https://github.com/bestdan/workflow-skills/pull/436) (Linear active-issue quota
precheck) touches `linear-common.md` and `linear-promote.md` only. Its `auto-eligible`
mentions are **Linear's** vocabulary, which this migration deliberately leaves alone —
`finplan` stays on Linear as the control. No action. The observation this carried for
task 10 — that #436 is building a workaround for the 250-issue cap that is one of the
three reasons this migration was chosen — **was borne out in the most direct way
available**: that same cap is what closed the gate early, by refusing new issues at ~253
until the migrated originals were archived. Evidence about Linear's cost, not about #436,
and `finplan` keeps living with it.

## Open questions

0. **RESOLVED 2026-08-24 — a routine CAN drive the loop, with two gaps.**
   > **Reopened 2026-09-04 by task 16, and closed again 2026-09-05 by the probe.**
   > Anthropic's documentation says a cloud session has `gh` preinstalled and
   > proxy-authenticated, and installs plugins a repo declares in a committed
   > `.claude/settings.json`. Measured against a consumer repo, **neither holds**
   > ([`2026-09-05-cloud-session-plugin-and-proxy.md`](../decisions/2026-09-05-cloud-session-plugin-and-proxy.md)):
   > nothing was installed, `$CLAUDE_PLUGIN_ROOT` was empty, and every `gh api` call
   > 403'd — the read as well as the write. The conclusion below stands; only the reason
   > `gh` is unusable differs between a routine (no binary) and a cloud session (a binary
   > with a dead token). Gaps (a) and (b) were never in question: neither turns on `gh`.
   >
   > MCP is the credentialed channel (raw HTTP carries no credential and `gh` is absent). Issue
   > writes work; `mcp__github__create_branch` is create-only and rejects duplicates, so
   > the claim lock holds. Two confirmed gaps change the design:
   > **(a)** a routine cannot _release_ a lock — no delete-ref tool, and `git push
   --delete` 403s — so a bailing routine strands a claim (task 4);
   > **(b)** there is **no dependency tool**, so `blocked_by`/`blocking` edges are
   > unreachable unattended, making task 8 and dependency-aware selection local-only.
   > Still open: whether `issue_write` **replaces** or **merges** labels, which decides
   > whether task 3's atomicity guarantee holds on the routine path. See §10b.
1. **Image attachments.** GitHub reportedly has no API for uploading issue
   images (browser-only). Unverified. If true, Linear-hosted screenshots in
   comments cannot be migrated headlessly. Accept the loss, or re-home them into
   a repo?
2. **`auto-eligible` → `auto:eligible` rename.** Settled as a schema decision,
   but across 40 repos it is a migration. Do it during the pilot, or defer until
   all repos migrate?
3. **Linear project → GitHub milestone mapping.** Half of all reads are
   project-scoped, so the grouping dimension has to survive. Milestones are the
   presumed mapping; unconfirmed.
4. **Codeberg ToU.** Reportedly restricts LLM-generated content. Irrelevant
   unless the direction is revisited, but unverified and worth knowing.
