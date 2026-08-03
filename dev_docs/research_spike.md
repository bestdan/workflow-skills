# research-spike — the obligation ledger, engineering record

This is the durable maintainer's doc for `scripts/research-spike.py` and
`skills/research-spike/`. It graduates the load-bearing decisions and gotchas
from the thirteen-task plan that built the skill (all delivered) before that
plan's scaffolding is deleted, per `dev_docs/tasks/`'s convention that a plan
folder is temporary project-tracker scaffolding, not permanent documentation.
If you are about to touch the script or the skill and want to know why it
looks the way it does, this is the doc — not the design doc, and not (any
longer) the plan.

The product design — the problem, the measured evidence, the record formats,
the full on-disk structure — is settled and lives at
[`dev_docs/designs/research_spike_skill.md`](designs/research_spike_skill.md).
Read it for the "why does this skill exist at all" story (the four-day
`aiutopilot` incident, the 6-discharged-against-23-open numbers, the
deferral-visibility table). This doc does not repeat that story; it covers
what changed or hardened between that design and the shipped code, and the
operational traps a maintainer will hit that the design doc, written before
implementation, could not yet know about.

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

Presenting the two as one flat verb list was reviewed as the most likely way
to implement the wrong half of this split, which is why both `SKILL.md` and
this doc state the boundary before listing anything else.

### The on-disk convention

Fixed by the skill, not configurable per repo — this is what replaces the
reference implementation's three per-repo config knobs (coverage-file
location, heading regex, coverage scope):

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
files (the design's "what the skill must not do" list otherwise forbids
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
  says so) rather than against this doc's or the design's memory of it.

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

## Load-bearing decisions and rationale

| Decision                                                                                | Rationale                                                                                                                                                                                                                                               |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Directory convention is skill-owned, not configurable                                   | Replaces the reference implementation's three per-repo config knobs. A mechanical rule can be applied structurally once the skill owns the layout, instead of trusting per-repo config to be right.                                                     |
| `destination:` must resolve to an **existing regular file**, not a directory            | This is the entire mechanism. In the origin repo, a deferral stayed visible exactly when its destination was a file that already existed; a bare directory can exist while saying nothing about the work.                                               |
| Coverage rule: every question section declares an `obligation` or a bare `none:` reason | "Did you use a deferral word?" is a heuristic and unanswerable over English prose. "Is the field present?" is mechanical — forgetting stops being available, only deliberate silence remains, and a reviewer can see and challenge silence.             |
| No `sweep` verb in v1                                                                   | Bridged work (obligations handed to a tracker via a receipt card) is not reconciled — completing the tracker task does not discharge the local obligation. Receipt cards are what would make a later `sweep` possible; building it now is out of scope. |
| Cross-project destinations are forbidden                                                | A destination pointing into a foreign project's tree would hide that project's inbound dependency from its own ledger. The fix is to file the work in the receiving project and point the local obligation at a receipt card recording the handoff.     |
| No trend computation                                                                    | The ledgers store no history. "Questions converging while obligations climb" is read by a human across commits (`git log` on the ledger lines); the script must not pretend to compute or print a delta.                                                |
| No auto-repair, anywhere                                                                | `validate` never rewrites a stale ledger and never creates a missing destination. Either would let the tool quietly satisfy its own gate, which is how a check becomes theatre.                                                                         |
| `blocking:` obligations are warned scarce (>1/3 of open obligations, fixed threshold)   | If everything blocks, nothing converges and the flag has become emphasis rather than signal. Not configurable, deliberately — a repo tuning the threshold is a repo negotiating with the warning instead of fixing the cause.                           |
| Retired questions never fold into "answered"                                            | Folding them together would let a project converge by giving up. Retirement is legitimate scope reduction, counted separately everywhere, including in the header totals.                                                                               |

## Gotchas

Seven traps a maintainer will hit. Five are the ones the design and the plan
paid for; two more surfaced only once the plan was implemented and are worth
graduating from its history.

### 1. Why the ledger is stored, not computed on demand

A number you must remember to run reproduces the exact failure this whole
instrument exists to prevent: invisible unless someone thinks to look. The
ledger — questions answered/open/retired, obligations discharged/open (with
blocking/stub/external subtotals) — is stored as a bullet list at the top of
each track's `questions.md`, and rolled up into `LEDGER.md` per project.
`validate` checks the stored numbers against a fresh derivation and fails if
they disagree; `write-ledger` is the only thing that ever rewrites them, and
it is always an explicit, human-triggered act (see gotcha 7).

### 2. Why `suggest` can never fail a run

The lexical scan (`deferred to`, `gated on`, `belongs to`, …) is the tempting
half — grep for deferral vocabulary and flag anything unregistered. It
survives only as an advisory mode with a hard-structural exit-0 guarantee,
and the reason is measured, not fastidious: run against the reference tree
(`aiutopilot`), `suggest` returned **29 hits**, most of them prose
_describing_ behaviour rather than deferring work (e.g. "the stop is never
gated on the lock"). In a repo with no baseline file, no allowlist, and no
skip flag, a lexical scan as a gate would have been noise with nowhere legal
to go. The rule that generalizes: **a check whose false positives have
nowhere to go must not be able to fail the build.** In a repo that _does_
have an advisory tier, `suggest` may run there as a non-failing report — see
`skills/research-spike/references/adoption.md` step 5.

### 3. Why decision readiness is derived, never stored

A decision (`decisions.md`) stores only `state: pending | decided` (plus
`decided_in:` / `reopened_because:` where required). There is no `ready` or
`blocked` key — the `KNOWN_FIELDS` table for `decision` does not include
them, so a hand-typed `ready: true` is an unknown-key error, not a silently
accepted lie. Readiness (every blocking question answered/retired, every
blocking obligation discharged) is computed fresh on every `status`/`validate`
run. Storing both the derived and a hand-edited copy was reviewed as a
defect, not a convenience, precisely because a stale stored status can
disagree with the derived one — the record format itself is what makes that
class of bug impossible, rather than a rule that has to be remembered.

### 4. Why both bridges route through receipt cards

`defer` → task loop, and `decided` → plan-with-docs both had their first
drafts rejected in review, for the same reason: tracker handlers return
**URLs, not paths** (`/add-task`'s contract hands back `issue.url`, never a
tracker id or a filesystem location), and `/push-plan` **deletes** plan
directories once every task in them has migrated. An obligation's
`destination:` or a decision's `decided_in:` pointing directly at either
would violate the one invariant the whole instrument rests on
(path-must-exist) or rot on the very next push.

Both bridges therefore write a `kind: receipt` card under
`tracks/<track>/obligations/` — a file, which is a path that exists — and put
the external reference (`url:`, optional `handler:`/`tracker_id:`) inside the
card's _content_, which the validator never path-checks and (being offline by
contract) could not verify anyway. The obligation's `destination:` or the
decision's `decided_in:` then points at the card, not at the tracker. A
receipt card survives `/push-plan` because it lives in the spike's own
`tracks/<track>/obligations/` directory, not in the plan directory that
command deletes — but `/push-plan` has no receipt-discovery step, so updating
a card's `url:` to a pushed tracker URL is a manual part of the handoff, not
something the push performs.

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

- **Decisions must be named before the backfill can validate.** `blocks:`
  must resolve, so an empty `decisions.md` forces every question to `none:`.
  The source repo had one implicit decision encoded as a section heading and
  three real ones; making them explicit was a prerequisite, not a tidy-up.
  Now step 1a.
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
repo's own check script **passed** the broken tree, the research-spike job
failed, and the pull request flipped to blocked — then reverted.

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
| `dev_docs/designs/research_spike_skill.md`           | The product design — problem, evidence, and the settled architecture.                                                                                |
| `dev_docs/research_spike.md` (this file)             | The engineering record — decisions, rationale, gotchas.                                                                                              |
