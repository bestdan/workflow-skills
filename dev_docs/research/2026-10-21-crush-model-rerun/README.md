---
created: 2026-10-21
question: "Is there a model on Hyper that reviews as well as crush's `kimi-k3` pin, faster and without its failures on large diffs?"
feeds: "https://github.com/bestdan/workflow-skills/pull/TBD"
---

# crush's model pin, re-run across Hyper's open-weight models (2026-10-21)

**`hyper/deepseek-v4.1-flash-1017` matches `kimi-k3` on review quality, and it
was the fastest model on both fixtures.** On the planted-bug fixture every model
tried caught both bugs, and DeepSeek finished in 13s against Kimi K3's 73s. On
a large real PR no model failed, and DeepSeek finished in 140s against 228s. Its
Hyper price is about a tenth of Kimi K3's. This is two fixtures with one run
per model, so treat the re-pin as a trial and read it back from the ledger.

## Question

The co-review findings ledger showed crush with the worst failure rate in the
pool and the second-slowest p90, while its paired accuracy was in line with
codex and copilot and it was one of four reviewers with sole catches of
high-severity bugs. Every ledger row was on `hyper/kimi-k3`, so the ledger could
not say whether a different model would do better. This record holds the
provider fixed, at Hyper, and changes only the model.

## Method

Candidates were the open-weight models scoring at or near Kimi K3 on Artificial
Analysis's Intelligence Index that Hyper serves: `deepseek-v4.1-flash-1017`,
`glm-5.3`, `glm-5.3-flash`, `qwen3.8-max` and `minimax-m3`, with `kimi-k3` re-run
as the baseline. crush v0.98.1, one run per model, sequential (concurrent
`crush run`s race the data directory's SQLite migration).

**Fixture 1, planted bugs.** The 2026-09-27 fixture and runner, unchanged:
[`../2026-09-27-co-review-reviewer-models/references/run.sh`](../2026-09-27-co-review-reviewer-models/references/run.sh)
on `bug.diff`, two planted bugs in PR #913's diff. Raw output and timings are in
[`references/out/`](references/out/).

**Fixture 2, a large real PR.** A 1,986-line PR from a private repo, rebuilt at
the exact head the ledger recorded, as `review_prompt.md` plus `gh pr diff`
(142KB; the original run also carried that repo's review conventions, about
16KB more). `kimi-k3` had failed on it in co-review, with empty output after
267s. The answer key is the two medium findings that review's reconciler
confirmed. The input and the outputs are not committed, because the repo is
private.

## Findings

### Fixture 1: planted bugs (27KB)

| Model                      | Time | Bug 1 (`break`) | Bug 2 (counter) | Test gaps (of 2) | Extra findings       |
| -------------------------- | ---- | --------------- | --------------- | ---------------- | -------------------- |
| `kimi-k3`                  | 73s  | yes             | yes             | 1                | 2 questions          |
| `deepseek-v4.1-flash-1017` | 13s  | yes             | yes             | 2                | 1 question           |
| `glm-5.3`                  | 90s  | yes             | yes             | 2                | 1 question           |
| `glm-5.3-flash`            | 147s | yes             | yes             | 2                | 3, mostly weak       |
| `qwen3.8-max`              | 57s  | yes             | yes             | 1                | none                 |
| `minimax-m3`               | 94s  | yes             | yes             | 1                | 3 documentation nits |

Every model ties on the bugs, so this fixture separates them only on speed,
test-gap coverage and noise.

### Fixture 2: large real PR (142KB)

| Model                      | Time | Completed | Key finding 1 | Key finding 2 | Findings |
| -------------------------- | ---- | --------- | ------------- | ------------- | -------- |
| `kimi-k3`                  | 228s | yes       | no            | no            | 4        |
| `deepseek-v4.1-flash-1017` | 140s | yes       | partly        | no            | 4        |
| `glm-5.3`                  | 262s | yes       | partly        | no            | 5        |
| `glm-5.3-flash`            | 210s | yes       | no            | yes           | 7        |
| `qwen3.8-max`              | 227s | yes       | no            | no            | 2        |
| `minimax-m3`               | 95s  | yes       | no            | no            | 14       |

"Partly" means the model named the same gap at a different site from the one
the reconciler confirmed. Three models, DeepSeek among them, independently
reported the same additional crash path that the answer key does not contain;
whether it is real was not checked. `minimax-m3`'s 14 findings were mostly
notes and unverified questions.

Verified: every cell comes from the run outputs. Inferred, not measured: that
`kimi-k3`'s ledger failures are intermittent rather than input-bound, since it
completed fixture 2 here after failing on it in co-review.

### Cost and context, per crush's Hyper catalog on 2026-10-21

| Model                      | Input $/1M | Output $/1M | Context |
| -------------------------- | ---------- | ----------- | ------- |
| `kimi-k3`                  | 3.27       | 16.33       | 1M      |
| `deepseek-v4.1-flash-1017` | 0.33       | 1.31        | 1M      |
| `glm-5.3`                  | 1.52       | 4.79        | 1M      |
| `glm-5.3-flash`            | 0.16       | 0.54        | 1M      |
| `qwen3.8-max`              | 2.00       | 6.00        | 1M      |
| `minimax-m3`               | 0.33       | 1.31        | 512K    |

## Not settled

Hyper itself still limits speed: DeepSeek's 140s on fixture 2 is far slower than
the fastest hosts Artificial Analysis measures for the same model. A different
provider is a separate, later change, so that model and provider are not changed
at once.

## Feeds

The re-pin of crush to `hyper/deepseek-v4.1-flash-1017`. Read it back with
`/co-review-stats --since 2026-10-21`.
