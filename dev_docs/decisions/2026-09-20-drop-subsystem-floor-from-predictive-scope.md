---
created: 2026-09-20
status: accepted
convention: ../typed-model-calls.md
---

# Drop the subsystem floor before asking a typed call to forecast `scope`

## Context

[`../designs/2026-09-18-assess-task-typed-profile.md`](../designs/2026-09-18-assess-task-typed-profile.md)
splits `scope` in two. A **concrete** card links a diff, so code counts the changed
files; §3 of
[`../research/2026-09-17-jev-applications.md`](../research/2026-09-17-jev-applications.md)
settled that at 40/40 and rule 4 says code owns arithmetic. A **predictive** card is
prose describing work not yet done, and there the design keeps `scope` as a model
judgment, adds a seventh question — "three or more unrelated subsystems?" — and makes
it an **upward-only floor**: it can raise a level, never lower one. That asymmetry was
reasoned from §3, where 31 of 33 misses were under-reads, so only an upward correction
could help. The design says of it: "That direction is not a preference."

[`../research/2026-09-20-predictive-scope/`](../research/2026-09-20-predictive-scope/README.md)
measured the predictive half against 45 cards from this repository whose text predates
the pull request that closed them. The floor carries the result:

|                             | as the design specifies   | floor removed     |
| --------------------------- | ------------------------- | ----------------- |
| four-level exact, mean      | 60.7%                     | 68.1%             |
| margin over 55.6% base rate | +5.2, spread 4.4          | +12.6, spread 2.2 |
| boundary only, mean         | 58.9% vs a 58.1% baseline | 66.7%             |
| error direction             | 38 over-reads / 15 under  | 0 over / 43 under |

The floor fires on 26–28 of 45 cards per pass. On 38 of its 81 firings the card is
truly below `multi-file`, so raising it is wrong **before any decoder is chosen**. Under
the instrument's decoder it is right 25 times against 35 wrong. Removing it also
restores §3's error direction, which is evidence the model reads blast radius
consistently in both regimes and the floor was masking it. Which decoder reads the
Score — round its mean, or take the argmax of its distribution — moves any cell above
by at most one case per pass.

Two limits bound this. No agent baseline was run, so nothing here says the call beats a
model reasoning unaided. And the floor-free number is **selected on the corpus it is
quoted against**: two variants were scored on the same 45 cards and the better one is
reported. It reproduced on an independent run, which rules out noise and not
selection.

## Decision

Remove the upward-only subsystem floor from the predictive `scope` path before any
further work on it. Its premise — that only an upward correction can help — was
inherited from a post-hoc measurement of a different question and does not hold once
the question is a forecast. Do not adopt the floor-free variant on this evidence
either: its 68.1% was chosen on the same cases it is quoted against and needs
confirmation on cards this run did not touch before it licenses anything. Until then
predictive `scope` stays with today's model, and the concrete lane is untouched.

## Consequences

- **Good, because** it removes a component that measurably makes the answer worse, on
  evidence that does not depend on the decoder: 38 of 81 firings are wrong by
  construction.
- **Good, because** it corrects a premise the design states in the one place it was
  most confident. It was a preference, inherited from a measurement of a different
  question.
- **Good, because** the ablation is reproducible from committed evidence
  (`--analyze --ablate-floor`) at no API cost, and the committed run now carries the
  full distribution, so the next reader can re-check it under either decoder.
- **Bad, because** it leaves the design's predictive lane unresolved rather than settled.
  A reader wanting to know whether to build it still has to run the confirming
  measurement.
- **Bad, because** the corpus cannot see its own worst case: `whole-codebase` has zero
  instances, so that cut point remains unlicensed however the question is asked.
- **Bad, because** the design's cost case is untouched and may not survive scrutiny. It
  rests on `select-coder` paying a subagent spawn per packet; if a predictive card still
  needs the model for `scope`, the spawn does not go away and the typed call is paid on
  top of it. That is a separate question this record does not answer.

## Revisit when

- **The floor-free variant is scored on cards this run did not touch.** That is the one
  measurement standing between here and a decision on the predictive lane. Finding 5
  warns the corpus grows slowly: 44% of closed issues never acquire a linked merged
  pull request.
- **A typed call is already being made for the other dimensions.** Extra questions ride
  free in the same request, so the marginal cost of asking `scope` falls to zero and the
  comparison changes from "is it worth a round trip" to "is it better than nothing".
  This record answers the first question only.
- **A consumer is asked whether one level of error is tolerable.** `within one level`
  was 100% in every pass, with and without the floor, under both decoders. If
  `select-coder`'s routing is insensitive to a single-level miss, the call is more
  useful than any exact-match number here suggests, and nobody has checked.
- **A Jev version ships whose distributions separate differently.** `--suite --repeat 3`
  re-runs this in about two minutes for pennies.

## Confirmation

Nothing enforces this. The probe's tests pin the floor's direction, both decoders, and
that a committed run carries the distribution, so none can regress by accident — but
they sit in the record's `references/` and no gate runs them. The design still
specifies the floor; this record is the only thing saying not to build it.

## Alternatives

- **Keep the floor and accept 60.7%.** Rejected: the same configuration scored 60.0%
  inside its own spread on the previous run, and the ablation shows the floor is what
  puts it there.
- **Make the floor bidirectional.** Rejected as unmeasured. The subsystem answer's own
  discriminating power was never established separately, and inverting an asymmetry
  nobody has tested would repeat this record's mistake in the other direction.
- **Adopt the floor-free variant now.** Rejected: the number is selected on the corpus
  it is quoted against. The routing record's decision makes the same refusal about
  thresholds fitted to the set they are scored on.
- **Drop the `scope` dimension entirely.** Rejected by the design's own constraint: it
  "must not lose `scope` outright, which would leave a predictive card with no producer
  for it."
