---
title: "Pilot evaluation gate: keep, extend, or revert"
priority: high
size: 2
status: done
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_9
related_files:

tags: [evaluation, gate]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Pilot evaluation gate: keep, extend, or revert

## Decision — KEEP. Decided 2026-09-15, the day the clock would have opened.

**The verdict is keep: `workflow-skills` stays on `gh-issue`, `finplan` stays on
Linear, and nothing is scheduled to migrate next.**

**The gate was not run to its two-week term, and that was the decision too.** The
clock was due to run 2026-09-15 → 2026-09-29. It was closed on day 0 after
working through what the remaining fourteen days would actually buy, and the
answer was: less than they cost.

**Why the wait had little value.** The gate was specified as a comparison against
a `finplan` Linear control, but there is no held-still baseline to compare
against. Both boards keep moving — issues resolve, new ones arrive — so a
verdict on 09-29 would be scoring a two-week-old snapshot against a board that
had moved on from it. The task file already conceded the comparison is "not
controlled" and "a judgement, not a measurement"; the missing step was noticing
that a judgement does not need fourteen more days of a moving target to be made.
The asymmetry recorded below made it worse rather than better: this repo lost
unattended auto-pilot on 09-07 and the control did not, so the one dimension the
extra time would have measured was the dimension already known to be unfair.

**Why the wait had real cost.** Holding the gate open meant holding the revert
path open, and the revert path was what kept the 123 migrated originals
unarchived in Linear. That was not free: Linear's free plan counts every
**non-archived** issue of every state, so 123 cancelled-but-unarchived issues
still consumed 123 of the 250 and the workspace sat at ~253 — **over the cap,
with Linear refusing to create new issues.** Archiving took it to ~130. The cap
relief this whole migration exists for was being withheld, for fourteen days, to
preserve an escape hatch the amendment below had already measured as
impractical.

**So the decision was taken in favour and executed.** The 123 originals are
archived as of 2026-09-15 (`Archived 123, failed 0`).

**What "keep" settles, and what it does not:**

- **Keep, not extend.** `finplan` is not scheduled to migrate. It remains on
  Linear, which is what keeps the Linear handler and its four Linear-only
  commands alive under task 11's rule.
- **Not-extend removes the migration assets' justification.** `linear-import.py`,
  `linear-verify.py` and `linear-successor.py` were kept against the possibility
  that this gate said extend. It did not, so that reason is gone and retiring
  them is live in [task 11](../phase_5_cleanup/gh_migration_task_11.md). This is
  the one downstream consequence the verdict creates rather than closes.
- **It does not settle task 13.** `/auto-pilot` still does not support
  `gh-issue`, so this repo is still outside unattended auto-pilot. That was a
  reason to distrust the gate; it is not resolved by closing it, and it now has
  no gate standing behind it as a reason to wait.

**The five diagnostic questions below were not answered individually, and are
retired unanswered rather than left looking owed.** They were the instrument for
a two-week measurement that was not taken.

## Context

The pilot is deliberately a live A/B: `workflow-skills` on GitHub Issues,
`finplan` still on Linear. The comparison is **not controlled** —
`workflow-skills` is both the instrument and the subject, so handler bugs and
tracker friction will be hard to tell apart, and the two repos carry different
workloads. Treat it as a judgement, not a measurement.

The original doubt this whole exercise started from was _"maybe I didn't spend
enough time setting it up"_ — this gate is where that gets answered.

## Task

After roughly two weeks of real use, assess and decide:

- Did claim racing stop?
- Did any issue close without passing `needs_review` (reconciler rule 3)?
- Did the search rate limit (**30/min**, the binding constraint — core is
  5,000/h and irrelevant here) bite under `/auto-pilot` fan-out?
- Did label state drift, and did the reconciler catch it?
- What broke that the spike did not predict?

Then decide: extend to `finplan` and `aiutopilot`, hold, or revert. Record the
decision and its reasons.

## Acceptance Criteria

**Code-enforced**

**User-run**

- A written decision exists naming which of the three outcomes was chosen and why
- If extending: the remaining repos are scheduled. If reverting: the reason is specific enough to be actionable
