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

Sibling to [`../2026-09-17-jev-applications/`](../2026-09-17-jev-applications/README.md),
whose §3 measured the **post-hoc** case — 71.6% exact against 116 merged pull requests,
with 31 of 33 misses under-reading blast radius — and recorded its own scope limit:
ground truth was the file count of a pull request that had already merged, so every
case handed the model a change that had already happened.

**As the design specifies it, no — and the design's own seventh question is why.**
Asked with the upward-only subsystem floor the design adds, the call scores 56.7%
against a 56.0% base rate and falls below the majority-class baseline of the one
boundary this corpus can measure. Remove that floor and the same answers reach 62.0%,
identical on all three passes, with every miss an under-read — the direction §3
measured. Which decoder reads the Score — round its mean, or take the argmax of its
distribution — gives the same number in every cell.

So the finding is about the wrapper, not the model: **the correction the design built
against §3's under-read is what breaks the question when it is asked predictively.**
And one correction to this record's own earlier runs is a finding too: sending the
body alone, as every earlier run did, reported 68.1%; sending title and body — what the
consumer sends — scores 64.4% on the same cards. Findings 6 to 12 are the measurement; 1 to 5
are what the corpus can and cannot support, and they bound what the measurement is
allowed to claim.

## Method

`jev-1.13.0`, pinned rather than `jev-latest`, run 2026-09-20 with a live key. Three
passes of 50 cases, both questions riding on one request per case, each card sent as
title and body. The corpus is this repository's own issue and pull request history,
read through `gh` the same day — and it is a **snapshot**: `--from-api` reads the live
issue list, so a rebuild after more issues close will hold more cases than the run
covers. A test asserts the committed run covers exactly the committed corpus, which is
the check that the rates are over the denominator the record quotes.

```
D=dev_docs/research/2026-09-20-predictive-scope/references
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json  # every number
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json --ablate-floor
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json --decoder argmax
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json --ablate-floor --ids $D/measurement/run3-ids.json
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json --spreads
python3 $D/build-corpus.py --profile $D/measurement/corpus.json             # the corpus
python3 $D/test_jev_predictive_scope.py                                     # hermetic
python3 $D/build-corpus.py --from-api                        # rebuild the corpus; needs gh
python3 $D/jev-predictive-scope.py --suite --repeat 3        # a fresh run; costs a key
```

**Every number in this record is printed by one of the first six commands**, which
need neither a key nor the network — they read committed evidence. `--analyze`
recomputes each figure from the committed raw answers rather than reading back an
aggregate frozen at run time; finding 9 is why that matters. The first version of the
sibling routing record committed none of its evidence and reported numbers from two
different runs computed in a scratch script, and closing that gap is what its finding
3 cost.

`references/measurement/` is that evidence:

- `corpus.json` — the 50 cases, each with the card as filed, the pull request that
  closed it, and the changed-file count that labels it.
- `suite-run.json` — one `--suite --repeat 3` run: per case, per pass, the raw Score,
  the four-entry distribution it is the mean of, the Noul, and the level they produced.
  This is the fourth run. The first stored a mis-decoded level; the second stored the
  Score's mean without its distribution and could not be re-decoded; the third sent
  the body without the title and used a closing-PR rule that dropped a valid card
  (finding 9). All are in this file's git history, and the third's means are cited
  where the input change is the point.
- `run3-ids.json` — the 45 ids the third run covered, so `--ids` can score this run on
  exactly those cards and isolate what changing the input did (finding 12).

**The corpus is 50 task cards paired with the blast radius the work actually had.** A
case is a closed issue whose text existed before the pull request that closed it,
labelled with that pull request's changed-file count under the design's cut points.
`created_at(issue) < created_at(pull request)` is a gate rather than a nicety: a card
written or rewritten after its pull request would reproduce exactly the post-hoc defect
this record exists to remove, and would do it invisibly. Where an issue has more than
one merged closing pull request, only those opened after the card are candidates, and
the first of them to open is the forecast target. An earlier version of the rule chose
the earliest-opened pull request before checking it postdated the card, selected one
opened two days before its issue, and dropped the case at the gate — finding 9. Under
the current rule no case is dropped by it.

## Findings

### 1. The corpus exists, and it is thinner than §3's

86 closed issues at the snapshot; 36 have no merged pull request that closed them; all
50 of the rest have a card that predates the pull request that closed it.

| level            | rule                     | cases |
| ---------------- | ------------------------ | ----- |
| `single-file`    | 1 changed file           | 2     |
| `pr-sized`       | 2–5                      | 28    |
| `multi-file`     | 6+                       | 20    |
| `whole-codebase` | ≥ half the tracked files | **0** |

50 is comparable to the routing record's 36 cases and well short of §3's 116. The
changed-file counts run 1 to 24, with a single case above 10.

### 2. `whole-codebase` has no instance, so the probe cannot license that cut

The design invents the `whole-codebase` boundary — "changed files ≥ half the tracked
files" — and flags it as the one cut with no measurement behind it. Step 4 of #782
asked this probe to re-check it while it was in there.

**It cannot.** The corpus carries the denominator — 437 tracked files at the checkout's
HEAD, an approximation of each pull request's own tree that cannot move a label at
this distance — and `bucket()` applies it to every case. The largest pull request
touched 24 files, **5.5% of the tree**, against a cut at 50%; `--profile` prints that
line. So the zero is computed, not asserted: the boundary is never reached in
practice, which is worth knowing and is not the same as validating where it sits.
Three reviewers independently caught the earlier version of this finding, where the
builder never passed a denominator and the `whole-codebase` branch was unreachable by
construction. A test now pins that the branch is reachable and that the committed
corpus carries what it needs to recompute every label.

### 3. The base rate is 56.0%, and §3's 71.6% is not comparable to it

Always answering `pr-sized` scores 28/50. A result has to clear that, not 25%: a
four-level question over a distribution this skewed is easy to score well on for the
wrong reason. Finding 6 is what that constraint does to the measured number.

This also breaks the comparison the design invites. §3's 71.6% was measured against a
different distribution — 116 merged pull requests, not 50 cards — so the two numbers
cannot be set beside each other, and a result landing near 71.6% here would mean
something different from what §3's did. `--analyze` prints the base rate beside every
accuracy figure so the two cannot drift apart in the reporting.

### 4. The four-level question is mostly one boundary wearing a larger label

48 of the 50 cases sit in `pr-sized` or `multi-file`, split at the 5/6-file line.

That is a limitation and an aim at once. §3's failure was concentrated at exactly this
boundary — 31 of 33 misses were `multi-file` read as `pr-sized` — so a corpus massed
there is pointed at the known failure mode. But a "four-level accuracy" number off it
would be that binary in disguise. So the instrument reports both: the four-level exact
rate the design asked for, and the boundary-only rate, from the same run at no extra
cost.

### 5. The linkage rate is itself a constraint on any future corpus

36 of 86 closed issues had no merged pull request that closed them — 42%. Some were
closed as duplicates or won't-fix, but the rate means that widening this corpus by
waiting for more issues to close is slower than it looks: roughly half of what closes
never becomes a labelled case.

### 6. As specified, the four-level result is inside its own run-to-run spread

| pass | exact         | within one   | boundary only |
| ---- | ------------- | ------------ | ------------- |
| 1    | 29/50 — 58.0% | 50/50 — 100% | 27/48 — 56.2% |
| 2    | 27/50 — 54.0% | 50/50 — 100% | 25/48 — 52.1% |
| 3    | 29/50 — 58.0% | 50/50 — 100% | 27/48 — 56.2% |

Mean exact is 56.7% against a **56.0% base rate** — a 0.7-point margin under a
4.0-point spread. The design as written does not beat a constant.

That is rule 5 of [`../../typed-model-calls.md`](../../typed-model-calls.md) biting:
Jev is not deterministic, and a single pass is an anecdote whatever it says. Two of the
three passes here read 58.0% and would have looked like a two-point win.

### 7. As specified, the call falls below the baseline on the boundary that matters

The `pr-sized`/`multi-file` slice is 48 of the 50 cases and has its own majority class:
28 of 48 are `pr-sized`, so always answering `pr-sized` scores **58.3%**.

The call scores 56.2%, 52.1% and 56.2% — mean 54.9%, below the constant on every pass.
**This is the finding for the design as written**, because it is the only boundary the
corpus has enough cases on either side of to say anything about. Finding 4 predicted
the four-level number would mostly be this binary in disguise, and the two rates move
together exactly as it said.

The overall base rate (56.0%) is the easier comparison and flatters the result; the
slice's own baseline (58.3%) is the honest one. `--analyze` prints both beside their
accuracies so they cannot be quoted apart.

### 8. As specified, the floor splits the misses; without it every miss is an under-read

§3 measured 31 of 33 misses as under-reads — `multi-file` read as `pr-sized`. Here, as
the design specifies, the misses run 34 over-reads against 31 under-reads across three
passes. With the floor removed (finding 11) they are 57 under-reads and 0 over-reads.

So the raw signal under-reads, exactly as §3 found post-hoc, and the floor converts
about half of the cases it touches into over-reads without fixing the under-reads it
was built for. Whatever the model is doing, it is doing it consistently across the
post-hoc and predictive regimes, and the floor was masking it.

### 9. The instrument was wrong three times, and the record keeps all three

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
reads a bimodal answer as its midpoint — {0: 0.4, 1: 0.2, 2: 0.4} has a mean of 1.0
and rounds to `pr-sized`, the one level the model was ruling out. The second run
stored only the mean, so the argmax decoder could not be applied to it and it had to
be run again. Every run from now on stores the whole distribution, a test asserts it
is present and that the stored mean is its mean, and `--decoder` reports both readings
from the same committed answers.

**Third, caught in review: the input was not the consumer's, and the corpus rule
dropped a valid card.** The third run sent each card's body alone; `assess-task` is
handed title and body. And its closing-PR rule took the earliest-opened pull request
before checking it postdated the card, so for one issue it selected a PR opened two
days before the issue existed — later edited to reference it — and then dropped the
case at the provenance check, which the record reported as the gate catching a
rewritten card. Finding 12 measures what the input change did; a test pins the rule.

All three are the same defect class as the routing record's finding 2, where a rule
resting on a signal whose per-label spread nobody had printed cost 42 points. Three
rules in [`../../typed-model-calls.md`](../../typed-model-calls.md) carry what they
cost here.

### 10. The decoder does not matter

Same committed answers, all four combinations, mean four-level exact:

|                          | `round` the mean | `argmax` the distribution |
| ------------------------ | ---------------- | ------------------------- |
| with floor (as designed) | 56.7%            | 56.7%                     |
| floor removed            | **62.0%**        | **62.0%**                 |

The two decoders agree in every cell — every pass, every slice. The distributions the
model returns are concentrated enough that the mean and the mode always land on the
same level here, which could not have been known without storing the distribution.

### 11. Removing the design's floor is what the result turns on

The subsystem Noul fires on 24 of the 50 cases in every pass. Two ways of counting what
it does, printed by `--analyze` on every pass, one of which needs no decoder at all:

- **Decoder-free.** The floor can only raise a level to `multi-file`. On 11, 12 and 11
  of its firings the card's true label is _below_ `multi-file`, so the raised answer is
  wrong however the Score is read: **34 of 72 firings wrong by construction.**
- **Under `round`.** It changes the level on 19 cases per pass, and is right 23 times
  against 31 wrong.

`--spreads` shows why it cannot do better. The subsystem Noul averages **0.45 on
`pr-sized` cards and 0.54 on `multi-file`**, with ranges of 0.13–0.76 and 0.22–0.77:
it barely separates the two labels the floor exists to discriminate, and the 0.5
threshold cuts through the middle of both. The Score does separate them — means of
0.95 and 1.40 — which is the signal the floor overrides.

Nor is 0.5 the problem. The sensitivity block `--analyze` prints pools the three passes
and moves the line: at 0.5 the floor fires 72 times and 34 (47%) are wrong by
construction; at 0.6, 44 and 26 (59%); at 0.7, 17 and 11 (65%); at 0.8 it never fires.
Tightening the threshold raises the share of firings that are wrong — the confident
`yes` answers are more often on `pr-sized` cards, not less — so there is no setting at
which the floor helps. The threshold was never fitted, and this is why fitting it would
not have rescued it either. Rule 3 of
[`../../typed-model-calls.md`](../../typed-model-calls.md) is the general form: a
signal's per-label spread has to be printed before anything branches on it, and this
one was not until review asked for it.

Re-scoring the same committed answers with it removed — `--ablate-floor`, no new API
call:

|                        | as the design specifies | floor removed              |
| ---------------------- | ----------------------- | -------------------------- |
| four-level exact, mean | 56.7%                   | **62.0%**                  |
| margin over base rate  | +0.7 (spread 4.0)       | **+6.0 (spread 0.0)**      |
| boundary only, mean    | 54.9% (baseline 58.3%)  | **60.4%** (baseline 58.3%) |
| error direction        | 34 over / 31 under      | **0 over / 57 under**      |

Three things change at once. The margin over the base rate goes from inside the
run-to-run spread to well outside it. The error direction reverts to §3's. And the
spread collapses to zero — all three passes returned the same 31 correct answers,
which says the raw Score is stable across passes and the Noul jittering around its
0.5 threshold was the noise.

**The floor-free variant is selected on the corpus it is quoted against, and its
boundary margin is 2.1 points.** Two variants were scored on the same 50 cards and the
better one is reported — the hazard rule 4 names, even though nothing here is a fitted
threshold. What the ablation establishes solidly is the negative half: the floor as
specified makes this worse, and 34 of its 72 firings are wrong before any decoder is
chosen. That the floor-free variant clears a constant by two points on the boundary is
a result wanting confirmation on cards this run did not touch, not a measurement of it.

### 12. The consumer's input scores lower than the input two runs measured

The third run sent the body alone and reported 68.1% with the floor removed, and a
fresh draw reproduced it. This run sends title and body. Scored on exactly the 45 cards
the third run covered — `--ids measurement/run3-ids.json` — the same configuration:

| floor removed, 45 cards | body only (run 3) | title + body (this run) |
| ----------------------- | ----------------- | ----------------------- |
| four-level exact        | 68.1%             | **64.4%**               |
| boundary only           | 66.7%             | **62.8%**               |
| error direction         | 43 under / 0 over | 48 under / 0 over       |

Four points, all of it more under-reading. A title is the tersest statement of the work
a card carries, and the model reads terse as small. The five cards that entered the
corpus with this run score 40% on their own — two `multi-file` cards under-read — and
take the 50-card number the rest of the way to 62.0%.

Two things follow. The 68.1% was a number about an input `assess-task` never sees, and
reproducing it across runs did not make it the right one. And whether the question can
be phrased so a title does not pull the estimate down is untested — the decision record
lists it under what would reopen the choice.

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
- **That the floor-free variant reaches 62.0% on cases it has not seen.** Two variants
  were scored on the same 50 cards and the better one is reported, and its margin on
  the boundary is two points. The negative half — the floor as specified makes this
  worse — is solid; the positive number wants a fresh corpus. See finding 11.
- **Anything about the design's named-path lower bound.** The design wires two
  upward-only corrections: the subsystem Noul and a floor from the count of paths a
  card names. Only the subsystem floor was implemented here; the ablation and finding
  11 speak to it alone. The named-path bound was not tested, and implementing it now
  would be a third variant selected on the same 50 cards.
- **That no card was edited after its work began.** Each card is the issue's body as
  it stands now, and the provenance gate checks creation order only, so a body edited
  or backfilled after its pull request opened passes it. The gate rules out the
  post-hoc case §3 measured; it does not rule this one out. Closing it needs GitHub's
  `userContentEdits` history per issue — a GraphQL query this environment's tooling
  does not run — after which a case with a post-PR edit would be dropped or reverted
  to its last pre-PR body. `corpus.json`'s `note` field points here.
- **That a better question would not do better.** The phrasing is one attempt, written
  to the design's spec.
- **That 50 cards from one repository generalise.** Same limit §3 has, under half the
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
