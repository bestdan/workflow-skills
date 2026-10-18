---
created: 2026-09-19
status: accepted
convention: ../typed-model-calls.md
---

# No typed call for "which tool should do this job"

## Context

An agent decides, many times a session, whether a small job wants ordinary code, a
typed model call, or its own reasoning. The proposal was to answer that with a typed
call cheap and fast enough to use reflexively — a `pick-tool` command an agent could
reach for without thinking about it.

[`../research/2026-09-19-jev-tool-routing/`](../research/2026-09-19-jev-tool-routing/README.md)
measured it against 36 routing decisions this repo already shipped. **Both sides
scored 36/36, three times each**: a two-rung ladder over Jev signals, and three Claude
subagents given only a blind case list and the three-line table below.

What separates them is not accuracy but what the score cost. The ladder needed two
thresholds fitted to this very set and cross-validates at 97%; the agents needed three
lines of prose, no fitting, and agreed with one another on all 36 cases. The agents
also caught an error in the labels they were being graded against — one case the
ladder got wrong in the same direction the label did.

The forces: the table is three lines of prose in a file an agent reads, costing a read
it was going to make anyway; the call costs a round trip, an API key, and a dependency
on a third party. Neither `typed-model-calls.md` nor this record is auto-loaded, so the
table is cheap rather than literally always in context — the comparison is a read
against a network call, not free against paid.

## Decision

Do not build the routing tool. Carry the three-line table in guidance instead, where
it now lives as **The rule, when you just need the answer** in
[`../typed-model-calls.md`](../typed-model-calls.md) — that file is the one copy, and
this record cites it rather than holding a second.

Keep the probe, as an artifact of the record rather than as tooling: it lives in that
record's `references/`, nothing imports it, and no gate runs it. The negative result is
only as good as its labels and its ladder, so both have to stay inspectable and
re-runnable for it to mean anything a year from now — which is what a bundle is for,
and is a weaker promise than a maintained script makes.

## Consequences

- **Good, because** the cheapest option won on the evidence, and an agent that would
  have paid a round trip per decision now pays nothing.
- **Good, because** the probe surfaced a defect that outlives it: a precedence rule
  argued from first principles, resting on a signal whose per-label spread nobody
  checked, cost 42 points. That is a review question for any future Jev ladder.
- **Good, because** it puts a reading on agreement that the shadow-mode rollout in
  [`../designs/2026-09-18-assess-task-typed-profile.md`](../designs/2026-09-18-assess-task-typed-profile.md)
  will need. That design logs the typed profile and today's model side by side and
  promotes what agrees. Agreement is the green light there — but this measurement is
  a case where the two agreed completely and the right conclusion was _don't adopt_,
  because agreement equally means the incumbent was already sufficient. What
  distinguishes the two readings is cost: here one side needed thresholds fitted to
  the set and a round trip, and the other needed three lines of prose. Agreement
  licenses a switch only when the typed side is also cheaper, faster or steadier than
  what it replaces.
- **Bad, because** the measurement rests on 36 cases written by one person from one
  repository, and a ceiling effect means it can support "the call is not needed" but
  not "the call is worse."
- **Bad, because** the probe left the gate when it became an artifact. Its tests no
  longer run in `just check` and mypy no longer reads it, so it can rot unnoticed
  until someone re-runs the record. That is the trade the `dev_docs` layout makes
  deliberately — evidence nobody maintains, rather than code everybody must — and the
  cost lands on whoever reopens this, not on the gate.

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

Nothing enforces this, and less than before. The probe's own tests still hold its
ladder and case set to their shape — including one pinning the flat-signal defect so
it cannot return by accident — but they sit in the record's `references/` and no gate
runs them, so they check nothing until someone runs them by hand. No gate stops
someone adding a routing call elsewhere either.

## Alternatives

- **Ship the tool anyway, for the confidence number.** Rejected: §5 of
  [`../research/2026-09-17-jev-applications/`](../research/2026-09-17-jev-applications/README.md)
  measured confidence 16 points overconfident against ground truth, so there is no
  threshold worth gating on yet.
- **Ask Jev the tri-choice directly rather than seven task properties.** Rejected
  before measuring: it needs the model to reason about three tools' capabilities, which
  §6 of that note records as a non-fit, and a question naming the vendor's product
  invites a bias nobody can subtract afterwards.
