# task-fill — the priority/estimate backfill, defined once

Read by every `/promote-tasks` flow — `commands/promote-tasks.md` (the `repo-pr`
file path), `commands/handlers/linear-promote.md`, `commands/handlers/gh-issue-promote.md`,
and `commands/handlers/jira-promote.md`. It owns the **judgment**: what the two
backfilled values mean, how they are produced, when one is withheld, and which
reason wins when two checks fail at once. That judgment is identical on all four
paths, so it lives here and nowhere else.

What it does **not** own is the units and the mechanism. A tracker's priority
encoding, its estimate ladder, the call that writes a value, and where provenance
lands all genuinely differ — those are the per-handler **adapters** at the bottom
of this file, and a promote flow carries only its own.

**Changing a backfill rule means editing this file.** A promote flow that restates
one has drifted; cite this instead.

## When it runs

Before scoring, on every candidate the promote flow is about to score — HIGH or
LOW. Backfilling a candidate that will fail some other check anyway is deliberate:
the human who picks it up out of `needs_refinement` has less to redo.

The one exception is a **held** card: one waiting on an unresolved blocker is left
entirely untouched — no backfill, no transition, no comment — so it stays in the
scanned pool and is re-scored next run once the blocker clears. `dry-run` reports
the intended backfills and writes nothing.

A card **already past the promoter's lane** is the other way in. Scoring is
one-way and one-time, so a card scored before backfill existed would never be
reached by the rule above — it carries no `priority`/`size` and nothing else
writes one. `backfill-only` is that path: it fills the two fields at any rung,
writes **nothing else**, and is not a re-score. It never moves a card between
rungs in either direction, because demotion stays a human's call. The adapter
says what implements it.

## Priority — a static default, and a symbolic value

Missing or unset → **`medium`**. A flat static default is correct here because
priority only **orders** work; it gates nothing. **Never auto-set `urgent`**:
escalation is a human's call, and the promoter has no signal for it.

The shared vocabulary is symbolic — `urgent` / `high` / `medium` / `low` / `none`
— and every adapter encodes it on the way out. **Never carry a bare integer
between this spec and a handler.** The integers collide in the worst possible way:
Linear's priority `0` is _none_ (unset), while GitHub's `prio:0` is _highest_
(`commands/handlers/gh-issue.md` sorts `prio:0` first through `prio:3`, none last).
A shared integer would silently invert itself between those two trackers, turning
an unprioritised issue into the top of the board. The encoding table below is the
only place that mapping is written down.

## Estimate — a model judgment, on a Fibonacci ladder

Missing, or not a value the tracker's ladder admits → **estimate it** from the
card body (`## Task` steps / issue description) and the breadth of
`related_files`. This is deliberately **not** a static default: the estimate feeds
the one-task-one-PR ceiling and `auto_execute_max_size` / `max_estimate` routing
downstream, so a blind constant misroutes work.

Producing the number costs nothing extra. The scope-fit check every promote flow
already runs — "does this plausibly fit ~300 lines / ~5 files" — **is** the
reasoning that produces it; backfill just records the number instead of judging it
and throwing it away.

`5` is the one-PR ceiling (see **Task size** in `skills/task/SKILL.md`). Ladders
below the ceiling are the same everywhere: `1` / `2` / `3` / `5`.

### The over-ceiling rule

When the honest estimate exceeds `5`, **never write a bogus `5`** — that is the
one value that would make an oversized card look routable.

- **Record it where the tracker's ladder admits it.** `gh-issue` admits `8` and
  `13` (`commands/handlers/assets/labels.yml`), so write the honest number there.
  A recorded `8` is strictly better than a blank: it feeds the deterministic
  `max_estimate` gate and tells the human how far over the card is.
- **Leave it unset where the ladder does not** — both above the ladder's top and
  where there is no rung for it at all. `repo-pr` and `linear` admit
  `1`/`2`/`3`/`5` only. `gh-issue` stops at `13`, so an honest `21` is left unset
  there too: every ladder is finite, and the rule is the ladder's, not one
  tracker's.

A value off the ladder is not a near-miss to round — it is a write that fails.
`gh-issue-state.py` validates every name against `labels.yml` before its network
call and refuses an unknown one outright (`est:7` is refused today, and so would
`est:21` be), so writing an off-ladder number does not degrade to a missing label:
it fails the whole transition, taking the `status:`/`auto:` rungs with it.

Either way the scope-fit check scores **LOW** with the split reason — recording
the number is not a promotion, and neither is declining to record one.

### Reason precedence

When the estimate is over the ceiling, the reason slot goes to
`scope exceeds size 5 — split into sub-tasks` (`sub-issues` on a tracker path),
**not** to `no estimate set` / `size_valid` / `required_fields_present`. Ordering
by first-failure buries the one reason that says what to actually do behind one
that reads as "fill in a number". `skills/break-down-task/SKILL.md` is how that
split gets performed.

## Where the estimate gate lives

**`max_estimate` is applied at CLAIM, on every handler that has it. Never at
promote.** This section is the single home for that rule; `gh-issue-promote.md`,
`linear-promote.md`, `gh-issue-claim.md` and `linear-claim.md` cite it and
restate none of it.

The two lifecycle points answer different questions, and the difference is not
cosmetic:

| Gate         | Question                                           | What a card does when it trips                                                      |
| ------------ | -------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Promote-time | Is this card well enough specified for automation? | Demoted. Only a human can retrieve it — the promoter never re-scores a scored card. |
| Claim-time   | May the loop take this card _right now_?           | Stays `ready` and visible; passed over until the bound or the card changes.         |

Size is the second question. A card sized `5` is not badly written, and nothing
about it improves by a human looking at it — it is simply larger than one
unattended session should start. Gating it at promote answers a routing question
with a quality verdict, and the card then needs human action to undo a hold that
was never about human judgment.

**This was measured, not reasoned.** On `bestdan/dotfiles`, 2026-09-20, five
cards with complete acceptance criteria and no open questions were demoted to
`needs_refinement` by nothing but the default `max_estimate: 3` against an
exclusive bound — three of them sized exactly `3`. Recovering them required
editing committed config and re-running, because a demoted card is out of the
promoter's lane. See bestdan/workflow-skills#746.

**The bound is EXCLUSIVE on every handler**: `est:3` / `estimate 3` fails
against `max_estimate: 3`. The reason string is `estimate <n> >= <max>`,
byte-identical across handlers so one board reads the same on all of them.

**A missing estimate is handled per handler, and the two differ on purpose.**
`linear` drops it (`no estimate set` — Linear has no backfill at promote, so an
unestimated issue is genuinely unsized). `gh-issue` does not, because its
promoter backfills `est:` on every card it scores, so a card with no `est:` has
never been scored and its `status:` rung already says so far more precisely.

**Anyone may override the bound for one run** — see
`commands/task-config.md` → "Run-scoped overrides". A human present outranks a
default.

## Provenance

Every backfilled value is recorded where a human can cheaply spot and correct it,
naming **which** fields were auto-set. The adapter says where that lands. A
candidate that scores HIGH still gets the note — there is no failed check to
report in that case, just the backfill.

**A batch leaves one record, not one per card.** The per-card note is right for a
promote run touching a handful of cards; at thirty it is notification noise, and
the noise is what stops the next batch from being run at all. So a `backfill-only`
run emits a single record naming every card it touched. Per-card provenance still
has to exist — the adapter says what carries it there.

## Backfilled values are trusted downstream

Exactly like a human-set one, and with no carve-out. An auto-estimated size is
eligible for headless `auto_execute_max_size` batch auto-execution, and it is read
by the deterministic `max_estimate` gate on the paths that have one — a backfilled
`est:5` fails `max_estimate: 3` just as a human's would. That is the point: before
backfill those gates read an absent value and passed unconditionally, degrading the
deterministic check into the judgment check it exists to backstop. The auto-execute
path still ends in a PR a human reviews, and a mis-estimated task beats a
permanently blocked one.

## Adapters

Four questions, and nothing else: which of the two fields this tracker can hold,
how a symbolic priority encodes, how a value is written, where provenance lands.

| handler    | priority encoding                                                                                            | estimate ladder     | written how                                                                               | provenance                                                                                        |
| ---------- | ------------------------------------------------------------------------------------------------------------ | ------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `repo-pr`  | `priority:` — the symbolic word verbatim                                                                     | `1/2/3/5` → `size:` | `Edit` on the YAML frontmatter                                                            | `# promoter:` frontmatter comment                                                                 |
| `linear`   | `none 0 · urgent 1 · high 2 · medium 3 · low 4`                                                              | `1/2/3/5`           | fields on the step-8 `save_issue` (not a separate write)                                  | one-line issue comment                                                                            |
| `gh-issue` | `gh-issue-backfill.py encode` — `urgent prio:0 · high prio:1 · medium prio:2 · low prio:3 · none = no label` | `1/2/3/5/8/13`      | `gh-issue-backfill.py` — `encode` into the transition's `--labels`, or `apply` standalone | on a transition, the issue comment; on a standalone batch, the timeline event plus one run record |
| `jira`     | **not backfilled** — read only                                                                               | **no field at all** | —                                                                                         | —                                                                                                 |

### repo-pr

Both fields are plain frontmatter, so the encoding is the vocabulary itself.
Backfill is written by the same `Edit` that flips `status:`, with
`# promoter: priority defaulted to medium` / `# promoter: size auto-estimated`
(both lines when both were backfilled) appended to the frontmatter.

### linear

Priority is a native integer field on the 0–4 scale above; `0` means _none_, which
is what the missing-priority default replaces. Estimate is the native `estimate`
field on the same Fibonacci scale as the file path's `size`
(`commands/handlers/linear-common.md`). Both ride out on the step-8 `save_issue`
call that carries the state/label transition — never a second write — with a
one-line `save_comment` naming what was auto-set.

### gh-issue

GitHub has no native priority or estimate field, so both live as the optional
`prio:`/`est:` labels in `commands/handlers/assets/labels.yml`. **`prio:0` is
highest**, the inverse of Linear's `0`; encode from the symbolic value and never
from Linear's integer.

**The encoding above has one implementation: `commands/handlers/assets/gh-issue-backfill.py`.**
Never compose a `prio:`/`est:` label from this table by hand — `encode` is what
reads it, and `scripts/test_gh_issue_backfill.py` pins the mapping in both
directions so a Linear integer leaking in fails the gate instead of inverting the
board.

Two call shapes, because the write differs and the judgment does not:

- **On a transition** (the promote flow), the write is free. `gh-issue-state.py`
  already takes the **complete** managed label set in one full-set PATCH, so a
  backfilled `prio:`/`est:` is an argument change to the call the transition
  already makes — no extra write, no extra round trip. The flow asks
  `gh-issue-backfill.py encode --priority <word> [--estimate <n>]` for the label
  names and splices them into that `--labels` value. Provenance is an issue
  comment either way, the way the Linear path's is: on a LOW issue it is folded
  into the comment naming the failed check, and a HIGH issue that was backfilled
  gets one of its own.
- **Standalone** (`backfill-only`), there is no transition to ride on, so
  `gh-issue-backfill.py apply` owns the whole write: it re-reads each issue, holds
  the blocked ones, fills only the **missing** field, and composes the full label
  set with the `status:`/`auto:` rungs byte-identical to the ones it read. Per-issue
  provenance is the issue's own `labeled` timeline event — actor, timestamp,
  correctable in place, and no notification — and the batch's one record is the run
  report, posted as a single comment with `--provenance-issue <n>`.

This is the one handler whose ladder admits `8`/`13`, so it is the one that records
an over-ceiling estimate rather than discarding it.

### jira

**Declared gaps, not oversights.** Jira has no estimate field — story points are a
per-project custom field this handler does not resolve — so size folds into the
scope judgment and nothing is written. Priority is native and is **read**, but the
site's priority scheme names are configured per Jira site, so the symbolic
vocabulary has no fixed encoding without a config key the handler does not yet
have; backfilling it would mean guessing a scheme. Until that key exists, jira
backfills neither field.
