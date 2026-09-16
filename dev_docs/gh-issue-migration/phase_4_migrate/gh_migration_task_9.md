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

> **All four steps are done as of 2026-09-15.** Step 1's export is
> `$HOME/src/linear-export/2026-09-13-prethink.json` (810 issues, archived included).
> Step 2 landed all **125** selected issues on GitHub — 117 created as #617–#733 and 8
> reopened (#284, #288, #289, #295, #296, #297, #299, #302) — with the **27 `blocked_by`
> edges** and **15 sub-issue links** written by
> [#513](https://github.com/bestdan/workflow-skills/issues/513) and the whole board
> independently checked by [#514](https://github.com/bestdan/workflow-skills/issues/514).
> Step 3's project→milestone mapping came with the import. Step 4's mapping file is
> `$HOME/src/linear-export/2026-09-13-mapping.json`, **and it is the only copy**.
>
> **The Linear side is closed too**, by
> [#515](https://github.com/bestdan/workflow-skills/issues/515): 123 of the 125 originals
> carry a successor comment, a link attachment and `Canceled`. The 2 exceptions are
> PRE-503 and PRE-504, archived since 2026-08-01 — Linear refuses every mutation against
> an archived issue. That they reached the import at all is a defect in the **selection**
> (an archived issue keeps its state type, so #511's live-state filter passed them), and
> nothing has been filed for it.
>
> **"Do not delete anything in Linear until task 10 decides" still stands.** The issues
> are canceled, not archived, deliberately: archiving them would make the documented
> revert — a scripted un-cancel over the mapping file — fail, because an archived issue
> refuses `issueUpdate` too. Recovery would need `issueUnarchive` first, which nobody has
> written.
>
> **The plan file is no longer regenerable.** Regenerating re-runs the create-versus-reopen
> decision, and the eight reopen targets are now OPEN — which that decision refuses,
> correctly, as two live homes. Treat the export, the plan and the mapping all as
> provenance now.

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
