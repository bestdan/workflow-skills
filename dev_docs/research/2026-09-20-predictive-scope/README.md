---
created: 2026-09-20
question: "Can a typed call forecast blast radius from a task card, before the work exists — and can this repository's own history measure that?"
feeds: ../../designs/2026-09-18-assess-task-typed-profile.md
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

**The answer is no, on this corpus.** The typed call does not beat always answering
the majority class on the boundary this repository's history can actually measure, and
on the four-level question its run-to-run spread is wider than its margin over that
baseline. Findings 6 to 9 are the measurement; 1 to 5 are what the corpus can and
cannot support, and they bound what the measurement is allowed to claim.

## Method

`jev-1.13.0`, pinned rather than `jev-latest`, run 2026-09-20 with a live key. Three
passes of 45 cases, both questions riding on one request per case. The corpus is this
repository's own issue and pull request history, read through `gh` the same day.

```
D=dev_docs/research/2026-09-20-predictive-scope/references
python3 $D/jev-predictive-scope.py --analyze $D/measurement/suite-run.json  # every number
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
- `suite-run.json` — one `--suite --repeat 3` run: per case, per pass, the raw Score
  and Noul and the level they produced.

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

### 6. The four-level result does not survive its own run-to-run spread

| pass | exact         | within one   | boundary only |
| ---- | ------------- | ------------ | ------------- |
| 1    | 26/45 — 57.8% | 45/45 — 100% | 24/43 — 55.8% |
| 2    | 29/45 — 64.4% | 45/45 — 100% | 27/43 — 62.8% |
| 3    | 26/45 — 57.8% | 45/45 — 100% | 25/43 — 58.1% |

Mean exact is 60.0% against a **55.6% base rate** — a margin of 4.4 points. The spread
across three passes over identical inputs is **6.7 points**, wider than the margin it
would have to establish.

That is rule 5 of [`../../typed-model-calls.md`](../../typed-model-calls.md) biting:
Jev is not deterministic, and a single pass is an anecdote whatever it says. A one-pass
run here could have reported 64.4% and read as a clear win over the baseline. Three
passes show that number is inside the noise.

### 7. On the boundary the corpus can measure, the call performs at the baseline

The `pr-sized`/`multi-file` slice is 43 of the 45 cases and has its own majority class:
25 of 43 are `pr-sized`, so always answering `pr-sized` scores **58.1%**.

The call scores 55.8%, 62.8% and 58.1% — mean 58.9%. One pass below the baseline, one
level with it, one above. **This is the finding**, because it is the only boundary the
corpus has enough cases on either side of to say anything about. Finding 4 predicted
that the four-level number would mostly be this binary in disguise, and the two rates
move together exactly as it said.

The overall base rate (55.6%) is the easier comparison and flatters the result; the
slice's own baseline (58.1%) is the honest one. `--analyze` prints both beside their
accuracies so they cannot be quoted apart.

### 8. The error direction inverts against §3, which is the predictive case showing

§3 measured 31 of 33 misses as under-reads — `multi-file` read as `pr-sized`. Here the
misses run the other way: 12–13 over-reads against 4–7 under-reads, dominated by
`pr-sized` read as `multi-file`.

That is a difference between the two questions, not a contradiction. §3 handed the
model a change that existed and it under-read what it could see. Asked to forecast from
a card, it over-reads: a card describing work at length reads as large work. Every
correction the design wires — the named-path floor, the subsystem Noul — only raises a
level, because §3's under-read was the failure being designed against. **In the
predictive case those corrections push in the direction the error already goes.**

Not measured here: whether removing the subsystem floor improves the result. The floor
fired on 25 of 45 cases and raised exactly 1 of them, so its effect on this corpus is
near-nil either way, but that is an observation rather than a test.

### 9. The instrument's first mapping was wrong, and the shape of the error is what caught it

**A Jev Score is a position on the criteria index scale, 0 to n−1 — not [0, 1].** The
first version of `level_from` assumed a normalised score and multiplied by four, so
every case scoring above 0.75 landed in `whole-codebase`: 39 of 45.

That run reported 4.4% exact against a 55.6% base rate, with misses running 42
over-reads to 1 under-read. The number alone is not what exposed it — a bad result is
survivable. **The direction was**: §3 measured a one-directional under-read, and a
near-total inversion of that is a claim about the instrument before it is a claim about
the model.

Confirming the scale used committed evidence rather than assumption: the sibling
routing probe's `stakes`, also a four-criterion Score, runs 0.580 to 2.390 over 108
answers, and a normalised score cannot exceed 1. Re-scoring this record's own stored
raw answers under the corrected mapping gave 60.0 / 57.8 / 64.4 — the same picture the
fresh run then reproduced, which is what establishes the mapping alone was responsible
rather than a lucky second draw.

Two things follow, and both are now in the instrument. `--analyze` recomputes from raw
answers, so a scorer fix can be applied to committed evidence without buying a new run.
And a test pins the scale, including that 1.95 — the corpus maximum — is `multi-file`
and not `whole-codebase`.

This is the same defect class as the routing record's finding 2, where a rule resting
on a signal whose per-label spread nobody had printed cost 42 points. Printing the
per-label spread is again what found it.

## What the instrument does, that the design did not specify

- **The questions quote no numbers.** §3's re-run scored 40/40 by redefining the levels
  as file-count ranges, at which point the question was arithmetic and rule 4 says code
  owns it. There is no count to quote in the predictive case, so a criterion quoting one
  would invite the model to invent a count and bucket it — scoring well for the wrong
  reason. A test asserts no digit appears in any question.
- **The subsystem Noul raises and never lowers.** The design adds it as a seventh
  question and makes it a floor. The asymmetry was load-bearing on §3's evidence:
  under-reading was the measured failure and over-reading was not. Finding 8 inverts
  that premise for the predictive case, so the asymmetry now points the wrong way — it
  is preserved here because it is what the design specifies and the record measures the
  design, not a variant of it. A test pins the direction.
- **Confidence is recorded and never scored on.** Rule 6 — it measured 16 points
  overconfident against ground truth.
- **No threshold is fitted.** The level is the nearest criterion index. The routing
  record's decision warns that thresholds tuned on the set you then score are not a
  measurement, and there is nothing here worth fitting.

## What this does not establish

- **That the call is worse than a model reasoning unaided.** No baseline was run
  against agents, as the routing record did. This measures the call against arithmetic
  baselines only.
- **That a better question would not do better.** The phrasing is one attempt, written
  to the design's spec. Finding 8 suggests an obvious variant — drop the upward-only
  floor — that was not tested.
- **That 45 cards from one repository generalise.** Same limit §3 has, one-fifth the
  size.

## Feeds

[`../../designs/2026-09-18-assess-task-typed-profile.md`](../../designs/2026-09-18-assess-task-typed-profile.md)
— its predictive `scope` lane, which findings 6 and 7 measure; its `whole-codebase` cut
point, which finding 2 establishes this corpus cannot license; and its upward-only
correction, which finding 8 shows points the wrong way once the question is predictive.

Tracked as [#782]. A decision record, if one follows, is the place for what to do about
this — per `README.md` in this directory, the recommendation does not belong here.

[#773]: https://github.com/bestdan/workflow-skills/issues/773
[#782]: https://github.com/bestdan/workflow-skills/issues/782
