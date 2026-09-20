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
could help.

[`../research/2026-09-20-predictive-scope/`](../research/2026-09-20-predictive-scope/README.md)
measured the predictive half against 45 cards from this repository whose text predates
the pull request that closed them. The floor turned out to carry the result:

|                             | as the design specifies        | floor removed               |
| --------------------------- | ------------------------------ | --------------------------- |
| four-level exact, mean      | 60.0%                          | 68.1%                       |
| margin over 55.6% base rate | +4.4, under a 6.7 spread       | +12.6, over the same spread |
| boundary only, mean         | 58.9% against a 58.1% baseline | 67.4%                       |
| error direction             | 37 over-reads / 17 under       | 1 over / 42 under           |

The floor fires on 24–27 of 45 cases and changes the level on 18–21. It does not break
even: 22 right against 33 wrong. Removing it also restores §3's error direction, which
is evidence the model reads blast radius consistently in both regimes and the floor was
masking it.

Two limits bound this. No agent baseline was run, so nothing here says the call beats a
model reasoning unaided. And the floor-free number is **selected on the corpus it is
quoted against** — two variants were scored on the same 45 cards and the better one is
reported, which is the hazard rule 4 names even though no threshold was fitted.

## Decision

Remove the upward-only subsystem floor from the predictive `scope` path before any
further work on it. Its premise — that only an upward correction can help — was
inherited from a post-hoc measurement and does not hold once the question is a
forecast. Do not adopt the floor-free variant on this evidence either: its 68.1% was
chosen on the same cases it is quoted against, so it needs confirmation on cards this
run did not touch before it licenses anything. Until then predictive `scope` stays with
today's model, and the concrete lane is untouched.

## Consequences

- **Good, because** it removes a component that measurably makes the answer worse, and
  the evidence for that is the negative half of the ablation — the half not weakened by
  having been selected on this corpus.
- **Good, because** it corrects a premise the design states explicitly, in the one place
  it was most confident: "That direction is not a preference." It was a preference, and
  it was inherited from a measurement of a different question.
- **Good, because** the ablation is reproducible from committed evidence
  (`--analyze --ablate-floor`) at no API cost, so the next person can re-check it rather
  than take it on trust.
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
  was 100% in every pass, with and without the floor. If `select-coder`'s routing is
  insensitive to a single-level miss, the call is more useful than any exact-match
  number here suggests, and nobody has checked.
- **A Jev version ships whose Score separates differently.** `--suite --repeat 3`
  re-runs this in about two minutes for pennies.

## Confirmation

Nothing enforces this. The probe's tests pin the floor's direction and the index-scale
mapping so neither can regress by accident, but they sit in the record's `references/`
and no gate runs them. The design still specifies the floor; this record is the only
thing saying not to build it.

## Alternatives

- **Keep the floor and accept 60.0%.** Rejected: it is inside the run-to-run spread, so
  it is not a result, and the ablation shows the floor is what puts it there.
- **Make the floor bidirectional.** Rejected as unmeasured. The subsystem answer's own
  discriminating power was never established separately, and inverting an asymmetry
  nobody has tested would repeat this record's mistake in the other direction.
- **Adopt the floor-free variant now.** Rejected: the number is selected on the corpus
  it is quoted against. The routing record's decision makes the same refusal about
  thresholds fitted to the set they are scored on.
- **Drop the `scope` dimension entirely.** Rejected by the design's own constraint: it
  "must not lose `scope` outright, which would leave a predictive card with no producer
  for it."
