---
created: 2026-10-18
status: accepted
convention: ../reviewer-quality-score-design.md
---

# Co-review ledgers sync to a private repo of their own

## Context

Each machine keeps its own co-review ledger, and `/co-review-stats` should
count every machine's runs. The flush that copies rows off a machine,
`scripts/co-review-ledger-sync.py`, treats its clone as disposable: it moves
the branch only with `git reset --hard`, and deletes and reclones the clone on
any failure. Those operations are safe only on a repo that holds nothing else.
The owner chose the destination on 2026-10-18, in the design
`dev_docs/designs/2026-10-18-co-review-ledger-sync.md` (deleted on
graduation), which preceded [PR #1057](https://github.com/bestdan/workflow-skills/pull/1057), which built
the flush.

## Decision

The shared ledgers live in a new private repo, `bestdan/co-review-ledgers`,
one file per machine at `ledgers/<host>.jsonl` on `main`.
`CO_REVIEW_LEDGER_REMOTE` names it, in the synced Claude settings' `env` block.

As shipped, the value set there is the HTTPS URL
(`https://github.com/bestdan/co-review-ledgers.git`), not the `git@` form the
design showed. The script accepts either, and treats a clone whose
`remote.origin.url` differs from the variable as unusable and reclones it.

## Consequences

- Good, because the flush's resets and reclones can never act on anything but
  ledger files.
- Good, because the repo's access list is the ledger's access list, and nothing
  else widens it.
- Bad, because every new machine needs read and write access to one more repo,
  and a machine without it reports `unreachable` on each flush.

## Revisit when

- The ledgers need to live somewhere other than GitHub, or with an owner other
  than `bestdan`.
- Another kind of synced data wants the same treatment and a shared repo would
  be simpler than a second one.

## Confirmation

`test/co-review-ledger-sync.bats` runs the flush against a local bare repo and
covers the reclone of a clone pointed at another remote. Nothing checks that
the real repo stays private; that rests on the owner.

## Alternatives

None was recorded when the choice was made.
