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

4. [phase_3_handler/gh_migration_task_4.md](phase_3_handler/gh_migration_task_4.md) — Claim lifecycle at `linear`-level depth. **NEXT.**
   It also owns deleting the bridges task 5 left, all marked in-file. Removing them
   is not optional tidying: while claim writes the old markers and promote writes the
   new ones, both spellings are live, and dropping a bridge early breaks the loop.
   - `gh-issue-claim.md` — the either-spelling ready term in the candidate query, and
     the legacy `priority:` arm in the ranking.
   - `gh-issue-promote.md` — the `-label:auto-eligible -label:human-approval-requested`
     exclusions in the query and the matching backstop clause.
   - `gh-issue.md` — the legacy `auto-eligible` / `human-approval-requested` arms in the
     `## List` section table, and the legacy `priority:` fallback in the card line.
   - Task 5 also left claim **not** consulting native `blocked_by`, so `/list-tasks` can
     show an issue dependency-blocked while `/do-tasks` claims it. A candidate-scoped
     `gh-issue-ready.py` pass belongs here.
5. ~~[phase_3_handler/gh_migration_task_5.md](phase_3_handler/gh_migration_task_5.md)~~ — State model across add / list / promote / do. **Done** — PR #439, merged `a4815d7`.
   Added `gh-issue-ready.py` for dependency-readiness. It left **migration bridges**
   that task 4 must remove, listed under task 4 below.
6. [phase_3_handler/gh_migration_task_6.md](phase_3_handler/gh_migration_task_6.md) — `needs_review` transition, its reverse, and the Action backstop.
7. [phase_3_handler/gh_migration_task_7.md](phase_3_handler/gh_migration_task_7.md) — Reconciler rules for the label invariants.
8. [phase_3_handler/gh_migration_task_8.md](phase_3_handler/gh_migration_task_8.md) — Upgrade `reoptimize` from report-only to native dependency edges.

**Phase 4 — migrate**

9. [phase_4_migrate/gh_migration_task_9.md](phase_4_migrate/gh_migration_task_9.md) — Export Linear, import the active `workflow-skills` issues.
10. [phase_4_migrate/gh_migration_task_10.md](phase_4_migrate/gh_migration_task_10.md) — Pilot evaluation gate: keep, extend, or revert.

**Phase 3 — handler (added by co-review of PR #415)**

12. Stale claim-ref sweep — find `task/<KEY>` refs with no open PR and no started
    issue, and delete them. **This is what gates flipping the routine claim default.**
    A routine can acquire the ref lock (`mcp__github__create_branch`, verified
    create-only) but cannot release it, so a crashed routine would deadlock an issue
    permanently. Until this sweep exists, `claim-lock.md` keeps routines on the
    self-healing comment election. Note `scripts/claim-scan.sh` and `/doctor` cover
    `repo-pr` claim PRs, not refs — this is new work, not a config change.

**Phase 4 — migrate (added 2026-08-30)**

13. **Teach auto-pilot the `gh-issue` handler.** `/auto-pilot` today supports **linear
    and plan sources only**: `skills/auto-pilot/SKILL.md:92` stops outright on "any
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
