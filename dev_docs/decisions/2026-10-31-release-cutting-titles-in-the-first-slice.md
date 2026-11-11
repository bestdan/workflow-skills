---
created: 2026-10-31
status: accepted
---

# Release-cutting titles may land in workflow-skills' first `auto` slice

## Context

In workflow-skills a `feat` or `fix` merge cuts a release that every installed
user pulls, so an agent landing one can ship a bad version before anyone
reverts it. Keeping those types out shrinks the slice sharply. On the 60-day
history
([`../research/2026-10-03-merge-oversight-history.md`](../research/2026-10-03-merge-oversight-history.md)),
non-releasing types alone would have cleared about 12 PRs (~5%); allowing
`feat`/`fix` where the path tier allows would have cleared about 52 (~22%).
Shadow mode graduates a repo at 30 would-land PRs, which at those rates is
about 5 weeks under the wider slice and about 5 months under the narrow one.

[The merge-autonomy design](../designs/2026-10-03-graduated-merge-autonomy.md) (PR #952)
put this as its open question 1 and recommended the narrow option, because a
bad release reaches every user before the revert. A blind `/consult` picked
the wider option at 70%, with the crux "how many consumers besides the owner
pull releases".

## Decision

Option B. The owner chose on 2026-10-31 to let `feat` and `fix` land in
workflow-skills when the path tier allows. A bad landing can ship a release,
and `/revert-landing` is the undo: it opens the revert with a title type that
cuts the next release.

## As shipped

- `allowed_types: [feat, fix, perf, refactor, test, chore, ci, build, style,
  docs]` in `.github/merge-policy.yml`, PR #1354 (issue #1313).
- `/revert-landing` and `scripts/revert-landing.py`, PR #1437 (issue #1323).
- The path tier still decides most of it: `skills/**`, `commands/**` and the
  plugin metadata are always human, so a releasing `auto` PR in this repo is a
  change under `scripts/**` or `test/**`.

## Consequences

- Good, because shadow data accrues at the wider slice's rate, so the 30-PR
  gate is reachable in weeks.
- Bad, because a bad `auto` landing reaches every user who updates before the
  revert's release, and the revert costs a second release.

## Revisit when

- The plugin gains a meaningful install base beyond the owner, which is the
  consult's crux: narrow `allowed_types` to non-releasing types.
- `scripts/merge-audit.py --mode landed` shows a landed `feat`/`fix` reverted
  for a defect no gate could have caught.

## Confirmation

`allowed_types` in the policy, which `merge-tier.py` applies; the policy is
always human-tier.

## Alternatives

- **Option A, non-releasing types only.** Rejected by the owner: too few PRs
  (~5%) for useful shadow data, and about five months to graduate.
