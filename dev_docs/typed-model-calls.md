# Typed model calls

What to know before proposing that some judgment in this repo becomes a typed
model call — today that means [Jev](https://typesafe.ai/), TypeSafe's System
One model, which takes a state plus yes/no, multiple-choice and rating
questions and returns typed answers with probabilities.

No installed user and no CI run needs a key: nothing in `skills/`, `commands/`
or the blocking gate calls Jev. **Reproducing any measurement below does call it
and does need one**, resolved as [`auth_key_access.md`](auth_key_access.md)
describes. Five measurements sit behind that posture. Four ended in a decision
not to adopt what they measured; the fifth adopted a typed call as a companion
to an existing check rather than a replacement for it, and stayed out of the
gate. This file is the short version of what they cost to learn.

| Read this when                                                     | Go to                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| You want to know whether your judgment is Jev-shaped               | [`research/2026-09-17-jev-applications/`](research/2026-09-17-jev-applications/README.md) — seven ranked candidates, the non-fits, the vendor's own rules, and what it is documented to be bad at                                                                                                                                            |
| You are about to build a ladder over Jev signals                   | [`research/2026-09-19-jev-tool-routing/`](research/2026-09-19-jev-tool-routing/README.md) — a ladder that scored 58% and why                                                                                                                                                                                                                 |
| You are about to ask a model to forecast something from prose      | [`research/2026-09-20-predictive-scope/`](research/2026-09-20-predictive-scope/README.md) — a forecast whose wrapper, not the model, put it on its base rate, and three instrument defects the record caught in itself                                                                                                                       |
| You are about to replace a check with a cheaper proxy              | [`decisions/2026-09-21-jev-alongside-the-routing-evals.md`](decisions/2026-09-21-jev-alongside-the-routing-evals.md) — 120× faster, agreed on every case, and blind to the failure the check exists to catch                                                                                                                                 |
| You are about to swap a model judgment for a steadier, cheaper one | [`decisions/2026-09-24-assess-task-stays-a-subagent.md`](decisions/2026-09-24-assess-task-stays-a-subagent.md) — 10× faster, 780× cheaper, as self-consistent as the subagent, and different from it in a consistent direction; [the controls](research/2026-09-24-assess-task-comparison-controls/README.md) show codex misses the same bar |
| You want the key plumbing                                          | [`auth_key_access.md`](auth_key_access.md)                                                                                                                                                                                                                                                                                                   |

## The rule, when you just need the answer

Measured against this repo's own decisions and not beaten by a typed call — see
[`decisions/2026-09-19-no-tool-routing-call.md`](decisions/2026-09-19-no-tool-routing-call.md).
The rungs are in order: the first that matches wins.

1. Computable exactly from what you have → **write code**.
2. One of a fixed set, a yes/no, or a point on a scale, judged from text you
   already have, **and** no model makes the judgment where it is needed, or the
   typed call supplies something the model that does cannot → **a typed call**,
   compared first against whatever makes the judgment there now.
3. Everything else → **reason about it yourself.** That covers text a human
   reads, a job that needs steps or fetching, and a closed-set judgment a model
   already makes. Replacing that last one is possible but rare, and has its own
   bar, below.

Rung 1 beats rung 3: a job that reads files and then computes an exact answer is
code's, not an LLM's. Retrieval only disqualifies the typed call, which cannot
fetch anything — and it is not a general veto, because that is precisely the flat
signal that misrouted 10 of 12 code cases in rule 3 below.

**Rung 2 turns on what you would be replacing, not on the question's shape.**
Every rung-2-shaped question measured here that proposed replacing a model
judgment already in place was declined: tool routing, predictive `scope`, and
the `assess-task` profile. The one adoption,
[the description check](decisions/2026-09-21-jev-alongside-the-routing-evals.md),
did not replace a model judgment. It was taken for a job the incumbent cannot do
at any price: a margin that says which way to edit. That gives two cases.

- **Replacing a model judgment.** In this repo the incumbent is usually an agent
  already running with the context in hand, so its marginal cost is a few lines
  of prose. The best a typed call competes with is a spawn: about $0.047 and
  3.8 s per packet for `assess-task`. There, a large ratio is a small absolute
  saving, and it has to pay for a second key, a second vendor and a fallback
  path. The bar is agreement with the incumbent. Where no outcome exists to
  score against, that bar measures resemblance to the incumbent's model, which
  [the controls](research/2026-09-24-assess-task-comparison-controls/README.md)
  show no non-Claude rater cleared. Expect to decline. Adopt only when the
  incumbent is a spawn or a round trip, not prose already in context, and when
  the consumer tolerates the direction the typed call errs in (rule 12). Score
  the consumer's decision where you can, not just the intermediate label.
- **Adding a judgment where none is made.** A hook, a script, a CI step, or a
  batch too large to spawn for has no model in the loop. There the incumbent is
  a heuristic or nothing, and rule 1's comparison is against that. The same case
  covers a judgment that needs a property the agent in the loop cannot supply:
  a distribution rather than a self-report, answers that cannot anchor one
  another, or independence from the work being graded. One adoption supports
  this case and no measurement has contradicted it. It is the likelier home for
  a typed call, but it is not yet a proven one.

## The twelve that cost us something

Each of these was learned by running it, not by reading the vendor's docs. The
vendor's own rules — one judgment per question, decompose and weight in code,
fan out speculatively, never ask what code can compute — are in the 2026-09-17
note under **Using it well**, and are not repeated here.

1. **Measure against the incumbent, not against ground truth.** The question is
   never "is the typed call accurate" but "is it better than what we do now."
   A 71.6% against ground truth means nothing until you know what today's model
   scores on the same cases. This is the single most common gap in the records
   above, and the routing probe exists because of it.

   **"Better" is not "more accurate."** Equal accuracy at materially lower cost
   or latency beats the incumbent. Where both sides were scored on the same
   cases, the typed call has never beaten the incumbent on the incumbent's own
   question. It tied on tool routing. On the description check it scored higher
   only by answering a narrower question. On `assess-task` it fell 15–17 points
   short. So a comparison is unfinished until it carries cost and wall clock
   beside the score, for **both** sides. Rule 2 is the other half of this: the
   tie that agreement produces is broken on those axes.

2. **Agreement is ambiguous, and cost breaks the tie.** A both-logged rollout
   that promotes what agrees reads agreement as a green light. It is equally
   evidence the incumbent was already sufficient — that is exactly what
   happened in the routing measurement, where both sides scored 36/36 and the
   answer was don't adopt. Agreement licenses a switch only when the typed side
   is also cheaper, faster or steadier than what it replaces.

3. **Check a signal's per-label spread before you branch on it.** A question
   the model answers the same way for every class looks decisive and decides
   nothing. `needs_fetch` measured 0.56 on one class and 0.56 on another; a
   veto resting on it cost 42 points and was invisible until the spreads were
   printed side by side.

4. **Report leave-one-out, not the fitted number.** Thresholds tuned on the set
   you then score are not a measurement. Refitting on n−1 and scoring the
   held-out case costs a few lines and is the number worth quoting.

5. **Run it more than once.** Jev is not deterministic across runs. A margin
   measured once moved threefold on the identical prompt over four runs. A
   single pass is an anecdote whatever it says.

6. **Do not gate on `confidence`.** It measured 16 points overconfident against
   ground truth here — most assured where it was wrong. It _is_ sharply
   responsive to injected pressure, so it earns its keep as a tripwire on an
   unexplained collapse, never as a threshold on being right.

7. **Quote the base rate of the slice you scored, not of the set.** Rule 1 asks
   what the incumbent scores; this asks what a constant scores. They are
   different floors and the slice's is usually the higher one. The predictive
   `scope` probe, as its design specified, read 56.7% against the set's 56.0%
   and looked like a narrow win; on the 48-case boundary that carried the
   result, the majority class alone scores 58.3% and the call scored 54.9% —
   below it. Print the baseline beside every accuracy figure, in the tool, so
   the two cannot be quoted apart.

8. **Know what an answer _is_ before you map it, and keep all of it.** A Jev
   `Score` is not a number in `[0, 1]` and not itself a choice: it is the
   expected value of a `probabilities` distribution over the criteria indices,
   `0` to `n-1`, and the response carries both — the vendor's skill calls it a
   "probability-weighted position on ordered levels". Reading it as normalised sent
   39 of 45 cases to the top bucket and reported 4.4% against a 55.6% base
   rate. What exposed it was not the number but the _direction_ — the errors
   inverted against a prior measurement, and an inversion that total is a claim
   about the instrument before it is a claim about the model. Then the first
   corrected run stored only the mean, and could not be re-decoded when the
   distribution turned out to matter. Dump one raw response before writing the
   decoder, and store the whole answer, not the field you think you need.

9. **Score the raw signal beside anything you add to it.** Three probes, three
   cases where the wrapper was the defect and the model was fine: a flat
   signal a veto rested on, a mis-scaled decoder, and an upward-only floor
   that fired on 24 of 50 cards per pass and was wrong on 34 of its 72 firings
   by construction. None was visible from the wrapped number alone. Every
   report prints the unwrapped result next to the wrapped one —
   `--ablate-floor` here — so the mapping's contribution is a column, not an
   assumption.

10. **Measure the input the consumer sends, not the input you have handy.**
    The predictive `scope` probe sent each card's body alone for two runs and
    reported 68.1% with the floor removed. `assess-task` is handed title and
    body. With that input, on the same 45 cards, the same configuration scored
    64.4% and under-read more — a terse title reads as small work. A number
    measured on an input the consumer never sees is a number about a different
    system, and reproducing it across runs does not make it the right one.

11. **Ask what the cheap side cannot see.** Rules 1 and 2 get you to "same
    answers, far cheaper", and that is where a replacement looks safest and is
    most dangerous. A proxy agrees with a check by answering a narrower
    question, so the agreement is loudest exactly where the blind spot is. The
    typed call scored 14/14 on the manifest cases against a harness scoring
    10/14 and 12/14, 120× faster — and the four it "won" include two skills
    that fired **nothing** in a real session, which a Choice over the roster
    cannot observe at all. Before swapping a check for a proxy, name the
    failure the check exists to catch and show the proxy detecting it.

12. **Stability is not agreement.** A typed call can be as self-consistent as
    the incumbent and still differ from it in a consistent direction. On
    `assess-task`, Jev's passes agreed with each other as often as the panel's
    runs did, or more, yet agreed with the panel 15–17 points less often than
    the panel's runs agreed with each other.
    Score agreement with the incumbent, not self-agreement, and check which way
    the misses fall: 32 of 32 read `complexity` higher, and a one-way error on a
    label trigger moves routing systematically, where noise at the same rate
    would not.

## Before you open the PR

- **Is it Class B?** Anything reached from `skills/` or `commands/` makes an
  installed user need a key. Only defensible as a fast path that degrades to
  today's model judgment when the key is absent — never as a requirement.
- **Not in the blocking gate.** `just check` is hermetic and offline, and a
  network call adds a flake source and a secret requirement to the one thing
  that must have neither.
- **Logic goes in a typed file with a test pair**, called from the prose — see
  [CONTRIBUTING.md](../CONTRIBUTING.md#logic-goes-in-a-typed-file).
- **Fence third-party text, and treat the answer as advisory anyway.** Task
  cards, PR bodies and review findings are attacker-adjacent. A fence is a hint
  to the model, **not a boundary it enforces** — a delimiter inside the data
  escapes it unless you neutralise it on the way in, which the routing probe's
  `fence()` did not do until co-review caught it. Neutralising the delimiter is
  worth doing and is not what makes this safe: no injection flipped an answer
  when this was probed, but a plausible authority claim turned a 1.000/0.000
  answer into a near-tie every time, and that attack never needed to escape
  anything. Advisory is the control; the fence is hygiene.
- **A probe script is a record's artifact, not tooling.** It belongs in that
  record's `references/`, not in `scripts/` — see [`README.md`](README.md).
