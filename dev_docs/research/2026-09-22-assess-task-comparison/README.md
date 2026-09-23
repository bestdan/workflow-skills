---
created: 2026-09-22
question: "Does a typed call profile a task as well as the assess-task subagent it would replace, at a cost that pays for any difference?"
---

# `assess-task`: the typed call against the subagent — method and decision rule (2026-09-22)

This record is written before any request is sent. It fixes the corpus, the input
both sides get, what counts as right, and the rule that turns the numbers into a
verdict. Nothing has run. [#810] asks for the comparison. [#813] builds the
instrument and runs the typed call, [#814] runs the incumbent, and [#815] writes the
verdict against the rule below.

**The rule is fixed as of the commit that adds this file, and a record's body is never
rewritten.** So when the results arrive they cannot move it. #813 and #814 add their
evidence under this record's `references/`, and #815's record cites this one. If #815
revises the rule after seeing the numbers, it reports the revised verdict **beside**
the one this rule gives. It does not replace it. The routing record flagged its own
fitted thresholds as the weak point of its result. A rule written after the numbers
would be fitted to them in the same way.

The design under test is
[`../../designs/2026-09-18-assess-task-typed-profile.md`](../../designs/2026-09-18-assess-task-typed-profile.md).

## What is measured

**Six dimensions: `complexity`, `creativity`, `autonomy`, `speed_sensitivity`,
`cost_sensitivity`, `verification_criticality`.** They are the design's typed-call
lane.

Out, each already settled:

- **`scope`**: code counts files where there is something to count.
  [`../2026-09-20-predictive-scope/`](../2026-09-20-predictive-scope/README.md)
  measured the forecast half.
- **`label`** and **`runner_up`**: a lookup, once a precedence order is written
  ([`../../decisions/2026-09-21-label-table-is-not-pure-code/`](../../decisions/2026-09-21-label-table-is-not-pure-code/README.md)).
- **`confidence`**: it has no producer under the design's split (the same decision).
  It is **not counted as a benefit of the typed call.**
- **`notes`**: code composes it in the design, and no consumer reads it.

## Corpus

[`references/measurement/corpus.json`](references/measurement/corpus.json) holds 50
cards. They are the predictive-scope record's 50, and
[`references/build-corpus.py`](references/build-corpus.py) derives them from that
record's committed corpus with no network:

```
D=dev_docs/research/2026-09-22-assess-task-comparison/references
python3 $D/build-corpus.py > $D/measurement/corpus.json   # the corpus
python3 $D/build-corpus.py --prompt issue-277             # one filled prompt
python3 $D/test_build_corpus.py                           # hermetic
```

Why reuse them rather than build fresh:

- **They are pre-work text.** Each passed that record's provenance gate: the issue
  existed before the pull request that closed it. A card written after the work
  describes finished work, which would bias every one of these six dimensions.
- **They are the consumer's input.** Each case is a title and a body. Rule 10 of
  [`../../typed-model-calls.md`](../../typed-model-calls.md) exists because that
  record found a body-only input scored differently from what `assess-task` is
  actually handed.
- **Nothing here needs the closing pull request.** So `build-corpus.py` drops every
  field describing it: `pr`, `pr_created`, `changed_files`, `churn`, and the `scope`
  label. They are hindsight, and a test asserts no case carries them.

What the corpus is not. These cards are issues this repository filed. They are not
the per-packet sub-tasks an `orchestrate-coders` run hands `select-coder`, which is
where `assess-task` spends most of its spawns. They are also one repository's work,
and each body is as it stands now, so it may have been edited after the work began.
That record names this edit risk, and it applies here unchanged.

## Input: both sides get the same state

- **Title and body, fenced, and nothing fetched.** The skill reads `related_files`
  when a description is thin. The typed call cannot fetch at all. A fair run has to
  give both sides the same state, so the choice is between assembling fetched context
  for both sides or for neither. It is **neither**, because the repository at HEAD
  holds the finished work for every card. A read would give the incumbent hindsight it
  never has in production. The cost of that choice: the incumbent is measured with its
  thin-description escalation turned off, and which way that biases it is unknown.
- **One fresh context per card, for every incumbent run.** That is the shape
  `select-coder` pays for per packet, and the cost half of this comparison needs it.
- **The incumbent is asked for its full block and scored on six fields.** Its cost is
  the cost of producing what it produces today.

The prompt is
[`references/measurement/baseline-prompt.md`](references/measurement/baseline-prompt.md).
It quotes the skill's contract, rubric, label table, **Ambiguity** and **Rules**
verbatim from commit `903dce5`, and a test checks that. The routing record's baseline
prompt had one hard constraint: no file reads. This one keeps it, for a different
reason. There, a read would have found the answer key. Here there is no answer key,
but a read would find the finished work.

## Ground truth: the incumbent's panel, not an answer key

[#810] weighed three options. The choice is the third.

1. **Hand labels, with the labeller blind to both outputs.** Rejected as the
   reference. Only two labellers are available, and each biases the result. One is
   this repository's author, who wrote the rubric and most of the cards. The routing
   record's labels had the same weakness, and that record wrote that a third of them
   were "opinion". Here it would be 300 judgments. The other is a Claude model, the
   same family as the incumbent, which would grade the incumbent on agreement with
   itself.
2. **Truth taken from outcomes.** None exists for these six. The candidates were
   review rounds, commit count and time to merge for `complexity` and `autonomy`, and
   the share of test or gate files touched for `verification_criticality`. All were
   rejected: they measure how the work went, with a particular coder and reviewer,
   not what the card demanded. `scope` had a changed-file count to check against. No
   dimension here does.
3. **Agreement with a panel of the incumbent. Adopted.** Rule 1 of
   [`../../typed-model-calls.md`](../../typed-model-calls.md) is "measure against the
   incumbent, not against ground truth". Every consumer of `task_profile` today is
   tuned to what the incumbent emits, so the question an adoption must answer is
   whether swapping the producer is **detectable**. The reference is three
   independent incumbent runs, and the typed call is scored exactly as if it were a
   fourth.

### Definitions

For each dimension `d`, over the cards:

| term           | definition                                                                                                                                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| panel          | `baseline/agent-{1,2,3}.json`: three incumbent runs, one fresh context per card each ([#814])                                                                      |
| `A_panel(d)`   | mean pairwise agreement among the three agents (3 pairs × cards)                                                                                                   |
| `A_jev(d)`     | mean agreement between each Jev pass and each agent (passes × 3 pairs × cards; at least 3 passes, per [#813])                                                      |
| `S_jev(d)`     | mean pairwise agreement among Jev passes: its stability, set against `A_panel`, which is the incumbent's                                                           |
| `A_const(d)`   | agreement with the panel of a constant answering `d`'s most frequent value across all panel answers. **The base rate** (rule 7)                                    |
| majority       | the value at least two agents gave; a card with no majority is **contested** on `d`, counted, and left out of the direction check only                             |
| Jev's answer   | per card, the value most passes gave; the direction check uses it                                                                                                  |
| missing answer | a value outside `d`'s enum, or an unparseable block, disagrees with everything. It is counted separately, so a formatting failure is never mistaken for a judgment |

The same agreement function scores agent-against-agent and Jev-against-agent. That is
what "the same `--score` path" means here, and it is the only reason the two numbers
can sit in one table.

**Order of work:** `A_jev` needs the panel. #813 therefore reports `S_jev` and the
typed call's cost from its own run, and the agreement terms wait for #814.

### What it can and cannot support

- **It can** say, per dimension, whether replacing the incumbent would be visible to
  a consumer, whether the typed call is as steady as the incumbent, and whether
  either beats a constant.
- **It cannot** say which side is right. Suppose the incumbent is consistently wrong
  on a dimension. A typed call that matches it inherits the error, and one that is
  right is scored as disagreeing. The routing record's finding 5 shows this happening:
  the agents found an error in the labels they were graded against. That is what
  adjudication, below, exists to catch.
- **A dimension is measurable only if at least 5 cards have a panel majority on a
  value other than its most frequent one.** Below that, a constant cannot be told
  apart from any producer on 50 cards. The record then says the dimension is
  unmeasurable here, and it makes no accuracy claim for either side. The expectation
  is that `speed_sensitivity` and `cost_sensitivity` fail this test, because this
  repository's work is prompt text and tooling, rarely a latency loop or bulk work.
  That is an expectation, not a result. The rule decides.

### Adjudication: optional, human, blind to source

Adjudication applies to a measurable dimension that fails **only** gate (a) below.
For each card where Jev's answer differs from the panel majority, a human sees the
card and the two answers, unlabelled and in random order, and picks A, B or both
defensible. The adjudicator must be a person: a Claude model shares the incumbent's
priors, so it cannot referee the incumbent. If nobody adjudicates, the gate stands as
computed.

## Decision rule

### Cost comes first

`R` is the incumbent's p95 wall clock per packet divided by the typed call's p95.
Wall clock runs end to end, from "profile needed" to "profile in hand". It includes
the spawn's context load on one side and the caller's state assembly on the other, as
#813 and #814 measure them. Dollars per packet are taken from each side's token
counts at the providers' prices on the run date.

**If `R < 2`, or the typed call is not cheaper per packet in dollars: don't adopt,
whatever the accuracy.** Adoption carries fixed costs that the per-packet saving has
to cover:

- a key for anyone who wants the fast path;
- a second code path, and a fallback to today's model that has to keep working;
- a vendor dependency.

The routing record declined a tie because its incumbent was nearly free. `2×` is the
smallest saving this record treats as more than nearly free.

### The tolerance the cost buys

| `R`    | per-dimension `δ` | whole-profile `δ_tuple` |
| ------ | ----------------- | ----------------------- |
| `< 2`  | no adoption       | no adoption             |
| `2–10` | 0 points          | 0 points                |
| `≥ 10` | 5 points          | 10 points               |

The top band is the likely one. The routing record timed the typed call at 0.55s
median, and a spawn loads a skill into a fresh model context. So 5 and 10 points are
the tolerances that will probably apply, and they are written here so they cannot be
tuned to the results.

Why 5 points: on 50 cards it is about 2–3 cards per dimension. Each of the four
binaries is a single-dimension trigger for a label (per the label decision), so one
disagreement can change which coder a packet goes to. Five points is the most a
packet at least 10× cheaper is allowed to buy. Six dimensions could each lose up to
their `δ` and add up to a large loss, so the whole-profile cap bounds the total.

### Per dimension

A measurable dimension `d` **matches** only when all four gates hold:

| gate                   | condition                                                                                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| (a) agreement          | `A_jev(d) ≥ A_panel(d) − δ`                                                                                                                                      |
| (b) stability          | `S_jev(d) ≥ A_panel(d) − δ`                                                                                                                                      |
| (c) beats the constant | `A_jev(d) > A_const(d)`. Otherwise code can hard-wire the value                                                                                                  |
| (d) no one-way error   | among cards where Jev's answer differs from the panel majority, if there are at least 5 and at least 80% fall on one side (lower or higher), the dimension fails |

Gate (d) is here because a rate hides direction. `scope` missed 31 of 33 times in one
direction. If a trigger dimension errs one way, it suppresses or inflates one label
every time it errs, which is a different failure from scattered noise at the same
rate.

**The one revision allowed in advance:** gate (a) counts as passed if a
source-blind human adjudicator sides with Jev on at least half of that dimension's
disagreements. No other result moves a gate.

### Whole profile

Take exact agreement on the tuple of measurable dimensions, pairwise, in the same way
as a single dimension. It must satisfy `A_jev(tuple) ≥ A_panel(tuple) − δ_tuple`.

### Verdict

- **Adopt:** every measurable dimension matches, and the whole-profile gate passes.
- **Adopt for these dimensions only:** allowed **only** if every failing dimension
  can come off the subagent anyway, meaning it is dropped from the contract, computed,
  or made a constant. The cost win depends on removing the spawn. A spawn kept for one
  dimension costs the whole spawn, and then the saving that justified `δ` is gone.
  Otherwise a failing dimension means don't adopt.
- **Don't adopt:** in every other case.

Whatever the verdict, #815's decision record lists any unmeasurable dimension by
name. It says whether that dimension rides on the typed call unverified or becomes a
constant. This record does not make that choice.

## What this does not establish

Nothing yet. There are no results. The limits already known are listed above, and
#815 carries them: the corpus is issues, not packets; the incumbent's
thin-description read is disabled; the reference is agreement, not correctness; and
two dimensions will probably be unmeasurable.

[#810]: https://github.com/bestdan/workflow-skills/issues/810
[#813]: https://github.com/bestdan/workflow-skills/issues/813
[#814]: https://github.com/bestdan/workflow-skills/issues/814
[#815]: https://github.com/bestdan/workflow-skills/issues/815
