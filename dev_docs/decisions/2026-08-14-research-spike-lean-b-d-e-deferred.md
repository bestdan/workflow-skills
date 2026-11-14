---
created: 2026-08-14
status: accepted
convention: ../research_spike.md
---

# research-spike defers three Lean concepts: an assumption card, a trend verb, `status --json`

## Context

The lean-concepts design (`dev_docs/designs/2026-08-14-research-spike-lean-concepts.md`,
deleted on graduation) named five concepts worth importing from Lean. Two, A
(taint) and C (counting `none:`), shipped
([record](2026-10-18-research-spike-taint-and-none-in-status-only.md)). The
other three were recorded as deferred and unscheduled so that nobody has to
rediscover them. None is built: `CARD_KINDS` is still `("stub", "receipt")`,
there is no `trend` verb, and nothing in `scripts/research-spike.py` emits
JSON.

## Decision

B, D and E stay unbuilt. Each has an open issue that carries its shape:

- **B, a permanent assumption card**
  ([#1054](https://github.com/bestdan/workflow-skills/issues/1054)). A stub
  must declare `superseded_when:`, so a deliberate, permanent assumption has
  nowhere honest to live. It invents a deletion condition or hides in a
  `none:` reason. Lean's `axiom` is that third thing: few, named, and always
  surfaced. Shape: `kind: assumption` with a required `holds_unless:`, counted
  in its own column, never folded into stubs. Deferred because it changes the
  record grammar, unlike A and C.
- **D, a decreasing measure**
  ([#1055](https://github.com/bestdan/workflow-skills/issues/1055)). The
  instrument's central claim is the divergence between converging questions
  and climbing obligations. The ledgers store no history, so that is left to a
  human reading `git log`. Shape: a `trend` verb that reads the stored ledger
  lines out of `git log -p` and prints both series. Deferred because it needs
  care about the script's read-only, offline contract and about shallow
  clones.
- **E, a certificate for consumers**
  ([#1056](https://github.com/bestdan/workflow-skills/issues/1056)). Every
  consumer of the derived state (`auto-pilot`, `reconcile-tasks`, a future
  `sweep`) must re-parse the markdown and re-implement the derivation. Lean's
  answer is a proof term that a separate small kernel re-checks. Shape:
  `status --json` emitting the derived state. Deferred because no consumer
  needs it yet.

Decision-to-decision edges were also left out, with A, because they make
cycles representable and need a cycle check.

## Consequences

- Good, because the record grammar, the read-only contract and the output
  format all stay stable until a concrete need arrives.
- Bad, because permanent assumptions keep hiding in `none:` reasons or fake
  `superseded_when:` conditions.
- Bad, because the divergence signal stays a snapshot that a human has to
  trend by hand.
- Bad, because any consumer built before E re-implements the derivation, with
  the drift that brings.

## Revisit when

- **B:** a real spike carries an assumption it has had to express as a stub
  with an invented `superseded_when:`, or as a `none:` reason, more than once.
- **D:** someone asks whether a spike is converging and the answer requires
  reading ledger history by hand. That was the original day-four question.
- **E:** a second program needs research-spike's derived state, for example
  the `sweep` verb, or auto-pilot reading readiness.
- Any ledger format bump: B's column and A/C's stored-ledger move should go in
  the same bump.

## Confirmation

Nothing; each item is an open issue. `CARD_KINDS` in
`scripts/research-spike.py` and the absence of a `trend` subcommand or JSON
output show the state.

## Alternatives

- **Build B with A and C:** it touches the grammar, which A and C were scoped
  to avoid.
- **Drop B, D and E entirely:** each names a real gap, and the issues cost
  nothing while open.
