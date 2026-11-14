---
created: 2026-10-28
status: accepted
convention: ../../skills/task-dashboard/SKILL.md
---

# The score tables were renamed, with no aliases

## Context

Once one ranker scored every instance's Now
(`2026-10-27-one-now-ranker-for-every-instance.md`), its weights still lived
in two tables named for the old split: `[score.fleet]` for Ship's categories
and `[score.weights]` for Jira's terms. A GitHub issue's `prio:` label and a
Jira ticket's priority were two terms for one idea. Jira's `review` and
`blocked` weights did what tiers now do. There were two installs, both the
owner's.

## Decision

`[score.fleet]` became `[score.categories]`, and `[score.weights]` became
`[score.terms]` (priority, deadline, unblocks, stuck, age).
`[score.fleet].prio` merged into `[score.terms].priority`: a `prio:` label
and a Jira priority feed one term on one level scale, `prio:0` weighing as
Jira's Highest. Jira's `review` and `blocked` weights were dropped. A config
that names an old table is refused at load, naming its replacement
(`score.weights is renamed: use [score.terms]`). `tune` names each weight's
table in its proposals.

Landed in PR [#1241](https://github.com/bestdan/workflow-skills/pull/1241).
That PR scaled Jira's deadline, unblocks and stuck defaults to keep their
proportions against GitHub's 0.2 priority weight; those starting values are
left to `tune`.

## Consequences

- Good, because the table names say what the weights are, on every instance.
- Good, because there is no alias path to keep.
- Bad, because a config naming an old table stops loading until edited.
- Bad, because Jira issue cards outside Now (Waiting on me, Stale, Epics)
  show scores about 1/15 of their old size, and Blocking's order changed
  where a ticket was blocked.

## Revisit when

- The dashboard has installs the owner does not edit by hand.

## Confirmation

`ConfigTests` in `scripts/test_task_dashboard_fleet_now.py` refuses both old
table names with the replacement in the message.

## Alternatives

- **Accept the old names as aliases:** rejected, because a one-time edit to
  two configs costs less than keeping the alias path.
