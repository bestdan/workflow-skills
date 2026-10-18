---
created: 2026-09-27
question: "Were agy, devin and crush giving weaker co-reviews because they only see the diff, or because of the models they were pinned to — and which available models review better?"
feeds: "https://github.com/bestdan/workflow-skills/pull/920"
---

# co-review's external reviewers: diff-only access versus model tier (2026-09-27)

**The pinned model looks like the cause; diff-only access does not.** Every
external reviewer, codex included, gets the same diff-only input: the rubric
plus `gh pr diff`, with no repo access. Given one fixture with two planted bugs,
codex caught both. So did every stronger candidate for agy, devin and crush.
The two misses came from the pinned models: devin's `swe-1.6` missed a bug, and
crush's `kimi-k2.7-code` raised one only as a question. This is one diff with
one run per model: enough to rule out "they can't see enough", not enough to
rank models that tied.

## Question

Were agy, devin and crush giving weaker co-reviews because they only see the
diff, or because of the models they were pinned to — and which available models
review better?

## Method

**Access, by reading the reviewer files.** Each reviewer's invocation and prompt
in `skills/co-review/reviewers/` at `main` `310c720c`:

- **codex** runs in the repo, but its prompt says "Do NOT explore the
  filesystem".
- **agy** trusts only the input file's directory (`--add-dir "<INPUT-DIR>"`),
  and its prompt says "Do NOT explore any other file".
- **devin** runs from an empty directory (`cd "<NEUTRAL>"`).
- **crush** runs with `--cwd` set to an empty directory, and the shipped
  `crush-readonly.json` disables all 29 of its tools, `view`, `grep` and `glob`
  included.

None of the four CLIs is limited to diffs; co-review keeps them away from the
repo on purpose. The reasons are recorded in each reviewer's file:
contributor-controlled config in the reviewed repo, and command strings that
must stay identical from run to run so an exact-match allow-rule can approve
them once.

**Fixture.** PR #913's diff (`references/orig.diff`) with two bugs planted by
`references/plant-bugs.py` (`references/bug.diff`). Both can be found from the
diff alone:

1. The `doctor()` loop's new `skipped)` arm uses `break` where the neighbouring
   `handed-off)` arm uses `continue`, so the loop stops at the first skipped
   row.
2. The `status_report()` one-line summary prints `skipped=$counts_parked`.

The input was `review_prompt.md` followed by `bug.diff`, 26,895 bytes, with no
conventions and no reviewer requests.

**Runs.** `references/run.sh` copies each reviewer's co-review invocation: the
same pointer text, read-only flags and neutral cwd, with one model swapped in.
Each pair ran once, all in parallel, on 2026-09-27. CLI versions: agy 1.2.12,
devin 3000.11.3, codex-cli 0.154.0, crush v0.92.0. The candidates came from each
CLI's own model listing (`agy models`, `devin models list`,
`crush models`) that day.

In the parallel batch the two `crush run`s raced the SQLite migration of one
fresh data directory, and the `kimi-k3` run died within a second with
`duplicate column name: summary_message_id`. `kimi-k2.7-code` completed normally
in that batch; `kimi-k3` was re-run alone, and its output file is the re-run.

Raw output and timings: `references/out/<tag>.txt` and `.meta`.

## Findings

### Bugs caught, per model

| Reviewer | Model                   | Bug 1 (`break`)               | Bug 2 (counter) | Also found                                     | Wall time |
| -------- | ----------------------- | ----------------------------- | --------------- | ---------------------------------------------- | --------- |
| codex    | `gpt-5.6-terra`         | yes                           | yes             | —                                              | 13s       |
| devin    | `swe-1.6`               | **no**                        | yes             | —                                              | 4s        |
| devin    | `swe-2-high`            | yes                           | yes             | status test doesn't check the one-line summary | 107s      |
| devin    | `swe-2-max`             | yes                           | yes             | —                                              | 121s      |
| agy      | Gemini 3.6 Flash (High) | yes                           | yes             | —                                              | 29s       |
| agy      | Gemini 3.8 Flash (High) | yes                           | yes             | both test gaps (below)                         | 116s      |
| agy      | Gemini 3.1 Pro (High)   | yes                           | yes             | doctor test row order                          | 127s      |
| crush    | `hyper/kimi-k2.7-code`  | as a `question`, not an issue | yes             | —                                              | 195s      |
| crush    | `hyper/kimi-k3`         | yes                           | yes             | both test gaps, plus one UNVERIFIED question   | 66s       |

"Both test gaps": the PR's doctor test puts the `skipped` row last, so it
cannot tell `break` from `continue`, and its status-report test checks
`STATUS.md` but not the one-line summary where bug 2 lives. Those two gaps are
why the PR's own tests would not have caught either bug. Four models reported at
least one of them; `swe-1.6`, `kimi-k2.7-code`, `swe-2-max`, 3.6 Flash and codex
reported neither.

Verified: every cell above comes from the output files. Inferred, not measured:
that one fixture separates models the same way real PRs would. The models that
caught both bugs tie on this fixture; the "also found" column is the only thing
that separates them, and it is one run each.

### Cost and context, per Hyper's `/api/v1/models` on 2026-09-27

| Model            | Input $/1M | Output $/1M | Context |
| ---------------- | ---------- | ----------- | ------- |
| `kimi-k2.7-code` | 1.03       | 4.36        | 256K    |
| `kimi-k3`        | 3.27       | 16.33       | 1M      |

devin lists `swe-1.6`, `swe-2-high` and `swe-2-max` as free on this account's
tier. The agy models are billed against account quota, which was not measured.

## Feeds

bestdan/workflow-skills#920, which moves the pins to `swe-2-high`,
Gemini 3.8 Flash (High) and `hyper/kimi-k3`.
