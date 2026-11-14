# research-spike — the obligation ledger, engineering record

This is the durable maintainer's doc for `scripts/research-spike.py` and
`skills/research-spike/`. If you are about to touch the script or the skill,
start here.

The choices behind the instrument, each with what would reopen it, are
decision records under [`decisions/`](decisions/INDEX.md), listed under
"Decisions" below. The "why does this skill exist at all" story (the four-day
`aiutopilot` incident, roughly 6 obligations discharged against 23 open, the
rule that a deferral stayed visible only when its destination was a file) is
the context of the
[destination record](decisions/2026-08-02-research-spike-destinations-must-exist.md).
This doc covers how the pieces fit and the operational traps a maintainer will
hit.

## Architecture

### The script/LLM boundary

One rule decides which side of the tool any given behavior belongs on:

> If two runs over the same tree could disagree, it belongs to the script.

`scripts/research-spike.py` is stdlib-only, deterministic, and owns: parsing,
every validation rule, decision-status computation (readiness), the `status`
report, ledger derivation and freshness, and the advisory lexical scan. It
never edits prose and never decides whether something _is_ a deferral.

`skills/research-spike/SKILL.md` is the judgment half: prose, proposed ids,
`none:` reasons, the interactive `file`/`answer`/`defer`/`backfill` walks, and
the call on whether a sentence is a deferral at all. It never computes a
status or a count — every procedure ends by running `validate`, and the
validator, not the procedure, is what guarantees the result held.

Why the line falls there:
[script/agent record](decisions/2026-08-02-research-spike-script-computes-agent-judges.md).

### The on-disk convention

Fixed by the skill, not configurable per repo
([layout record](decisions/2026-08-01-research-spike-layout-is-skill-owned.md)):

```
dev_docs/research/<project>/
  PROJECT.md            # charter
  decisions.md          # organizer-owned
  LEDGER.md             # generated roll-up, organizer-owned, never hand-edited
  tracks/<track>/
    questions.md        # questions + answers + the track's own stored ledger
    contracts/          # optional; preconditions register here, same coverage rule
    obligations/        # stub and receipt cards
```

This is the convention `init` scaffolds and the skill relies on — it is not
what discovery _requires_. `discover()` walks every `*.md` file under each
project directory recursively (`pdir.rglob("*.md")`); it does not check that
`PROJECT.md`, `decisions.md`, or any of the `tracks/` subdirectories shown
above exist, and a record kind is decided per-block by the fence's own info
string (`` ```question ``, `` ```obligation ``, …), not by which file it sits in.
A record in an unexpected file — a stray `notes.md` at a project's root, say
— is still discovered and parsed like any other; only a file under some
track's `obligations/` directory is required to hold a `card` block (checked
by `validate_cards`, not by discovery itself).

Multiple research projects coexist under one root; each is self-contained.
Ids are declared **bare** in a record and qualified by the script:
`project/track/id` for questions and obligations, `project/id` for decisions
(a decision qualifies project-wide even while `proposed` inside a track, so
promoting it into `decisions.md` never changes its id). Cards carry no `id:`
at all — a card is addressed by its file path under
`tracks/<track>/obligations/`. In-record references (`blocks:`, `blocking:`)
name decisions by bare id, resolved within the enclosing project only —
cross-project destinations are forbidden by construction, so no reference
ever needs qualifying.

`init <project> [--track <name>]` is the one verb that legitimately creates
files (SKILL.md's "What the skill must not do" list otherwise forbids
creating a destination to make a record resolve). It scaffolds a tree that
passes its own `validate` immediately — ledger markers already fresh, an
inert worked example of the `### Q<n>.` convention inside an HTML comment.

### Where the record grammar lives

Two places, deliberately kept in sync by pointing at the same source:

- **The enforced grammar** is `scripts/research-spike.py` itself — the
  `KNOWN_FIELDS` table, the `*_STATUSES`/`*_STATES` enum constants near the
  top of the file, and the `validate_*` functions. This is what actually runs.
- **The human/agent reference** is
  [`skills/research-spike/references/record-grammar.md`](../skills/research-spike/references/record-grammar.md) —
  the full field table for all four record kinds plus the `none:` sentinel,
  written explicitly against the script as source of truth (its own header
  says so) rather than against this doc's memory of it.

If the two ever disagree, the script is right and the reference file has
drifted — fix the doc, not the code, unless the code itself needs to change.
That is aspirational, not enforced: nothing compares `record-grammar.md`
against `KNOWN_FIELDS` or the other validator constants, so there is no
fixture that fails if the two drift apart. The reference is currently kept in
sync by hand, verified against the code at review time, not by a test pinning
it — worth knowing precisely because it is the file a maintainer trusts by
default. Adding that fixture is a known gap, out of scope for this PR.

## The script surface

Six subcommands, plus two global options registered on the top-level parser
(both must precede the subcommand — see the gotcha below): `--root <dir>`,
defaulting to the current working directory, and `-v`/`--verbose`, which dumps
every discovered record and its parsed fields. `-v` is accepted before any
subcommand, but only `validate` reads it (`args.verbose`) — passing it before
`ledger`, `status`, `write-ledger`, `init`, or `suggest` parses cleanly and
changes nothing.

| Subcommand                                      | Behavior                                                                                                  |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `init <project> [--track <name>]`               | Scaffold a project, or add a track to an existing one. The only verb that creates files.                  |
| `validate [<project>] [--track <t>] [--strict]` | The gate. Whole tree, one project, or one track. `--strict` fails a stale `LEDGER.md` instead of warning. |
| `ledger [<project>] [--track <t>]`              | Print the derived ledgers. Writes nothing, ever.                                                          |
| `write-ledger [<project>] [--track <t>]`        | Rewrite the stored ledger blocks in place. Explicit act, never auto-repair.                               |
| `status [<project>]`                            | The convergence report — what still blocks each decision, per track and total.                            |
| `suggest`                                       | Advisory lexical scan for unregistered deferral prose. Always exits `0`.                                  |

`--track <name>`, on `validate`/`ledger`/`write-ledger`, never guesses. With a
`<project>` positional given, the track must exist in that project. Without
one, the bare track name is looked up across every project's every track: it
resolves only when exactly one match exists tree-wide. Zero matches or more
than one are both caller errors (exit `2`) — the ambiguous case lists every
candidate as `project/track` so the caller can copy the disambiguating form
straight from the message, rather than the script picking one silently. One
implementation (`resolve_scope`) backs all three verbs, so this is not
something that can drift between them.

Exit codes are not a flat contract shared by every subcommand — each verb's
own docstring in `scripts/research-spike.py` is the source of truth, and they
deliberately diverge:

- `main()`'s dispatcher standardizes only **argument and `--root` errors** as
  `2` — an unparseable invocation, an unknown subcommand, or a malformed or
  already-existing project name passed to `init`. Argparse's own usage exit is
  `2` and always wins; this matters for `suggest`, whose _scan_ is exit-0 by
  design but which still exits `2` on `suggest --bogus-flag`, because that is
  the dispatcher rejecting the invocation before the scan ever runs.
- `validate` is the only real gate: it exits `1` on a tree-content violation
  (a rule broken by what is actually in the tree) and `0` when clean.
- `status`, `ledger`, and `suggest` exit `0` even when discovery or the card
  checks found problems, on purpose — a report that could fail is a report
  people stop running. What happens to those findings differs by verb:
  `status` counts them into a completeness footer ("this report may be
  incomplete") without reprinting them, so a broken tree cannot read as
  healthier than a correct one, but nothing in the footer can fail the run.
  `ledger` builds the same discovery/card report and then discards it
  entirely — it exists only for its side effect of populating stub/receipt
  tallies — so `ledger` surfaces nothing about tree problems at all. `suggest`
  never collects discovery's findings in the first place (its own docstring:
  "discover's findings are validate's, not ours") and prints only its own
  lexical-scan hits, unconditionally exit `0`.
- `write-ledger` exits `1` only for a failure of the **requested write itself**
  (a missing file, missing ledger markers) — never for unrelated tree content.
  It deliberately discards `discover()`'s and `validate_cards()`'s findings
  (see gotcha 7): a card violation in a sibling track must not fail a scoped
  write that did exactly what it was asked. Its exit code answers "did the
  write happen," not "is the tree otherwise clean" — always follow it with a
  separate `validate` if you need the latter.

The reason for the divergence, not just its shape: a check whose failure
stops people from running it is worse than no check at all (the same logic
gotcha 2 states for `suggest`), so only `validate` — the one verb explicitly
invoked as the gate — is allowed to fail a run over ordinary tree content.

## Reading the `status` report

Each decision prints one line: `DECIDED`, `READY` (nothing outstanding,
waiting on a human), `BLOCKED` (with its live blockers and their paths
beneath), or `PROPOSED` (filed in a track, not yet promoted). Two suffixes can
follow:

- `(unblocked by retirement: <ids>)` on a `READY` line whose only remaining
  blockers were retired rather than answered.
- `— rests on N open obligation(s) (N stub(s), N receipt(s))` under any
  label: open obligations in a section whose question `blocks:` this
  decision, other than ones already shown as `blocking:` blockers. The label
  does not change.

Then one line per track and a total, for example
`account:  Q 4 answered / 3 open / 1 retired    O 2 discharged / 8 open (2 blocking, 2 stubs) / 3 declared none`.
Zero subtotals inside the parentheses are omitted. The last figure counts bare
`none:` obligation blocks. A `⚠` line follows when
more than a third of open obligations carry `blocking:`, and a footer when the
tree has validation errors or unresolved references, in which case run
`validate`. Neither the taint suffix nor the `declared none` figure is in the
stored ledgers.

## Decisions

Each is a record with its context, consequences and reopen conditions:

- [The layout is skill-owned](decisions/2026-08-01-research-spike-layout-is-skill-owned.md):
  multiple projects, scoped ids, no cross-project destinations.
- [The script computes, the agent judges](decisions/2026-08-02-research-spike-script-computes-agent-judges.md):
  the six subcommands and the five procedures.
- [Destinations must be existing files](decisions/2026-08-02-research-spike-destinations-must-exist.md),
  including the plan-directory warning and the `done`/`abandoned` error.
- [Every question section declares coverage](decisions/2026-08-02-research-spike-coverage-is-declared-per-section.md),
  as do `contracts/` files.
- [`suggest` can never fail a run](decisions/2026-08-02-research-spike-suggest-cannot-fail.md).
- [The ledger is stored and checked](decisions/2026-08-02-research-spike-ledger-is-stored.md):
  scoped freshness, the `--strict` tier, no auto-repair, no trend.
- [Readiness is derived](decisions/2026-08-02-research-spike-readiness-is-derived.md):
  decisions store only `state`, and the reopen shape.
- [The `status` report's rules](decisions/2026-08-02-research-spike-status-report-rules.md):
  separate counts, per-track lines, the fixed scarcity warning, retirement.
- [Bridges use receipt cards](decisions/2026-08-02-research-spike-bridges-use-receipt-cards.md):
  no reconciliation and no `sweep` in v1.
- [Taint and the `none:` count live in `status` only](decisions/2026-10-18-research-spike-taint-and-none-in-status-only.md).
- [Assumption cards, `trend` and `status --json` are deferred](decisions/2026-08-14-research-spike-lean-b-d-e-deferred.md).

## Gotchas

Seven traps a maintainer will hit. The reasons behind the first four are in
the decision records linked from each.

### 1. A new record makes the stored ledger stale

The ledger is stored at the top of each track's `questions.md` and rolled up
into `LEDGER.md`, and `validate` fails when a stored block disagrees with a
fresh derivation. Any record you add or change therefore fails the gate until
you run `write-ledger`, scoped to the track you touched (see gotcha 7). That
is the check working. Why it is stored:
[ledger record](decisions/2026-08-02-research-spike-ledger-is-stored.md).

### 2. Do not put `suggest` in the gate

`suggest` always exits `0`, and wiring it into a blocking check would gain
nothing. In a repo with an advisory tier it may run there as a non-failing
report (`skills/research-spike/references/adoption.md` step 5). That
licenses nothing about `validate`, which belongs in the blocking gate. Why:
[`suggest` record](decisions/2026-08-02-research-spike-suggest-cannot-fail.md).

### 3. There is no field for readiness

A decision stores only `state: pending | decided` (or `proposed`, inside a
track's `questions.md` only), plus `decided_in:` and `reopened_because:` where
required. A hand-typed `ready:` or `blocked:` is an
unknown-key error, because `KNOWN_FIELDS` for `decision` omits them.
Readiness is computed on every `status` and `validate` run. Why:
[readiness record](decisions/2026-08-02-research-spike-readiness-is-derived.md).

### 4. `/push-plan` does not update receipt cards

Both bridges write a `kind: receipt` card under `tracks/<track>/obligations/`
and point `destination:` or `decided_in:` at it; the tracker URL lives in the
card's `url:`. The card survives `/push-plan`, which deletes only the plan
directory, but `/push-plan` has no step that finds receipt cards. Updating a
card's `url:` to the pushed tracker URL is a manual part of the handoff. Why
receipts:
[bridges record](decisions/2026-08-02-research-spike-bridges-use-receipt-cards.md).

### 5. The dprint/generated-block stability trap, and how it is verified

If the repo's formatter rewrites a generated block, the freshness check and
the formatter fight forever — every `write-ledger` produces a diff `dprint`
then un-produces, or vice versa. The fix is to use markdown constructs the
formatter leaves alone: `render_counts()` emits a **bullet list**, not an
aligned table, specifically because `dprint` reflows table columns but leaves
bullets untouched.

This is not asserted by eye. `scripts/test-research-spike.sh` runs
`dprint check --config "$ROOT/dprint.json" --incremental=false` — the repo's
own `dprint.json`, not a copy or an assumption about its rules — against a
fixture tree carrying `init`-scaffolded and `write-ledger`-written generated
markdown, and asserts a clean exit (dprint's exit `20` means "would rewrite,"
which is treated as a hard failure). The check skips cleanly (not silently),
printing which case it hit, in two situations: when `dprint` is not on `PATH`
at all, and when `dprint` _is_ installed but exits `12` ("could not resolve a
plugin," e.g. a bare machine with no network) — CI installs `dprint` from
`mise` and resolves the pinned plugins, so `12` is never how the assertion
passes in the gate, only how the harness stays runnable offline. Every other
non-zero, non-`20` exit (a bad invocation, no matching files, a crash) is a
hard failure, not a skip — a formatter assertion that "passes" without
actually reading a generated file would be worse than no assertion.

### 6. `--root` is a global option and must precede the subcommand

`--root` is registered on the top-level `argparse.ArgumentParser`, before
`add_subparsers()` — not on each subcommand's parser. That means
`research-spike.py validate --root <dir>` is a usage error (`unrecognized
arguments: --root`, exit `2`); the working form is
`research-spike.py --root <dir> validate`.

This is not a hypothetical footgun: an acceptance criterion on this plan
checked only the flag's _presence_ in documented example commands, never its
_position_, and a whole PR's worth of documented invocations shipped
unrunnable as a result before it was caught. Every example command in
`SKILL.md` and `references/{record-grammar,adoption}.md` now carries `--root`
in the correct position, and `record-grammar.md`'s own worked-example block
is explicit about this ordering. If you add a new example command anywhere
in this skill, put `--root` first and actually run the command once — don't
trust that it looks right.

### 7. `validate` never auto-repairs; `write-ledger` is a writer, not a gate

Two related but distinct rules. First: `validate` finding a stale ledger is
purely diagnostic — it reports and exits `1`, and never touches the file.
Rewriting requires the separate, explicit `write-ledger` invocation, for the
same reason described in gotcha 1: fold rewriting into validation and the
number stops being _checked_ and starts being _generated_, silently, on
every run.

Second, and easy to get backwards: `write-ledger`'s own exit code answers
**only** "did the write happen," not "is the tree otherwise clean." Its
internal `Report` deliberately discards findings from `discover()` and
`validate_cards()` (needed only as a side effect, to populate stub/receipt
tallies) rather than surfacing them — a card violation in a sibling track or
project must not fail a scoped write that did exactly what it was asked, and
`write-ledger` must not half-gate by inheriting some of `validate`'s checks
but not others. A writer that inherited the gate's verdict would make "did my
write succeed?" unanswerable from the exit code alone — you'd have to guess
whether a non-zero exit meant the write failed or some unrelated tree content
was invalid. Always follow `write-ledger` with a separate `validate` call if
you need to know the tree is clean; never read `write-ledger`'s exit code as
a proxy for it.

## Testing

`scripts/test-research-spike.sh` is a hermetic bash fixture harness, modeled
on `scripts/test-validate.sh`: every fixture builds its own tree under
`mktemp -d` and passes it via `--root`, so no test ever reads the real
repository — the central assertion throughout is "this path exists," and a
test scanning the real tree would pass or fail on whatever happened to be
checked in that day. It is wired into `scripts/check.sh`.

This repo does not gate on its own `dev_docs/research/` tree (there isn't
one yet); `scripts/check.sh`'s header comment explains why and says what to
add, and when, once a real project is initialized here. See
[`skills/research-spike/references/adoption.md`](../skills/research-spike/references/adoption.md)
for the full setup sequence.

## First adoption — `bestdan/aiutopilot`, August 2026

The first repo to run this on live work. Recorded here because the skill was
built from that repo's bespoke predecessor, and its numbers are quoted
throughout these docs as "the reference implementation" — so this is the
instrument turned back on the tree it was derived from, and the places it
did not fit are worth more than the places it did.

### What the adoption exercised — and what it did not

**Not exercised: `backfill` (SKILL.md procedure 4), the playbook's payload
step.** That repo had already backfilled its free-form doc into the bespoke
predecessor's record format, so this adoption converted 13 existing
structured records rather than walking prose and judging what counted as a
deferral at all. **Nothing here is evidence about the backfill's ergonomics,
and the "4 of 13 had no destination" figure quoted in `adoption.md` remains
un-replicated.** The next adopter starting from real prose is still the
first test of that path.

Exercised, and held: `init`, the record grammar, `validate`/`--strict`,
`write-ledger`, `status`, stub cards with `superseded_when:`, a two-track
split, and the gate wiring.

### What held up

- **The record grammar was portable without loss.** The predecessor's
  `id`/`owes`/`destination`/`status`/`discharged_by` mapped one-to-one; 13
  records and both count pairs (8 answered / 4 open questions, 3 discharged
  / 10 open obligations) came across identically.
- **`init`'s scaffold passes its own `validate` immediately**, and the inert
  HTML-comment worked example survived a real `dprint fmt` pass untouched.
- **The `--root`-before-subcommand insistence was warranted.** It is stated
  in three places and needed to be — the adopter reached for the wrong order.
- **The `_plan/` warning-not-error distinction was right.** Four
  destinations pointed into in-flight plan directories. Errors would have
  forced a pointless repoint; warnings recorded the debt and let the work
  land.
- **Generated ledgers were `dprint fmt`-stable and `write-ledger`
  idempotent** — verified by re-running both over an already-fresh tree.
- **`validate`'s error messages carried the adoption.** They state the
  failure, the remedy, and why the rule exists, which is why the eventual CI
  failure needed no interpretation. Worth protecting in future edits.

### The tool correcting its user — the most valuable behaviour observed

1. **`status`' `blocking:` density warning fired, and was right.** The
   adopter marked 5 of 10 open obligations `blocking:`; the warning said
   more than a third means the flag has become emphasis. Three were
   double-counting a gate the question already carried via `blocks:`.
   Trimmed to 2.
2. **Derived readiness could not be faked.** There is no field to type a
   status into, so the number an adopter would most like to flatter is the
   one they cannot touch.

The first is the behaviour to generalize: a warning that pushes back on the
person operating the instrument caught a modelling error no reviewer had.

### The two gaps in `adoption.md`, now fixed there

- **Decisions must be named before the backfill is worth anything.**
  `blocks:` must resolve to a decision record — promoted in `decisions.md`,
  or `state: proposed` in the track's own `questions.md`, which validates
  either way. What an unnamed decision list costs is convergence, not
  validation: `status` reports a `proposed` decision only as "awaiting
  promotion" and derives ready/blocked for promoted ones alone. The source
  repo had one implicit decision encoded as a section heading and three real
  ones; making them explicit was a prerequisite, not a tidy-up. Now step 1a.
- **The playbook assumed the script is reachable from the gate.** It is not,
  on a CI runner with no plugin install — and the naive `if [ -f … ]` guard
  no-ops green, which is the validator's own fail-open relocated into the
  wiring. Resolved by fetching at a pinned commit with a `sha256` check; the
  rejected alternative (fall back to the local plugin) reintroduces version
  drift between laptop and CI. Now a subsection of step 4.

### Where the adopter deviated from step 4, and was wrong

Step 4 says to add `validate --strict` as the gate. The adopter instead put
it in an advisory, non-blocking lane, reasoning that `validate` bundles three
checks of which only destination resolution is a property an unrelated commit
can break. Two independent consults rejected it: coverage and ledger
freshness derive from the records, so they already fail only the author who
caused them; and "authoring lapse" does not distinguish them from a formatter
check, which blocks everywhere.

**The instructive part is what tempted the deviation.** Step 5's
advisory-tier generalization — "a check whose false positives have nowhere to
go must not be able to fail the build" — reads as a principle about checks in
general. It is a statement about `suggest` only. Having built an advisory lane
for `suggest`, extending it to `validate` felt like applying the documented
rule rather than departing from it. §5 now carries an explicit limit: the
boundary falls between the two subcommands, never inside `validate`.

The demotion also inverted the point. **Coverage is the check that carries
this instrument's purpose**; destination resolution only protects an
already-recorded obligation from a later rename. Gating on destinations alone
enforces the weaker property and waves through the stated one.

### Proving the gate fails

Confirmed by deliberate breakage rather than assumed: a removed coverage
declaration, a renamed destination, and an unregenerated ledger each produced
the expected error. The coverage break was then pushed to CI, where the
repo's own check script **passed** the broken tree and the research-spike job
failed — while the merge button stayed enabled. Then reverted.

That last step surfaced something no adopter should have to rediscover:
**adding a blocking job to a workflow does not make it a required check.**
The repo's branch protection listed only its pre-existing context, so the new
job showed red while the merge button stayed enabled. `adoption.md` now says
to verify this.

## File map

| Path                                                 | What it is                                                                                                                                           |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/research-spike.py`                          | The script half — stdlib-only, deterministic — the only research-spike thing CI runs (via `scripts/check.sh`, which runs many other checks besides). |
| `scripts/test-research-spike.sh`                     | The hermetic fixture harness, wired into `scripts/check.sh`.                                                                                         |
| `skills/research-spike/SKILL.md`                     | The judgment half — the five procedures and the script/LLM boundary.                                                                                 |
| `skills/research-spike/references/record-grammar.md` | The full field reference, written by hand against the script's own constants — see "Where the record grammar lives" for why that sync is not tested. |
| `skills/research-spike/references/adoption.md`       | The setup playbook for turning this on in a repo with real deferred work.                                                                            |
| `dev_docs/decisions/*-research-spike-*.md`           | The decision records — why each choice was made and what would reopen it.                                                                            |
| `dev_docs/research_spike.md` (this file)             | The engineering record — architecture, the status report, gotchas.                                                                                   |
