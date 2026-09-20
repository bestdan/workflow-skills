---
created: 2026-09-19
question: "Can a typed call tell an agent whether a job wants code, a typed call, or an LLM — and does it beat the agent's own judgment?"
feeds: ../../decisions/2026-09-19-no-tool-routing-call.md
---

# Routing a job to code, Jev or an LLM (2026-09-19)

Evidence for whether a typed model call can pick the right tool for a job better than
the agent facing the job can pick it unaided. What was decided from this is in
[`../../decisions/2026-09-19-no-tool-routing-call.md`](../../decisions/2026-09-19-no-tool-routing-call.md).

The rules this and the sibling note produced, for anyone proposing a typed call
rather than revisiting this one, are in
[`../../typed-model-calls.md`](../../typed-model-calls.md).

Sibling to [`2026-09-17-jev-applications/`](../2026-09-17-jev-applications/README.md), which
ranked where a typed call might fit at all. This grades one candidate that note did
not rank: the meta-decision an agent makes dozens of times a session.

## Method

`jev-1.13.0`, pinned rather than `jev-latest`, run 2026-09-19 with a live key. The
instrument is [`references/jev-pick-tool.py`](references/jev-pick-tool.py), kept beside
this record so the numbers can be re-run rather than taken on trust. It is frozen
evidence, not tooling: nothing imports it and no gate runs it.

Three passes of 36 cases, one request per case — each case is its own state, so the
docs' batching win does not apply to this shape. 96,942 input tokens over the three
passes, roughly $0.004. Median round trip 0.55s.

```
D=dev_docs/research/2026-09-19-jev-tool-routing/references
python3 $D/jev-pick-tool.py --analyze $D/measurement/suite-run.json   # every number below
python3 $D/jev-pick-tool.py --score $D/measurement/baseline/agent-1.json
python3 $D/test_jev_pick_tool.py                   # hermetic; no key, no network
python3 $D/jev-pick-tool.py --suite --repeat 3     # a fresh Jev run; costs a key
python3 $D/jev-pick-tool.py --cases > blind.json   # the blind dump, labels withheld
```

**Every number in this record is printed by one of the first three commands**, which
need neither a key nor the network — they read committed evidence. `--analyze` emits
the signal table, the fitted ladder and the leave-one-out figure; `--score` grades the
baseline answers; the test suite checks the ladder and the case set.

`references/measurement/` is that evidence, and it is a single coherent run rather
than an assemblage:

- `suite-run.json` — one `--suite --repeat 3` run: per case, per pass, the seven raw
  Jev answers and the routing they produced.
- `baseline/agent-{1,2,3}.json` — the three subagents' answers, exactly as written.
- `baseline-prompt.md` — the prompt they were given, verbatim.

The first version of this record committed none of it and reported numbers from two
different runs computed in a scratch script. That is the defect the record itself
warns about — a number in a document tracing to nothing committed — and finding 3
records what it cost when the gap was closed.

The baseline was three Claude subagents in independent contexts, each given the blind
dump, the three-line table, and one instruction not to read any repository file. That
last constraint is the measurement: an agent that grepped the repo would find the
labels in `jev-pick-tool.py` and score 36/36 by reading the answer key. Both sides are
graded by the same scorer, which is the only reason the two numbers can sit in one
table.

### The question shape, and why it is not a single Choice

The obvious design asks one Choice over `code | jev | llm`. This probe does not:

1. **It is not one snap judgment.** Choosing between three tools means modelling what
   each can do, which is the reasoning shape §6 of the sibling note records as a
   non-fit. Jev's rule 1 says split a question that needs extended reasoning.
2. **A question naming Jev is a question about Jev.** Asking a vendor's model whether
   its own product fits invites a bias nobody can subtract afterwards.

So seven questions ask only about **properties of the task** — none names a tool, none
can know Jev exists — and code maps properties to a tool. A test asserts that neither
a question nor a case text names a tool, because that phrasing rule is the validity of
the whole suite.

| question          | type  | asks                                                                 |
| ----------------- | ----- | -------------------------------------------------------------------- |
| `exact`           | noul  | Could a program with no model produce this answer exactly?           |
| `tally`           | noul  | Does the answer depend on counting or arithmetic?                    |
| `closed_output`   | noul  | Is the output one value from a set, a yes/no, or a point on a scale? |
| `prose_output`    | noul  | Does the output include text a person reads as language?             |
| `dependent_steps` | noul  | Does a later step depend on what an earlier one found?               |
| `needs_fetch`     | noul  | Must something be retrieved that is not already to hand?             |
| `stakes`          | score | How costly is acting on a wrong answer?                              |

`stakes` does not route. §5 of the sibling note measured confidence 16 points
overconfident against ground truth, so nothing here gates on being sure.

### The cases

36 decisions this repo already made and shipped, each phrased as the job was phrased
before the tool was chosen. Twelve of each at the time of the run; **13 / 12 / 11**
after finding 5 moved one case from `llm` to `code`:

- **`code`** — each a script in `scripts/`: `task-scan.py`'s readiness scan,
  `plan-graph.py`'s topological sort, `tier-coverage.py`'s partition check,
  `bump-version.py`'s bump level, two `validate.py` checks, and so on.
- **`jev`** — the sibling note's ranked fits: §7's deferral detection, §5's
  Conventional Commit type and three semantic lints, §1's skill routing, §3's
  `assess-task` dimensions, §4's size check, §6's dedupe and screening, §2's
  output-quality rating.
- **`llm`** — model judgments a skill body still asks for: authoring a PR body,
  §6's reconciler grading, `break-down-task`'s slicing, `plan-with-docs`' drafting,
  `deliver-task`'s diff judgment, ordinary debugging and search.

**Two thirds of the labels are shipped decisions; the other third is opinion.** Each
`code` case is a script that exists in `scripts/`, and each `llm` case is a model
judgment a skill body still asks for — those 24 are what the repo actually does. The 12
`jev` labels are not: they come from the sibling note's ranked applications, and that
note opens "Nothing here is adopted." They are one reader's judgment of where a typed
call _would_ fit.

That matters for how the scores read. Against the `code` and `llm` cases, a method is
being graded on what the repo shipped. Against the `jev` cases it is being graded on
agreement with a taxonomy the same author wrote — so an absolute accuracy over all 36
is worth less than the fact that **both methods were graded against the same labels**,
which is what makes the comparison survive the labels being partly opinion.

Two further biases, both against the set rather than the comparison: a job that landed
as code is one where code turned out to work, so the shipped two thirds are filtered by
survivorship; and two cases remain marked contested and are scored separately, so one
arguable label cannot carry the result.

## Findings

### 1. The first ladder scored 58%, and the cause was a signal nobody checked

Five rungs, three of them vetoes — retrieval, prose output, chained steps — placed
**ahead** of everything else, on the argument that a job which has to go and fetch
something is not one a single-shot script can do either, whatever else is true of it.

Measured, that argument is false:

- **58%**, identical to the point across all three passes.
- **10 of 12 `code` cases were vetoed into `llm`**, every one by a veto rung, with
  their `exact` signal sitting at 0.84–0.96.
- `needs_fetch` measured a mean of **0.56 on `code` cases and 0.56 on `llm` cases**.
  Identical. Not a weak signal — no signal. The model reads almost any job as needing
  to look something up, so a veto on it fires everywhere.
- `dependent_steps` was nearly as flat: 0.57 against 0.67.

This is the finding with the widest reach, and it is not about routing: a precedence
rule argued from first principles, resting on a signal whose per-label spread nobody
had checked, silently cost 42 points. Any ladder built on Jev signals is exposed to
it, and the flat signals are the dangerous ones — a signal that fires everywhere looks
decisive and decides nothing.

### 2. `exact` separates `code` cleanly; four of the seven signals separate nothing

Means over three passes, with the range across cases, against the corrected labels
(see finding 5):

| signal            | `code`               | `jev`                | `llm`            |
| ----------------- | -------------------- | -------------------- | ---------------- |
| `exact`           | **0.87** [0.62–0.97] | 0.17 [0.10–0.29]     | 0.23 [0.11–0.51] |
| `closed_output`   | 0.63 [0.10–0.97]     | **0.89** [0.79–0.96] | 0.20 [0.04–0.71] |
| `prose_output`    | 0.34                 | 0.40                 | 0.66 [0.21–0.97] |
| `dependent_steps` | 0.56                 | 0.35                 | 0.70             |
| `needs_fetch`     | 0.53                 | 0.21                 | 0.59             |
| `tally`           | 0.54                 | 0.16                 | 0.17             |

Regenerate this table with `--analyze` over the committed run (see **Method**); it is
printed, not transcribed.

The lowest `code` case sits at **0.62** (`rank-the-coders`) and the highest non-`code`
case at **0.51** (`find-the-assumers`) — a clean cut with 0.11 of daylight. `closed_output` separates `jev` at 81% on its own.
No other signal's best single cut beats 83%, and `needs_fetch` and `dependent_steps`
separate nothing at all.

### 3. Two rungs on two signals: 36/36 fitted, 34/36 leave-one-out

```
exact >= 0.60          -> code
closed_output >= 0.75  -> jev
otherwise              -> llm
```

Leave-one-out refits the thresholds on the other 35 cases each time and scores the
held-out one: **34/36 (94%)**, missing `did-the-coder-deliver` and `find-the-assumers`.
Three fresh passes against the live API with the fitted thresholds: 36/36 each, no case
unstable across passes.

**That 94% was published as 97% in the first version of this record, and the
difference was an undocumented tie-break.** Two threshold pairs reach 100% on the full
set — `exact >= 0.55` and `exact >= 0.60`, both with `closed >= 0.75` — because the
optimum is a plateau, not a point. Which one a fold picks decides the held-out answer:
resolving ties toward the higher cut gives 35/36, toward the lower gives 34/36. The
first number came from a scratch script whose `max()` happened to break ties upward,
and nothing recorded that it had. `fit_thresholds` now resolves ties toward the
**lower** cut and says why in its docstring, so the figure is 94% and it is derivable
rather than incidental.

Read the honest version as **94–97% depending on an arbitrary choice** — quoting a
single figure to two significant digits overstated what a 36-case plateau can support.
The conservative end is the one reported.

`exact`'s shipped constant is 0.60. The fit prefers 0.55; both score 100% on the full
set, and the shipped value is left where the earlier refit put it because moving it
changes no case. 94% is the ceiling of a friendly measurement, not an accuracy claim:
the thresholds are fitted to this set and the set is one person's.

### 4. The unaided agent matched it with no fitting at all

|                                | score      | uncontested |
| ------------------------------ | ---------- | ----------- |
| Jev, two-rung ladder, 3 passes | 36/36 each | 34/34 each  |
| Unaided agent × 3 runs         | 36/36 each | 34/34 each  |

A tie on the score, and not a tie on what it cost. Jev's side needed two thresholds
fitted to this very set, and its cross-validated estimate is 97% rather than 100%. The
agents' side needed three lines of prose and no fitting, and produced no
cross-validation gap because there was nothing to cross-validate.

**The three agents also agreed with each other on every one of the 36 cases** — no
disagreement anywhere. Jev's answers flipped on two cases between passes under the
first ladder. On this evidence the agent is the more stable of the two as well as the
cheaper.

### 5. The agents caught an error in the ground truth

`rank-the-coders` — _given a task profile already scored on six dimensions and a matrix
of each coder's strengths, order the coders by fit._ It was labelled `llm`. All three
agents said `code`, against the label and against Jev, which agreed with the label.

**The agents were right.** The sibling note says "once a profile exists, mapping it
through `matrix.md` is a lookup," and a lookup is code. The `llm` label came from
reading that note's _Where it does not belong_ section as "belongs to no tool" when it
means "does not belong to **Jev**."

The label was corrected in a commit of its own, after the run, so the run's numbers
stay readable as they were measured. Both scorings are reported here: against the
label as shipped, Jev 36/36 and the agents 35/36; against the corrected label, both
36/36, with Jev needing its `exact` threshold refit from 0.65 to 0.60 to get there.

The correction moves a point away from the thing being probed and toward the
incumbent, which is the direction that makes it safe to apply after the fact. Had it
gone the other way it should have stayed as measured.

**This is the strongest single result in the run.** Not that the agents scored well —
that they found a defect in the labels they were being graded against, which the
typed call did not.

## What these numbers cannot support

Both methods sit at 36/36, so **nothing here ranks them on accuracy.** That is a
ceiling effect and it says the cases are easy — `exact` separating `code` with 0.13 of
daylight is a symptom of the same thing. What the run separates is cost and fitting,
not correctness: one side needed two thresholds tuned to this set and still
cross-validates at 97%, the other needed three lines of prose.

The tie is enough for the decision, which turns on whether the call is _needed_, and
not enough for a claim that either method is better.

Verified here: the scores, the signal spreads, the inter-agent agreement, the
leave-one-out figure. Inferred, not verified: that the case set generalises. It was
written by one person from one repository's history, and the labels are that person's
reading of what shipped.
