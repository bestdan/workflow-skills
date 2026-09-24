---
created: 2026-09-24
status: accepted
convention: ../typed-model-calls.md
---

# `assess-task` stays a subagent: the typed profile is not adopted

## Context

`select-coder` spawns the `assess-task` subagent for every packet it routes.
`designs/2026-09-18-assess-task-typed-profile.md`, deleted with this record, proposed replacing most of that
spawn. A typed Jev call would answer the six judgment dimensions, code would compute a
concrete `scope` and compose `notes`, and today's model would be the fallback.
[`../research/2026-09-22-assess-task-comparison/`](../research/2026-09-22-assess-task-comparison/README.md)
fixed the corpus, the input and a decision rule before any request was sent.
[`../research/2026-09-24-assess-task-comparison-results.md`](../research/2026-09-24-assess-task-comparison-results.md)
applies that rule to the results.

The typed call clears both floors easily. Its p95 latency is 0.465 s against the
subagent's 5.013 s (`R` = 10.79), and it costs $0.000061 per packet against
$0.047522. So it earns the rule's loosest tolerance: 5 points per dimension and 10
on the whole profile. It still fails all three measurable dimensions. Its agreement
with a three-run incumbent panel is 17.3 points below the panel's own on
`complexity`, 17.3 on `creativity` and 14.7 on `verification_criticality`. On the
tuple it is 38.9 points below. The misses are not noise. Jev reads cards as harder
on 32 of 32 pooled `complexity` misses, more creative on 25 of 28, and less
verification-critical on 24 of 30. Those values trigger the `architecture` and
`verification-sensitive` routing labels. Jev's answers are as steady as the panel's,
or steadier, and they are steadily different.

## Decision

Do not adopt. `assess-task` keeps answering the whole `task_profile` as a subagent,
and nothing in `skills/` or `commands/` calls Jev for it. The verdict is the one the
pre-registered rule gives, and the rule was honoured, not revised. The rule asks
whether a consumer would notice the swap, and a one-way error on a label trigger is
what `select-coder` notices. Re-weighting toward stability after seeing that
stability is Jev's strong axis would fit the rule to the numbers.

The three dimensions this corpus could not measure stay with the subagent, and none
becomes a constant. They are `autonomy` (`bounded` ×150 from the panel),
`speed_sensitivity` (`low` ×150) and `cost_sensitivity` (`low` 143, `high` 7). The
corpus is this repository's issues, not the per-packet sub-tasks the subagent
routes, so a panel that gave the same answer on all or nearly all of them does not
license hard-wiring that answer. Had `autonomy` ridden on the typed call unverified, as the method
record allowed, Jev's disagreement there would have gone unseen: 0.813 against a
constant's 1.000, with 28 of 28 misses toward `long-horizon`.

The design is deleted. Its other lanes fall with the typed call. Their value was
removing the spawn, and without the typed call the spawn stays, so computing
`scope` alone saves nothing. That is the cost risk
[`2026-09-20-drop-subsystem-floor-from-predictive-scope.md`](2026-09-20-drop-subsystem-floor-from-predictive-scope.md)
flagged. Some of the design's reasoning still holds and needs no design to
carry it. Where a diff exists, `scope` is countable. Making it computed would be a
separate change, and nobody has proposed it. The predictive-scope decision and
[`2026-09-21-label-table-is-not-pure-code/`](2026-09-21-label-table-is-not-pure-code/README.md)
stand on their own. The decision records the design's Graduation list asked for
are not written, because their substance is already live guidance. "Jev is a fast path, never a requirement" is **Before
you open the PR** in [`../typed-model-calls.md`](../typed-model-calls.md).
`confidence` as a tripwire, never a threshold, is its rule 6, and a threshold fitted
to the set it is scored on is its rule 4.
Sampling a gating number more than once is its rule 5.

## Consequences

- **Good, because** routing keeps the profile its consumers are tuned to. The typed
  call would have sent more work to `architecture` and less to
  `verification-sensitive`, in the same direction every time.
- **Good, because** no installed user gets a second code path, a fallback to keep
  working, or a vendor dependency, and the gate stays keyless.
- **Bad, because** each packet still pays about 3.8 s median and about $0.047 cold, as
  measured on the `claude -p` stand-in,
  which the typed call would have cut roughly tenfold and 780-fold.
- **Bad, because** the reference is the incumbent, not ground truth, so this does
  not show that the subagent reads these cards better. It shows only that the swap
  would be visible.

## Revisit when

- **A Jev version ships whose misses lose their direction, or shrink to within 5
  points of the panel.** Re-running costs three passes (`--repeat 3`) of the 2026-09-22 record's
  `jev-assess-task.py` over the committed corpus, then the offline
  `compare-assess-task.py --score` against the committed panel, under the same fixed
  rule.
- **A corpus of real `orchestrate-coders` packets exists.** This one is 50 issues. On
  the packets the subagent actually routes, the unmeasurable dimensions may become
  measurable, and the misses may lose their direction.
- **The consumers are re-tuned so a one-way shift on these dimensions no longer changes routing.** The
  label table in `skills/assess-task/SKILL.md` is then no longer the reason
  direction matters.
- **A source-blind human adjudication shows the panel wrong on these dimensions.**
  That would reopen the question of which side reads cards better. On its own it
  does not flip this verdict. The rule allows adjudication only for a dimension that
  fails gate (a) alone, and all three also fail gate (d). Acting on it means writing
  a new rule before a new run.

## Confirmation

Nothing enforces this. The design is gone, and nothing in `skills/` or `commands/`
calls Jev for `assess-task`. The corpus, both sides' raw answers, the scorer and its
tests are committed under the 2026-09-22 record's `references/`. `--score` re-derives
every number above offline, at no API cost.

## Alternatives

- **Adopt for these dimensions only.** Rejected. The rule allows it only when every
  failing dimension can come off the subagent. Every measurable dimension fails, and
  none of them can come off: they are judgments consumers read, so a kept spawn
  brings back the latency and dollars the tolerance was bought with.
- **Revise the rule to weight Jev's stability.** Rejected, for the reasons in
  **Decision**. The method record allows a revised verdict only beside the original,
  and it asks what revision would be fitted to the numbers. This one would be.
- **Re-prompt Jev and re-run.** Not this record's call. It is a new measurement
  against the same fixed rule, and the first bullet under **Revisit when** covers it.
