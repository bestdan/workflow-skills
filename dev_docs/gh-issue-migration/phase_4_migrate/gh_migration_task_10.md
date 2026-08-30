---
title: "Pilot evaluation gate: keep, extend, or revert"
priority: high
size: 2
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_9
related_files:

tags: [evaluation, gate]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Pilot evaluation gate: keep, extend, or revert

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
