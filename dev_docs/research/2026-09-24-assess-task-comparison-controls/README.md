---
created: 2026-09-24
question: "Is the assess-task comparison's don't-adopt verdict a finding about Jev, or about any rater that is not the Opus panel?"
feeds: ../../decisions/2026-09-24-assess-task-stays-a-subagent.md
---

# `assess-task` comparison: controls and a re-asked typed call (2026-09-24)

**Mostly about any rater that is not the Opus panel.** Two general-purpose LLMs
were given the incumbent's own prompt, plus a one-line instruction to follow it. Both miss gate (a) on every
measurable dimension. Codex misses by 11–14 points and an open-weight model by
more. Jev sits a few points below codex and above the open-weight model on all
three dimensions, at about 15× lower latency than codex. The verdict in
[`../2026-09-24-assess-task-comparison-results.md`](../2026-09-24-assess-task-comparison-results.md)
stands under its rule. What this adds is that the rule's reference, three runs of
one model, is a bar no non-Claude rater measured here clears.

Re-asking Jev with the incumbent's own prior (finding 4) closed the two
unmeasurable yes/no dimensions and left the verdict unchanged. It did not help
either graded dimension, and it made `complexity` worse.

This record sits beside that verdict and does not replace it. The method record,
[`../2026-09-22-assess-task-comparison/README.md`](../2026-09-22-assess-task-comparison/README.md),
allows a revised reading only in that position. Everything here was run after the
original numbers were seen, on the same 50 cards that verdict scored.

## Question

Is the don't-adopt verdict a finding about Jev, or about any rater that is not the
Opus panel? And which of Jev's gaps come from how it was asked rather than from the
model?

## Method

Everything is scored against the committed Opus panel
(`../2026-09-22-assess-task-comparison/references/measurement/baseline/agent-{1,2,3}.json`)
with the scorer's own functions: `mean_cross` for agreement and `gate_d` for
direction. Each control arm is one run over all 50 cards, so it is scored as a
fourth panel member would be. `references/score-arms.py` prints every number in
findings 1–3:

```
D=dev_docs/research/2026-09-24-assess-task-comparison-controls/references
python3 $D/score-arms.py $D/measurement/opus-jevq.json $D/measurement/codex.json $D/measurement/crush.json
python3 $D/decoder-sensitivity.py
```

The three control arms, all run on 2026-09-24 by
[`references/run-arms.py`](references/run-arms.py). Each makes one fresh process
per card, in a fresh temporary directory, on the same fenced card text. Tool
access differs by arm, and the table says how:

| arm         | model                                   | prompt                                                                                           | tools                                                                                                   |
| ----------- | --------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| `opus-jevq` | `claude-opus-5-5`                       | Jev's six questions (v1 `questions.json`) verbatim, as a form to fill                            | none (`--tools ""`)                                                                                     |
| `codex`     | `gpt-5.6-terra`, `codex exec` read-only | the incumbent's skill prompt byte for byte on stdin, plus a one-line argument prompt (`POINTER`) | its shell remains, sandboxed read-only; only the prompt tells it not to explore or run commands         |
| `crush`     | `hyper/kimi-k2.7-code` (open-weight)    | the same stdin payload and `POINTER`                                                             | every built-in tool disabled by `skills/co-review/reviewers/assets/crush-readonly.json`, in its own cwd |

`POINTER` tells the model its input is on stdin, to follow it exactly, and not to
explore the filesystem or run commands. So the codex and crush inputs are the
incumbent's prompt plus that one line, not the prompt alone.

All 150 calls returned a parseable block. No value was missing or outside its enum.

## Findings

### 1. No non-Claude rater clears gate (a); Jev falls between the two that were tried

Agreement with the Opus panel. `A_const` is the base rate, and "panel" is the
panel's agreement with itself:

| dimension                  | panel | `A_const` | **Jev** (v1) | opus-jevq | codex | crush  |
| -------------------------- | ----- | --------- | ------------ | --------- | ----- | ------ |
| `complexity`               | 0.973 | 0.727     | **0.800**    | 0.947     | 0.833 | 0.760  |
| `creativity`               | 0.973 | 0.753     | **0.800**    | 0.827     | 0.860 | 0.680  |
| `verification_criticality` | 0.920 | 0.720     | **0.773**    | 0.913     | 0.793 | 0.640  |
| tuple of the three         | 0.880 | 0.420     | **0.491**    | 0.700     | 0.573 | 0.267  |
| median latency per card    | 3.8 s |           | **0.4 s**    | 3.3 s     | 6.4 s | 10.7 s |

At the rule's 5-point tolerance, codex fails gate (a) on all three dimensions
despite having the incumbent's own prompt. Crush falls below the base rate on
`creativity` and `verification_criticality`. Jev trails codex by 3, 6 and 2 points
and leads crush by 4, 12 and 13.

The raters also agree with each other little better than they agree with Jev. On
the three dimensions, mean pairwise agreement is 0.700 for Jev (its first pass) with codex, 0.713
for codex with crush, and 0.660 for Jev with crush.

### 2. Jev's lean is stronger than the others', but it is not unique to Jev

Gate (d) misses, lower : higher, against the panel majority:

| dimension                  | Jev (3 passes pooled) | opus-jevq | codex  | crush  |
| -------------------------- | --------------------- | --------- | ------ | ------ |
| `complexity`               | 0 : 32                | 0 : 2     | 1 : 8  | 3 : 9  |
| `creativity`               | 3 : 25                | 0 : 8     | 4 : 3  | 2 : 14 |
| `verification_criticality` | 24 : 6                | 2 : 3     | 1 : 10 | 0 : 18 |

On `complexity` only Jev is one-way on every miss, but codex also leans upward
(8 of 9). On `verification_criticality`, codex and crush also err one way, but
upward, the opposite side from Jev. Gate (d) would fail codex on both of those
dimensions: `complexity` (8 of 9, on 9 cards) and `verification_criticality`
(10 of 11, on 11 cards). It would fail crush on `creativity` and
`verification_criticality`, and opus-jevq on `creativity` (0 : 8).

### 3. On `complexity` and `verification_criticality` the gap is the model; on `creativity` and `autonomy` it is the asking

Opus given Jev's exact questions stays near the panel on `complexity` (0.947) and
`verification_criticality` (0.913). So Jev's gap on those two is not the wording.
Opus reads the literal "the check is the point" question the panel's way as well.

On `creativity` (0.827, misses 0 : 8) and `autonomy` (0.820, misses 0 : 9 against a
panel that said `bounded` 150 times), Opus drifts upward just as Jev does. The v1
questions drop the rubric's prior, "Most tasks are obvious; reach for the harder
value only when the description warrants it", which the incumbent always had.
These two dimensions are where that shows.

`references/decoder-sensitivity.py` points the same way without a new call. It
re-decodes the committed Jev run, fitting the escalation cutoff on 49 cards and
scoring the held-out 50th:

- the `autonomy` gap goes from −18.7 (the scorer's figure) to −0.7, and
  `cost_sensitivity` from −19.4 to −5.3;
- the `creativity` gap roughly halves, to −10;
- `complexity` gets worse (−24) and so does `verification_criticality` (−17).

### 4. The re-asked typed call (v2)

`references/questions-v2.json` restores the prior on every question and drops
`complexity`'s "with a clear approach", a clause v1 added that the rubric lacks.
Nothing else changes. It is committed, with this section, before any request using
it is sent. [`references/jev-rerun.py`](references/jev-rerun.py) runs it through
the 2026-09-22 client unchanged, with 3 passes on the same model, cards and state.

**Expected, stated before the run.** Finding 3's control suggests:

- `creativity` and `autonomy` move toward the panel;
- `complexity` and `verification_criticality` move little;
- under the original rule the verdict stays don't adopt, because gate (d) on
  `complexity` is Jev's own.

If `complexity` or `verification_criticality` closes to within 5 points, finding 3
is wrong about where their gap comes from.

**Result.** Three passes run on 2026-09-24, after the commit above. The run is
[`references/measurement/jev-run-v2.json`](references/measurement/jev-run-v2.json),
scored by the 2026-09-22 scorer unchanged:

```
O=dev_docs/research/2026-09-22-assess-task-comparison/references
python3 $O/compare-assess-task.py --score $D/measurement/jev-run-v2.json \
    $O/measurement/baseline/agent-1.json $O/measurement/baseline/agent-2.json \
    $O/measurement/baseline/agent-3.json
```

| dimension                  | panel | `A_const` | Jev v1 | **Jev v2** | v2 misses (L : H) | predicted           |
| -------------------------- | ----- | --------- | ------ | ---------- | ----------------- | ------------------- |
| `autonomy`                 | 1.000 | 1.000     | 0.813  | **0.980**  | 0 : 3             | toward the panel: ✔ |
| `cost_sensitivity`         | 0.987 | 0.953     | 0.793  | **0.947**  | 6 : 1             | not predicted       |
| `creativity`               | 0.973 | 0.753     | 0.800  | **0.793**  | 3 : 26            | toward the panel: ✘ |
| `complexity`               | 0.973 | 0.727     | 0.800  | **0.713**  | 0 : 45            | little movement: ✘  |
| `verification_criticality` | 0.920 | 0.720     | 0.773  | **0.760**  | 33 : 3            | little movement: ✔  |
| tuple of the three         | 0.880 | 0.420     | 0.491  | **0.420**  | —                 | —                   |

Under the original rule the verdict is unchanged: don't adopt, with no measurable
dimension matching. `S_jev` is 0.97–1.00 throughout, and latency and dollars are
unchanged (p95 0.432 s, $0.000065 per packet).

The prediction held for the binaries and failed for both Scores:

- **The prior fixed what it was aimed at on the Nouls.** On `autonomy` and
  `cost_sensitivity` the in-question prior did what the decoder re-fit in finding 3
  did, and both are now within about 4 points of the panel.
- **The prior did not move `creativity`**, although Opus given v1's wording drifted
  up the same way Jev did. The same sentence steers a Claude model and does not
  steer Jev's Score.
- **`complexity` got worse, not stable.** It fell 8.7 points to below its base rate,
  and every miss is still upward (45 of 45). The two edits were a prior toward the
  lower levels and a looser "Standard" level, and both should have pulled
  downward. Jev moved the other way. Which edit did it is not separable from one
  run.
- **`verification_criticality` barely moved**, but its misses grew more one-sided
  (33 : 3 from 24 : 6). The prior points toward "no", which is the side Jev already
  erred on.

So finding 3's split holds for where the gap on `complexity` and
`verification_criticality` comes from: a rewording did not close it. It does not
hold for `creativity`. There, a wording that steers a general model left Jev
unmoved. How Jev responds to a question's wording is not predictable from how a
chat model responds to it.

## What this does not establish

- **An independent test.** These 50 cards have now been studied closely. The v2
  wording was written after the v1 numbers were seen, so its result is a
  sensitivity check on these cards. Only a run on cards this work never looked at
  could move the verdict.
- **That any rater is right.** Every number here is agreement with one model's
  panel. Finding 1 says the bar is effectively that model. It does not say which
  rater reads a card correctly.
- **The control arms' stability.** Each is one run, so there is no `S` for codex,
  crush or opus-jevq, and their agreement figures carry one run's noise.
- **Production cost for codex or crush.** Their latencies are one CLI process per
  card, measured like the incumbent's stand-in. No dollars were recorded for codex.
  Crush's calls ran on Charm Hyper's free credits.

## Feeds

[`../../decisions/2026-09-24-assess-task-stays-a-subagent.md`](../../decisions/2026-09-24-assess-task-stays-a-subagent.md)
