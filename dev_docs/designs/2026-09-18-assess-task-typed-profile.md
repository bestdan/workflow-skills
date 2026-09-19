---
created: 2026-09-18
status: proposed
---

# `assess-task`: a typed profile, with code owning what code can compute

## Summary

`assess-task` asks a model for a `task_profile` of seven fixed enums, a derived
`label`, a self-reported `confidence`, and a `runner_up`. Every consumer of that block
pays a subagent spawn for it. This proposes splitting the profile by **who can answer
each field**: code computes what is arithmetic, a typed model call answers the
genuine snap judgments, and today's model answers everything when no key is present.
The `task_profile` block consumers read does not change shape.

The evidence is
[`dev_docs/research/2026-09-17-jev-applications.md`](../research/2026-09-17-jev-applications.md).
Read §3 and §8 before reviewing this; the measurements are there and are not repeated
here.

## What is true today

`skills/assess-task/SKILL.md` emits:

```yaml
task_profile:
  complexity: mechanical | standard | hard
  creativity: low | medium | high
  scope: single-file | pr-sized | multi-file | whole-codebase
  autonomy: bounded | long-horizon
  speed_sensitivity: low | high
  cost_sensitivity: low | high
  verification_criticality: low | high
  label: <one of the routing labels>
  confidence: high | low
  runner_up: <routing label> | null
```

Three things about that block matter here, and all three are in the skill's own text:

- **`scope` is defined as counting.** "Count the blast radius… subsystem count trumps
  line/file count when they disagree."
- **`label` is derived, not judged.** The table under **Deriving `label`** maps the
  other dimensions onto the label set.
- **`confidence` and `runner_up` are self-reported**, which is the least reliable
  thing a model produces.

Consumers: `select-coder` routes on the profile, `break-down-task` sizes on
`complexity` + `scope`, `promote-tasks` uses it as one input to its confidence check.
Each of those pays a subagent spawn per packet.

## Proposal

Three lanes, decided by who can answer the field.

**Code computes `scope` when, and only when, there is something to count.** The skill
consults `related_files` / linked context "if the raw description is thin" — an
escalation, not a guarantee. So there are two cases and they are not the same problem:

- **Concrete.** The task names paths, or links a diff, branch or PR. A file count and
  a subsystem set exist, the four levels are bucket boundaries over them, and a helper
  — `scripts/task-profile.py`, a typed file with a test pair, per
  [CONTRIBUTING.md](../../CONTRIBUTING.md#logic-goes-in-a-typed-file) — does the
  bucketing. No model is involved.
- **Predictive.** The task is prose describing work not yet done. Nothing can be
  counted, because the files do not exist. `scope` here is a forecast of blast radius
  from a description, and it stays a model judgment.

This split is the part of the design most likely to be wrong, and it is worth saying
why plainly: **the §3 measurement only covers the concrete case.** It scored merged
PRs, where the file count is a fact available after the work. The predictive case —
which is what `assess-task` actually faces most of the time — has no ground truth in
that run at all. What was measured is "classify a known count", and the 100% result
for a numerically-defined question says only that arithmetic is arithmetic.

The one part that is never arithmetic in either case is the skill's own override, "3+
unrelated subsystems", which becomes a separate narrow judgment rather than being
folded into a size estimate.

**A typed call answers the six remaining dimensions.** `complexity`, `creativity`,
`autonomy`, `speed_sensitivity`, `cost_sensitivity` and `verification_criticality` are
snap judgments over closed sets — Scores for the ordered pair, Nouls for the four
binaries — and they ride in one request against one state. Where a field comes from a
distribution, `confidence` is that distribution's shape rather than a self-report.

**Today's model answers everything when no key is present.** This is the only
defensible Class B shape: a fast path, never a requirement. No installed user acquires
a `TYPESAFE_API_KEY` obligation, and the block is identical either way.

### The interface that must not move

Consumers read `task_profile`. They must not learn where a field came from, or the
adoption leaks into three other skills and the degrade path stops being transparent.
So the block's keys, enum values and semantics are unchanged by this design, and the
helper's contract is "return a `task_profile`", not "call Jev".

The corollary is that the helper is **consumer code**: it runs as bare `python3` on
other people's machines, so it sits in `scripts/typecheck.sh`'s `CONSUMER_FILES` at
the 3.9 floor, not the dev tier. Key resolution is the existing ladder in
[`auth_key_access.md`](../auth_key_access.md) — `typesafe.api_key`, no new mechanism.

## Decisions

**`scope` is computed wherever it is countable, and asked only where it is a
forecast.** Measured at 71.6% exact with the error systematic in one direction —
`multi-file` under-read as `pr-sized` in 31 of 33 misses. The rejected alternative is
to keep asking even where a count exists, with a better-posed question: restating the
count first and defining levels numerically scores 100%, but at that point the
question is arithmetic and rule 4 applies. Asking a model to bucket an integer buys
nondeterminism and a network call for nothing.

The measurement does **not** license the predictive case, and the first
implementation step is a probe that does: score prose task cards whose work has since
landed, with the merged PR's real file count as truth. If that comes back like the
post-hoc run — systematic under-reading — then `scope` is unreliable exactly where it
cannot be computed, and the honest answer is to leave the whole dimension with
today's model rather than split it.

**`label` stays derived; the open part is whether its table is total.** If the
derivation table is complete, `label` is a lookup and asking for it duplicates
deterministic logic. If it has genuine overlap ("or a genuinely hard bug"), it is a
Choice and `runner_up` falls out of its distribution. This is settled by reading the
table, not by an API call, and it is the first thing implementation does — because the
tidy argument that `confidence` and `runner_up` come free depends entirely on the
answer.

**Nothing gates on a threshold until it is calibrated against this repo's cards.**
Rejected: the cookbooks' own thresholds, which the docs say to treat as examples.
The commit-type probe is the cautionary case — 62.9% agreement at 0.79 mean
confidence, so the model was most wrong about how wrong it was. A threshold set from
someone else's data would have shipped that.

**Any number that gates something is sampled more than once.** Jev is not
deterministic across runs: an identical prompt and option set moved a margin between
0.16 and 0.48 over four runs. A single call is a sample, not a measurement.

**Roll out by logging both, then promoting what agrees.** The helper computes the
typed profile and today's model produces its own, both are recorded, and no consumer
switches until they agree on this repo's real cards. This is the step that caught the
§1 collision claim before it shipped as fact.

## Not decided here

- **§5 semantic lint.** Held. 62.9% at 0.79 confidence is not fit to guard the release
  lever, and the next probe — the real patch in the state, plus the two-stage shape —
  has not been run.
- **§4 `promote-tasks`.** Its judgment is a size question, so it inherits whatever the
  `scope` decision lands on. It should follow this design, not accompany it.
- **§1 `evals/` scoring.** The mechanism works and there is no regression to find.
- **A fitness-screening skill.** A screener that asks structural questions about a task
  and combines them in code reaches 75% against known-answer tasks, catching flagrant
  misfits but missing the subtle one — it green-lit the size-5 judgment this design
  removes. It is a case study for the rules above, not a product.

## Graduation

Decision records, one per choice above that someone could revisit: `scope` is
computed rather than judged; Jev is a fast path and never a requirement; calibration
precedes any threshold. Conventions: `skills/assess-task/SKILL.md` gains the degrade
path and loses `scope` from the asked set, and the helper's contract is documented
where its consumers can find it. This design is deleted in the PR that finishes the
rollout, or the one after.
