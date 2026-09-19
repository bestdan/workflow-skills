---
created: 2026-09-19
question: "Can a typed call tell an agent whether a job wants code, a typed call, or an LLM — and does it beat the agent's own judgment?"
feeds: ../decisions/2026-09-19-no-tool-routing-call.md
---

# Routing a job to code, Jev or an LLM (2026-09-19)

Evidence for whether a typed model call can pick the right tool for a job better than
the agent facing the job can pick it unaided. What was decided from this is in
[`../decisions/2026-09-19-no-tool-routing-call.md`](../decisions/2026-09-19-no-tool-routing-call.md).

Sibling to [`2026-09-17-jev-applications.md`](2026-09-17-jev-applications.md), which
ranked where a typed call might fit at all. This grades one candidate that note did
not rank: the meta-decision an agent makes dozens of times a session.

## Method

`jev-1.13.0`, pinned rather than `jev-latest`, run 2026-09-19 with a live key. The
instrument is [`scripts/jev-pick-tool.py`](../../scripts/jev-pick-tool.py), which is
kept so these numbers can be re-run rather than taken on trust.

Three passes of 36 cases, one request per case — each case is its own state, so the
docs' batching win does not apply to this shape. 96,942 input tokens over the three
passes, roughly $0.004. Median round trip 0.55s.

```
python3 scripts/jev-pick-tool.py --suite --repeat 3     # the Jev run
python3 scripts/jev-pick-tool.py --cases > blind.json   # labels withheld
python3 scripts/jev-pick-tool.py --score answers.json   # grades either side
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
before the tool was chosen:

- **12 `code`** — each a script in `scripts/`: `task-scan.py`'s readiness scan,
  `plan-graph.py`'s topological sort, `tier-coverage.py`'s partition check,
  `bump-version.py`'s bump level, two `validate.py` checks, and so on.
- **12 `jev`** — the sibling note's ranked fits: §7's deferral detection, §5's
  Conventional Commit type and three semantic lints, §1's skill routing, §3's
  `assess-task` dimensions, §4's size check, §6's dedupe and screening, §2's
  output-quality rating.
- **12 `llm`** — model judgments a skill body still asks for: authoring a PR body,
  §6's reconciler grading, `break-down-task`'s slicing, `plan-with-docs`' drafting,
  `deliver-task`'s diff judgment, ordinary debugging and search.

**The labels are shipped decisions, not annotations invented for the probe.** That is
their strength and their bias: a job that landed as code is one where code turned out
to work, so the set is filtered by survivorship. Three cases are marked contested and
scored separately, so one arguable label cannot carry the result.

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

### 2. `exact` separates `code` with no overlap; four of the seven signals do not separate anything

Means over three passes, with the range across cases:

| signal            | `code`               | `jev`                | `llm`            |
| ----------------- | -------------------- | -------------------- | ---------------- |
| `exact`           | **0.89** [0.66–0.97] | 0.17 [0.10–0.31]     | 0.25 [0.11–0.63] |
| `closed_output`   | 0.66 [0.10–0.97]     | **0.88** [0.79–0.96] | 0.20 [0.04–0.70] |
| `prose_output`    | 0.35                 | 0.41                 | 0.63 [0.22–0.97] |
| `dependent_steps` | 0.57                 | 0.35                 | 0.67             |
| `needs_fetch`     | 0.56                 | 0.21                 | 0.56             |
| `tally`           | 0.52                 | 0.16                 | 0.23             |

Every `code` case sits at 0.66 or above on `exact`; every other case at 0.63 or below.
A clean single cut, no overlap. `closed_output` separates `jev` at 81% on its own. The
best single cut for any other signal is 83% (`prose_output` for `llm`), and the rest
are noise.

### 3. Two rungs on two signals score 36/36 fitted, 35/36 leave-one-out

```
exact >= 0.65          -> code
closed_output >= 0.75  -> jev
otherwise              -> llm
```

Leave-one-out refits the thresholds on the other 35 cases each time: **35/36 (97%)**.
Three fresh passes against the live API with the fitted thresholds: 36/36 each.

The thresholds are fitted to this set and the set is one person's, so 97% is the
ceiling of a friendly measurement, not an accuracy claim.

### 4. The unaided agent matched it, and agreed with itself perfectly

|                                | score      | uncontested |
| ------------------------------ | ---------- | ----------- |
| Jev, two-rung ladder, 3 passes | 36/36      | 33/33       |
| Unaided agent × 3 runs         | 35/36 each | 33/33 each  |

**The three agents agreed with each other on every one of the 36 cases** — no
disagreement anywhere. Jev's own answers flipped on two cases between passes under the
first ladder. On this evidence the agent is the more stable of the two, not only the
cheaper.

### 5. The single disagreement is one where the label is wrong

`rank-the-coders` — _given a task profile already scored on six dimensions and a matrix
of each coder's strengths, order the coders by fit._ All three agents said `code`. The
label says `llm`, and Jev agreed with the label.

The agents have the better of it. The sibling note says "once a profile exists, mapping
it through `matrix.md` is a lookup," and a lookup is code. The label came from reading
that note's _Where it does not belong_ section as "belongs to no tool" when it means
"does not belong to **Jev**."

**The label is left as written.** Correcting it now would be grading an answer against
a label chosen after seeing the answer, which is what §7 of the sibling note refuses
to do with its own single miss. It stays marked contested, the reasoning sits in a
comment on the case, and the uncontested column is the number to read. Corrected, the
scoreline would read agent 36/36 against Jev 35/36.

## What these numbers cannot support

Two methods both sitting at 97–100% cannot be ranked against each other. That is a
ceiling effect and it says the cases are easy — `exact` separating `code` with
literally zero overlap is a symptom of the same thing. Any claim that one method is
_better_ than the other is outside what this run measured.

Verified here: the scores, the signal spreads, the inter-agent agreement, the
leave-one-out figure. Inferred, not verified: that the case set generalises. It was
written by one person from one repository's history, and the labels are that person's
reading of what shipped.
