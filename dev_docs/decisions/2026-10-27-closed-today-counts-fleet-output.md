---
created: 2026-10-27
status: accepted
convention: ../task-dashboard.md
---

# "Closed today" counts the fleet's output, from one search per sync

## Context

Little mode borrows from Amabile and Kramer's _The Progress Principle_: small,
visible progress drives motivation. The design,
`dev_docs/designs/2026-10-26-now-modes.md` (deleted on graduation), put a
"closed today: N" header over Little's list. The mirror could not supply the count:
`github_fleet.CLOSED_QUERY` covers only the owner's PRs, and the open-issue
mirror drops closed issues.

## Decision

The fleet sync step runs one GraphQL call per sync, two `issueCount`
searches per configured owner, and stores the `closed_today` row: PRs merged
and issues closed since local midnight, by anyone: the fleet's output rather
than the owner's clicks. The sync runs that step only with
`github.scope = "owner"` (`sync.fleet_enabled`), so a `"me"` instance has no
`closed_today` row. `github_fleet.closed_today` has a `"me"` variant that
counts only the owner's merged PRs and closed assigned issues, but the sync
never calls it. The count is in the agent view's notes for Little, and only
when the row counts from today's local midnight.

That differs from the design: the page does not show it. PR
[#1224](https://github.com/bestdan/workflow-skills/pull/1224) shipped the
header line, and PR
[#1259](https://github.com/bestdan/workflow-skills/pull/1259) removed it
while tightening Now's cards, leaving the count to agents. The unused `me` variant
came with `github.scope` in PR
[#1234](https://github.com/bestdan/workflow-skills/pull/1234).

## Consequences

- Good, because the search costs one call and no page of nodes.
- Good, because an agent asked "what did we get done today?" has the number.
- Bad, because the owner no longer sees the progress cue the design wanted
  from it.

## Revisit when

- The owner wants the count back on the page.
- A per-day count proves misleading across time zones, since it starts at
  the syncing host's local midnight.

## Confirmation

`scripts/test_task_dashboard_fleet_gh.py` covers the `closed_today` search,
and `test_closed_today_reads_todays_count_only` in
`scripts/test_task_dashboard_fleet_now.py` covers reading only today's
count.

## Alternatives

- **Count from the mirror:** rejected, because it holds only the owner's
  closed PRs and drops closed issues.
