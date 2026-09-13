---
title: Export Linear and import the active workflow-skills issues
priority: high
size: 5
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_8
related_files:
  - commands/handlers/linear-common.md
tags: [migration, data]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Export Linear and import the active workflow-skills issues

## Context

781 issues exist; 513 are archived and may be discarded, **but export first**.
Those Linear keys are baked into branch names and commit messages already in git
history (e.g. `bestdan/ss-earnings-record-task-6`), so deleting without an export
turns a free snapshot into a permanent provenance hole. One GraphQL call with the
API key `linear-archive.md` already holds.

> **The 781 is a 2026-08-24 snapshot and has moved.** The workspace was at **PRE-835** on
> 2026-09-12, so the true total is that or higher. Treat every "781" here as "all of them,
> whatever the count is at export time" — the export must paginate to exhaustion and report
> what it found, never assert a literal. The acceptance criteria are rewritten accordingly.

Only `workflow-skills` migrates in this plan. `finplan` stays on Linear as the control.

> **Re-assessed 2026-09-12 — "84 backlog issues plus its share of the active set" is
> stale, and it was never a usable specification.** Live (non-terminal, unarchived) counts
> across `backlog`/`unstarted`/`started`: `workflow-skills backlog` **82**,
> `workflow-skills: Handler parity follow-ups` **14** — 96 in the two projects named for
> the repo. Four more projects are unambiguously this repo's work under names that do not
> say so: `Auto-pilot mode — /deliver-task + /auto-pilot` **18**, `autopilot-harness`
> **15**, `reviewer-quality` **5**, `reconcile-tasks` **1** — bringing it to **135**.
> `Deterministic-script extraction` (1) is ambiguous. Two read as in-scope and are not:
> `Plugin data-ops enhancement` (7) is **finplan's** plugin (PRE-325/327 are
> `scripts/finplan.py`), and `aiutopilot backlog` (2) is a separate repo.
>
> **Linear has no repo field, so project name is the only proxy and it lies in both
> directions.** A selector matching the name `workflow-skills` drops 39 issues; one
> matching "plugin-shaped" imports finplan's. **The live set must be an explicit,
> human-approved list of project ids — never a name pattern.** That decision belongs to
> [#511](https://github.com/bestdan/workflow-skills/issues/511); this task's export
> (#510) is team-wide regardless, so it is not blocked on the answer.
>
> Not urgent: the newest workflow-skills issue in Linear is PRE-823, created 2026-09-03.
> Nothing has been filed there since the 09-07 switch, so the two boards are parked rather
> than drifting.

**Labels must be provisioned before any issue lands** (task 2), or the state is
silently lost on arrival.

## Task

1. Export **every** issue (781 at plan time; ≥835 by 2026-09-12) to a date-named JSON file kept outside the plan
   folder — identifiers, titles, bodies, comments, labels, estimates,
   priorities, relations, sub-issue links, attachment URLs.
2. Import the active `workflow-skills` issues to GitHub: map status/priority/
   estimate to labels via the task 3 helper, recreate `blocks` edges natively,
   recreate sub-issue links.
3. Map Linear projects to GitHub milestones — half of all reads are
   project-scoped, so the grouping dimension must survive.
4. Record a Linear-key → GitHub-number mapping in the export file so historical
   branch names stay traceable.

Do **not** delete anything in Linear until task 10 decides.

## Acceptance Criteria

**Code-enforced**

- The export file contains every issue the paginated query returns, including archived, with comments and relations — asserted against the workspace's own count at export time, not against a literal (781 was the 2026-08-24 figure; ≥835 by 2026-09-12)
- A verification script asserts every imported issue has exactly one `status:` and one `auto:` label
- A verification script asserts the dependency edge count matches the source

**User-run**

- Spot-check five imported issues in the GitHub UI against their Linear originals — labels, blockers, milestone, comments
- Confirm the export file is stored outside `dev_docs/tasks/` and is not gitignored away by accident
