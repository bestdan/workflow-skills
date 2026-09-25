---
created: 2026-09-21
status: accepted
convention: ../../typed-model-calls.md
---

# `label`'s derivation table is not pure code, and one cell can never be

## Context

[`../../designs/2026-09-18-assess-task-typed-profile.md`](../../designs/2026-09-18-assess-task-typed-profile.md)
rests a chain of claims on one table. `label` is "derived, not judged" — the
**Deriving `label`** table in `skills/assess-task/SKILL.md` maps the other
dimensions onto the eight routing labels. If that table is complete, `label` is a
lookup and asking a typed call for it duplicates deterministic logic; if it has
genuine overlap, `label` is a Choice and, in the design's tidiest claim,
`confidence` and `runner_up` "fall out of `label`'s `probabilities` — no
self-report".

§3 of
[`../../research/2026-09-17-jev-applications/`](../../research/2026-09-17-jev-applications/README.md)
made the question urgent by removing `scope` from the model's side: code counts
the files and buckets them. With one of the table's inputs computed, the design
says the totality question "is settled by reading the table, not by an API call,
and it is the first thing implementation does".

This is that reading. The table is finite — seven dimensions over fixed enums,
3 × 3 × 4 × 2 × 2 × 2 × 2 = **576 tuples** — so it was enumerated rather than
argued about. [`references/enumerate-label-table.py`](references/enumerate-label-table.py)
encodes each row's tuple-expressible conditions — its docstring names the two it
drops, both of which reach outside the seven dimensions — and reports, for every
tuple, how many rows fire.

### What the enumeration says

Read literally, over all 576 tuples:

| rows that fire | tuples | share |
| -------------- | ------ | ----- |
| 0              | 8      | 1.4%  |
| exactly 1      | 65     | 11.3% |
| 2 or more      | 503    | 87.3% |

The table is therefore **neither total nor deterministic**, and neither failure
is an edge case. Ambiguity is the overwhelming majority behaviour; a single
unambiguous answer is the 11.3% exception.

**The two failures come apart, and only one is recoverable.** One row's
conditions are genuinely ambiguous — `mechanical-bulk`'s "and/or" — so the
headline above is one reading rather than the only one, and the script prices a
narrower reading instead of arguing about it: ambiguity falls to **462 of 576
(80.2%)** and the silent tuples double to **16**.

Reading that row differently cannot rescue either failure, and the bound does not
depend on enumerating readings. Every reading of a row can only _add_ firings
relative to deleting it, so deleting `mechanical-bulk` outright bounds what any
reading of it can achieve: **416 of 576 (72.2%)** still ambiguous, and 28 silent
tuples rather than fewer. Narrowing that row trades ambiguity for silence in one
direction only.

**Totality is recoverable; determinism is not.** These are separate axes.
Rewriting `standard-pr` as a bare default branch (below) leaves **0** silent
tuples under every reading of `mechanical-bulk` — so the table can be made total
by one decision. Nothing available makes it deterministic: ambiguity stays above
**70%** however that row is read, and stays there even with the row gone.

Three specifics carry the rest of this record.

**The silent tuples are the ordinary ones.** All 8 have every binary at its low
value and `creativity` below `high`; what varies is the complexity/scope pair,
and the four that survive are `standard`×`single-file`, `standard`×`multi-file`,
`hard`×`single-file` and `hard`×`pr-sized`. So a small plain bugfix in one file
fires nothing, because `standard-pr` demands `standard` complexity **and**
`pr-sized` scope — and so does a genuinely hard problem confined to a handful of
files, because `architecture` demands `multi-file`. The gap is not a corner; it
is the diagonal either row would have had to widen to cover.

**Ambiguity comes from the table's shape, not from hard cases.** Six of the eight
labels fire off a _single_ dimension reaching the top of its own enum
(`creativity: high`, `speed_sensitivity: high`, `scope: whole-codebase`,
`verification_criticality: high`, `autonomy: long-horizon`, and
`cost_sensitivity: high` via `mechanical-bulk`'s "and/or"). Nothing in the
dimension rubric makes those mutually exclusive, so any task hitting two of
**those six** fires two rows. `mechanical-bulk` alone fires on **66.7%** of tuples, because the
"and/or" reads as an inclusive or and `cost_sensitivity: high` is half the space.

**The two labels describing ordinary work almost never win.** Of the 65 tuples
that resolve to exactly one label, `standard-pr` is the answer on **2** and
`architecture` on **2**. Twenty-two go to `mechanical-bulk`. `standard-pr` is
the starker case: because its third conjunct is "nothing extreme", those 2 are
the only tuples it fires on **at all** — 0.3% of the space. The "unremarkable
middle" the table names is, under the table's own rules, nearly unreachable.

### What the other readings change

`--variants` reports three more readings. The first two are **repairs** — each
widens a row the headline reads narrowly, to see whether the failures go away:

- **`architecture` widened to cover `scope: whole-codebase`** (it names only
  `multi-file`, though whole-codebase is the wider radius): changes nothing about
  totality — the same 8 tuples stay silent — and makes ambiguity slightly worse.
- **`standard-pr` read as a bare default branch** — dropping its `standard` /
  `pr-sized` conjuncts so it fires whenever no other row does: this _does_ make
  the table total, 0 silent tuples, and it widens `standard-pr` from 2 tuples to
  10 (1.7%). It changes ambiguity not at all — **503 of 576 (87.3%)** either way,
  since the row only ever fires alone.

The third is **not** a repair, and that is the point of having it:

- **`mechanical-bulk` narrowed to `complexity == mechanical` alone**, so
  `cost_sensitivity: high` no longer fires it by itself. Neither reading of that
  row is more faithful — "and/or" is ambiguous on its face, and the rubric assigns
  `cost_sensitivity: high` both to bulk work and to work merely "only worth doing
  cheaply", which can be hard or creative. So this reading exists to **price the
  default**, not to replace it: ambiguity falls to **462 (80.2%)**, silent tuples
  rise to **16**, and the row's own firing rate halves from 66.7% to 33.3%. It is
  a price, not a bound — "and/or" admits narrower readings still (requiring both
  conjuncts gives 439, 76.2%), which is why the bound above comes from deleting
  the row rather than from any reading of it. The
  headline keeps the inclusive reading because it is what the row literally
  spells; this variant is what makes that choice auditable rather than buried.

So the missing piece is not eight fuzzy cells. It is **one missing function**:
a precedence order over the eight labels, which the table never states and the
prose above it delegates to a reader — "pick the one that captures why the task
is _hard to route_ — the standout dimension wins over the mild ones". That
sentence is not computable as written: the dimensions sit on incommensurable
scales (two 3-level ordinals, one 4-level, four booleans), and "standout" has no
definition that ranks `creativity: high` against `speed_sensitivity: high`. Both
are simply the top value of their own enum.

A precedence order is a **specification** decision, though — written once by a
human, then executed by code. It does not need a model at runtime.

### The one cell that is not a specification gap

`architecture` fires on "`complexity: hard` with `multi-file`/refactor scope, **or
a genuinely hard bug**". That second disjunct names a fact the seven dimensions do
not carry. There is no bug dimension, and no precedence order derives one. It has
exactly two honest readings, and both end the same way:

- It is **redundant** with `complexity: hard`, whose own rubric already reads
  "architecture, cross-cutting refactor, **subtle bug**, or unclear approach" — in
  which case delete it, and nothing is lost.
- It reaches for something outside the tuple — in which case `label` is **not**
  derived from the dimensions, and the design's "derived, not judged" claim is
  false by construction rather than by omission.

Two lesser cells are underspecified in the same direction but are repairable by
decision: `mechanical-bulk`'s literal "and/or" (making `cost_sensitivity: high`
sufficient on its own, hence the 66.7% — the variant above prices that reading at
7.1 points of ambiguity and 8 silent tuples), and `architecture`'s "refactor
scope", which names a word rather than an enum value.

### What this does not establish

- **Nothing about the tuples real cards produce.** The enumeration weights all 576
  equally. No corpus of scored `task_profile` blocks exists in this repo — every
  prior measurement in the Jev workstream scored `scope` alone — so the _frequency_
  of collisions on real work is unmeasured. What the structure does say, independent
  of frequency, is that a tuple is unambiguous only when at most one of the **six
  single-dimension triggers** is at the top of its enum. Not every top is a trigger:
  `complexity: hard` tops its own enum and fires nothing on its own, since
  `architecture` also requires a scope, so 20 tuples carry two tops and still
  resolve to one label.
- **Nothing about whether a model reads the table better than code would.** No
  baseline was run. This is a reading of a specification, not a comparison of
  producers.
- **Nothing about the other six dimensions.** Whether a typed call scores
  `complexity`, `creativity` and the four Nouls well enough to adopt is #810's
  question, untouched here.

## Decision

**`label` is not pure code today, and must not be shipped as a lookup until a
precedence order is written into `skills/assess-task/SKILL.md`.** The table is
neither total (8 silent tuples) nor deterministic (87.3% ambiguous), and the
prose that resolves collisions delegates to a reader rather than defining a rule.

**It can become pure code, with one deletion.** Add a precedence order over the
eight labels and read `standard-pr` as the default branch; that is a one-time
specification decision, not a runtime judgment. Delete `architecture`'s "or a
genuinely hard bug" disjunct, folding it into `complexity: hard` where the
dimension rubric already puts subtle bugs. Narrow `mechanical-bulk` so
`cost_sensitivity: high` is not sufficient alone — the variant above shows what
that buys (7.1 points of ambiguity) and what it costs (8 more silent tuples, which
the precedence order's default branch then has to absorb).

**Therefore do not ask a typed call for `label`.** Once the precedence order
exists, asking a model for `label` duplicates deterministic logic over inputs
already in hand — six dimensions from the call, `scope` from code. The producer
split matters here: three rows (`architecture`, `standard-pr`, `whole-codebase`)
read `scope`, which the call does not return, so the lookup spans both producers
rather than sitting inside the call's own output.

**And the design's `confidence` / `runner_up` claim does not survive.** It depends
entirely on `label` being a Choice with a probability distribution. A lookup has
no distribution. Specifically:

- **`runner_up` survives, and is better than the claim promised** — but by a
  different mechanism. Under a precedence order it has an exact deterministic
  definition: the next-highest-priority label that also fired. It is genuinely
  free, needs no distribution, and it almost always exists — 87.3% of tuples fire
  a second row. `select-coder` consumes it by also weighing that label's row in
  `matrix.md`, which works unchanged.
- **`confidence` does not survive, and has no producer left.** Defining it from
  collisions collapses: "two or more rows fired" is true of 87.3% of the space, so
  `confidence: low` would be the near-constant answer and could gate nothing.
  Propagating confidence from the six input dimensions' own distributions is a
  different design that nobody has specified or measured — it is not something
  that "falls out". Until that design exists, `confidence` stays whatever the
  producer of the dimensions makes it, and #810 should not count it as a benefit
  of the typed call.

## Consequences

- **Good, because** it removes the design's tidiest argument before it is built on.
  "`confidence` and `runner_up` come free" was load-bearing for the typed call's
  cost case, and half of it was never true.
- **Good, because** the finding is arithmetic a reader can re-run for free
  (`python3 references/enumerate-label-table.py --variants`, no API key, under a
  second), rather than a judgment about a table.
- **Good, because** it narrows #810: `label` joins `scope` as a dimension the
  comparison should not measure, leaving six.
- **Good, because** the conclusion does not depend on how the one genuinely
  ambiguous row is read. Deleting that row bounds what any reading of it can do —
  72.2% still ambiguous — and along that axis the silent set only grows, so a
  reader who disagrees with the headline's reading of "and/or" still reaches the
  same decision.
- **Good, because** the same enumeration that kills the claim hands `assess-task` a
  real defect list. The table has been shipping with 8 silent tuples and a
  `standard-pr` row reachable on 2, which no consumer has reported because no
  consumer checks.
- **Bad, because** it leaves `assess-task` in a worse-documented state than it
  found it: the table is now _known_ to be partial, and nothing has been fixed. The
  precedence order is unwritten and this record does not write it.
- **Bad, because** the precedence order, once written, is an unmeasured guess.
  Nobody knows whether `verification-sensitive` should outrank `latency-loop`, and
  the collision counts say the choice will decide the label on most real tasks.
- **Bad, because** `confidence` is left with no defined producer under the design's
  own split. Today's skill self-reports it; the design removes the self-report and
  now has nothing to replace it with.

## Revisit when

- **A precedence order is proposed.** That is the one thing standing between here
  and `label` as code. The collision table in the script's output is the right
  input to writing it: the eight most frequent pairs are the orderings that
  actually matter.
- **A corpus of scored `task_profile` blocks exists.** The frequency question is
  unanswerable today. If real cards turn out to concentrate in the 65 unambiguous
  tuples, the precedence order matters less than the counts here suggest — though
  the 8 silent tuples would still need the default branch.
- **`select-coder` is asked whether `confidence` gates anything.** It reads
  `confidence: low` only to also consider the `runner_up` row. If `runner_up` is
  always populated when a second row fired, `confidence` may have no consumer at
  all, and the missing producer stops being a problem.
- **A consumer starts reading `label` for something other than a `matrix.md` row.**
  The tolerance for an arbitrary precedence order is entirely a function of what
  `label` decides downstream.

## Confirmation

Nothing enforces this. `scripts/validate.py` does not read the derivation table,
and no gate runs the enumerator — it is a record artifact, kept out of the
typecheck tiers alongside `dev_docs/research/*/references/*.py` for the reason
already written in `scripts/typecheck.sh`. The claim is re-checkable on demand and
only on demand: the script reads nothing from the repo, so if the table in
`skills/assess-task/SKILL.md` changes, the script keeps reporting the old table
until someone edits it. That is the known cost of freezing the evidence rather
than gating on it.

## Alternatives

- **Declare the table total by fiat and ship `label` as a lookup now.** Rejected:
  8 tuples fire nothing, and the first one is an ordinary single-file bugfix. The
  lookup would return no label for exactly the tasks `assess-task` sees most.
- **Keep asking a model for `label` as a Choice, and keep the distribution.**
  Rejected on rule 4 grounds, the same rule that took `scope` out of the call: once
  a precedence order exists, the answer is a function of values already in hand —
  the call's six dimensions plus the code-computed `scope` — and code owns that. It also fails on its own terms — the Choice would be
  fitted to a table the model can read, so its distribution would measure the
  table's ambiguity rather than the task's.
- **Drop `label` from the contract and have `select-coder` switch on the six
  dimensions directly.** Not rejected, but out of scope here: it removes the
  precedence problem by moving it into `matrix.md`, which would need its rows
  re-keyed. Worth weighing when the precedence order is written, since writing one
  is most of the work either way.
- **Keep `confidence` as a self-report while `label` becomes a lookup.** Rejected
  as incoherent with the design: the whole point of the typed profile is to remove
  the model that produces the self-report. A self-reported field with no
  self-reporter is a field that silently stops being filled.
