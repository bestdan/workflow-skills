---
created: 2026-08-02
status: accepted
convention: ../research_spike.md
---

# research-spike stores its ledger and checks it, rather than computing it on demand

## Context

The whole instrument exists because obligations were invisible: nobody
counted them, so progress was read off the one number that looked healthy. A
count that has to be run on purpose reproduces that, since nobody runs it
until something already feels wrong. Projects with several tracks add a
second problem: parallel agents working different tracks must not fail each
other's checks, or contend on one file.

The design (`2026-08-01-research-spike-skill.md`, deleted on graduation) chose
stored ledgers with scoped freshness. They landed in
[#327](https://github.com/bestdan/workflow-skills/pull/327).

## Decision

Each track stores its ledger, two bullet lines between
`<!-- research-spike:ledger -->` markers at the top of its `questions.md`:

```markdown
- **Questions:** 8 answered, 4 open, 1 retired
- **Obligations:** 3 discharged, 10 open (2 blocking, 1 stub, 1 external)
```

The project's `LEDGER.md` rolls up a decisions list, the same lines per track,
and totals. `validate` compares each stored block with a fresh derivation and
reports a mismatch; it never rewrites one. `write-ledger` is the only writer
and is always run on purpose. Freshness is scoped: a track's ledger is checked
against that track only, and a stale `LEDGER.md` is a warning under plain
`validate` and an error under `validate --strict`, the organizer's gate. The
ledger stores no history, so the script computes no trend.

The blocks are bullet lists because `dprint` reflows table columns and leaves
bullets alone; a generated block the formatter rewrites would fight the
freshness check forever. The design asked the skill to detect the adopting
repo's formatter. That did not ship: the test harness checks the generated
blocks against this repo's `dprint.json` only.

## Consequences

- Good, because the obligation count sits beside the question count in a file
  people read, and the gate keeps it true.
- Good, because a track agent gates on `validate --track <theirs>` and cannot
  be failed by another track's activity.
- Good, because two agents colliding on one track get an ordinary git merge
  conflict on the ledger block, with no locking machinery.
- Bad, because adding a record without running `write-ledger` fails the gate,
  which is friction by design.
- Bad, because any change to the ledger format makes every existing consumer
  tree stale at once, which is why later additions went to `status` instead.
- Bad, because a repo whose formatter does rewrite bullet lists has no
  detection to warn it.

## Revisit when

- An adopter's formatter rewrites the generated blocks.
- A history-reading `trend` verb is built
  ([deferred, D](2026-08-14-research-spike-lean-b-d-e-deferred.md)), which
  may change what the stored lines need to carry.
- The roll-up's organizer-only ownership becomes the bottleneck in a project
  with many tracks.

## Confirmation

`validate` enforces freshness. `scripts/test-research-spike.sh` covers a stale
ledger failing and `write-ledger` repairing it, a stale foreign track not
failing `--track`, the `LEDGER.md` warn/strict split, and `dprint check`
against the generated blocks (skipped, with a message, when `dprint` is absent
or cannot resolve its plugins).

## Alternatives

- **Compute on demand only:** the failure the instrument exists to fix.
- **`validate` auto-repairs a stale ledger:** the number would be generated
  rather than checked.
- **One shared project ledger:** every track PR would fail on a file it is not
  allowed to touch.
