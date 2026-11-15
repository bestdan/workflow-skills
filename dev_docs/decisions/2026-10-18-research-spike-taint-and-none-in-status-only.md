---
created: 2026-10-18
status: accepted
convention: ../research_spike.md
---

# research-spike reports obligation taint and the `none:` count in `status` only

## Context

[`../research/2026-08-14-research-spike-lean-evaluation.md`](../research/2026-08-14-research-spike-lean-evaluation.md)
found Lean the wrong language for the script but the right model for it, and
the lean-concepts design (`dev_docs/designs/2026-08-14-research-spike-lean-concepts.md`,
deleted on graduation) picked concepts to import. Two gaps were worth closing
at once:

- **A, taint.** A decision could read `DECIDED` while the answer to a question
  gating it had created an obligation still open on a stub card. Only an
  explicit `blocking:` connects an obligation to a decision, and SKILL.md
  keeps `blocking:` deliberately scarce. In Lean terms, a theorem proved from
  a `sorry`, with no `#print axioms` to show it.
- **C, the escape hatch.** A bare `none:` block satisfies the coverage rule
  and was never counted, so a spike declaring `none:` in every section read as
  a clean sheet.

The stored ledgers are checked for freshness, so adding a number to them makes
every existing consumer tree stale until `write-ledger` runs. `status` stores
nothing.

C landed in [#413](https://github.com/bestdan/workflow-skills/pull/413); A in
[#852](https://github.com/bestdan/workflow-skills/pull/852)
([issue #632](https://github.com/bestdan/workflow-skills/issues/632)), with
the singular-suffix fix in
[#1047](https://github.com/bestdan/workflow-skills/pull/1047).

## Decision

Both are reported in `status` and nowhere else. For A, `resolve_taint` treats
every open, non-`none:` obligation in a `### Q<n>.` section as resting under
each decision that section's question `blocks:`. Section co-location is the
edge, so no field or grammar changes. It is shown as a suffix on the
decision's line, `— rests on N open obligation(s) (N stub(s), N receipt(s))`,
under any label. The label itself never changes, and an obligation already
shown as a live `blocking:` blocker is not repeated. For C, each track line
and the total end with `/ N declared none`. Neither adds a validation rule or
a threshold, and `Counts`, `render_counts` and the `LEDGER.md` decisions list
are unchanged.

Both shipped as the design specified.

## Consequences

- Good, because a decided decision resting on open work now says so, without
  diluting what `BLOCKED` means.
- Good, because the escape hatch is counted beside the other numbers.
- Good, because neither change made any consumer tree stale.
- Bad, because the numbers people quote, the stored ledgers, still omit both,
  so a reader who never runs `status` never sees them.
- Bad, because stubs and receipts are reported apart and never summed, which
  is one more figure to read.

## Revisit when

- The ledger format is bumped for another reason. A and C then travel into
  the stored ledger together in that one bump, rather than as two breaking
  changes.
- Taint on real spikes turns out noisy, many decisions resting on trivial
  obligations, which would argue for narrowing the edge.

## Confirmation

`scripts/test-research-spike.sh` covers the taint suffix (stub vs receipt,
dedupe, exclusion of live blockers, non-`none:` only) and the `declared none`
column, and asserts that the stored `LEDGER.md` carries no taint suffix.

## Alternatives

- **Treat every section-local obligation as blocking:** floods `BLOCKED` and
  destroys the scarcity rule. Lean does not downgrade a theorem that rests on
  `sorry` either; it names what it depends on.
- **Put both in the stored ledger now:** a breaking change for every consumer
  tree, for numbers not yet used in anger.
- **Fail `validate` on taint or a high `none:` count:** resting on open work
  is a normal state, and a threshold here is the knob SKILL.md refuses for
  the scarcity warning.
