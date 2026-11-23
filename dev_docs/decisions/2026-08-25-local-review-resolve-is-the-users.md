---
created: 2026-08-25
status: accepted
convention: ../local-review.md
---

# Only the user resolves a review thread

## Context

Resolving a thread hides it from the diff view. A wrong resolution hides a
comment the user still cares about, and the failure is silent: Reopen exists,
but only if the user goes looking.

## Decision

Resolve is the user's click in the page, and the agent never calls `/resolve`.
The agent proposes resolution in reply prose ("resolving unless you object").
Resolution is a bit, not a deletion: a resolved thread stays in the store and in
every later round's `threads`, and a per-file "N resolved" toggle shows it again
with a Reopen control. The agent holds the same bearer token, so this rests on
the skill's instructions, not on the server. Landed in [#420](https://github.com/bestdan/workflow-skills/pull/420) and [#422](https://github.com/bestdan/workflow-skills/pull/422).

## Consequences

- Good, because resolution stays the human's signal that a point is settled.
- Good, because the agent-facing write surface is one verb, `/reply`.
- Bad, because the user resolves every thread by hand.

## Revisit when

- Resolving by hand proves tedious in real use. Extending `/resolve` to the
  agent is additive.

## Confirmation

`skills/local-review/SKILL.md` step 4 forbids the agent the call. The server
does not enforce it, since both callers share one token.

## Alternatives

- **Agent-side resolve:** doubles the write surface before the loop had real
  use, and a wrong auto-resolve is silent.
