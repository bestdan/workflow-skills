---
created: 2026-10-28
status: accepted
convention: ../task-dashboard.md
---

# Blocked items get a last tier of their own on Ship

## Context

Jira's score sank a blocked ticket with a −10 `blocked` weight. Under the
shared ranker weights only order items within a tier, so that weight could
only reorder one tier. Within a tier the category weight still counts: a
blocked `quick_win` (weight 10) would sort above an unblocked `ready` (1.5).
Zeroing the category term is not enough either, because `prio` and `age` can
still lift a blocked item by up to 1.3, past an unblocked `own_pr` (1). The
dashboard's standing rule is that blocked work sinks but stays visible.

## Decision

A blocked item goes to a final Ship tier, "blocked", after "the rest",
whatever its category, and its why line reads `ship: blocked`. Blocked means
any of `score.is_blocked`'s Jira signals (the `blocked` label, Flagged, a
status in `jira.blocked_statuses`, an unresolved inward Blocks link); for a
GitHub issue, the `blocked` label or an open native `blocked_by`; and, since
PR #1264, for a Linear issue, an open blocking issue or the `blocked` label.
Jira's `blocked` weight is gone. A folded PR that is ready to merge still
lifts a blocked parent out of this tier
(`2026-10-28-linked-prs-fold-into-their-parent.md`).

Landed in PR [#1237](https://github.com/bestdan/workflow-skills/pull/1237),
which added the native `blockedBy` field to `github_fleet.py`'s issue query.

## Consequences

- Good, because no weight can lift a blocked item above unblocked work.
- Good, because blocked work stays on Now, last, where the owner can still
  see and unblock it.
- Bad, because a blocked item's category no longer says where it sits; the
  why line has to.

## Revisit when

- Blocked items crowd unblocked ones off the list's LIMIT, which would argue
  for hiding them or capping the tier.
- A source has a blocked signal that is too noisy to sink an item on.

## Confirmation

`JiraBlockedTests`, `GithubIssueBlockedTests` and `BlockedTierOrderTests` in
`scripts/test_task_dashboard_jira_now.py`; the Linear case in `NowTests` in
`scripts/test_task_dashboard_linear_now.py`.

## Alternatives

- **Hide blocked items:** rejected, because blocked work should sink but stay
  visible.
- **Zero the category term:** rejected, because `prio` and `age` still lift a
  blocked item past `own_pr`.
