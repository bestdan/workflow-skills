---
created: 2026-10-02
status: accepted
convention: ../typed-model-calls.md
---

# Decision models are one interface with interchangeable backends

## Context

Every typed-model-call record in this repo names one vendor. Five measurements were
taken against [Jev](https://typesafe.ai/) `jev-1.13.0`, and each probe in a record's
`references/` calls TypeSafe's endpoint directly. The class was always the subject:
[`../typed-model-calls.md`](../typed-model-calls.md) defines it as a model that takes a
state plus typed questions and returns typed answers with probabilities, and
[`2026-09-21-jev-alongside-the-routing-evals.md`](2026-09-21-jev-alongside-the-routing-evals.md)
says a different model of the same shape inherits its argument.

That is no longer hypothetical. Cloudflare has announced
[Clef](https://blog.cloudflare.com/clef-decision-models/): a 27B model and a 9B
`Clef-flash`, described as Jev-API compatible, open weights under Apache 2.0, hosted on
Workers AI, reported 2.5× and 13× faster than Jev at the median over 43 benchmark runs,
and shipped alongside an RL fine-tuning service. **None of that is verified here.** The
post itself is blocked by this environment's egress proxy, and those figures come from
search-result snippets of it. They are the vendor's claims, quoted as such.

The five records split along one line. The three that proposed **replacing** a model
judgment already in place were declined: tool routing, predictive `scope` and
`assess-task`. The one adoption added a judgment the existing check could not make.
Open [bestdan/workflow-skills#900](https://github.com/bestdan/workflow-skills/pull/900)
moves the convention's quick rule onto that line. That split depends on where a call
sits, not on whose model answers it.

The twelve rules in `typed-model-calls.md` mix two kinds of finding:

- **Method**, true of any decision model: measure against the incumbent (rule 1),
  check per-label spread (3), report leave-one-out (4), sample more than once (5),
  print the slice's base rate (7), store the whole answer (8), score agreement and the
  direction of misses (12).
- **Instrument**, true of Jev at one version: `confidence` ran 16 points overconfident;
  answers vary across runs; a `Score` is an expected index, not a value in `[0, 1]`.

Read as written, an instrument fact passes for a law of the class, and a Clef
measurement would have to re-derive the method from scratch.

## Decision

Treat **decision model** as the class and each vendor as a backend behind one
contract: a state plus `noul`, `choice` and `score` questions in, full probability
distributions out. Three things follow.

1. **Placement is a property of the call site, not the backend.** Whether a decision
   model belongs somewhere is answered by the convention's quick rule, which #900
   revises, and by the Class A/B split in
   [`../research/2026-09-17-jev-applications/`](../research/2026-09-17-jev-applications/README.md).
   A faster or cheaper backend does not reopen a placement that was declined because
   the incumbent already had the context.
2. **Method and instrument are kept apart.** The method rules govern every backend.
   An instrument fact is recorded against the backend and version that produced it,
   and is not assumed to carry to another. A backend that has not been measured has no
   instrument facts, which is a reason to measure it, not to borrow Jev's.
3. **The next measurement goes through a shared adapter and harness, not another
   one-off probe.** The adapter takes a backend name. The harness makes the method
   rules mechanical: it runs the incumbent beside the candidate, prints the base rate
   beside every score, samples `n > 1`, reports leave-one-out, stores raw
   distributions, and tabulates the direction of misses, with cost and wall clock for
   both sides. A record's probe then becomes a case file and a config. "Revisit when a
   newer model ships" becomes a re-run.

Nothing in `skills/`, `commands/` or the blocking gate calls any backend, and this
record does not change that. The Class B rule holds for every vendor: a runtime call is
defensible only as a fast path that degrades to today's model judgment.

## Consequences

- **Good, because** a declined record can be re-run against Clef or a later Jev
  without rewriting its probe. That turns the "Revisit when" clauses of three records
  into a command.
- **Good, because** the `assess-task` verdict gets the right next question.
  [`2026-09-24-assess-task-stays-a-subagent.md`](2026-09-24-assess-task-stays-a-subagent.md)
  declined a call that was steady and one-sided: 32 of 32 `complexity` misses read
  higher. A bias that consistent can be corrected by calibration in code or by
  fine-tuning on the incumbent's labels; noise at the same rate could not be. A raw
  re-run on a faster backend asks the wrong question; a calibrated one asks the right
  one.
- **Good, because** an open-weights backend changes Class B's cost. A user without a
  third-party key has a plausible route. A 9B local model is still a heavy dependency
  for a plugin user, so this widens the fallback options and removes no rule.
- **Bad, because** the adapter and harness do not exist yet. Until they do, this
  record is a direction, and the next probe written by hand will drift from it.
- **Bad, because** the harness turns an artifact into tooling. The 2026-09-19 record
  kept its probe out of `scripts/` on purpose: evidence nobody maintains rather than
  code everybody must. A shared harness reverses that for the common part and has to
  earn its test pair and its place under `scripts/`.
- **Bad, because** Clef's claims rest on vendor snippets read secondhand. If the
  compatibility claim fails on a real request, item 3's adapter needs per-backend
  translation and the "one contract" framing weakens.

## Revisit when

- **A backend breaks the contract.** A model of the class that cannot answer all three
  question types, or does not return full distributions, means the interface is
  narrower than stated here.
- **A method rule turns out to be instrument-specific.** For example, a deterministic
  backend makes rule 5 a per-backend fact. Move the rule, and note it in the
  convention.
- **The harness is built.** At that point the convention should cite the harness for
  the method rules instead of restating them, and this record's item 3 is done.

## Alternatives

- **Keep Jev as the named subject and add Clef records beside it.** Rejected: each
  record would re-derive the method, and instrument facts would keep reading as class
  facts.
- **Adopt Clef in place of Jev now.** Rejected: nothing in this repo calls Jev at
  runtime to replace, and the description-scoring companion check has not been
  measured on Clef. Rule 1 applies to a backend swap as much as to a judgment swap.
- **Wait for #900 to merge first.** Rejected: this record depends on #900's placement
  rule only by reference, and the two change different things. If #900's wording
  changes, item 1 still points at wherever the quick rule lives.
