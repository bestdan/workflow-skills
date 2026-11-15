---
created: 2026-10-18
status: accepted
convention: ../reviewer-quality-score-design.md
---

# The ledger sync copies `repo` as-is

## Context

The cross-machine flush copies only an allowlist of ledger fields to the shared
repo, because git history is permanent and a leaked field costs a history
rewrite to take back. It leaves behind every field that carries code, prose or
a commit from a private repo. `repo` is the exception worth arguing about: it
names the repo a run reviewed, including work repos. It is also the only field
that lets `co-review-stats.py --repo` ask whether a reviewer does well on one
codebase and badly on another. The owner made the call on 2026-10-18, before
[PR #1057](https://github.com/bestdan/workflow-skills/pull/1057) built the
flush.

## Decision

`repo` is in `RUN_FIELDS` and reaches the shared repo unchanged. The shared repo
is private to its owner, so the names are visible only to someone who can
already see every machine's ledger, and copying them keeps `--repo` working on
the merged data. `host` is the other identifying field kept, since it names the
file and, with `run_id`, the run.

## Consequences

- Good, because `--repo` filters the merged table the same way it filters the
  local one.
- Bad, because work repo names now sit in the shared repo's history, and taking
  them out would need a history rewrite plus removing the rows from every local
  ledger first, or the next flush pushes them back.

## Revisit when

- The shared repo gains a reader who should not see work repo names.
- Nobody uses `--repo` on merged data, so the field earns nothing for its risk.

## Confirmation

`test/co-review-ledger-sync.bats` checks that a run with every field set
reaches the shared file with exactly the allowlisted fields, `repo` among them.

## Alternatives

- **A salted hash of `repo`:** rejected, because the salt would itself need
  syncing across machines.
- **Drop the field:** rejected, because `--repo` would then see only this
  machine's rows.
