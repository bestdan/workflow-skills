---
created: 2026-09-20
question: "Can a typed call forecast blast radius from a task card, before the work exists — and can this repository's own history measure that?"
feeds: ../../decisions/2026-09-20-drop-subsystem-floor-from-predictive-scope.md
---

# Forecasting `scope` from a card (2026-09-20)

Evidence for the half of
[`../../designs/2026-09-18-assess-task-typed-profile.md`](../../designs/2026-09-18-assess-task-typed-profile.md)
that nothing has measured: whether `scope` can be forecast from prose, where there is
nothing to count. That design's own text calls this "the part of the design most likely
to be wrong."

Sibling to [`../2026-09-17-jev-applications.md`](../2026-09-17-jev-applications.md),
whose §3 measured the **post-hoc** case — 71.6% exact against 116 merged pull requests,
with 31 of 33 misses under-reading blast radius — and recorded its own scope limit:
ground truth was the file count of a pull request that had already merged, so every
case handed the model a change that had already happened.

**As the design specifies it, no — and the design's own seventh question is why.**
Asked with the upward-only subsystem floor the design adds, the call scores 60.7%
against a 55.6% base rate and sits on the majority-class baseline of the one boundary
this corpus can measure. Remove that floor and the same answers reach 68.1%, a margin
of 12.6 points over a 2.2-point run-to-run spread, with the error direction reverting
to the under-read §3 measured. Which decoder reads the Score — round its mean, or take
the argmax of its distribution — moves the result by at most one case per pass.

So the finding is about the wrapper, not the model: **the correction the design built
against §3's under-read is what breaks the question when it is asked predictively.**
Findings 6 to 11 are the measurement; 1 to 5 are what the corpus can and cannot
support, and they bound what the measurement is allowed to claim.

## Method

`jev-1.13.0`, pinned rather than `jev-latest`, run 2026-09-20 with a live key. Three
passes of 45 cases, both questions riding on one request per case. The corpus is this
repository's own issue and pull request history, read through `gh` the same day.

```
D=dev_docs/research/2026-09-20-predictive-scope/references
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json  # every number
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json --ablate-floor
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json --decoder argmax
python3 $D/build-corpus.py --profile $D/measurement/corpus.json             # the corpus
python3 $D/test_jev_predictive_scope.py                                     # hermetic
python3 $D/build-corpus.py --from-api                        # rebuild the corpus; needs gh
python3 $D/jev-predictive-scope.py --suite --repeat 3        # a fresh run; costs a key
```

**Every number in this record is printed by one of the first three commands**, which
need neither a key nor the network — they read committed evidence. `--analyze`
recomputes each figure from the committed raw answers rather than reading back an
aggregate frozen at run time; finding 9 is why that matters. The first version of the
sibling routing record committed none of its evidence and reported numbers from two
different runs computed in a scratch script, and closing that gap is what its finding
3 cost.

`references/measurement/` is that evidence:

- `corpus.json` — the 45 cases, each with the card as filed, the pull request that
  closed it, and the changed-file count that labels it.
- `suite-run.json` — one `--suite --repeat 3` run: per case, per pass, the raw Score,
  the four-entry distribution it is the mean of, the Noul, and the level they produced.
  This is the third run. The first stored a mis-decoded level; the second stored the
  Score's mean without its distribution and could not be re-decoded (finding 9). Both
  are in this file's git history; their means are cited where reproducibility across
  runs is the point.

**The corpus is 45 task cards paired with the blast radius the work actually had.** A
case is a closed issue whose text existed before the pull request that closed it,
labelled with that pull request's changed-file count under the design's cut points.
`created_at(issue) < created_at(pull request)` is a gate rather than a nicety: a card
written or rewritten after its pull request would reproduce exactly the post-hoc defect
this record exists to remove, and would do it invisibly. One case was dropped by that
gate; a test pins it.

## Findings

### 1. The corpus exists, and it is thinner than §3's

82 closed issues; 36 have no merged pull request that closed them; 45 of the remaining
46 have a card that predates the work.

| level            | rule                     | cases |
| ---------------- | ------------------------ | ----- |
| `single-file`    | 1 changed file           | 2     |
| `pr-sized`       | 2–5                      | 25    |
| `multi-file`     | 6+                       | 18    |
| `whole-codebase` | ≥ half the tracked files | **0** |

45 is comparable to the routing record's 36 cases and well short of §3's 116. The
changed-file counts run 1 to 24, with a single case above 10.

### 2. `whole-codebase` has no instance, so the probe cannot license that cut

The design invents the `whole-codebase` boundary — "changed files ≥ half the tracked
files" — and flags it as the one cut with no measurement behind it. Step 4 of #782
asked this probe to re-check it while it was in there.

**It cannot.** No pull request in this repository's history comes near half the tracked
files; the largest touched 24. The corpus can establish that the boundary is never
reached in practice, which is worth knowing and is not the same as validating where it
sits. The boundary is implemented in `build-corpus.py` so it stays inspectable, and a
test pins its arithmetic, but nothing here measures whether it is in the right place.

### 3. The base rate is 55.6%, and §3's 71.6% is not comparable to it

Always answering `pr-sized` scores 25/45. A result has to clear that, not 25%: a
four-level question over a distribution this skewed is easy to score well on for the
wrong reason. Finding 6 is what that constraint does to the measured number.

This also breaks the comparison the design invites. §3's 71.6% was measured against a
different distribution — 116 merged pull requests, not 45 cards — so the two numbers
cannot be set beside each other, and a result landing near 71.6% here would mean
something different from what §3's did. `--analyze` prints the base rate beside every
accuracy figure so the two cannot drift apart in the reporting.

### 4. The four-level question is mostly one boundary wearing a larger label

43 of the 45 cases sit in `pr-sized` or `multi-file`, split at the 5/6-file line.

That is a limitation and an aim at once. §3's failure was concentrated at exactly this
boundary — 31 of 33 misses were `multi-file` read as `pr-sized` — so a corpus massed
there is pointed at the known failure mode. But a "four-level accuracy" number off it
would be that binary in disguise. So the instrument reports both: the four-level exact
rate the design asked for, and the boundary-only rate, from the same run at no extra
cost.

### 5. The linkage rate is itself a constraint on any future corpus

36 of 82 closed issues had no merged pull request that closed them — 44%. Some were
closed as duplicates or won't-fix, but the rate means that widening this corpus by
waiting for more issues to close is slower than it looks: roughly half of what closes
never becomes a labelled case.

### 6. As specified, the four-level result barely clears its own run-to-run spread

| pass | exact         | within one   | boundary only |
| ---- | ------------- | ------------ | ------------- |
| 1    | 26/45 — 57.8% | 45/45 — 100% | 24/43 — 55.8% |
| 2    | 28/45 — 62.2% | 45/45 — 100% | 26/43 — 60.5% |
| 3    | 28/45 — 62.2% | 45/45 — 100% | 26/43 — 60.5% |

Mean exact is 60.7% against a **55.6% base rate** — a 5.2-point margin over a 4.4-point
spread. On the previous run (git history) the same configuration scored 60.0% with a
6.7-point spread, and the margin was inside it. A result that clears the noise on one
draw and not on the next is not a result.

That is rule 5 of [`../../typed-model-calls.md`](../../typed-model-calls.md) biting:
Jev is not deterministic, and a single pass is an anecdote whatever it says. A one-pass
run here could report 62.2% and read as a clear win over the baseline.

### 7. As specified, the call performs at the baseline on the boundary that matters

The `pr-sized`/`multi-file` slice is 43 of the 45 cases and has its own majority class:
25 of 43 are `pr-sized`, so always answering `pr-sized` scores **58.1%**.

The call scores 55.8%, 60.5% and 60.5% — mean 58.9%. **This is the finding for the
design as written**, because it is the only boundary the corpus has enough cases on
either side of to say anything about. Finding 4 predicted the four-level number would
mostly be this binary in disguise, and the two rates move together exactly as it said.

The overall base rate (55.6%) is the easier comparison and flatters the result; the
slice's own baseline (58.1%) is the honest one. `--analyze` prints both beside their
accuracies so they cannot be quoted apart.

### 8. As specified, the error direction inverts against §3

§3 measured 31 of 33 misses as under-reads — `multi-file` read as `pr-sized`. Here, as
the design specifies, the misses run the other way: 38 over-reads against 15
under-reads across three passes, dominated by `pr-sized` read as `multi-file`.

Finding 11 shows this is the floor's doing, not the question's: with the floor removed
the direction reverts to §3's, 43 under-reads and 0 over-reads. Whatever the model is
doing, it is doing it consistently across the post-hoc and predictive regimes, and the
floor was masking it.

### 9. The instrument was wrong twice, and the record keeps both

**First: a Jev Score is not in [0, 1].** The first version of `level_from` assumed a
normalised score and multiplied by four, so every case above 0.75 landed in
`whole-codebase`: 39 of 45, an apparent 4.4% against a 55.6% base rate, with the misses
running 42 over-reads to 1 under-read. The number alone would have been survivable. The
_direction_ was not — §3 measured a one-directional under-read, and a near-total
inversion of that is a claim about the instrument before it is a claim about the model.
The scale was then confirmed against the sibling routing probe's committed `stakes`
answers (0.580 to 2.390 over 108 answers, four criteria) rather than assumed.

**Second: a Score is the mean of a distribution, and the distribution is in the
response.** Dumping one raw answer, which should have been the first step:

```
"score": 1.0,
"probabilities": {"0": 0.0, "1": 1.0, "2": 0.0, "3": 0.0}
```

`score` is Σ pᵢ·i. The vendor's own Claude Code skill, installed after these runs and
read afterwards, says the same in one line — a Score is a "probability-weighted
position on ordered levels" — which is what the applications record meant when it
called that skill "worth knowing before hand-writing a client". Rounding the mean
reads a bimodal answer as its midpoint — {0: 0.4, 1: 0.2,
2: 0.4} has a mean of 1.0 and rounds to `pr-sized`, the one level the model was ruling
out. The second run stored only the mean, so the argmax decoder could not be applied to
it and it had to be run again. Every run from now on stores the whole distribution, a
test asserts it is present and that the stored mean is its mean, and `--decoder`
reports both readings from the same committed answers.

This is the same defect class as the routing record's finding 2, where a rule resting
on a signal whose per-label spread nobody had printed cost 42 points. Two rules in
[`../../typed-model-calls.md`](../../typed-model-calls.md) carry what it cost here.

### 10. The decoder does not matter

Same committed answers, all four combinations, mean four-level exact:

|                          | `round` the mean | `argmax` the distribution |
| ------------------------ | ---------------- | ------------------------- |
| with floor (as designed) | 60.7%            | 60.0%                     |
| floor removed            | **68.1%**        | **67.4%**                 |

The two decoders differ by at most one case per pass. The distributions the model
returns are concentrated enough that the mean and the mode almost always land on the
same level — which is worth knowing, and could not have been known without storing the
distribution.

### 11. Removing the design's floor is what the result turns on

The subsystem Noul fires on 26–28 of the 45 cases per pass. Two ways of counting what
it does, one of which needs no decoder at all:

- **Decoder-free.** The floor can only raise a level to `multi-file`. On 13, 12 and 13
  of its firings the card's true label is _below_ `multi-file`, so the raised answer is
  wrong however the Score is read: **38 of 81 firings wrong by construction.**
- **Under `round`.** It changes the level on 20–22 cases per pass, and is right 25
  times against 35 wrong.

Re-scoring the same committed answers with it removed — `--ablate-floor`, no new API
call:

|                        | as the design specifies | floor removed              |
| ---------------------- | ----------------------- | -------------------------- |
| four-level exact, mean | 60.7%                   | **68.1%**                  |
| margin over base rate  | +5.2 (spread 4.4)       | **+12.6 (spread 2.2)**     |
| boundary only, mean    | 58.9% (baseline 58.1%)  | **66.7%** (baseline 58.1%) |
| error direction        | 38 over / 15 under      | **0 over / 43 under**      |

Three things change at once. The margin over the base rate becomes several times the
run-to-run spread. The error direction reverts to §3's. And the spread itself halves —
the floor was adding noise as well as bias.

**The floor-free 68.1% reproduced on an independent draw**: the second run (git
history) gave 68.1% under the same ablation. So it is not a noise artifact. It is still
the same 45 cards, so it is not a held-out confirmation either: two variants were
scored on one corpus and the better one is reported, which is the hazard rule 4 names
even though nothing here is a fitted threshold. What the ablation establishes solidly
is the negative half — the floor as specified makes this worse, and 38 of its 81
firings are wrong before any decoder is chosen. That the floor-free variant reaches
68.1% on cards this run did not touch is a result wanting confirmation, not a
measurement of it.

## What the instrument does, that the design did not specify

- **The questions quote no numbers.** §3's re-run scored 40/40 by redefining the levels
  as file-count ranges, at which point the question was arithmetic and rule 4 says code
  owns it. There is no count to quote in the predictive case, so a criterion quoting one
  would invite the model to invent a count and bucket it — scoring well for the wrong
  reason. A test asserts no digit appears in any question.
- **The subsystem Noul raises and never lowers.** The design adds it as a seventh
  question and makes it a floor. The asymmetry was load-bearing on §3's evidence:
  under-reading was the measured failure and over-reading was not. Finding 11 measures
  what it costs once the question is predictive, and the answer is most of the result.
  It is kept in the default path because it is what the design specifies and this record
  measures the design; `--ablate-floor` scores the variant without it, and a test pins
  the direction the default applies.
- **Confidence is recorded and never scored on.** Rule 6 — it measured 16 points
  overconfident against ground truth.
- **No threshold is fitted.** The level is the nearest criterion index. The routing
  record's decision warns that thresholds tuned on the set you then score are not a
  measurement, and there is nothing here worth fitting.

## What this does not establish

- **That the call is worse than a model reasoning unaided.** No baseline was run
  against agents, as the routing record did. This measures the call against arithmetic
  baselines only.
- **That the floor-free variant reaches 68.1% on cases it has not seen.** Two variants
  were scored on the same 45 cards and the better one is reported. It reproduced across
  two independent runs, which rules out noise, not selection. The negative half — the
  floor as specified makes this worse — is solid; the positive number wants a fresh
  corpus. See finding 11.
- **That a better question would not do better.** The phrasing is one attempt, written
  to the design's spec.
- **That 45 cards from one repository generalise.** Same limit §3 has, one-fifth the
  size.

## Feeds

[`../../designs/2026-09-18-assess-task-typed-profile.md`](../../designs/2026-09-18-assess-task-typed-profile.md)
— its predictive `scope` lane, which findings 6 and 7 measure; its `whole-codebase` cut
point, which finding 2 establishes this corpus cannot license; and its upward-only
correction, which findings 8 and 11 show points the wrong way once the question is
predictive.

What was decided from this is
[`../../decisions/2026-09-20-drop-subsystem-floor-from-predictive-scope.md`](../../decisions/2026-09-20-drop-subsystem-floor-from-predictive-scope.md).
Per this directory's conventions the recommendation lives there, not here, so the
evidence can be re-read without the conclusion colouring it.

Tracked as [#782].

[#773]: https://github.com/bestdan/workflow-skills/issues/773
[#782]: https://github.com/bestdan/workflow-skills/issues/782
