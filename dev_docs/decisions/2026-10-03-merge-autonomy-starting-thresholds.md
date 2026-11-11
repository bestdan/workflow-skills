---
created: 2026-10-03
status: accepted
convention: ../merge-autonomy.md
---

# Starting thresholds for merge autonomy: size cap, review-queue limit, consult bar

> [!NOTE]
> 2026-11-11: the thresholds were proposed on 2026-10-03, the record's date. Two were set
> later, as shipped: the consult bar with the lander on 2026-11-02, and the
> review-queue limit, at 8 rather than the proposed 5, on 2026-11-06.

## Context

Three numbers bound the `auto` slice and the owner's queue before shadow data
exists to calibrate them.
[The merge-autonomy design](../designs/2026-10-03-graduated-merge-autonomy.md) (PR #952)
proposed them as its open question 3:

- **Size cap, 300 changed lines.** Intercom's reviewer refuses large PRs, per
  the field survey
  ([`../research/2026-10-03-agent-merge-autonomy-field.md`](../research/2026-10-03-agent-merge-autonomy-field.md)).
- **Human-tier WIP limit, 5.** In the merge history, unattended runs opened PRs
  faster than the owner drained them; Osmani suggests 3 to 5.
- **Consult bar, 85%.** Co-review step 9 applies a consult-decided call at 70%;
  the design asked for 85%, consult's "deciding fact checked" level, before
  such a call may leave a PR `auto`, because `/consult` is Claude-only and its
  agreement correlates with the author's. This bar was left unresolved: the
  only blind consult on it ran on an earlier mechanism, where 85% was the bar
  for the call to apply at all, and agreed at 60%, with the crux "whether 'calls
  for you' items are genuinely reversible and confined to the technical class".

## Decision

Start at a 300-line size cap, a review-queue limit of 8, and an 85% consult
bar, and change each only on shadow or landing data.

## As shipped

- **Size cap**: `DEFAULT_SIZE_CAP = 300` in `scripts/merge-tier.py`, PR #1342
  (issue #1309), and `size_cap: 300` in `.github/merge-policy.yml`, PR #1354
  (issue #1313). Additions plus deletions; over it, the PR is human.
- **Review-queue limit**: the top-level `review_queue_limit` key in a repo's
  task config, default 8, overridable per run with `review-queue-limit=<n>`;
  PR #1436 (issue #1320). `/do-tasks` checks it before any claim and
  `/auto-pilot` before each task, with `scripts/review-queue.py --limit`. It
  counts open, non-draft PRs whose tier is `human` or `unknown`, the set the
  review-queue issue lists (`scripts/review-queue.py`, PRs #1411 and #1414,
  issue #1321). The design proposed 5; the owner set 8, leaving 3 PRs of
  headroom over the queue of 5 waiting at the time.
- **Consult bar**: the `consult_bar` policy key, default 85
  (`DEFAULT_CONSULT_BAR` in `merge-tier.py`), applied by `land-pr.py` from
  PR #1346 (issue #1315). workflow-skills' policy does not set it, so the
  default holds. `/promote-tasks`' Intent confirmation reuses the same bar
  (`scripts/tier-forecast.py`).

## Consequences

- Good, because each number is a policy or config key, so tuning it is a
  one-line, owner-landed change rather than a code change.
- Bad, because none was measured here. The consult bar in particular rests on
  a consult that agreed at 60% with a different mechanism.

## Revisit when

- Shadow comments show many otherwise-landable PRs held only by `size_cap`
  (raise it) or landed PRs over some size needing follow-up fixes (lower it).
- The review queue sits at the limit for days while the owner drains it slowly
  (lower it), or the gate stops claiming while the owner has capacity (raise
  it).
- A consult-decided call that kept a PR `auto` is later overturned: raise
  `consult_bar`, or stop letting consult keep a PR `auto`.

## Confirmation

`scripts/test_merge_tier.py` covers the size cap and the `consult_bar`
validation, `scripts/test_land_pr.py` the bar's demotion, and the
`review-queue.py` tests the limit.
