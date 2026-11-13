---
created: 2026-08-02
status: accepted
convention: ../research_spike.md
---

# research-spike: the lexical deferral scan is advisory and can never fail a run

## Context

The tempting way to find unregistered deferrals is to grep for phrases like
"deferred to" or "gated on". Run against the `aiutopilot` tree, that scan
returned 29 hits, most of them prose describing behaviour rather than
deferring work ("the stop is never gated on the lock"). That repo's check had
no baseline file, no allowlist and no skip flag, so a false positive in the
gate had nowhere legal to go.

The design (`2026-08-01-research-spike-skill.md`, deleted on graduation) kept
the scan out of the gate. It landed in
[#324](https://github.com/bestdan/workflow-skills/pull/324).

## Decision

`suggest` is a separate subcommand that reports lines matching the phrase list
and lacking a nearby record, and always exits `0` (only an argument error from
the dispatcher can make it exit `2`). The phrase list is `SUGGEST_PHRASES` plus
one regex for "once … lands", a constant at the top of
`scripts/research-spike.py` that an adopting repo may edit. The design asked
for the list to be configuration; it shipped as an editable constant instead.

The generalisation is narrow. A repo with an advisory CI tier may run
`suggest` there as a non-failing report. That licenses nothing about
`validate`, whose checks derive from the records and fail only the author who
caused them.

## Consequences

- Good, because the scan stays available without ever blocking a merge on
  English prose.
- Bad, because an advisory report is easy to stop reading.
- Bad, because editing the phrase list means editing the installed script,
  which a plugin update overwrites.

## Revisit when

- A phrase list proves precise enough on a real tree that its hits are nearly
  all genuine deferrals.
- Adopters keep local edits to `SUGGEST_PHRASES`, which would make real
  configuration worth its cost.

## Confirmation

`scripts/test-research-spike.sh` asserts `suggest` exits `0` with hits
present, stays quiet next to a record, and exits `2` only on a bad flag.
`skills/research-spike/references/adoption.md` step 5 states where the
advisory boundary falls.

## Alternatives

- **Lexical scan as a gate:** 29 mostly false hits on the reference tree, with
  nowhere to record exceptions.
- **Drop the scan entirely:** it still points at prose worth a second look,
  at no cost to the gate.
