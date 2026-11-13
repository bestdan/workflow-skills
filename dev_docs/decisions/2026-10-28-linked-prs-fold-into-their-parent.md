---
created: 2026-10-28
status: accepted
convention: ../task-dashboard.md
---

# A linked PR folds into its ticket or issue, and lends it its tier

## Context

Once Jira tickets and PRs shared one Now ranker
(`2026-10-27-one-now-ranker-for-every-instance.md`), a ticket and the PR that
implements it sat on Now as two cards with the same next step, and snoozing
one left the other on the list. Jira's old `review` term raised a ticket by
an additive bump when its linked PR was approved or green. Inside fixed
tiers that bump was too small to put a ticket whose PR is ready to merge
above a ticket that had only started.

## Decision

A Ship PR whose links name an item Ship also ranks has no card of its own. A
link is the Jira key in its title or branch, each GitHub issue it closes
(`closingIssuesReferences`), or, since PR #1264, a ranked Linear issue's key
in its title or branch. The PR folds into that item, the first such link
winning, and shows on the parent's card as a `↳` line with its evidence. The
parent takes the PR's tier when that tier is ready to merge or the review
queue and beats its own, and its why line names the PR
(`ship: ready to merge via o/r#12`). Every overlay action on the card acts on
the parent's key. The ticket is the work, and the PR is how far along it is.

Two details settled while building it differ from the design text. A ready
PR lifts a blocked parent out of the blocked tier, because a mergeable PR
means the work is done. Only PRs Now ranks fold: a draft, or one waiting on
CI or review, is still listed on its Jira ticket's card as a `↳` line that
changes no tier or score. A PR keeps its own card when Ship does not rank its
parent, or when the include filter leaves the parent out.

Landed in PR [#1239](https://github.com/bestdan/workflow-skills/pull/1239);
the status-to-tier mapping it raises landed in
[#1237](https://github.com/bestdan/workflow-skills/pull/1237).

## Consequences

- Good, because one card carries one piece of work, and one snooze hides it.
- Good, because a ticket whose PR is ready to merge sits in ready to merge.
- Bad, because ignoring a GitHub issue hides its folded PRs too: the action
  takes the bundle.
- Bad, because a PR linking two ranked items shows only under the first.

## Revisit when

- PRs that name several tickets become common enough that "the first link
  wins" hides work.
- A tracker links PRs by something other than a key in the title or branch,
  or a closing reference.

## Confirmation

`TicketFoldTests` and `IssueFoldTests` in
`scripts/test_task_dashboard_jira_now.py`, and the fold tests in `NowTests`
in `scripts/test_task_dashboard_linear_now.py`.

## Alternatives

- **One card for each:** rejected, because the pair share a next step and a
  snooze on one leaves the other.
- **Keep the additive `review` bump:** rejected, because inside tiers it
  cannot move a ticket past one that only started.
