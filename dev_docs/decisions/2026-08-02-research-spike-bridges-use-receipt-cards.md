---
created: 2026-08-02
status: accepted
convention: ../research_spike.md
---

# research-spike bridges to the task loop through receipt cards, with no reconciliation

## Context

Where the workflow-skills task loop is configured, a deferral could go to a
tracker (`/add-task`) and a decision's evidence could be a plan
(`plan-with-docs`, `/push-plan`). Both first drafts broke the
destination-must-exist rule
([record](2026-08-02-research-spike-destinations-must-exist.md)): tracker
handlers return URLs, not paths, and `/push-plan` deletes plan directories
after migration, so a pointer at either rots.

The design (`2026-08-01-research-spike-skill.md`, deleted on graduation)
routed both through cards. It landed in
[#330](https://github.com/bestdan/workflow-skills/pull/330).

## Decision

Both bridges write a `kind: receipt` card under `tracks/<track>/obligations/`
carrying `url:` (required) and optional `handler:` and `tracker_id:`. The
obligation's `destination:` or the decision's `decided_in:` points at the card,
which is a path that exists; the URL lives in content the offline validator
never checks. The `defer` procedure offers `/add-task` only when the repo's
merged task config resolves to an external tracker handler (`gh-issue`,
`jira`, `linear`), since `repo-pr` returns no URL. Neither bridge fires on its
own. Completing a tracker task does not discharge the local obligation, and
there is no `sweep` verb in v1.

The design said a plan-handoff receipt is "updated to the tracker URL" when
`/push-plan` migrates the plan. As shipped, `/push-plan` has no step that
finds or updates receipt cards; whoever runs it updates `url:` by hand.

## Consequences

- Good, because the path invariant holds for bridged work with no special
  case in the validator.
- Good, because the card survives `/push-plan`, being outside the plan
  directory it deletes.
- Bad, because the ledger reports declared debt as last hand-updated: a closed
  tracker issue leaves its obligation open until someone discharges it.
- Bad, because the plan-handoff receipt's URL goes stale unless a person
  remembers to update it.

## Revisit when

- Stale receipts are found in practice, obligations left open after their
  tracker work closed, often enough to justify the `sweep` verb (walk
  receipts, check tracker state, propose discharges). It would pair with the
  deferred `status --json`
  ([E](2026-08-14-research-spike-lean-b-d-e-deferred.md)).
- `/push-plan` gains a step that can find receipt cards.

## Confirmation

`validate` requires `url:` on every receipt. The bridge procedures are
SKILL.md conventions with nothing enforcing them.

## Alternatives

- **Point `destination:` at the tracker URL:** not a path, so it cannot be
  checked to exist.
- **Point `decided_in:` at the plan directory:** deleted on the first push;
  now an error.
- **Auto-discharge on tracker completion:** needs network access the
  validator does not have, and turns a human act into a generated one.
