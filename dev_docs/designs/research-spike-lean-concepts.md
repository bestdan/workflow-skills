# Lean concepts worth importing into research-spike

Companion to [`dev_docs/research-spike-lean-evaluation.md`](../research-spike-lean-evaluation.md),
which concluded that Lean is the wrong _implementation_ language for
`scripts/research-spike.py` but the right _conceptual model_ for it. This
document is the follow-through: which specific concepts the instrument is
missing, and how to land the two that are worth landing now.

Scope: **A** (transitive obligation taint) and **C** (counting the escape
hatch) are specified to implementation depth. **B**, **D** and **E** are
recorded as deferred, with enough detail to be picked up later.

## The constraint that shapes all of this

`LEDGER.md` and each `questions.md` carry a **stored** ledger block, and
`validate` fails on a stored block that disagrees with its derivation
(`validate_ledger_freshness`). `status` stores nothing — it derives on every
run and exits 0 by contract.

So the two surfaces have completely different costs:

| Surface                            | Cost of adding a derived number                                                                        |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `status` (report, unstored)        | **Zero.** No migration, no freshness interaction, no format contract.                                  |
| `LEDGER.md` / stored track ledgers | **Breaking.** Every existing tree in every consumer repo goes stale until someone runs `write-ledger`. |

**Therefore: land both A and C in `status` only.** Neither needs the stored
ledger to be useful, and putting them there first means each is an additive,
migration-free change. Whether the numbers eventually belong in the stored
ledger is a separate decision, made once the derivation has been used in
anger — and it would then be one deliberate format bump carrying both, not two.

This is also the honest reading of the instrument's own rule: the stored ledger
is the number people quote; the report is where you go to understand it. Taint
is understanding.

## A. Transitive obligation taint — `#print axioms`

### The gap, demonstrated

A fixture with one decision, one answered question that gates it, and one
obligation created by that answer, still open and pointed at a stub card:

```
$ research-spike validate
research-spike: OK — 1 projects, 1 tracks, 4 records
$ echo $?
0

$ research-spike status demo
demo — decisions: 1 decided, 0 ready, 0 blocked

  account-provisioning  DECIDED decided in dev_docs/research/demo/PROJECT.md

  account:  Q 1 answered / 0 open / 0 retired    O 0 discharged / 1 open (1 stub)
```

The decision reads `DECIDED`. The stub is counted on the track line. **Nothing
connects them.** In Lean terms this is a theorem proved from a lemma that is
still `sorry`, reported as proved — the exact condition `#print axioms` exists
to make impossible to miss.

### Why the gap exists

Taint reaches a decision only through an explicit `blocking:` on an obligation.
That is a **manual declaration**, and SKILL.md deliberately tells you to use it
sparingly: _"Add `blocking: <decision-id>` only when this obligation genuinely
gates a decision — most should not; scarcity is what keeps convergence
meaningful."_

That guidance is right and must not be weakened. The consequence is simply that
`blocking:` is a curated subset, and nothing derives the rest.

### Import the concept correctly

The naive fix — treat every section-local obligation as blocking its question's
decision — would flood `BLOCKED`, destroy the scarcity rule, and make the
convergence report useless. **Lean does not do this either.** It never
downgrades a theorem to unproved because it rests on a `sorry`; it reports the
theorem as proved _and_ names what it depends on. Provenness and taint are
orthogonal axes.

So the label is untouched and a taint clause is appended:

```
account-provisioning  DECIDED — rests on 1 open obligation (1 stub)
```

`BLOCKED` still means "has live blockers." `DECIDED` still means "a human took
this decision." The new clause means "here is the undischarged work underneath
it," which is a different question and deserves a different answer.

### The edge is already on disk

No new field, no grammar change. The relation is derivable from structure the
parser already produces:

1. `tree.sections[rel]` gives the `### Q<n>.` sections of each `questions.md`.
2. `records_by_file(tree)` gives the records in each file; a record belongs to
   the section whose `start_line <= rec.line <= end_line` — `validate_questions`
   already does exactly this grouping.
3. A section's `question` record declares `blocks: D`.
4. Therefore: the section's `obligation` records are **work that answering a
   question gating D created**, whether or not they declare `blocking:`.

That is the taint edge, free.

### Specification

Add to the convergence module, next to `resolve_blockers` (which stays the
single source of truth for `blocking:` — this is a second, clearly-named
relation, not a competing implementation of the first):

```python
def resolve_taint(tree: Tree) -> dict[tuple[str, str], list[Record]]:
    """Open obligations reachable from each decision through its questions.

    The `#print axioms` relation: not what *blocks* a decision (that is
    `resolve_blockers`, and it is a deliberately scarce, hand-declared set) but
    what the decision *rests on* — the undischarged work that answering its
    gating questions created. Section co-location is the edge, so this needs no
    field and no grammar change.

    Reported beside a decision's label rather than folded into it: a decision
    resting on open work is still decided, exactly as a Lean theorem depending
    on `sorryAx` is still proved. Folding the two would collapse the scarcity
    that makes `BLOCKED` mean anything.
    """
```

Rules:

- Walk each `questions.md` section once. If its `question` declares
  `blocks: D₁, D₂` (not the `none:` sentinel), every `obligation` record in
  that same section with `status: open` is tainting evidence for each named
  decision.
- A bare `none:` obligation block declares nothing owed, so it taints nothing.
  It is counted by **C** instead.
- An obligation whose `destination:` resolves to a `kind: stub` card is counted
  separately from one pointing at a `kind: receipt` card — the same reason
  `CardCounts` already separates them, and the same reason Lean distinguishes
  `sorryAx` from `Classical.choice`. A stub is work with nowhere to go; a
  receipt is work that went somewhere this validator cannot see. Report both,
  never their sum.
- Dedupe by record identity, as `resolve_blockers` does — an obligation whose
  question names the same decision twice is one obligation.
- A record already live-blocking the decision through `blocking:` is **not**
  restated in the taint clause; it is already in the `BLOCKED` note.

Render in `decision_status` / `print_project_status` only. `render_decisions_list`
(which writes the stored `LEDGER.md`) is **unchanged** — see the constraint
section.

### Explicitly not in scope

- **No new validation rule.** Taint does not fail `validate`. A decision resting
  on open work is a normal, legitimate state — Lean compiles such a file too. It
  is reporting, not gating. Making it an error would be the "widen coverage past
  questions files" mistake in a new costume.
- **No decision-to-decision edges.** Those would make cycles representable for
  the first time and require a cycle check. Out of scope; noted in **B/D/E**.

## C. Count the escape hatch

### The gap

`Counts` has eight fields — `answered`, `open_questions`, `retired`,
`discharged`, `open_obligations`, `blocking`, `stubs`, `external`. **None of
them counts `none:` declarations.**

The `none:` sentinel is validated carefully: a bare `none:` with no reason is an
error, and mixing `none:` with other fields is an error. But it is never
_counted_. A spike could satisfy the coverage rule in every section with
`none: filing only, no work identified yet` and every ledger in the tree would
read as a clean sheet.

Lean does not allow this. Every `sorry` is a live compiler warning, every
`partial def` is a visible marker, and neither ever fades. **The escape hatch is
always counted.** This is the cheapest correction on the list and it is the same
discipline SKILL.md already states for stubs: _"a stub that never gets
superseded is a new place for work to hide."_ A `none:` is the same hiding
place, one step earlier.

### Specification

- Count bare `none:` obligation blocks per track — `is_none_block` already
  identifies them exactly.
- Surface in `status` as a per-track and total figure, beside the obligation
  counts: `O 0 discharged / 1 open (1 stub) / 3 declared none`.
- Do **not** add to `Counts` / `render_counts` yet — that is the stored ledger,
  and the migration constraint applies. If it later earns a place there, it
  travels with A in one format bump.
- No new validation rule and no threshold. A high `none:` count is not an error;
  it is a number a human reads next to the others. Adding a threshold here would
  be the knob SKILL.md already refuses for the scarcity warning — _"a threshold
  with a knob is a threshold that gets turned up the first time it fires."_

## Deferred: B, D, E

Recorded so they are not rediscovered from scratch. None is scheduled.

**B. `axiom` — the named permanent assumption.** `CARD_KINDS` is
`("stub", "receipt")`. A stub _must_ declare `superseded_when:`, so a
deliberate, permanent assumption the spike builds on has nowhere honest to
live — it either invents a fake deletion condition or hides in a `none:`
reason. Lean's `axiom` is exactly this third thing, and its discipline is the
good part: few, named, and surfaced by `#print axioms` forever, never as a
warning that fades. Shape: `kind: assumption` with a required `holds_unless:`,
counted in its own column and never folded into the stub count. This one does
touch the record grammar, which is why it is not bundled with A and C.

**D. A decreasing measure.** Lean rejects a recursive definition that cannot
exhibit a decreasing measure. This spike has no variant at all, and SKILL.md
admits it: _"The divergence signal is a snapshot. The ledgers store no
history."_ That divergence — questions converging while obligations climb — is
the instrument's central claim, and it is the one number currently left to a
human reading `git log`. Shape: a `trend` verb that reads the stored ledger
lines out of `git log -p` and prints the two series. Needs care about the
read-only contract and about repos with shallow clones.

**E. The proof term and an independent re-checker.** Lean's elaborator emits a
term that a small separate kernel (`lean4checker`) re-verifies. There is no
`--json` anywhere in `research-spike.py`, so every consumer — `auto-pilot`,
`reconcile-tasks`, a future `sweep` — must re-parse the markdown and
re-implement the derivation, which is the drift `resolve_blockers`' own
docstring warns about _within_ the file. Shape: `status --json` emitting the
derived state as a certificate. Pairs naturally with the `sweep` verb SKILL.md
already describes as deliberately absent from v1.

## Not worth importing

Dependent types, tactics, universe levels, proof irrelevance. The evaluation
doc covers why: there is no subtle algorithm here for them to act on.
