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
[`../research/2026-09-17-jev-applications/`](../research/2026-09-17-jev-applications/README.md)
settled that at 40/40 and rule 4 says code owns arithmetic. A **predictive** card is
prose describing work not yet done, and there the design keeps `scope` as a model
judgment, adds a seventh question — "three or more unrelated subsystems?" — and makes
it an **upward-only floor**: it can raise a level, never lower one. That asymmetry was
reasoned from §3, where 31 of 33 misses were under-reads, so only an upward correction
could help. The design says of it: "That direction is not a preference."

[`../research/2026-09-20-predictive-scope/`](../research/2026-09-20-predictive-scope/README.md)
measured the predictive half against 50 cards from this repository whose text predates
the pull request that closed them, sent as title and body — the input `assess-task`
receives. The floor carries the result:

|                             | as the design specifies   | floor removed     |
| --------------------------- | ------------------------- | ----------------- |
| four-level exact, mean      | 56.7%                     | 62.0%             |
| margin over 56.0% base rate | +0.7, spread 4.0          | +6.0, spread 0.0  |
| boundary only, mean         | 54.9% vs a 58.3% baseline | 60.4%             |
| error direction             | 34 over-reads / 31 under  | 0 over / 57 under |

The floor fires on 24 of 50 cards per pass. On 34 of its 72 firings the card is truly
below `multi-file`, so raising it is wrong **before any decoder is chosen**. Under the
instrument's decoder it is right 23 times against 31 wrong. Removing it also restores
§3's error direction, which is evidence the model reads blast radius consistently in
both regimes and the floor was masking it. Which decoder reads the Score — round its
mean, or take the argmax of its distribution — gives the same number in every cell.

Two limits bound this. No agent baseline was run, so nothing here says the call beats a
model reasoning unaided. And the floor-free number is **selected on the corpus it is
quoted against**: two variants were scored on the same cards and the better one is
reported. Its margin on the boundary — the one slice with cases on both sides — is
2.1 points.

One thing this measurement corrected in itself is worth carrying forward. Two earlier
runs sent each card's body alone and reported 68.1% with the floor removed, reproduced
across runs. With title and body — what the consumer sends — the same cards score
64.4%. The 68.1% was a number about an input `assess-task` never sees.

## Decision

Remove the upward-only subsystem floor from the predictive `scope` path before any
further work on it. Its premise — that only an upward correction can help — was
inherited from a post-hoc measurement of a different question and does not hold once
the question is a forecast. Do not adopt the floor-free variant on this evidence
either: its margin over a constant is thin on the boundary that matters and was chosen
on the cases it is quoted against. Until a corpus this run did not touch says
otherwise, predictive `scope` stays with today's model, and the concrete lane is
untouched.

## Consequences

- **Good, because** it removes a component that measurably makes the answer worse, on
  evidence that does not depend on the decoder: 34 of 72 firings are wrong by
  construction.
- **Good, because** it corrects a premise the design states in the one place it was
  most confident. It was a preference, inherited from a measurement of a different
  question.
- **Good, because** the ablation and the floor audit are printed by the committed
  instrument (`--analyze --ablate-floor`) from committed answers, at no API cost, so
  the next reader can re-check them under either decoder.
- **Bad, because** it leaves the design's predictive lane unresolved rather than settled,
  and the floor-free variant's own case got weaker once the right input was sent.
- **Bad, because** the corpus cannot see its own worst case: `whole-codebase` has zero
  instances, so that cut point remains unlicensed however the question is asked.
- **Bad, because** the design's cost case is untouched and may not survive scrutiny. It
  rests on `select-coder` paying a subagent spawn per packet; if a predictive card still
  needs the model for `scope`, the spawn does not go away and the typed call is paid on
  top of it. That is a separate question this record does not answer.

## Revisit when

- **The floor-free variant is scored on cards this run did not touch.** That is the one
  measurement standing between here and a decision on the predictive lane. Finding 5
  warns the corpus grows slowly: 42% of closed issues never acquire a linked merged
  pull request.
- **A typed call is already being made for the other dimensions.** Extra questions ride
  free in the same request, so the marginal cost of asking `scope` falls to zero and the
  comparison changes from "is it worth a round trip" to "is it better than nothing".
  This record answers the first question only.
- **A consumer is asked whether one level of error is tolerable.** `within one level`
  was 100% in every pass, with and without the floor, under both decoders. If
  `select-coder`'s routing is insensitive to a single-level miss, the call is more
  useful than any exact-match number here suggests, and nobody has checked.
- **The title effect is understood.** Adding the title cost four points on identical
  cards and pushed every miss to an under-read. Whether the question can be phrased so a
  terse title does not read as small work is untested.
- **A Jev version ships whose distributions separate differently.** `--suite --repeat 3`
  re-runs this in about two minutes for pennies.

## Confirmation

Nothing enforces this. The probe's tests pin the floor's direction, both decoders,
that a committed run carries the distribution, and that a run covers exactly the
committed corpus — so none can regress by accident — but they sit in the record's
`references/` and no gate runs them. The design still specifies the floor; this record
is the only thing saying not to build it.

## Alternatives

- **Keep the floor and accept 56.7%.** Rejected: it is inside its own spread, below the
  boundary baseline, and the ablation shows the floor is what puts it there.
- **Make the floor bidirectional.** Rejected as unmeasured. The subsystem answer's own
  discriminating power was never established separately, and inverting an asymmetry
  nobody has tested would repeat this record's mistake in the other direction.
- **Adopt the floor-free variant now.** Rejected: 62.0% was selected on the corpus it is
  quoted against, and its boundary margin is 2.1 points. The routing record's decision
  makes the same refusal about thresholds fitted to the set they are scored on.
- **Drop the `scope` dimension entirely.** Rejected by the design's own constraint: it
  "must not lose `scope` outright, which would leave a predictive card with no producer
  for it."
