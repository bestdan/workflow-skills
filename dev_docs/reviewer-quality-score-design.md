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

## The key insight: the grader is already (mostly) blind

`co-review` step 8 hands the reconciler sub-agent findings from Claude and the
local agents labelled neutrally as "Reviewer A", "Reviewer B", … — deliberately,
so it cannot grade an agent instead of a finding. It then rates each finding
`high` / `medium` / `low`, where `low` explicitly means "wrong, not applicable,
or over-engineering."

That is already a blinded grade against a shared diff. The instrument exists; we
simply throw its readings away. This design writes them down — and closes the
one blinding gap that exists today: **GitHub comments currently reach the
reconciler with their real authors.** A finding graded under the name "Copilot"
is not a blind grade, so bot rows would be worthless. Section 1 fixes this by
relabelling _every_ source, not just the local ones.

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
  text into the main context; a script returns nothing.
- Acting on the data is a **human decision**, made deliberately against a large
  sample, not an automated demotion fired by a threshold on a handful of rows.

**Honesty about enforcement.** In a prose-driven skill, "no step reads the
ledger" is a _policy_ control, not a capability boundary — the agent retains
`Read` access to a predictable path, and an agent that ran `/co-review-stats`
earlier in a session carries the scores in context into any co-review run in
that same session. Mitigations, strongest first:

1. **Optional deny rule** (real capability boundary for direct reads): add
   `"Read(~/.claude/co-review/**)"` to `permissions.deny`. The stats script
   still works — it reads the file itself and is invoked via Bash.
2. **Same-session rule**, stated in both `/co-review-stats` and `co-review`:
   after viewing stats, don't run a co-review in the same session; if aggregate
   reviewer scores are already in context when co-review starts, say so in the
   run summary so the run's rows can be discounted.
3. The stats script prints the table itself (its stdout is the deliverable);
   the model adds no numbers of its own.

This is proportionate to a single-user tool. The failure it leaves open —
deliberately reading the ledger mid-review in defiance of the skill — also
defeats any local enforcement short of removing the file, and is visible in the
transcript.

## Components

### 1. Reconciler contract (edit to `co-review` step 8)

Three changes: one to what the reconciler is given, two to the JSON it returns.

**Blind every source, not just local ones.** Today step 8 anonymizes Claude and
the local agents but passes GitHub comments "with author". All sources —
including GitHub bots and human commenters — are relabelled to neutral
"Reviewer X" labels before the reconciler sees them, and the label→identity
mapping stays with the main agent. Humans are included in reconciliation (their
comments corroborate or contradict findings, and an identifiable human name
would unblind the grader just as a bot name does); they are anonymized like
everyone else and handled specially only at recording time (see
`human_corroborated` below).

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
  "schema": 1,
  "run_id": "2026-07-14T15-02-11Z-a3f9",
  "ts": "2026-07-14T15:02:11Z",
  "repo": "bestdan/workflow-skills",
  "pr": 231,
  "base_sha": "5ceb4a9",
  "head_sha": "3d1a42b",
  "mode": { "local": false, "post": false, "non_interactive": false },
  "diff_lines": 412,
  "co_review_version": "1.4.2",
  "reconciler_model": "claude-opus-4-8",
  "reviewers": [
    { "name": "claude", "disposition": "ran", "model": "claude-opus-4-8" },
    {
      "name": "devin",
      "disposition": "ran",
      "model": "swe-1.6",
      "command": "devin --sandbox --permission-mode auto --prompt-file ..."
    },
    { "name": "agy", "disposition": "skipped", "reason": "auth probe failed" },
    { "name": "codex", "disposition": "timed-out" }
  ]
}
```

Without this record, a reviewer that ran ten times and found nothing is
indistinguishable from one that never ran — and "finds nothing" is itself a
quality signal worth seeing.

**Provenance, because names drift.** "devin" is not one thing over time: its
model gets bumped in `.co-review.yml`, its command string changes, the
reconciler model changes with the session, and the skill itself evolves — and
any of those can move a wrong-rate. Each run therefore records a `schema`
version, the plugin version, the reconciler's model, the diff identity
(`base_sha`/`head_sha`), and each reviewer's model and exact command string.
That is the cheap subset that lets `--since` answer "did devin regress after
the model bump?" without probing CLI versions or hashing prompts on every run —
if the free-text `rationale` grep ever shows drift these fields can't explain,
add more then.

**Eligible cohort.** A run appears in the ledger **iff reconciliation
completed** — the record is written immediately after the reconciler returns.
An invocation that aborts earlier (staleness stop, no PR found, hard error) is
intentionally absent: no grades were produced, so it can neither help nor hurt
any reviewer. The denominator is "reconciled runs," and the stats output says
so.

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
  "human_corroborated": false,
  "verdict": "wrong",
  "rationale": "..."
}
```

**Who is scored:** Claude's own review (as `claude`), the local CLI agents, and
GitHub bot reviews. Humans are **never named in the ledger** — scoring
colleagues is not the goal. But a human source can't simply be dropped from
`sources`: if devin and a human reported the same issue, deleting the human
would make devin look like the sole discoverer. So the recording script strips
human identities and sets the anonymous `human_corroborated` flag instead — the
finding keeps its evidential weight without anyone being scored. A finding
raised _only_ by humans is not recorded at all.

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

A new step **immediately after the reconciler returns** (before fixes are
applied, so a failure later in the run can't lose the record — the grades exist
the moment reconciliation completes). The main agent writes three temp files
and calls:

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
- **Consistency model.** Auto-pilot can run co-reviews concurrently, and
  "never fatal" invites retries — so the append must not be naive. The script:
  takes an exclusive `flock` on the ledger; **dedups on `run_id`** (if the id
  already appears, exit `RECORDED: duplicate, skipped` — retries are
  idempotent); writes the run record and all its findings as **one batch under
  the one lock** (no interleaving, no run-without-findings torn state); releases
  the lock. All-or-nothing per run, plain JSONL, no database — proportionate to
  a ledger that will hold hundreds of rows.
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

| Column        | Definition                                                          |
| ------------- | ------------------------------------------------------------------- |
| `runs`        | Runs where the reviewer's disposition was `ran`                     |
| `findings`    | Rows listing the reviewer in `sources`                              |
| `wrong %`     | The headline: `wrong` ÷ findings                                    |
| `Δ claude`    | Paired wrong-rate delta vs `claude` (see below)                     |
| `n/a %`       | `not-applicable` ÷ findings — a _context_ problem                   |
| `oos %`       | `out-of-scope` ÷ findings — a _rubric_ problem                      |
| `high`        | Findings that survived as `high`                                    |
| `unique-high` | `high` rows: sole automated source **and** not `human_corroborated` |
| `finds/run`   | Volume — catches the reviewer that is quiet, not correct            |

**The baseline comparison is paired, not marginal.** Reviewers don't skip at
random — auth failures cluster in time, timeouts correlate with big diffs — so
devin's wrong-rate over _its_ runs against Claude's wrong-rate over _all_ runs
would confound reviewer quality with workload selection. `Δ claude` is computed
only over the runs where **both** that reviewer and `claude` ran: same diffs,
same reconciler, same session. The marginal `wrong %` stays in the table (it's
what you intuitively read), but `Δ claude` is the column that supports a
keep/drop decision. The script also prints each reviewer's paired-cohort size —
with N this small, honest denominators beat confidence-interval theater, and
`--repo` / `--since` provide the stratification that matters.

`unique-high` is the column that prevents the obvious mistake. Precision alone
would reward a reviewer that reports nothing (0 findings, 0% wrong, perfect
score) and might evict a noisy reviewer that is nonetheless the only one catching
real bugs. Both numbers have to be read together. Two caveats are inherent in
its definition: a `human_corroborated` catch is excluded (a human on the PR had
it anyway, so the reviewer added redundancy, not discovery), and "unique" is
always **relative to the reviewers that ran that day** — the run record makes
that cohort explicit, so the stats can't silently credit a reviewer for being
alone in an empty room.

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
   reconciled finding, in every mode; the write is locked, batched, and
   idempotent on `run_id`; and a recording failure never breaks a run.
2. The reconciler sees **only neutral labels** — including for GitHub bots and
   human commenters. No identifiable source name reaches the grader.
3. No agent in the review path reads `findings.jsonl`. Enforced by there being
   no step that does, stated as an explicit rule in `co-review`'s Rules section,
   and hardenable via the optional `permissions.deny` read rule.
4. `/co-review-stats` reports every reviewer plus the `claude` baseline, with
   the headline comparison **paired on common runs** and all arithmetic done in
   the script.
5. After ~30 runs, the table can answer: _is devin actually wrong more often
   than Claude on the same diffs, and does it catch anything nobody else does?_

## Open question, resolved by data

Whether `wrong` findings cluster by reviewer (drop it), by verdict type (fix the
rubric or the context we pass), or not at all (the impression was wrong and we
keep everyone). The design deliberately does not presuppose the answer.
