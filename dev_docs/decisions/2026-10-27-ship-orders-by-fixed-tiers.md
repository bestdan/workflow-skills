---
created: 2026-10-27
status: accepted
convention: ../task-dashboard.md
---

# Ship orders Now by fixed tiers, with weights only inside a tier

## Context

Now's single score let a backlog of 24 tied `auto:human-review-needed`
issues outrank in-flight work. Kanban's "stop starting, start finishing"
puts delay in wait states, and an unfinished task degrades work on the next
one (Leroy 2009, attention residue), so Ship, the default mode, has to lead
with what is closest to done. A weighted sum cannot guarantee that: a large
enough `prio` or `age` lifts backlog over a finished session.

## Decision

Ship's order is a fixed sequence of tiers in code (`fleet_now.TIERS`): a
session that asked; close-outs; ready to merge (`quick_win`, and
`own_approved`, my PR approved with CI green); the review queue; started;
the rest; and, last, blocked
(`2026-10-28-blocked-items-get-a-last-tier.md`). `score.fleet_score` orders
items only within a tier, and a tie goes to the longer wait. A session that
asked leads, above close-outs, because it is the one item whose wait stalls
work already in progress. An approved own PR with CI pending stays in the
rest as `own_pr`. `score.FLEET_CATEGORIES` gained `close_out` and
`own_approved`, each with a default weight, now in `[score.categories]`
(`2026-10-28-score-tables-renamed-without-aliases.md`). `tune` still
proposes weights, and they act within a tier.

Landed in PR [#1221](https://github.com/bestdan/workflow-skills/pull/1221).
The blocked tier came later, with the shared ranker, in
[#1237](https://github.com/bestdan/workflow-skills/pull/1237), and a folded
PR can lift its parent's tier
(`2026-10-28-linked-prs-fold-into-their-parent.md`).

## Consequences

- Good, because no weight can put backlog above a finished session or a
  mergeable PR.
- Good, because a weight that is off between sources (a Jira ticket and a
  GitHub PR) cannot move an item out of its tier.
- Bad, because the tier order itself takes a code change; `tune` cannot
  reach it.

## Revisit when

- "Why not now" answers keep skipping a whole tier, which would mean the
  tier order, not a weight, is wrong.

## Confirmation

`RankTests` and `CloseOutTests` in `scripts/test_task_dashboard_fleet_now.py`,
and `BlockedTierOrderTests` in `scripts/test_task_dashboard_jira_now.py`.

## Alternatives

- **Large category weights in one score:** rejected, because `prio` and
  `age` can still cross a boundary, and the order is harder to read off a
  card.
- **Close-outs above a session that asked:** the owner listed close-outs
  first; rejected, because the asking session blocks work in progress.
