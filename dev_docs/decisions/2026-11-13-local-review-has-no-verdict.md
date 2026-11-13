---
created: 2026-11-13
status: accepted
convention: ../local-review.md
---

# A local review carries no verdict

## Context

The review page had an `Approve` button, an `approved` payload field, and a
stdout block telling the agent to push the PR and mark it ready. A threaded
review's buttons say what happens to the session, not what the reviewer thinks
of the code, and one surface should not carry two models of what a submit
means.

## Decision

No mode carries a verdict. The `Approve` button, the `approved` field, the
`REVIEW APPROVED` header and its push-the-PR instruction are gone in every mode.
Threads mode has `Submit` (send this round to the agent) and `Finish` (end the
review); one-shot and human-only have `Submit review`. Making a PR ready is a
separate request the user makes in chat. Landed in [#417](https://github.com/bestdan/workflow-skills/pull/417), before the thread
work.

## Consequences

- Good, because a local review cannot trigger an outward action.
- Bad, because a user who wants "looks good, ship it" has to say so in chat.

## Revisit when

- A consumer needs a machine-readable approval from the human round, for
  example a `/co-review` or `/deliver-task` gate on it.

## Confirmation

`scripts/test-local-review.sh` asserts the payload has no `approved` key and
stdout prints no `APPROVED:` line. The server builds the persisted payload
field by field, so an `approved` key in a hand-crafted submit never reaches
`--out`.
