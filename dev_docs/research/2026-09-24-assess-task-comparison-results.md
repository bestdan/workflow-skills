---
created: 2026-09-24
question: "Under the decision rule fixed on 2026-09-22, does the typed call profile a task closely enough to the assess-task subagent, at a large enough latency and dollar gain, to replace it?"
feeds: ../decisions/2026-09-24-assess-task-stays-a-subagent.md
---

# `assess-task`: the typed call against the subagent — results and verdict (2026-09-24)

**No. Under the rule as written, the verdict is don't adopt, and the rule was
honoured, not revised.** The typed call is about 10× faster at p95 and about 780×
cheaper per packet, so it clears both floors and earns the loosest tolerance the
rule offers, 5 points per dimension. It still fails every measurable dimension by
about three times that tolerance, and on each one its misses run one way. Jev's
answers are as steady as the panel's, or steadier. They are steadily _different_
from the panel's.

The method, the corpus and the decision rule are
[`2026-09-22-assess-task-comparison/README.md`](2026-09-22-assess-task-comparison/README.md),
written before any request was sent. This record adds no artifacts of its own. Every
number below comes from that record's `references/`, or from a computation stated
beside it.

## Question

Under the decision rule fixed on 2026-09-22, does the typed call profile a task
closely enough to the `assess-task` subagent, at a large enough latency and dollar
gain, to replace it?

## Method

- **The typed call:** `jev-1.13.0`, run 2026-09-24 ([#813], [#850]). Three passes
  over the 50 cards, each card sent as its fenced title and body, with the six
  dimensions as questions on one request. The run is
  [`references/measurement/jev-run.json`](2026-09-22-assess-task-comparison/references/measurement/jev-run.json).
  It records `latency_s` and token counts per call, and its pricing:
  $0.042 per million uncached input tokens and zero for every other class.
- **The incumbent:** three runs of `claude-opus-5-5`, pinned, one fresh `claude -p`
  process per card, no tools, the same fenced input ([#814]). Setup, latency, tokens,
  dollars and cross-agent agreement are in
  [`references/measurement/baseline/results.md`](2026-09-22-assess-task-comparison/references/measurement/baseline/results.md).
- **Scoring:** the shared `--score` path, offline and free, reproduced for this
  record on 2026-09-24. Its output matches the copy in `results.md` line for line:

  ```
  D=dev_docs/research/2026-09-22-assess-task-comparison/references
  python3 $D/compare-assess-task.py --score $D/measurement/jev-run.json \
      $D/measurement/baseline/agent-1.json $D/measurement/baseline/agent-2.json \
      $D/measurement/baseline/agent-3.json
  ```

- **Jev token statistics** (the scorer prints only the mean dollars). They are
  computed over all 150 calls with this one-liner, `D` set as above:

  ```
  python3 -c "import json,statistics as s; d=json.load(open('$D/measurement/jev-run.json')); c=[x for p in d['passes'] for x in p['cards'].values()]; u=[x['tokens']['uncached'] for x in c]; print(len(c), s.median(u), s.mean(u), {x['tokens']['output'] for x in c}, s.median(u)*0.042/1e6, sum(u)*0.042/1e6)"
  ```

  It prints 150 calls, uncached input median 1,356.5 and mean 1,456.3, output 111
  tokens on every call, a median of $0.0000570 per call and a total of $0.00917. The
  run records no cache-read or cache-write tokens.

- **Ratios** in the prose below are plain division of the numbers in the tables, and
  the dividends and divisors are named where each ratio appears.

## Findings

### 1. Both floors pass, and `R` lands in the top band

| per packet (n = 150 each side)  | incumbent (subagent) | typed call (Jev) |
| ------------------------------- | -------------------- | ---------------- |
| wall clock, median              | 3.796 s              | 0.397 s          |
| wall clock, p95                 | 5.013 s              | 0.465 s          |
| uncached input tokens, median   | 2                    | 1,356.5          |
| cache-read tokens, median       | 531                  | 0                |
| cache-write tokens, median      | 5,282                | 0                |
| output tokens, median           | 201                  | 111 (every call) |
| USD, median                     | $0.04664             | $0.0000570       |
| USD, mean (the scorer's figure) | $0.047522            | $0.000061        |
| USD, all 150 calls              | $7.1283              | $0.00917         |

Sources: incumbent rows from `results.md`; Jev latency rows and both mean-dollar
figures from the scorer; Jev token rows, Jev median and total dollars from the
one-liner in **Method**.

- **Latency.** `R = 5.013 / 0.465 = 10.79`, which is at least 10, so `δ` is 5 points
  and `δ_tuple` is 10 points (scorer). At the median the ratio is
  3.796 / 0.397 = 9.6.
- **Dollars.** The floor passes (scorer). By mean, the incumbent costs about 780× as
  much per packet ($0.047522 / $0.0000612). That figure is cold-cache: the skill
  prompt was cache-warm on 0 of 150 incumbent calls. `results.md` estimates a warm
  call at about $0.009. That is an estimate, not a measurement, and against it the
  ratio is still about 150×. Jev clears the dollar floor either way.

### 2. Three dimensions are measurable, three are not

The rule counts a dimension as measurable when at least 5 cards have a panel
majority on a value other than its most frequent one. By that count
`complexity` has 13 such cards, `creativity` 13 and `verification_criticality` 14.
`autonomy` has 0: the panel said `bounded` on all 150 answers. `speed_sensitivity`
has 0 (`low` ×150), and `cost_sensitivity` has 2 (`low` 143, `high` 7). The record
expected the last two to be unmeasurable. `autonomy` was not expected.

### 3. Accuracy, with the base rate beside both sides

`A_const` is the base rate: the agreement with the panel of a constant answering
the panel's most frequent value (rule 7 of
[`../typed-model-calls.md`](../typed-model-calls.md)). Gap is `A_jev − A_panel` in points.
With `δ = 5`, gate (a) needs a gap no worse than −5.

| dimension                     | measurable (off-mode cards) | `A_const` (base rate) | `A_panel` (incumbent) | `A_jev` (typed call) | `S_jev` | gap   | gates failed  | match |
| ----------------------------- | --------------------------- | --------------------- | --------------------- | -------------------- | ------- | ----- | ------------- | ----- |
| `complexity`                  | yes (13)                    | 0.727                 | 0.973                 | 0.800                | 0.973   | −17.3 | (a), (d)      | NO    |
| `creativity`                  | yes (13)                    | 0.753                 | 0.973                 | 0.800                | 0.987   | −17.3 | (a), (d)      | NO    |
| `verification_criticality`    | yes (14)                    | 0.720                 | 0.920                 | 0.773                | 1.000   | −14.7 | (a), (d)      | NO    |
| `autonomy`                    | no (0)                      | 1.000                 | 1.000                 | 0.813                | 0.960   | −18.7 | (a), (c), (d) | n/a   |
| `speed_sensitivity`           | no (0)                      | 1.000                 | 1.000                 | 1.000                | 1.000   | 0.0   | (c)           | n/a   |
| `cost_sensitivity`            | no (2)                      | 0.953                 | 0.987                 | 0.793                | 1.000   | −19.4 | (a), (c), (d) | n/a   |
| tuple of the three measurable | —                           | 0.420                 | 0.880                 | 0.491                | 0.960   | −38.9 | `δ_tuple` 10  | FAIL  |

Every measurable dimension beats its constant: gate (c) passes on all three. On all
three, Jev is closer to the constant than to the panel. It gives up about 17, 17 and
15 points of agreement against a 5-point allowance. On the whole tuple it gives up 39
points against a 10-point allowance. That puts it 7 points above the constant's 0.420.
No card was contested on any dimension, and no answer on either side was missing or
outside its enum (scorer, `cont` and `miss` columns).

### 4. Stable is not the same as agreeing

Gate (b) passes everywhere. Jev's stability ties the panel's on `complexity` (0.973
against 0.973) and exceeds it on `creativity` (0.987 against 0.973),
`verification_criticality` (1.000 against 0.920) and the tuple (0.960 against 0.880).
So the typed call is not noisy. On any given card it gives the same answer across
passes, and on the cards where it differs from the panel, it differs the same way
every time. A stability figure cannot see that. Only agreement with the incumbent
can. The tool-routing measurement
([`2026-09-19-jev-tool-routing/`](2026-09-19-jev-tool-routing/README.md)) found the
reverse, with the agent as the steadier side.

### 5. The misses have a direction, and it is the direction routing reads

Gate (d) pools every (pass, card) miss against the panel majority. The scorer
reports each pool as pooled pairs / distinct cards, lower : higher:

| dimension                         | pooled / cards | lower : higher | Jev reads the card as…      |
| --------------------------------- | -------------- | -------------- | --------------------------- |
| `complexity`                      | 32 / 12        | 0 : 32         | harder, on every miss       |
| `creativity`                      | 28 / 10        | 3 : 25         | more creative, 25 of 28     |
| `verification_criticality`        | 30 / 10        | 24 : 6         | less critical, 24 of 30     |
| `autonomy` (unmeasurable)         | 28 / 11        | 0 : 28         | long-horizon, on every miss |
| `cost_sensitivity` (unmeasurable) | 30 / 10        | 3 : 27         | cost-sensitive, 27 of 30    |

All five pools clear the 80% one-side threshold on at least 5 distinct cards.

Why the direction matters more than the rate. This is inferred from the label table
in `skills/assess-task/SKILL.md`, not measured. Each of these values is a
single-dimension trigger for a routing label. `complexity: hard` fires
`architecture`, the tier where the expensive coders sit, so an upward bias sends
ordinary work there. `verification_criticality: high` fires `verification-sensitive`,
so a downward bias sends work whose deliverable is an honest check to a coder not
chosen for honest checking. Scattered noise at the same rate would misroute packets
both ways. A one-way error misroutes them the same way every time.

### 6. Adjudication cannot change the result

The rule's one revision allowed in advance, a source-blind human adjudication,
applies only to a measurable dimension that fails gate (a) **alone**. All three also
fail gate (d), so none qualifies, and none was adjudicated. Had Jev won an
adjudication on all three, gate (a) would pass and gate (d) would still fail them.

### 7. The verdict holds if `R` falls a band

`R` sits close to its band edge. An incumbent p95 below 4.65 s (10 × 0.465 s) would
drop it into the 2–10 band, where `δ` and `δ_tuple` are 0. The incumbent's 5.013 s
includes the CLI's own overhead, 1.04 s at the median (wall clock minus API time,
`results.md`), and an `Agent`-tool spawn may not pay it. As an illustration, not a
measurement: removing 1.04 s from the p95 gives 3.97 s and `R` ≈ 8.5. In that band
the allowance is zero, so the gaps in finding 3 fail by more, not less, and gate (d)
does not depend on `δ` at all. `R` would need to fall below 2 to change the reason
(the incumbent's p95 below 0.93 s), and the fastest incumbent call measured took
3.285 s.

### 8. The verdict against the pre-registered rule

The scorer's verdict line: **`DON'T ADOPT — no measurable dimension matches`.**

- **Adopt** needs every measurable dimension to match and the tuple gate to pass.
  None matches, and the tuple fails.
- **Adopt for these dimensions only** needs at least one matching dimension, with
  every failing one able to come off the subagent. None matches, so the matching set
  is empty (scorer: "matching: empty tuple").
- **Don't adopt** is what remains.

**The rule was honoured, not revised.** The owner's comment on [#815] asked whether
the rule weights agreement over consistency wrongly, since consistency is where Jev
does well. The rule was kept as written, for two reasons. First, the question it
answers is whether a consumer would notice the swap, and a directional bias on a
label trigger is exactly what a consumer notices. Second, re-weighting toward
stability after seeing that stability is Jev's strong axis would fit the rule to the
numbers, which the method record forbids. Because nothing was revised, there is no
revised verdict to report beside this one.

## What this does not establish

- **That Jev is wrong.** The reference is the incumbent panel, not ground truth. The
  result says a consumer would see the swap. It does not say which side reads the
  cards better. A panel that is consistently wrong in one direction would produce
  these same numbers. No human adjudication was run, and under the rule none could
  have changed the verdict (finding 6).
- **Anything about the per-packet sub-tasks `orchestrate-coders` routes.** The corpus
  is 50 of this repository's issues, as the method record warns. The packets where
  `assess-task` spends most of its spawns are a different population, and neither
  side's agreement or direction was measured on them.
- **The production latency on either side.** The incumbent is a `claude -p` stand-in
  for an `Agent`-tool spawn. It pays a CLI start a spawn may not pay, and it lacks the
  parent session's system prompt, which a spawn may carry. Nothing was fetched, so the
  typed call's state assembly is zero, and the incumbent's thin-card `related_files`
  read is switched off. Which way that last choice biases the incumbent's answers is
  unknown. Finding 7 shows the verdict does not depend on `R`'s band. It does not show
  what `R` is in production.
- **The incumbent's warm-cache dollars.** The $0.009 figure is `results.md`'s
  estimate. The floor passes with or without it.
- **Any accuracy claim on `autonomy`, `speed_sensitivity` or `cost_sensitivity`.**
  They are unmeasurable on this corpus. A panel answering one value on all 150 cards
  of this corpus shows that this repository's issues look alike on that dimension.
  It does not show that the dimension is a constant for the packets it routes.
- **Other Jev versions, other question wordings, or other incumbent models.** One Jev
  version, one phrasing of the six questions ([`questions.json`](2026-09-22-assess-task-comparison/references/questions.json)), and one
  pinned incumbent model were measured.
- **That a label would change on any given packet.** Finding 5's routing consequence
  is read off the label table. Labels were not derived and compared for either side.

## Feeds

[`../decisions/2026-09-24-assess-task-stays-a-subagent.md`](../decisions/2026-09-24-assess-task-stays-a-subagent.md).

[#813]: https://github.com/bestdan/workflow-skills/issues/813
[#814]: https://github.com/bestdan/workflow-skills/issues/814
[#815]: https://github.com/bestdan/workflow-skills/issues/815
[#850]: https://github.com/bestdan/workflow-skills/issues/850
