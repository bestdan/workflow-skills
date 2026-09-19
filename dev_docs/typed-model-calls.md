# Typed model calls

What to know before proposing that some judgment in this repo becomes a typed
model call — today that means [Jev](https://typesafe.ai/), TypeSafe's System
One model, which takes a state plus yes/no, multiple-choice and rating
questions and returns typed answers with probabilities.

No installed user and no CI run needs a key: nothing in `skills/`, `commands/`
or the blocking gate calls Jev. **Reproducing either measurement below does call
it and does need one**, resolved as [`auth_key_access.md`](auth_key_access.md)
describes. Two measurements sit behind that posture, and this file is the short
version of what they cost to learn.

| Read this when                                       | Go to                                                                                                                                                                                        |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| You want to know whether your judgment is Jev-shaped | [`research/2026-09-17-jev-applications.md`](research/2026-09-17-jev-applications.md) — seven ranked candidates, the non-fits, the vendor's own rules, and what it is documented to be bad at |
| You are about to build a ladder over Jev signals     | [`research/2026-09-19-jev-tool-routing/`](research/2026-09-19-jev-tool-routing/README.md) — a ladder that scored 58% and why                                                                 |
| You want the `assess-task` adoption's shape          | [`designs/2026-09-18-assess-task-typed-profile.md`](designs/2026-09-18-assess-task-typed-profile.md)                                                                                         |
| You want the key plumbing                            | [`auth_key_access.md`](auth_key_access.md)                                                                                                                                                   |

## The rule, when you just need the answer

Measured against this repo's own decisions and not beaten by a typed call — see
[`decisions/2026-09-19-no-tool-routing-call.md`](decisions/2026-09-19-no-tool-routing-call.md).
The rungs are in order: the first that matches wins.

1. Computable exactly from what you have → **write code**.
2. One of a fixed set, a yes/no, or a point on a scale, judged from text you
   already have → **a typed call**.
3. Text a human reads, or it needs steps or fetching → **reason about it
   yourself**.

Rung 1 beats rung 3: a job that reads files and then computes an exact answer is
code's, not an LLM's. Retrieval only disqualifies the typed call, which cannot
fetch anything — and it is not a general veto, because that is precisely the flat
signal that misrouted 10 of 12 code cases in rule 3 below.

## The six that cost us something

Each of these was learned by running it, not by reading the vendor's docs. The
vendor's own rules — one judgment per question, decompose and weight in code,
fan out speculatively, never ask what code can compute — are in the 2026-09-17
note under **Using it well**, and are not repeated here.

1. **Measure against the incumbent, not against ground truth.** The question is
   never "is the typed call accurate" but "is it better than what we do now."
   A 71.6% against ground truth means nothing until you know what today's model
   scores on the same cases. This is the single most common gap in the records
   above, and the routing probe exists because of it.

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
