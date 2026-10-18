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
them per reviewer, alongside how long each reviewer took. The output is evidence
for two decisions we currently make on vibes — **which reviewers to keep
running**, and **how to fix a reviewer's prompt**. A reviewer earns its place by
being right, by catching what others miss, and by not holding up every run while
it does either, so the evidence covers speed as well as correctness.

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
is not a blind grade, so bot rows would be worthless. The reconciler contract (Components §1) fixes this by
relabelling _every_ source, not just the local ones.

## The firewall (the load-bearing constraint)

**Nothing in the review path may ever read the ledger.** Not the reconciler, not
the main agent, in any mode, at any step. What the rule protects is an agent's
context, so it covers anything whose output reaches an agent. The recording
script's own scan is the one read it allows: under its lock it looks for an
existing `(host, run_id)` so a retry is not counted twice, and it hands back
nothing but its one-line receipt.

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

- co-review is **write-only** to the ledger. No agent in it reads the ledger —
  not even to warn you mid-run that a reviewer looks bad.
- The write is performed by a **script**, not an agent. A sub-agent would return
  text into the main context; a script returns nothing.
- Acting on the data is a **human decision**, made deliberately against a large
  sample, not an automated demotion fired by a threshold on a handful of rows.

**Honesty about enforcement.** In a prose-driven skill, "no step reads the
ledger" is a _policy_ control, not a capability boundary — the agent retains
`Read` access to a predictable path, and an agent that ran `/co-review-stats`
earlier in a session carries the scores in context into any co-review run in
that same session. Mitigations, strongest first:

1. **Optional deny rule**: add `"Read(~/.claude/co-review/**)"` to
   `permissions.deny`. It blocks the Read tool only; a Bash command such as
   `cat` still reads the file, which is also why the stats script, invoked via
   Bash, keeps working. So it closes the accidental path, not a deliberate one.
   The boundary for a Bash read is `sandbox.filesystem.denyRead`, and only for
   sandboxed commands.
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

**`run`** — one per reconciled co-review invocation. The denominator.

```json
{
  "type": "run",
  "schema": 1,
  "run_id": "2026-07-14T15-02-11Z-a3f9",
  "host": "lindev",
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
    {
      "name": "claude",
      "disposition": "ran",
      "model": "claude-opus-4-8",
      "duration_s": 94.2
    },
    {
      "name": "devin",
      "disposition": "ran",
      "model": "swe-1.6",
      "command": "devin --sandbox --permission-mode auto --prompt-file ...",
      "duration_s": 211.7
    },
    {
      "name": "agy",
      "disposition": "skipped",
      "reason": "auth probe failed",
      "duration_s": null
    },
    { "name": "codex", "disposition": "timed-out", "duration_s": 600.0 },
    { "name": "copilot", "disposition": "failed", "duration_s": 3.1 }
  ]
}
```

Without this record, a reviewer that ran ten times and found nothing is
indistinguishable from one that never ran — and "finds nothing" is itself a
quality signal worth seeing.

**Speed.** `duration_s` is the wall-clock seconds from dispatching a reviewer to
having its result, `null` for a reviewer that was never dispatched. Reviewers
run in parallel, so the slowest one sets how long the whole review waits; a
reviewer that is correct but takes four times as long as the rest costs every
run that wait. Three rules keep the number honest:

- **Measured, never estimated.** The dispatch records a timestamp before and
  after each reviewer and subtracts. A duration the model guessed from the
  transcript would be fabricated data in a ledger whose only value is that it
  isn't.
- **A timeout is censored, not slow.** A `timed-out` reviewer's `duration_s` is
  how long it was waited on, which is the timeout bound, not how long it would
  have taken. The stats keep those runs out of the duration percentiles and
  count them separately.
- **Failures cost time too.** A reviewer that runs and returns nothing usable
  (`disposition: failed`, e.g. answering NO INPUT) records its duration like any
  other, and the stats count it in the duration percentiles. Its time was spent
  whether or not it produced a finding, and the percentiles measure how long a
  run waits on the reviewer.

Duration is a measurement, not a grade, so recording it does not touch the
blinding. Reading it is still a ledger read, and the firewall still forbids it
in the review path.

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
  "host": "lindev",
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
ledger itself is local-only, under `~/.claude/`, and never committed.

**Several machines, one table.** Each machine keeps its own ledger, and the
`host` on every run and finding record (the recording script fills in the
hostname) makes every run identifiable across machines by `(host, run_id)`.
Findings carry `host` too, so a merged ledger can still tie each finding to its
run when two machines happen to produce the same `run_id`. Combining ledgers is then
concatenation plus dropping repeated pairs. Moving rows off a machine is a
separate flush (#1041), never part of the review: it sends a **redacted** copy,
without `issue`, `rationale`, `recommended_fix` or `file`, to a private repo
with one file per host, and the stats read the union. That shared copy is a
ledger like any other, so the firewall covers it too. A cloud session's
`~/.claude/` does not outlive its container, so a co-review run there loses its
rows unless it flushes before it ends.

### 3. The write — `scripts/co-review-record.py`

The script exists (#711, PR #907) with tests in `test/co-review-record.bats`;
nothing calls it yet. Wiring it into co-review is #709.

The call is a new step **immediately after the reconciler returns** (before
fixes are applied, so a failure later in the run can't lose the record — the
grades exist the moment reconciliation completes). The main agent writes three
temp files and calls:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-record.py" \
  --run <meta.json> --findings <recon.json> --mapping <map.json>
```

The plugin-root path is what makes the call work in any repo under review, the
way co-review already calls its other helpers; a bare `scripts/…` path resolves
only when the repo being reviewed is this one. The script is stdlib-only, so
plain `python3` runs it.

- `--mapping` maps each neutral label to an identity:
  `{"Reviewer A": {"name": "devin", "kind": "agent"}, "Reviewer D": {"kind": "human"}}`.
  The main agent necessarily holds this — it dispatched the reviewers.
  Identities were never the risk; **aggregate scores** are, and the main agent
  never sees one.
- The script performs the unblinding join, appends both record types, and prints
  exactly one line: `RECORDED: 14 findings, 4 reviewers`. It never echoes ledger
  contents. `--ledger <path>` overrides the default location, for tests.
- **The script owns the ledger's structural fields.** `type`, `run_id`, `host`
  and `schema` are set by the script and win over any same-named key in the
  input, so model-written JSON can't turn a finding into a run or move it to
  another run.
- **Invalid input writes nothing and exits 1:** an unknown verdict, a source
  label that is not a string or not in the mapping, an empty `sources` list, a
  `duration_s` that is negative, non-numeric, `NaN` or infinite, an empty
  `host`, or malformed JSON.
- **Consistency model.** Auto-pilot can run co-reviews concurrently, and
  "never fatal" invites retries — so the append must not be naive. The script:
  takes an exclusive `flock` on the ledger; **dedups on `(host, run_id)`** (if
  the pair already appears, exit `RECORDED: duplicate, skipped` — retries are
  idempotent); writes the run record and all its findings as **one batch under
  the one lock** (no interleaving, no run-without-findings torn state); releases
  the lock. All-or-nothing per run, plain JSONL, no database — proportionate to
  a ledger that will hold hundreds of rows.
- **A failed write leaves no trace.** A partial run on disk would read as
  recorded and block every retry, so the batch goes out as unbuffered bytes and
  a write that fails partway (a full disk) is truncated back before the script
  exits 1. If an earlier crash left the last line unterminated, the script ends
  it before appending, so the new run stays parseable. It does not `fsync`: only
  a whole-machine crash in the moments after a write could tear a run, and for a
  local, non-fatal log that is not worth the cost.
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
| `p50 s`       | Median `duration_s` over runs where the reviewer `ran` or `failed`  |
| `p90 s`       | 90th-percentile `duration_s` over the same runs                     |
| `fail %`      | (`timed-out` + `failed`) ÷ runs where the reviewer was dispatched   |

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

The speed columns are read against the quality ones, not on their own. `p90 s`
is the column that matters for the run as a whole, because the slowest reviewer
sets the wait. A reviewer that is slow, has a high `fail %`, and rarely adds a
`unique-high` is the clearest drop case the table can show; one that is slow but
the sole catcher of real bugs may be worth its wait. Durations depend on diff
size, so `diff_lines` on the run record is there to stratify by when a
reviewer's times look erratic.

Flags: `--since <date>`, `--repo <name>` (did a reviewer regress after a model
bump? is it fine on Python and hopeless on shell?).

## What is deliberately not being built

- **No auto-demotion.** A demoted reviewer stops producing the findings that
  would exonerate it — a trapdoor. And with per-reviewer N in the dozens, any
  threshold fires on noise. Eviction stays a human edit to `.co-review.yml`, made
  against a real sample.
- **No automatic reviewer selection, yet.** Choosing per run which reviewers to
  dispatch, from what the ledger shows, is a follow-up (#1040). It has to keep
  scores out of every agent's context and keep an exploration floor so a
  skipped reviewer can still be re-measured, and it can't be designed honestly
  before there is data to select on.
- **No mid-run nudge.** Considered and rejected: it would require the review path
  to read the ledger, which is the one thing the firewall forbids.
- **No failure-mode taxonomy** (`hallucinated-api`, `misread-diff`, …). This is
  what we'd eventually want for prompt tuning, but any taxonomy invented today is
  guesswork. `rationale` is free text, so the information is _captured_ — grep it
  by hand for the first month, then formalize the tags the data actually shows.
- **No thresholds.** None are needed: nothing fires automatically. A human reads
  a table.

## Success criteria

1. Every reconciled co-review run, in every mode, appends one `run` record and
   one `finding` record per reconciled finding; the write is locked, batched, and
   idempotent on `(host, run_id)`; and a recording failure never breaks a run.
2. The reconciler sees **only neutral labels** — including for GitHub bots and
   human commenters. No identifiable source name reaches the grader.
3. No agent in the review path reads `findings.jsonl`. Enforced by there being
   no step that does, stated as an explicit rule in `co-review`'s Rules section,
   and narrowed by the optional `permissions.deny` read rule.
4. `/co-review-stats` reports every reviewer plus the `claude` baseline, with
   the headline comparison **paired on common runs** and all arithmetic done in
   the script.
5. Every dispatched reviewer's `duration_s` is measured by the dispatch, not
   estimated, and the stats report its `p50 s`, `p90 s` and `fail %` with
   timed-out runs kept out of the percentiles.
6. After ~30 runs, the table can answer: _is devin actually wrong more often
   than Claude on the same diffs, does it catch anything nobody else does, and
   how long does every run wait for it?_

## Open question, resolved by data

Whether `wrong` findings cluster by reviewer (drop it), by verdict type (fix the
rubric or the context we pass), or not at all (the impression was wrong and we
keep everyone). The design deliberately does not presuppose the answer.
