---
created: 2026-10-03
status: accepted
convention: ../merge-autonomy.md
---

# Review-complete is a required `co-review` status on the reviewed head

## Context

Sixty days of merges across four repos
([`../research/2026-10-03-merge-oversight-history.md`](../research/2026-10-03-merge-oversight-history.md))
found 12 PRs merged while co-review was still running, which produced 10
follow-up fixes. The owner's own merge caught no defect that shipped. The
merges that outran review were human UI merges, so a rule an agent reads could
not have stopped them, and a check in a script or in prose binds only the
agents that run it.

[The merge-autonomy design](../designs/2026-10-03-graduated-merge-autonomy.md) (PR #952)
proposed this as Phase 0, the one change worth making whether or not agents
ever land anything. A blind `/consult` agreed at 85%, with the crux "whether the
merges that outran review were human UI merges". They were.

## Decision

`/co-review` posts a commit status, context `co-review`, on the head its round
reviewed (step 3's `headRefOid`): `pending` once the pre-flights pass, then
`success` when at least two reviewers ran and every other planned one was
skipped by configuration, `failure` otherwise. Each repo's `protect-main`
ruleset requires that context, so GitHub refuses the merge, from the UI or
`gh`, until the exact head carries `success`. A push leaves the new head
without a status, which binds the verdict to the SHA. Copilot's GitHub review
becomes advisory; the local `copilot` reviewer class is the one that gates.

workflow-skills keeps the admin bypass (`bypass_mode: always`) that the release
workflow's `RELEASE_TOKEN` relies on. The design's open question 2 proposed
moving it to a dedicated actor; the owner chose on 2026-10-31 to leave it
(#1314). So in this repo the check binds agents and non-admin merges, and an
admin merge can skip it. The audit below makes each skip visible afterwards.

## As shipped

- The status, PR #1339 (issue #1306). The rules are in
  `skills/co-review/references/status.md`.
- The required check in workflow-skills, documented in `dev_docs/releasing.md`
  by PR #1356 (issue #1314). finplan, agent-guidance and dotfiles also require
  `co-review` on `main` (checked on 2026-11-11 with
  `gh api repos/bestdan/<repo>/rules/branches/main`).
- The design said the status is never posted on co-review's own fix commit, so
  a round that fixes anything costs another round. PR #1409 (issue #1407)
  relaxed that. `scripts/co-review-carry.py` carries a passing round's
  `success` to its fix commit, marked as carried, when the round applied no
  blocking fix, its evidence asks for no further round, and the fix is exactly
  one commit on the reviewed head. The lander tells a carried head from a
  reviewed one by `carried_from`.
- The design's "success when every planned reviewer class ran" shipped as "at
  least two ran": a run that skips down to Claude alone is a `failure`.

## Consequences

- Good, because the gap that produced the 10 follow-up fixes is closed by
  GitHub, not by anyone remembering a rule.
- Good, because the lander can read "review complete on this SHA" as a status
  instead of parsing a comment.
- Bad, because the status is self-attested. Every agent posts as `bestdan`, so
  the authoring agent could post `success` without a completed round. The
  weekly audit catches a missing status, not a forged one.
- Bad, because in workflow-skills an admin merge, `dli gh merge` with the
  owner's token included, skips the check silently at merge time.

## Revisit when

- `scripts/merge-audit.py --mode review` lists a skip that was not deliberate,
  or the class "follow-up fix to a PR merged mid-review" comes back: a path
  around the check exists.
- The release workflow can push with an actor other than the admin role (a
  GitHub App or deploy key), which would let the bypass move off admins.
- Co-review stops running in the session that holds the posting credential.
  Pinning the check to an App's `integration_id` would then add independence.

## Confirmation

The ruleset itself. Three checks hold it up:

- **Structural**, once per repo: a PR whose `co-review` status is `pending` is
  refused, in the UI and with `gh pr merge`. #1314's acceptance asks for this
  as a user-run step; in workflow-skills an admin merge succeeds by design.
- **Audit**: `scripts/merge-audit.py --mode review` (PR #1368, issue #1322)
  lists merged PRs whose final head has no `co-review` `success`, named
  `bypass`, `ungated` or `unknown`. The repo schedules nothing; the script's
  docstring gives a `/loop 7d` invocation.
- **Outcome**: follow-up fixes to PRs merged mid-review should stay at zero.

## Alternatives

- **Enforce it in the lander or in prose.** Rejected: the early merges were
  the owner's UI merges, which no script or rule an agent reads can stop.
- **Move workflow-skills' bypass to a dedicated actor now.** Not taken; the
  owner kept the admin bypass on 2026-10-31 and accepted the after-the-fact
  audit instead.
