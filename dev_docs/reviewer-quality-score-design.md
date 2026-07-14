---
type: design
title: Reviewer quality score — grading co-review reviewers from reconciliation outcomes
status: draft
created: 2026-07-14
---

# Reviewer quality score

We run `/co-review` with several reviewers (`codex`, `agy`, `devin`, `copilot`,
GitHub bots) alongside Claude's own review. Impressionistically, some reviewers
produce findings that reconciliation regularly throws out as wrong. That
impression has never been measured.

This design turns **reconciliation into a measurement instrument**: every
co-review run already grades every finding, so record the grades and aggregate
them per reviewer. The output is evidence for two decisions we currently make on
vibes — **which reviewers to keep running**, and **how to fix a reviewer's
prompt**.

## The key insight: the grader is already blind

`co-review` step 8 hands the reconciler sub-agent every finding labelled
neutrally as "Reviewer A", "Reviewer B", … — deliberately, so it cannot grade an
agent instead of a finding. It then rates each finding `high` / `medium` / `low`,
where `low` explicitly means "wrong, not applicable, or over-engineering."

That is already a blinded grade against a shared diff. The instrument exists; we
simply throw its readings away. This design writes them down.

## The firewall (the load-bearing constraint)

**Nothing in the review path may ever read the ledger.** Not the reconciler, not
the main agent, in any mode, at any step.

If any in-review agent learns that "devin is wrong 38% of the time," it stops
grading the finding in front of it and starts grading the reviewer that produced
it — and the ledger degenerates into a self-fulfilling prophecy that confirms
whatever it already believed. The blinding in step 8 is the only reason this data
is worth collecting, and a read anywhere in the review path destroys it.

The feedback loop is therefore **entirely human-mediated and offline**:

```
co-review run ──write-only──> findings.jsonl ──> /co-review-stats ──> human reads
                                                                          │
                                        edits .co-review.yml / reviewer prompt
                                                                          │
                                                                          v
                                                              next co-review run
```

Consequences, all deliberate:

- co-review is **write-only** to the ledger. It never reads it — not even to warn
  you mid-run that a reviewer looks bad.
- The write is performed by a **script**, not an agent. A sub-agent would return
  text into the main context; a script returns nothing. Structural, not
  prompt-enforced.
- Acting on the data is a **human decision**, made deliberately against a large
  sample, not an automated demotion fired by a threshold on a handful of rows.

## Components

### 1. Reconciler contract (edit to `co-review` step 8)

Two changes to the JSON the reconciler returns.

**Split `low` into three verdicts.** The current `low` conflates failure modes
that demand opposite responses. The verdict set becomes:

| Verdict          | Meaning                                                    | Implied response         |
| ---------------- | ---------------------------------------------------------- | ------------------------ |
| `high`           | Clearly correct, low-risk fix                              | —                        |
| `medium`         | Probably correct, a judgment call                          | —                        |
| `wrong`          | Factually false about the code                             | Drop or retune the model |
| `not-applicable` | True in general, doesn't hold for this repo's actual setup | Fix the context we pass  |
| `out-of-scope`   | Real, but pre-existing or over-engineered for this change  | Fix the rubric           |

A reviewer that hallucinates and a reviewer that lacks repo context are both
"noisy" today, and the fix for each is completely different. This split is what
separates _drop the reviewer_ from _tune the prompt_.

**`source` → `sources`.** One row per **distinct issue**, listing every reviewer
who raised it:

```json
{
  "file": "skills/co-review/SKILL.md",
  "line": 42,
  "issue": "...",
  "sources": ["Reviewer A", "Reviewer C"],
  "verdict": "high",
  "recommended_fix": "...",
  "rationale": "..."
}
```

The reconciler already has to notice when two reviewers report the same issue in
order to reconcile them; this makes it say so. Uniqueness then falls straight
out: a `high` row with exactly one source is a bug **only that reviewer caught**.

The reconciler remains blind throughout. It labels "Reviewer A", and nothing in
its prompt reveals that a scoreboard exists.

### 2. The ledger — `~/.claude/co-review/findings.jsonl`

Global, not per-repo: reviewer quality is a property of the _reviewer_, and a
per-repo ledger would never reach a sample size where "devin is wrong 38% of the
time" is more than noise. The repo is recorded as a _field_, so slicing by repo
(or language) stays possible.

Append-only, two record types.

**`run`** — one per co-review invocation. The denominator.

```json
{
  "type": "run",
  "run_id": "2026-07-14T15-02-11Z-a3f9",
  "ts": "2026-07-14T15:02:11Z",
  "repo": "bestdan/workflow-skills",
  "pr": 231,
  "mode": { "local": false, "post": false, "non_interactive": false },
  "diff_lines": 412,
  "reviewers": [
    { "name": "claude", "disposition": "ran" },
    { "name": "devin", "disposition": "ran" },
    { "name": "agy", "disposition": "skipped", "reason": "auth probe failed" },
    { "name": "codex", "disposition": "timed-out" }
  ]
}
```

Without this record, a reviewer that ran ten times and found nothing is
indistinguishable from one that never ran — and "finds nothing" is itself a
quality signal worth seeing.

**`finding`** — one per reconciler row, with `sources` **unblinded** to real
reviewer names.

```json
{
  "type": "finding",
  "run_id": "2026-07-14T15-02-11Z-a3f9",
  "file": "...",
  "line": 42,
  "issue": "...",
  "sources": ["devin", "claude"],
  "verdict": "wrong",
  "rationale": "..."
}
```

**Who is scored:** Claude's own review (as `claude`), the local CLI agents, and
GitHub bot reviews. Human PR comments are **dropped, not logged** — scoring
colleagues is not the goal and the rows would only pollute the aggregate.

**Reviewer naming.** A local CLI agent is recorded under its config name
(`codex`, `agy`, `devin`, `copilot`); a GitHub bot is recorded under its bot
login, prefixed — `bot:copilot-pull-request-reviewer`. The local `copilot` CLI
and the GitHub Copilot bot are **different reviewers** reached by different
paths, and merging them under one name would silently average two things we may
well want to make opposite decisions about.

**Claude is the control group**, and this is the most important thing in the
schema. A 30% wrong-rate is uninterpretable in isolation: if Claude's rate is 8%,
30% is damning; if Claude's is 25%, the reconciler is simply a harsh grader and
the reviewer is roughly par. Claude's review goes through the _same_ blinded
reconciler on the _same_ diff, which makes it a genuinely fair baseline — and it
catches the failure mode where every reviewer looks terrible because the
**reconciler prompt** is miscalibrated.

**Privacy.** Rows contain issue text and file paths from private repos. The
ledger is local-only, under `~/.claude/`, never committed, never synced.

### 3. The write — `scripts/co-review-record.py`

A new step at the **end** of a co-review run, after reconciliation and after any
fixes are applied. The main agent writes three temp files and calls:

```bash
uv run scripts/co-review-record.py \
  --run <meta.json> --findings <recon.json> --mapping <map.json>
```

- `--mapping` is the label→reviewer dict (`{"Reviewer A": "devin", ...}`). The
  main agent necessarily holds this — it dispatched the reviewers. Identities
  were never the risk; **aggregate scores** are, and the main agent never sees
  one.
- The script performs the unblinding join, appends both record types, and prints
  exactly one line: `RECORDED: 14 findings, 4 reviewers`. It never echoes ledger
  contents.
- Failure to record is **never fatal** — a warning in the run summary, matching
  co-review's existing posture on reviewer failures. Telemetry must not be able
  to break a review.
- Runs in **every** mode, including `--non-interactive`. Auto-pilot's
  `/deliver-task` lifecycle therefore generates data for free, overnight, which
  is where most of the sample will come from.

### 4. The read — `/co-review-stats` + `scripts/co-review-stats.py`

The **only** thing that reads the ledger. Never invoked from within a review.

The **script** does the arithmetic; the model only presents it. Aggregating
hundreds of JSONL rows is precisely the task an LLM should not be eyeballing, and
a hallucinated percentage here would corrupt the decision the whole system exists
to inform.

Per reviewer:

| Column        | Definition                                               |
| ------------- | -------------------------------------------------------- |
| `runs`        | Runs where the reviewer's disposition was `ran`          |
| `findings`    | Rows listing the reviewer in `sources`                   |
| `wrong %`     | The headline: `wrong` ÷ findings                         |
| `n/a %`       | `not-applicable` ÷ findings — a _context_ problem        |
| `oos %`       | `out-of-scope` ÷ findings — a _rubric_ problem           |
| `high`        | Findings that survived as `high`                         |
| `unique-high` | `high` rows where the reviewer was the **sole** source   |
| `finds/run`   | Volume — catches the reviewer that is quiet, not correct |

`unique-high` is the column that prevents the obvious mistake. Precision alone
would reward a reviewer that reports nothing (0 findings, 0% wrong, perfect
score) and might evict a noisy reviewer that is nonetheless the only one catching
real bugs. Both numbers have to be read together.

Flags: `--since <date>`, `--repo <name>` (did a reviewer regress after a model
bump? is it fine on Python and hopeless on shell?).

## What is deliberately not being built

- **No auto-demotion.** A demoted reviewer stops producing the findings that
  would exonerate it — a trapdoor. And with per-reviewer N in the dozens, any
  threshold fires on noise. Eviction stays a human edit to `.co-review.yml`, made
  against a real sample.
- **No mid-run nudge.** Considered and rejected: it would require the review path
  to read the ledger, which is the one thing the firewall forbids.
- **No failure-mode taxonomy** (`hallucinated-api`, `misread-diff`, …). This is
  what we'd eventually want for prompt tuning, but any taxonomy invented today is
  guesswork. `rationale` is free text, so the information is _captured_ — grep it
  by hand for the first month, then formalize the tags the data actually shows.
- **No thresholds.** None are needed: nothing fires automatically. A human reads
  a table.

## Success criteria

1. A co-review run appends one `run` record and one `finding` record per
   reconciled finding, in every mode, and a recording failure never breaks a run.
2. No agent in the review path reads `findings.jsonl`. Enforced by there being no
   step that does, and stated as an explicit rule in `co-review`'s Rules section
   so a later edit doesn't quietly reintroduce one.
3. `/co-review-stats` reports every reviewer plus the `claude` baseline, with
   arithmetic done in the script.
4. After ~30 runs, the table can answer: _is devin actually wrong more often than
   Claude, and does it catch anything nobody else does?_

## Open question, resolved by data

Whether `wrong` findings cluster by reviewer (drop it), by verdict type (fix the
rubric or the context we pass), or not at all (the impression was wrong and we
keep everyone). The design deliberately does not presuppose the answer.
