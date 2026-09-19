---
created: 2026-09-19
question: "Can a typed call tell an agent whether a job wants code, a typed call, or an LLM — and does it beat the agent's own judgment?"
feeds: ../../decisions/2026-09-19-no-tool-routing-call.md
---

# Routing a job to code, Jev or an LLM (2026-09-19)

Evidence for whether a typed model call can pick the right tool for a job better than
the agent facing the job can pick it unaided. What was decided from this is in
[`../../decisions/2026-09-19-no-tool-routing-call.md`](../../decisions/2026-09-19-no-tool-routing-call.md).

Sibling to [`2026-09-17-jev-applications.md`](../2026-09-17-jev-applications.md), which
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
python3 $D/jev-pick-tool.py --suite --repeat 3     # the Jev run
python3 $D/jev-pick-tool.py --cases > blind.json   # labels withheld
python3 $D/jev-pick-tool.py --score answers.json   # grades either side
python3 $D/test_jev_pick_tool.py                   # hermetic; no key, no network
```

The baseline was three Claude subagents in independent contexts, each given the blind
dump, the three-line table in the decision record, and one instruction not to read any
repository file. Both sides are graded by the same scorer, which is the only reason
the two numbers can sit in one table.

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

**The labels are shipped decisions, not annotations invented for the probe.** That is
their strength and their bias: a job that landed as code is one where code turned out
to work, so the set is filtered by survivorship. Two cases remain marked contested and
are scored separately, so one arguable label cannot carry the result.

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
| `exact`           | **0.87** [0.63–0.97] | 0.17 [0.10–0.31]     | 0.22 [0.11–0.50] |
| `closed_output`   | 0.63 [0.10–0.97]     | **0.88** [0.79–0.96] | 0.20 [0.04–0.70] |
| `prose_output`    | 0.34                 | 0.41                 | 0.66 [0.22–0.97] |
| `dependent_steps` | 0.56                 | 0.35                 | 0.70             |
| `needs_fetch`     | 0.53                 | 0.21                 | 0.60             |
| `tally`           | 0.54                 | 0.16                 | 0.17             |

The lowest `code` case sits at **0.63** and the highest non-`code` case at **0.50** —
a clean cut with 0.13 of daylight. `closed_output` separates `jev` at 81% on its own.
No other signal's best single cut beats 83%, and `needs_fetch` and `dependent_steps`
separate nothing at all.

### 3. Two rungs on two signals: 36/36 fitted, 35/36 leave-one-out

```
exact >= 0.60          -> code
closed_output >= 0.75  -> jev
otherwise              -> llm
```

Leave-one-out refits the thresholds on the other 35 cases each time and scores the
held-out one: **35/36 (97%)**. Three fresh passes against the live API with the fitted
thresholds: 36/36 each, no case unstable across passes.

`exact`'s constant was 0.65 before finding 5 corrected a label, and 0.60 after.
**Leave-one-out is 97% at either value** — the cross-validated estimate does not move,
which is what says the refit tidies a constant rather than buying accuracy.

97% is the ceiling of a friendly measurement, not an accuracy claim: the thresholds
are fitted to this set and the set is one person's.

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
