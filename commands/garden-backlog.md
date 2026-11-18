---
description: Garden an existing tracker backlog ticket by ticket — judge each ticket's validity, kind, quality, size, importance and status, write a dry-run report, then apply only the owner-approved writes. Low-confidence verdicts go to a human queue instead of being written. The core is handler-agnostic; jira is the first handler, every other handler stops as not yet implemented.
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(python3 *), Bash(twg *), Bash("${CLAUDE_PLUGIN_ROOT}/scripts/work-tmp.sh":*), Glob, Grep, Read, Write, AskUserQuestion, Skill, Agent, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__getJiraIssue, mcp__claude_ai_Atlassian__getTransitionsForJiraIssue, mcp__claude_ai_Atlassian__transitionJiraIssue, mcp__claude_ai_Atlassian__editJiraIssue, mcp__claude_ai_Atlassian__addCommentToJiraIssue, mcp__claude_ai_Atlassian__getJiraIssueTypeMetaWithFields, mcp__claude_ai_Atlassian__getJiraProjectIssueTypesMetadata, mcp__atlassian__getAccessibleAtlassianResources, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getJiraIssue, mcp__atlassian__getTransitionsForJiraIssue, mcp__atlassian__transitionJiraIssue, mcp__atlassian__editJiraIssue, mcp__atlassian__addCommentToJiraIssue, mcp__atlassian__getJiraIssueTypeMetaWithFields, mcp__atlassian__getJiraProjectIssueTypesMetadata
argument-hint: "<epic> [--wide] [dry-run] [--limit N] | apply <report-file>"
---

# Garden Backlog

Tend a backlog that was filled haphazardly: for each open ticket, decide whether
it is still valid, whether it is a known fix or a spike, whether it is described
well enough to pick up, how big it is, how important it is, and which status it
belongs in. **Dry run is the default: nothing is written to the tracker without
`apply`**, and `apply` writes only what the owner approves from a dry-run
report. Any verdict the judge is unsure of goes to a human instead of the
tracker.

**The split.** This file owns the judgment and the per-ticket record. The handler
file owns the tracker I/O — the backlog query, the field reads, the writes — and
every field encoding (Jira's `estimate_field` and `priority_map` are in
`${CLAUDE_PLUGIN_ROOT}/commands/handlers/jira-config.md` → **Estimate and
priority encoding**). This file speaks only symbolic values. Its shape follows
`/reoptimize-tasks`: dispatch, read-only analysis, report, approve by group,
apply.

**Composed, not restated.** Each rung cites the file that defines it:

| Rung       | Defined in                                                                                                                          |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| validity   | `${CLAUDE_PLUGIN_ROOT}/skills/task/references/validity.md`                                                                          |
| kind       | `${CLAUDE_PLUGIN_ROOT}/skills/assess-task/SKILL.md` (`task_profile`; its `scope` read also feeds size)                              |
| quality    | `${CLAUDE_PLUGIN_ROOT}/skills/task/SKILL.md` → **Confidence check**, by condition name                                              |
| size       | `${CLAUDE_PLUGIN_ROOT}/skills/task/SKILL.md` → **Task size**; `${CLAUDE_PLUGIN_ROOT}/commands/handlers/task-fill.md` → **Estimate** |
| split      | `${CLAUDE_PLUGIN_ROOT}/skills/break-down-task/SKILL.md`                                                                             |
| importance | `${CLAUDE_PLUGIN_ROOT}/skills/task/references/importance.md`, then the repo overlay when one is set (§1)                            |

If a path does not resolve (the plugin root was not substituted), find it with
**Glob** (`**/<path>`).

## Arguments

- `<scope>` — an epic key by default. Missing → ask via `AskUserQuestion`
  (header: "Scope").
- `--wide` — dry run only; `apply` rejects it. Lets `<scope>` be wider than one epic: a project, a
  label, or a query the handler understands. Without it the handler refuses a
  wider scope and the run **stops** with its message, which names `--wide`.
  The reason is cost: one epic of 30 tickets took about 440k judge tokens
  (#1561), so a project of about 400 tickets would take about 6M.
- `dry-run` — the default. Judge every ticket in scope, write the report, write
  nothing to the tracker.
- `apply <report-file>` — apply a report a previous dry run wrote. The scope comes
  from the report; no re-judging.
- `--limit N` — dry run only; `apply` rejects it. List at most `N` tickets,
  early exits included. The report says
  when the limit cut the scope.

## 1. Resolve the handler

Resolve `handler:` from the **merged view**, exactly as `/reoptimize-tasks` §1
does (`commands/task-config.md` → "Resolving the handler").

- `handler: jira` → follow `${CLAUDE_PLUGIN_ROOT}/commands/handlers/<handler>-garden.md`
  with `<handler>` = `jira`. If that file does not exist in the installed
  plugin, **stop** with: "`/garden-backlog`: the installed workflow-skills
  plugin has no jira garden handler. Update the plugin."
- Any other handler (`gh-issue`, `linear`, `repo-pr`, absent) → **stop** with:
  "`/garden-backlog` is not yet implemented for the `<handler>` handler."
- Unknown value → **stop** with: "Unknown task handler `<value>` in
  dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

Then read `garden.importance_overlay` from the same merged view. Unset → the
generic rubric alone. Set → the overlay `importance.md` → **Repo overlay**
describes: an absolute path, a `$HOME` or `~` path, or a path relative to the
repo root (`git rev-parse --show-toplevel`). Resolve it with the helper rather
than by hand, so this read and `/doctor` agree:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/garden-overlay.py" --value '<value>' --root "<root>"
```

It prints the absolute path; that path is what §4's fan-out and §5's report
carry. Otherwise **stop**, with the message that fits:

- the value is not a string → "`garden.importance_overlay` must be a path
  string; `<value>` is not. Fix it or unset the key."
- the helper exits `2` (it prints no path) → "`garden.importance_overlay`:
  `<the helper's stderr line>`. Fix it or unset the key."
- the helper exits `3` → "`garden.importance_overlay` must name a file;
  `<value>` resolves to `<printed path>`, which is not one. Fix the path or
  unset the key."

Without the
overlay, importance would be judged on the generic harms the repo has said are
incomplete.

## 2. The handler contract

The handler implements four operations. Everything it returns is symbolic.

- **List** `(scope, wide, limit)` → one row per open ticket in scope, paginated to the
  end or to `limit`: `key`, `url`, `title`, `description`, `parent` (epic key or
  null), `status` (`initial` | `ready` | `refinement` | `started` | `other`),
  `updated_at`, `gardened_at` (the time of this command's newest provenance
  comment, or null), `size` and `size_human_set`, `importance` and
  `importance_human_set`, `kind` (`fix` | `spike`) and `kind_human_set`,
  `linked_prs`, `own_pr` (validity rung 1's verdict, which the handler
  resolves before the judges run), and `blocked_by` (the keys of unresolved
  blockers, `[]` when there are none, or null when the handler did not read
  them). Without `wide`, List refuses any scope wider than one epic
  before its first tracker read, with a message that names `--wide`; the
  handler decides what counts as an epic on its tracker. The handler defines
  **human-set** for its tracker — on Jira,
  priority is never empty, so it means a non-default value or a recorded change —
  and owns any adjustment needed so its own writes do not count as an update
  after `gardened_at`. A handler that cannot write kind returns `kind: null`.
- **Read** `(key)` → the same row for one ticket, fresh. Apply uses it.
- **Write** `(key, writes)` → apply one ticket's approved writes and return what
  landed. Status targets are `ready` / `refinement`; the handler maps them to
  its statuses (`ready_status` / `refinement_status` on Jira).
- **Comment** `(key, body)` → post the provenance comment.

## 3. The per-ticket record

Defined once, here. Every candidate in scope ends with exactly one record.

```yaml
key: PROJ-123
early_exit: null # or the reason it exited, e.g. "gardened, unchanged since"
validity: {
  verdict: valid,
  confidence: high,
  evidence: ["app/x.rb:40 @ 9f3c2e1"],
}
kind: { value: fix, confidence: high } # fix | spike | unclear
quality: pass # or the failed condition's name, e.g. has_acceptance_criteria
size: {
  fibonacci: 3,
  confidence: high,
} # or { split: true, pointer: break-down-task, reason: "two flows" }
importance: {
  level: major,
  confidence: high,
  reason: "Goal chart fails until refresh.",
}
status: {
  target: ready,
  reason: "quality pass",
} # ready | refinement | unchanged
proposed_writes: [{ group: size, field: size, value: 3 }]
human_queue: [] # reasons, one string each
outcomes: [] # e.g. [{ tag: partly_done, reason: "...", evidence: "..." }]
```

- `validity` is `validity.md`'s verdict and confidence; `evidence` is its list.
- `quality` names the **first** failed row of the Confidence check table, by its
  stable name, applying the rows a tracker handler cites (that section says
  which). Over-ceiling size takes the reason slot per `task-fill.md` → **Reason
  precedence**.
- `proposed_writes` groups are `closure`, `human-queue`, `kind`, `size`,
  `importance`, `status`, `gardened`. A `kind` write's value is `fix` or
  `spike`. A `closure` write is the `garden:close-candidate` label;
  `human-queue` is the `garden:needs-human` label; `gardened` is the provenance
  comment alone.
- `outcomes` carries the judge's three outcome tags that no other field
  holds, each with a one-line `reason` and an `evidence` pointer:
  `stale_description` (the code it names moved, or part of it shipped),
  `partly_done` (some of the asked-for change is on the default branch), and
  `product_decision` (it works as designed, so the open question is a product
  call, not a bug; kind is then `unclear`). Empty when none applies.
- An early-exit record carries `early_exit` and nothing else but `key`.

## 4. Judge each ticket (read-only)

### Early exit — checked first, from the listing row alone

1. `gardened_at` is set and `updated_at` is not later → exit, "gardened,
   unchanged since".
2. `size_human_set` and `importance_human_set` are both true, the quality check
   passes on the row's title and description, **and** `status` is `ready` or
   `started` → exit, "human-set and passes quality".

### The ladder

Run in order: **validity → kind → quality → size → importance → status →
outcomes.**
Validity comes before quality because a closed ticket needs no better
description.

1. **Validity** — run `validity.md`'s ladder.
   - `fixed`, `duplicate` or `obsolete` at `high` confidence → propose the
     `closure` write only, with the evidence, and **stop** this ticket's ladder.
     This command never closes a ticket; a human does.
   - The same verdicts below `high` → human queue (`validity <verdict> at
     <confidence>`), and carry on as for `unsure`.
   - `unsure` → human queue with the reason, and **carry on**: the report shows
     what gardening the ticket would get if it is still valid. A ticket too vague
     to know what code to look for is `unsure` with reason `too vague to check`;
     it normally fails quality too, and that pairing is the description finding.
2. **Kind** — `fix` when the ticket names a change to make; `spike` when its
   deliverable is an answer (an unknown approach, a question to settle). The
   `assess-task` profile is the input — `complexity: hard` with an unclear
   approach leans `spike`. Torn → `unclear`, `confidence: low`, human queue.
   Kind informs the reader and the quality read, and is written only as
   **What may be written** says.
3. **Quality** — the Confidence check, as `quality` above says. A spike passes
   like a fix: a clear question with a checkable definition of done meets
   `has_acceptance_criteria`. **That condition is not the judge's:** the row's
   `checks.has_acceptance_criteria` (from **Fan-out**) is final either way.
   Judge the other conditions. A criterion that waits on a product or design
   decision still owed is an open question: it fails
   `no_open_questions_or_tbd`.
4. **Size** — a Fibonacci estimate on the `1`/`2`/`3`/`5` scale, judged as
   `task-fill.md` → **Estimate** says, using the `assess-task` `scope` read. Over
   `5` → `split: true` with a pointer to `break-down-task`; no size write.
5. **Importance** — `importance.md`'s level, confidence and one-line reason,
   with the overlay's harms and examples read in when one is set.
6. **Status** — from the verdicts, never from the default. `garden-checks.py
   finalize` derives it (**Fan-out**); the rules it applies:
   - `started` (in progress, in review, blocked) or `other` → `unchanged`,
     always.
   - quality pass and an unresolved blocker → `unchanged`, reason `blocked by
     <keys>`; quality pass and `blocked_by` null → `unchanged`, reason
     `blocker links were not read`. Neither gets a `gardened` write, so the
     next run judges it again.
   - quality pass with no blocker → `ready`. A quality-passing ticket is never
     moved to the refinement status, but one held by a blocker stays where it
     is, which may be the refinement status.
   - quality fail → `refinement`, reason = the failed condition's name.
   - the check passed but the judge failed `has_acceptance_criteria` →
     `unchanged`, reason `quality unknown`, no status write, and a human-queue
     reason: the judge never ruled on the later conditions.
   - Target equal to the current status → no write.
7. **Outcomes** — tag each of §3's `outcomes` that the evidence above
   supports. A ticket that works as designed is `kind: unclear` plus
   `product_decision`, not a bug.

### What may be written

- **Low confidence proposes no write for that field** and adds a `human_queue`
  reason instead (`<field> low confidence: <why>`).
- **Human-set values are never overwritten.** Where the verdict disagrees with a
  human-set size, importance or kind, record the disagreement in `human_queue` and
  leave the field, worded `<field> <verdict> disagrees with human-set
  <value>`; §5 groups size disagreements by that wording.
- **Kind is written only at `high` confidence, and only where the handler can
  write it.** Propose a `kind` write when the verdict is `fix` or `spike` at
  `high`, the row's `kind` is not null, and the verdict differs from it. Where
  the row's `kind` is human-set, the disagreement goes to `human_queue`
  (`kind <verdict> disagrees with human-set <kind>`) instead. A `kind: null`
  row gets no kind write: the report carries the verdict alone.
- **Importance is written only once `importance.md` is signed off.** While its
  PM / EM sign-off table is empty, record the level and propose no importance
  write; the report says why once, not per ticket.
- **Validity not settled holds the status and kind writes.** While validity
  is `unsure` (or a sub-`high` closure verdict), show the status target and the
  kind verdict in the record but propose neither write: a ticket that may
  already be fixed is not sent to `ready`, nor retyped.
- A ticket with a `human_queue` reason gets the `human-queue` write. A ticket
  with no writes and no queue reason gets the `gardened` write, so the next run
  exits it early.

### Fan-out

Two deterministic passes bracket the judges, both in
`${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/garden-checks.py`, whose
docstring says why each rule lives there. Every input file is written with
the **Write** tool.

1. **Before.** Once the handler has resolved `own_pr` on the rows, run
   `python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/garden-checks.py" prejudge --rows <rows file>`.
   The rows file is a top-level JSON array with one object per ticket, each
   carrying §2's List fields, `own_pr` included; any extra fields the handler
   adds pass through. `prejudge`
   prints the same array, adding to each row a `checks` object:
   `has_acceptance_criteria` (`pass` | `fail`) and `ac_forms`, the forms it
   found. Write its stdout to a file in the directory
   `"${CLAUDE_PLUGIN_ROOT}/scripts/work-tmp.sh" --sub garden-backlog` prints:
   that file is the rows the judges get, never the ones before it ran, and the
   `--rows` step 3 passes to `finalize`.
2. **Judge.** Group the rows by `parent` (null is its own group); split a group
   above 25 tickets into chunks. Hand each group to one `Agent` subagent whose
   brief is §3 and §4 of this file, the rubric paths in the table above, the
   overlay's absolute path (resolved in §1) when one is set, and the group's
   rows. Subagents read; they never write. Run no more at once than the
   session's agent budget allows. Merge their records; a candidate with no
   record, or a record that fails §3's shape, gets one whose `human_queue`
   says so.
3. **After.** Write the merged records to a JSON file and run
   `python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/garden-checks.py" finalize --rows <prejudged rows file> --records <records file>`.
   Its output is the record set the report carries: it reconciles quality with
   the check, sets each status target, applies the holds above, and rebuilds
   the `status`, `human-queue` and `gardened` writes. It exits `2` on bad
   input.

## 5. Dry-run report

Write the report to `<dir>/<scope-slug>-<UTC yyyymmddTHHMMSSZ>.md`, where `<dir>`
is what `"${CLAUDE_PLUGIN_ROOT}/scripts/work-tmp.sh" --sub garden-backlog`
prints, and print the path. It holds, in order:

1. Scope, handler, run time, the importance overlay path (or none), and
   whether `--limit` cut the scope.
2. Counts: early exits, validity verdicts, kinds (and how many differ from
   the ticket's current kind), quality pass / each failed
   condition, status targets.
3. **Overview by outcome**, one heading per group below, in this order, each
   line `<key> — <one-line reason> (<evidence pointer>)`. A ticket may sit in
   several groups; an empty group is left out.
   - **Close candidates** — validity `fixed`, `duplicate` or `obsolete` at any
     confidence; the verdict, its confidence and the first `evidence` entry.
   - **Stale descriptions** — an `outcomes` tag `stale_description`.
   - **Partly done** — an `outcomes` tag `partly_done`.
   - **Product decisions rather than bugs** — an `outcomes` tag
     `product_decision`.
   - **Code located** — validity `valid` with a `file:line` in `evidence`;
     that pointer is the evidence.
   - **Size disagreements with a human-set size** — a `size … disagrees with
     human-set …` reason in `human_queue`; that reason is the line.
   - **Splits** — `size.split`; its `reason`, pointing at `break-down-task`.
4. The human queue, each ticket with its reasons.
5. Proposed writes grouped by `group`, one line per ticket.
6. **Per-ticket table**: one row per record, early exits included, so every
   ticket in the run appears here whether or not a group above lists it.
   Columns: key, early exit, validity, kind, quality, size, importance,
   status target, outcome groups.
7. All records as one fenced `yaml` list — what `apply` reads.

Then print a chat summary of sections 2–5 and the path.

## 6. Apply (gated)

`apply` given `--wide` or `--limit` → **stop** with: "`--wide` and `--limit`
shape a dry run's scope; `apply` takes its tickets from the report. Drop the
flag." Apply runs no scope gate: it reads each ticket by key, so a report from
a `--wide` run applies like any other.

1. Read the report's records. For each ticket with proposed writes, **Read** it
   fresh; if `updated_at` is later than the report's run time, skip it and list it
   as changed since the dry run.
2. Confirm each `group` with the owner via `AskUserQuestion`, showing its
   tickets. Only approved groups go on.
3. Per ticket, hand the approved writes to the handler's **Write**, serially or
   in concurrent groups of at most 5 tickets, waiting for each group to finish
   (the bound is the batch-writes incident in
   `${CLAUDE_PLUGIN_ROOT}/commands/handlers/jira-promote.md` → **5. Apply**).
4. Each written ticket gets **one** provenance comment through **Comment**: the
   marker `garden-backlog`, the date, each field changed (kind included) with
   its old and new value and the reason, and the human-queue reasons if any.
5. Print what was applied, skipped and failed.

**Hard rules:** never close a ticket or move it to a done status; never
overwrite a human-set value; never move a started ticket; write nothing in
`dry-run`.
