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

**This record is in two phases, and only the first has run.** Phase 1 builds the corpus
and the instrument and asks what this repository's history can support. Phase 2 is the
Jev measurement, which needs a live key. The findings below are all phase 1, and they
already change what phase 2 can claim.

## Method

No model was called. Everything below comes from this repository's own issue and pull
request history, read through `gh` on 2026-09-20 and committed as
`references/measurement/corpus.json`.

```
D=dev_docs/research/2026-09-20-predictive-scope/references
python3 $D/build-corpus.py --profile $D/measurement/corpus.json  # every phase-1 number
python3 $D/test_jev_predictive_scope.py                          # hermetic; no key
python3 $D/build-corpus.py --from-api                            # rebuild; needs gh
python3 $D/jev-predictive-scope.py --suite --repeat 3            # phase 2; needs a key
```

The first two need neither a key nor the network — they read committed evidence. That
is deliberate: the first version of the sibling routing record committed none of its
evidence and reported numbers from two different runs computed in a scratch script,
and closing that gap is what its finding 3 cost.

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

Always answering `pr-sized` scores 25/45. Any phase-2 result has to clear that, not
25%: a four-level question over a distribution this skewed is easy to score well on for
the wrong reason.

This also breaks the comparison the design invites. §3's 71.6% was measured against a
different distribution — 116 merged pull requests, not 45 cards — so the two numbers
cannot be set beside each other, and a phase-2 result that lands near 71.6% would mean
something different from what §3's did. `format_analysis` prints the base rate beside
every accuracy figure so the two cannot drift apart in the reporting.

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

## What phase 2 needs, and why it did not run

A live TypeSafe key. This machine has none: no `TYPESAFE_API_KEY`, no
`dev_docs/tasks/.task-config.local.yml`, no `OP_SERVICE_ACCOUNT_TOKEN`, and no desktop
session for `op signin` to prompt against. That is the constraint [#773] already
describes, met from the other side.

The instrument is complete and its report path is exercised: a synthetic random-guess
pass scores 20.0% exact against the committed corpus, below the 55.6% base rate, which
is the check that the scorer discriminates at all.

Phase 2 runs `--suite --repeat 3` from a machine with a key, commits the result as
`references/measurement/suite-run.json`, and fills in a findings section here. Three
passes because Jev is not deterministic and a margin measured once moved threefold over
four runs — [`../../typed-model-calls.md`](../../typed-model-calls.md), rule 5.

### What the instrument does, that the design did not specify

- **The questions quote no numbers.** §3's re-run scored 40/40 by redefining the levels
  as file-count ranges, at which point the question was arithmetic and rule 4 says code
  owns it. There is no count to quote in the predictive case, so a criterion quoting one
  would invite the model to invent a count and bucket it — scoring well for the wrong
  reason. A test asserts no digit appears in any question.
- **The subsystem Noul raises and never lowers.** The design adds it as a seventh
  question and makes it a floor. The asymmetry is load-bearing: under-reading is the
  measured failure and over-reading is not, so a floor that could pull a level down
  would reintroduce the error being removed. A test pins the direction.
- **Confidence is recorded and never scored on.** Rule 6 — it measured 16 points
  overconfident against ground truth.
- **No threshold is fitted.** The level is the quartile the Score falls in. The routing
  record's decision warns that thresholds tuned on the set you then score are not a
  measurement, and there is nothing here worth fitting.

## Feeds

[`../../designs/2026-09-18-assess-task-typed-profile.md`](../../designs/2026-09-18-assess-task-typed-profile.md)
— specifically its predictive `scope` lane, and its `whole-codebase` cut point, which
finding 2 establishes this corpus cannot license.

Tracked as [#782]. Phase 1 does not close it.

[#773]: https://github.com/bestdan/workflow-skills/issues/773
[#782]: https://github.com/bestdan/workflow-skills/issues/782
