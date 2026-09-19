---
created: 2026-09-19
status: accepted
---

# No typed call for "which tool should do this job"

## Context

An agent decides, many times a session, whether a small job wants ordinary code, a
typed model call, or its own reasoning. The proposal was to answer that with a typed
call cheap and fast enough to use reflexively — a `pick-tool` command an agent could
reach for without thinking about it.

[`../research/2026-09-19-jev-tool-routing.md`](../research/2026-09-19-jev-tool-routing.md)
measured it against 36 routing decisions this repo already shipped. **Both sides
scored 36/36, three times each**: a two-rung ladder over Jev signals, and three Claude
subagents given only a blind case list and the three-line table below.

What separates them is not accuracy but what the score cost. The ladder needed two
thresholds fitted to this very set and cross-validates at 97%; the agents needed three
lines of prose, no fitting, and agreed with one another on all 36 cases. The agents
also caught an error in the labels they were being graded against — one case the
ladder got wrong in the same direction the label did.

The forces: the table is free and permanently in an agent's context; the call costs a
round trip, an API key, and a dependency on a third party.

## Decision

Do not build the routing tool. Carry the three-line table in guidance instead:

> - computable exactly from what you have → **write code**
> - one of a fixed set, a yes/no, or a point on a scale, judged from text you already
>   have → **a typed call**
> - text a human reads, or it needs steps or fetching → **reason about it yourself**

Keep `scripts/jev-pick-tool.py` as a dev-only instrument. The negative result is only
as good as its labels and its ladder, and both have to stay inspectable and re-runnable
for it to mean anything a year from now.

## Consequences

- **Good, because** the cheapest option won on the evidence, and an agent that would
  have paid a round trip per decision now pays nothing.
- **Good, because** the probe surfaced a defect that outlives it: a precedence rule
  argued from first principles, resting on a signal whose per-label spread nobody
  checked, cost 42 points. That is a review question for any future Jev ladder.
- **Bad, because** the measurement rests on 36 cases written by one person from one
  repository, and a ceiling effect means it can support "the call is not needed" but
  not "the call is worse."
- **Bad, because** keeping an instrument nothing depends on is a maintenance cost with
  no gate defending it. It is in `scripts/` so the linters and typechecker cover it,
  which bounds the cost but does not remove it.

## Revisit when

- **Harder cases separate the two methods.** The decisive gap is not more passes over
  these 36 — it is decisions where the tool choice was genuinely contested at the time,
  written and labelled by different people. If the unaided agent drops below the ladder
  on such a set, this reopens.
- **A Jev version ships whose signals separate differently.** `--suite --repeat 3`
  re-runs the measurement in about a minute for pennies.
- **The decision moves somewhere an agent's context is not free.** The table wins
  because it is always loaded. A caller that has to route jobs without a model in the
  loop at all — a script, a queue, a hook — has no table to read and is a different
  question this record does not answer.

## Confirmation

Nothing enforces this. `scripts/test_jev_pick_tool.py` holds the instrument's ladder
and case set to their shape, including a test pinning the flat-signal defect so it
cannot return by accident, but no gate stops someone adding a routing call elsewhere.

## Alternatives

- **Ship the tool anyway, for the confidence number.** Rejected: §5 of
  [`../research/2026-09-17-jev-applications.md`](../research/2026-09-17-jev-applications.md)
  measured confidence 16 points overconfident against ground truth, so there is no
  threshold worth gating on yet.
- **Ask Jev the tri-choice directly rather than seven task properties.** Rejected
  before measuring: it needs the model to reason about three tools' capabilities, which
  §6 of that note records as a non-fit, and a question naming the vendor's product
  invites a bias nobody can subtract afterwards.
