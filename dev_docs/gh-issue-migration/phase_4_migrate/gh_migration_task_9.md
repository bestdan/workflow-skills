---
title: Export Linear and import the active workflow-skills issues
priority: high
size: 5
status: active
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

> **Settled 2026-09-13 — the counting arguments earlier revisions made here are over.**
> "84 backlog issues plus its share of the active set" was never a usable specification,
> and neither were the 96-by-name and 135-by-content estimates that replaced it: both
> predate an export. The selected set is **125 live issues** (state type `backlog`,
> `unstarted` or `started`), enumerated by
> [#511](https://github.com/bestdan/workflow-skills/issues/511) and realised in
> `$HOME/src/linear-export/2026-09-13-import-plan.json`. **Anything computing a count now
> reads that file, not this one.** `autopilot-harness`'s 15 are excluded by the
> [#508](https://github.com/bestdan/workflow-skills/issues/508) deferral.
>
> **The reasoning under those numbers still holds: Linear has no repo field, so project
> name is the only proxy and it lies in both directions.** A selector matching the name
> `workflow-skills` drops issues; one matching "plugin-shaped" imports finplan's
> (`Plugin data-ops enhancement` is finplan's plugin — PRE-325/327 are
> `scripts/finplan.py`). **That is why the selection is an enumerated list of project
> names and explicit keys, never a pattern**, and why nothing downstream may re-derive it;
> the argument now lives with the code, in `commands/handlers/assets/linear-import.py`'s
> header.
>
> Not urgent: the newest workflow-skills issue in Linear is PRE-823, created 2026-09-03.
> Nothing has been filed there since the 09-07 switch, so the two boards are parked rather
> than drifting.

**Labels must be provisioned before any issue lands** (task 2), or the state is
silently lost on arrival.

> **The import ran on 2026-09-13 — step 2 is half done and step 1 is finished.** All
> **125** selected issues are on the GitHub board: 117 created (#617–#733) and 8 reopened
> (#284, #288, #289, #295, #296, #297, #299, #302), each with its labels, milestone,
> provenance footer and comment transcript, and one assignee. Verified by reading the
> board rather than the run's own log.
>
> **What step 2 still owes is the graph**: no `blocked_by` edge and no sub-issue link has
> been written — 27 and 15 of them respectively, and that is
> [#513](https://github.com/bestdan/workflow-skills/issues/513). Step 4's mapping is
> written, at `$HOME/src/linear-export/2026-09-13-mapping.json`, and it is the only copy.
>
> **The plan file is no longer regenerable**, which the earlier note above does not say.
> Regenerating re-runs the create-versus-reopen decision, and the eight reopen targets are
> now OPEN — which that decision refuses, correctly, as two live homes. Treat the export,
> the plan and the mapping all as provenance now.

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
