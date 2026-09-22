---
created: 2026-09-21
status: accepted
convention: ../typed-model-calls.md
---

# A typed call scores skill descriptions; it does not replace the routing evals

## Context

This record is about a **typed model call** — an AI decision model that takes a state
and typed questions and returns typed answers with probabilities, rather than prose to
parse. [`../typed-model-calls.md`](../typed-model-calls.md) is the convention for the
class; [Jev](https://typesafe.ai/) is the instance measured here, pinned at
`jev-1.13.0`. Everything below is about what the class can and cannot do for this
check, so a different model of the same shape inherits the argument; only the measured
numbers are Jev's.

`evals/` is this repo's only check that a skill's `description` makes Claude load it
from a naive prompt. It runs one `claude -p` session per manifest row, up to six turns,
serially, and greps the log for a `Skill` invocation. It costs API tokens and is
nondeterministic, so it is opt-in, non-blocking, and manual-dispatch only in CI — and
**that workflow has never run**: `gh run list --workflow=evals.yml` returns nothing as
of 2026-09-21, so no baseline exists for any row.

Section 1 of
[`../research/2026-09-17-jev-applications/`](../research/2026-09-17-jev-applications/README.md)
proposed replacing that with one Jev Choice over the skill descriptions, and measured
it repeatedly: 0 misfires and 0 collisions over four passes, plus a no-skill Noul at 0
false positives on plainly off-topic prompts. It was the best-rated of the seven
candidates in that record and, four days on, the only one with no decision either way.

What was never measured is the comparison [`../typed-model-calls.md`](../typed-model-calls.md)
rule 1 requires: not accuracy against ground truth, but both sides on the same cases.
Measured 2026-09-21 on one Linux host, same 14 manifest rows — Jev four passes, the
eval harness twice:

|                           | `scripts/eval.sh`              | Jev, same 14 rows            |
| ------------------------- | ------------------------------ | ---------------------------- |
| suite wall clock, serial  | 752.8s and 983.4s              | 6.9 – 7.9s over four passes  |
| per case                  | median 30.6s, mean 62.0s       | median 0.50s, mean 0.52s     |
| agreement with the labels | 10/14 then 12/14               | 14/14 in each of four passes |
| cost                      | 14 agent sessions; not metered | $0.0029 per pass             |

About 120× on wall clock, and steadier: the incumbent's two runs differ by 230s and
disagree with each other on two rows.

**The incumbent's failures are what settles this.** `orchestrate-coders` and
`select-coder` failed in both runs by invoking no skill at all; `task` and `tutor`
flapped. Jev reported clean on every one, in all four passes, at margins of 0.40 and
above — because the Choice is conditioned on a skill firing and structurally cannot
observe a session that loaded none, and the Noul says what ought to happen rather than
what did. A clean Jev sweep is compatible with a skill that never fires in a real
session, which is the state those two skills were in on the day both were measured.

The rule-2 reading in
[`2026-09-19-no-tool-routing-call.md`](2026-09-19-no-tool-routing-call.md) is that
agreement licenses a switch only when the typed side is also cheaper, faster or
steadier. Jev is all three here. That record's tie-break is satisfied and is still not
sufficient, because the two sides are not answering one question.

## Decision

Adopt the typed call as a **description-discriminability check that runs beside the
routing evals**, and keep `scripts/eval.sh` as the only thing that answers whether
Claude actually routes to a skill. The typed call earns its place on what the
incumbent cannot produce at all — the margin between winner and runner-up, and the
no-skill Noul — not on being a cheaper way to ask the incumbent's question. Neither
becomes a blocking check: the typed call needs a key and the network, which
`just check` must not, so it joins `evals/` as opt-in and run by hand. No workflow
dispatches it — unlike `evals/`, which has one — and wiring it into CI is a separate
choice this record does not make.

**Gating the harness on roster change is endorsed; gating it on the margin is not.**
Skipping the expensive suite when nothing could have moved routing is worth doing, and
it needs no model: "did any `skills/*/SKILL.md` frontmatter change" is a diff, which
rung 1 of the convention assigns to code. Key it on the **roster**, not one skill —
routing is a function of the whole option set, so adding a skill can move where an
untouched one routes, and a per-description gate would skip every row on exactly the
pull request most likely to have broken something. Two limits to state plainly: the
harness is nondeterministic, so a green prior result the gate relies on may itself
have been a coin flip; and this repo has no prior results at all. Using the typed
call's **margin** as the skip signal instead — "this description did not move enough
to re-run" — is the rejected half, and the "Revisit when" bullet below is its
condition.

## Consequences

- **Good, because** a description regression becomes cheap to notice. The companion
  procedure `evals/README.md` documents — four passes over all three suites — measured
  77s and $0.031, and can run on every PR that edits a `description`; 13 to 16 minutes
  and 14 agent sessions cannot, which is why nothing currently watches descriptions
  between manual eval runs. The 7s and $0.0029 in the table above are the manifest-only
  slice, measured for comparison against the harness on identical rows, and are not
  what the companion check costs.
- **Good, because** it produces two signals nothing here had: the runner-up margin,
  which narrows as two descriptions converge and does so before either takes the
  other's prompt — a property of the ranking, and **not** a prediction that a real
  session will misroute, which the "Revisit when" bullet below says is untested — and
  a false-positive rate over prompts that should load nothing, ground truth
  `evals/manifest.tsv` cannot express, since every row names an expected skill.
- **Good, because** it keeps the measurement that caught the real defect. Had the
  typed call replaced the harness, the 2026-09-21 runs would have reported 14/14 and
  `select-coder` and `orchestrate-coders` would have gone on failing to fire unseen.
- **Bad, because** the repo now has two checks over one manifest that can disagree,
  and a contributor who reads a clean typed run as "routing is fine" has been misled
  in exactly the way this record exists to prevent. The disagreement is the point and
  has to be documented where the check is run, not only here.
- **Bad, because** it adds a second API key to the opt-in path — `TYPESAFE_API_KEY`
  beside `ANTHROPIC_API_KEY` — and a second vendor whose availability the check
  depends on.
- **Bad, because** the numbers above are one host, one day, two incumbent runs. The
  incumbent's variance is established; its central tendency, on two samples, is not.
- **Bad, because** the incumbent's dollar cost is still unmeasured, so "cheaper" here
  rests on 14 agent sessions versus $0.0029 as a structural claim, not a metered one.

## Revisit when

- **The two stable failures are fixed.** `select-coder` and `orchestrate-coders` not
  firing is a live defect this measurement surfaced, and it is a separate piece of
  work. If their descriptions change, both sides need re-running — the typed call
  because its margins move, the harness because that is the check that would confirm
  the fix.
- **A low margin is shown to predict a routing failure.** The tightest margin in the
  run (0.40) sits on the two skills that failed to fire. Two cases is a coincidence
  worth testing, not a signal — and the coincidence is weaker than it looks, because
  those two are a **standing** defect rather than drift: neither description has
  changed since 2026-08-13 and 2026-07-28, so the margin was never asked to detect a
  change. If a correlation holds over cases where a description actually moved, the
  typed call becomes a cheap early warning for the expensive check, the margin-based
  skip gate above becomes buildable, and this decision gets stronger; if it does not,
  the two checks stay fully independent and the gate stays keyed on the diff.
- **The harness gets cheap enough to run often.** Parallelising `scripts/eval.sh`
  across rows would cut its **wall clock** by most of the 120× — and nothing else. The
  14 agent sessions still run and still cost what they cost, so the money half of the
  comparison is untouched, as is the margin and Noul half. Only the speed argument
  weakens.
- **A newer decision model ships whose distributions separate differently.** These
  margins were measured on `jev-1.13.0`, and the thresholds are the model's, not the
  class's.

## Confirmation

Nothing enforces this. The instrument's tests pin the latency record's shape and the
summary arithmetic, and no gate runs them — it is an artifact of a record, the same
trade [`2026-09-19-no-tool-routing-call.md`](2026-09-19-no-tool-routing-call.md) made.
Nothing stops someone deleting `evals/` and pointing at the typed call's clean sweep
as evidence that routing is covered; the only guard is that the consequence above says
plainly that it is not.

## Alternatives

- **Replace the eval harness with the typed call.** Rejected on the 2026-09-21 run:
  it reports 14/14 on rows where two skills fire nothing in a real session.
- **Adopt neither, and leave section 1 undecided.** Rejected: it was measured four
  times over four days and the cost of leaving it open is that nothing watches
  descriptions at all between manual eval runs.
- **Make the typed call blocking in `just check`.** Rejected by the repo's own rule —
  the gate is hermetic and offline, and this needs a key and a network call.
- **Run the typed call on every PR automatically.** Rejected for now as a separate
  choice that needs a CI secret and a workflow; this record settles what the check is
  for, not where it runs.
