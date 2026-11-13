---
created: 2026-11-13
status: accepted
convention: ../reviewer-quality-score-design.md
---

# The ledger sync leaves out cloud sessions

## Context

A cloud session's co-review ledger is lost when its container is. Saving those
rows through the same flush depends on three conditions nobody had checked when
the sync was built: that the plugin is installed in the cloud session, that
`CO_REVIEW_LEDGER_REMOTE` reaches it (cloud sessions do not read the synced
settings' `env` block), and that the container can push to the shared repo.
The owner scoped them out on 2026-10-18, before
[PR #1057](https://github.com/bestdan/workflow-skills/pull/1057) built the
flush for local machines.

## Decision

The sync covers local machines only. A cloud session's rows stay lost, as they
were before the sync existed. Checking the three conditions is
[issue #1048](https://github.com/bestdan/workflow-skills/issues/1048), still
open on 2026-11-13.

## Consequences

- Good, because the sync shipped without waiting on facts about an environment
  it could not test.
- Bad, because the table undercounts every reviewer by the runs made in cloud
  sessions, and that undercount is invisible in the table itself.

## Revisit when

- Issue #1048 finds that all three conditions hold, or that one of them can be
  arranged.
- Cloud sessions become a large share of co-review runs.

## Confirmation

Nothing. With the variable unset, the flush prints `ledger sync: off` and does
nothing, so a cloud session simply does not sync.

## Alternatives

None was recorded when the choice was made.
