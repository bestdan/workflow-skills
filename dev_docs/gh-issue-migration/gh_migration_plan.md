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
reconciler; migration of `workflow-skills` issues.

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
   issue need `--reopen`. **Nothing calls it yet** — that is task 5.

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
6. [phase_3_handler/gh_migration_task_6.md](phase_3_handler/gh_migration_task_6.md) — `needs_review` transition, its reverse, and the Action backstop. **NEXT.**
7. [phase_3_handler/gh_migration_task_7.md](phase_3_handler/gh_migration_task_7.md) — Reconciler rules for the label invariants.
8. [phase_3_handler/gh_migration_task_8.md](phase_3_handler/gh_migration_task_8.md) — Upgrade `reoptimize` from report-only to native dependency edges.
   **It also inherits reoptimize's vocabulary migration.** `gh-issue-reoptimize.md` still
   speaks `auto-eligible` / `auto-claimed` / `priority:*`, so on a migrated board its
   `statusType` derivation matches nothing and every issue reads as `new`. Task 4 removed
   the definitions it pointed at and left it a scope note saying so, rather than migrating
   it in a PR about claiming.

**Phase 4 — migrate**

9. [phase_4_migrate/gh_migration_task_9.md](phase_4_migrate/gh_migration_task_9.md) — Export Linear, import the active `workflow-skills` issues.
10. [phase_4_migrate/gh_migration_task_10.md](phase_4_migrate/gh_migration_task_10.md) — Pilot evaluation gate: keep, extend, or revert.
    **Blocked by task 13's postponement, unless the pilot runs attended.** Flipping
    `.task-config.yml` to `gh-issue` drops this repo out of auto-pilot entirely (task 13),
    so a pilot run today is a hand-driven one. That is a legitimate way to run it — the
    handler works fine in a foreground session — but decide it deliberately rather than
    discovering it at switch time.

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
      is the only unattended blocked-ness signal — a hint, never the graph. Task 8's
      **Constraint** section carries this; it bears on that task's "drop the footer
      entirely" instruction.
    - **Reoptimize's limit is now a handler gap, not a platform one.** Its report-only
      status is unchanged, but the reason in the prose is corrected. Task 8 still owns
      teaching it to write edges.

**Phase 4 — migrate (added 2026-08-30)**

13. **Teach auto-pilot the `gh-issue` handler. POSTPONED 2026-09-02** — `/auto-pilot`
    is under active development with a new harness. Teaching it a fifth handler against a
    moving target buys rework, so this waits until that harness lands; resume by
    re-reading the three `SKILL.md` line references below, which will have moved.
    **This holds Phase 4 with it** — see the note under task 10. `/auto-pilot` today
    supports **linear and plan sources only**: `skills/auto-pilot/SKILL.md:92` stops outright on "any
    handler other than `linear`/`repo-pr`". So the moment `workflow-skills` switches its
    `.task-config.yml` to `gh-issue`, **the repo drops out of auto-pilot entirely** — it
    does not degrade, it refuses to launch. Three pieces need doing:
    - **Source detection** (`SKILL.md:90`) — recognise a gh-issue source (a milestone, or
      a `status:`-label query) alongside `dev_docs/tasks/<name>_plan/` and a Linear
      project.
    - **Effective-handler mapping** (`SKILL.md:95`) — the current rule is plan ⇒
      `repo-pr`, linear ⇒ `linear`; add gh-issue ⇒ `gh-issue`.
    - **`/deliver-task` gh-issue path** (`SKILL.md:242`) — the handler is passed in
      rather than re-derived, so `/deliver-task` must accept and honour it.

    **This gates task 10.** The pilot evaluation asks "keep, extend, or revert", and that
    comparison is not fair if the pilot repo silently lost unattended operation while the
    Linear control kept it. Sequence 13 before 10, and before flipping
    `.task-config.yml`.

**Phase 5 — cleanup**

11. [phase_5_cleanup/gh_migration_task_11.md](phase_5_cleanup/gh_migration_task_11.md) — Graduate durable decisions to `dev_docs/`, delete the plan folder.

## In-flight PRs against files this plan owns

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
`finplan` stays on Linear as the control. No action. One observation for the pilot
evaluation (task 10): it is building a workaround for the 250-issue cap that is one of
the three reasons this migration was chosen, which is evidence about Linear's cost, not
about #436.

## Open questions

0. **RESOLVED 2026-08-24 — a routine CAN drive the loop, with two gaps.** MCP is the
   credentialed channel (raw HTTP carries no credential and `gh` is absent). Issue
   writes work; `mcp__github__create_branch` is create-only and rejects duplicates, so
   the claim lock holds. Two confirmed gaps change the design:
   **(a)** a routine cannot _release_ a lock — no delete-ref tool, and `git push
   --delete` 403s — so a bailing routine strands a claim (task 4);
   **(b)** there is **no dependency tool**, so `blocked_by`/`blocking` edges are
   unreachable unattended, making task 8 and dependency-aware selection local-only.
   Still open: whether `issue_write` **replaces** or **merges** labels, which decides
   whether task 3's atomicity guarantee holds on the routine path. See §10b.
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
