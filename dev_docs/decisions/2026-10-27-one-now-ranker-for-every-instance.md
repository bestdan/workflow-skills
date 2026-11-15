---
created: 2026-10-27
status: accepted
convention: ../task-dashboard.md
---

# One Now ranker for every instance, and being a fleet means ingesting

## Context

Now used to be two rankers, picked by `fleet_views.enabled`. A Jira instance
ranked the owner's tickets by `score.score`; a fleet instance ranked
sessions, PRs, issues and close-outs into Ship's tiers, with Jira tickets in a
separate lane below. On the work laptop, measured 2026-10-27, the `prs` table
held 65 PRs authored by the owner and 27 asking for the owner's review, and
only 18 and 6 of them carried a ticket key, so 68 PRs reached no ranking at
all. Every ranking change (the Now modes, `tune`, a new category) had to land
twice or reached one instance only.

"Fleet" also merged two unrelated switches: `github.fleet`, a choice about
how much to read from GitHub, and `ingest.machines`, collecting from other
hosts.

## Decision

Every instance builds Now the same way: each source returns
`candidate.Candidate` rows (`candidate.py`), linked candidates fold into their
parent, and `fleet_now.rank` puts each in a tier and orders within it under
one set of weights. Jira tickets are one source among GitHub PRs, GitHub
issues, sessions, workstreams and, since PR #1264, Linear issues. A Jira
ticket's tier comes from its status: in progress is `started`, and to do in
the active sprint is `ready`, which sits in "the rest". `tune` and "why not
now" read the same candidates, so they work on every instance.
`fleet_views.enabled` now means only "this instance ingests snapshots from
other hosts". The fleet tabs, Now as the home page and the Stale lanes follow
`fleet_views.fleet_pages`: owner scope or any ingest machine.

Landed in PR [#1233](https://github.com/bestdan/workflow-skills/pull/1233)
(the seam), [#1237](https://github.com/bestdan/workflow-skills/pull/1237)
(Jira as a source, the Jira lane removed),
[#1241](https://github.com/bestdan/workflow-skills/pull/1241) (`tune` on
every instance) and [#1242](https://github.com/bestdan/workflow-skills/pull/1242)
(being a fleet narrowed to ingest), under issue #1225.

## Consequences

- Good, because a PR with no ticket key reaches the work laptop's Now, and a
  ranking change lands once for every instance.
- Good, because a new source is one more function returning Candidates.
- Good, because the Now mode button and Health's "Why not now" section now
  show on a `github.scope = "me"` instance too.
- Bad, because a ticket and a PR now compete in one order, so their weights
  have to be comparable. Tiers contain this: weights only order items within
  a tier, so a weight that is off between sources cannot move an item out of
  its tier.

## Revisit when

- The owner decides whether every instance should get the Pull requests and
  Issues tabs in place of Waiting on me and I'm waiting on. Until then
  `fleet_pages` keeps the tab and home-route callers on "owner scope or any
  ingest machine", apart from `enabled`; settling that question is what lets
  the two merge or part for good.
- A source needs a tier the shared ladder cannot express, so ranking it
  would mean a second ranker again.

## Confirmation

`scripts/test_task_dashboard_jira_now.py` ranks Jira tickets through the
shared ranker. `FleetSwitchTests` drives every `enabled` and `fleet_pages`
caller under the four scope-by-ingest combinations. The seam golden
(`scripts/task-dashboard/fixtures/now/golden.json`) pins the agent view's
output.

## Alternatives

- **Keep a ranker per instance type:** rejected, because it left 68 PRs
  unranked on the work laptop and doubled every ranking change.
- **Keep "fleet" as owner scope or ingest:** rejected, because the GitHub half
  is a decision about read width, which `github.scope` now owns
  (`2026-10-27-github-scope-replaces-github-fleet.md`).
