---
title: "research-spike: obligation and card validation, including the destination rules"
priority: high
size: 5
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - scripts/research-spike.py
  - scripts/test-research-spike.sh
is_blocked_by: research_spike_task_1
parent: research_spike
expires: 2026-08-31
tags: [research-spike, script, validation]
---

[[research_spike_plan]]

## Context

This is the load-bearing rule of the whole instrument. A deferral stayed
visible in the origin repo **exactly when its destination was a file that
already existed**; "defer to the account track" was invisible because naming a
track does not create one — the sentence reads as though it routed the work
somewhere, and it routed it to a string.

So `destination` is not a comment. It is a repo-relative path that must
resolve, and the design tightens it further after review (§"Record formats and
grammar", obligations): a bare **directory no longer qualifies** — a directory
can exist while saying nothing about the work.

Cards make stubs and handoffs first-class instead of folklore. A stub must
carry the condition of its own deletion, because **a stub that never gets
superseded is a new place for work to hide**.

## Task

In `scripts/research-spike.py`, validate `obligation` and `card` records.

**Obligation fields** — `id`, `owes`, `destination`, `status`, plus
`discharged_by` and `blocking` conditionally:

- `id` is kebab-case (`^[a-z0-9]+(-[a-z0-9]+)*$`), and **globally unique** once
  qualified as `project/track/id`. A duplicate must be reported with both
  source locations. Duplicates must be caught **across files**, not just within
  one.
- `owes` is required and non-empty.
- `status` ∈ `open | discharged`. `discharged_by` is required **iff**
  `discharged` and forbidden while `open`. It is free text (typically a PR or
  commit ref) and is deliberately **not** path-checked — the discharging
  artifact usually lives outside the tree.
- `blocking: <decision-id>` is optional; its referential check belongs to task
  5. Accept and record it here.

**`destination` rules** — rules 1-6 are errors, rule 7 is a warning. Each
message must say why it matters (the message is part of the mechanism; a bare
"invalid path" teaches nobody):

1. required and non-empty;
2. **repo-relative** — an absolute path fails;
3. normalized — `../` traversal fails, and the resolved path must stay inside
   `<root>`;
4. a **symlink that escapes** the root fails (resolve and re-check
   containment);
5. must resolve to an **existing regular file** — a missing path fails, and so
   does a directory. **The error must state the fix, not just the rule**: a
   folder-shaped deferral points at a **specific file inside** the folder — the
   plan's epic file (`<name>_plan/<name>_plan.md`), a specific task card, or a
   stub/receipt card under `tracks/<track>/obligations/` — because a directory
   can exist while saying nothing about the work, whereas a file names it. (See
   rule 7 before pointing at a `*_plan/` file.)
6. **cross-project destinations are forbidden**: the resolved path must not
   land under a different `dev_docs/research/<other-project>/` tree. Work an
   answer creates for another project is filed in that project, and the local
   obligation points at a **receipt card** recording the handoff (design
   §"On-disk structure"). Say that in the error message.
7. **plan directories are a rot hazard — warned, not rejected.** A destination
   resolving under a `*_plan/` directory emits a **warning** naming the
   `/push-plan` deletion hazard and pointing at the receipt-card route (task
   10's `defer` bridge). Unlike `decided_in:` (task 5, a hard **error** —
   decision evidence is permanent), an obligation may legitimately point at an
   in-flight plan card; the warning is what stops it outliving the folder.

> **Why rule 5's suggested fix and rule 7 must be read together.** The obvious
> replacement for a folder destination — a file inside that folder — is exactly
> the destination rule 7 warns about when the folder is a `*_plan/`. That is
> deliberate, not a contradiction: pointing at the epic file is right for an
> in-flight plan and still needs to be revisited before `/push-plan` deletes it.
>
> Note this tightens the design's own origin story, where "a plan folder that
> was created" is cited as a **visible** (successful) deferral. That was true
> under the loose reference-implementation rule ("a path that must exist"); the
> post-review tightening makes a bare directory insufficient, which is why the
> migration has to be stated rather than left for the implementer to infer.

**Card records** — files under `tracks/<track>/obligations/`, one block each:

- `kind` ∈ `stub | receipt`.
- `kind: stub` requires `superseded_when:` — the condition of the card's own
  deletion.
- `kind: receipt` requires `url:`, with optional `handler:` and `tracker_id:`.
  `url` is **never** fetched or verified — the validator is offline by
  contract.
- A file under `obligations/` carrying no card block is an error; a card block
  outside `obligations/` is an error.
- Count stubs and receipts by kind and expose the tallies for the ledgers
  (task 7) — do not compute or print ledger lines here.

## Acceptance Criteria

**Code-enforced:**

- New fixtures in `scripts/test-research-spike.sh`, one per rule:
  - a nonexistent destination fails **and the message says why it matters**;
  - a destination under a `*_plan/` directory **warns**, names `/push-plan`, and
    still **exits 0** — the one non-fatal destination rule;
  - a destination that is a directory fails;
  - an absolute destination fails;
  - a `../` traversal destination fails;
  - a symlink pointing outside the root fails;
  - a destination inside a sibling project fails, naming the cross-project rule;
  - a valid destination pointing at a regular file passes;
  - duplicate ids fail **across two different files**, and the error names both;
  - the same bare id in two different tracks (or projects) is **accepted** —
    qualification is what prevents the collision;
  - `discharged` without `discharged_by` fails; `discharged_by` while `open`
    fails; `discharged_by` is not path-checked (a PR URL passes);
  - a `stub` card without `superseded_when` fails; a `receipt` without `url`
    fails; a receipt's `url` is not fetched (assert offline behaviour by using
    an unroutable URL and expecting a clean pass);
  - an unknown obligation key (`desination:`) fails naming the key.
- `bash scripts/check.sh` green.

**User-run:**

- Register an obligation by hand against a path you then `rm`, run `validate`,
  and confirm the error reads as an explanation rather than a lint code.
