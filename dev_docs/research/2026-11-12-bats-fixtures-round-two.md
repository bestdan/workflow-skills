---
created: 2026-11-12
question: "Does applying #1505's two moves to the next two heaviest bats files shorten the gate on the work laptop?"
feeds: "https://github.com/bestdan/workflow-skills/issues/1509"
---

# Fewer processes in the next two bats files (2026-11-12)

**Building fixtures once in `test/worktree-remove.bats` and
`test/co-review-ledger-sync.bats` cut about 240 processes and 4.4 CPU-seconds
from the two files, but the full gate cannot see it: the saving is under 1% of
the gate's CPU, and an interleaved A/B was all noise.** Most of what the two
files still start is the script under test doing its own work.

All measurements are from the 18-core work laptop, unsandboxed, for #1509,
which follows #1505 ([the first round](2026-11-12-bats-process-starts.md)).

## Where the processes went

Counted by putting logging shims for `git` and `python3` ahead of `PATH` and
recording each call's parent:

- **`co-review-ledger-sync`**: 817 `git` and 67 `python3` calls. 730 of the
  `git` calls came from `scripts/co-review-ledger-sync.py` itself, about 12
  per flush across 58 flushes. The tests' own share was about 90, mostly one
  `git init --bare` per test, plus the `reference()` helper, which runs a whole
  extra flush to produce the expected file.
- **`worktree-remove`**: 468 `git` calls. Each test's `setup` rebuilt the same
  fixture repo with about five, and five tests rebuilt an eight-call submodule
  fixture.

Neither file builds hook payloads, so the second #1505 move (bash
`json_string` in place of `python3`) had nothing to replace.

## The change

- `worktree-remove`: `setup_file` builds the fixture repo and the submodule
  fixture once; each test copies them with `cp -R`.
- `co-review-ledger-sync`: `setup_file` builds the empty bare remote once and
  each test copies it. `reference()` keeps each result for the file, keyed by
  the ledger's bytes and the host, because seven tests compare against one of
  two ledgers.

All 30 and 32 tests still pass; none was dropped or changed.

| file                    | processes before → after | solo CPU (user + sys) |
| ----------------------- | ------------------------ | --------------------- |
| `worktree-remove`       | 468 → 320                | 13.3 → 11.4 CPU-s     |
| `co-review-ledger-sync` | 884 → 795                | 17.9 → 15.4 CPU-s     |

## The full gate

`scripts/check.sh --no-cache`, base (`main` at `0f546b4f`) and branch
interleaved, three passes each:

| pass | base            | branch                            |
| ---- | --------------- | --------------------------------- |
| 1    | 186s, 570 CPU-s | 222s, 601 CPU-s                   |
| 2    | 216s, 607 CPU-s | 210s, 610 CPU-s, one check failed |
| 3    | 215s, 620 CPU-s | 194s, 586 CPU-s                   |

The spread within each arm (about 30s, 50 CPU-s) is ten times the expected
saving, and the machine was busier than in the first round, where `main` took
about 175s. A rerun of the branch's gate passed; the failing check in pass 2
was not captured, and its cause is unknown. Afterwards both changed files ran
five times each, the two in parallel with each other, and passed every time
(30/30 and 32/32). No conclusion about the gate follows from these runs beyond
"too small to see".

## What it means

The heaviest bats files are now bounded by the scripts they test, as the
orchestrator suite is. Cutting further means making
`co-review-ledger-sync.py` or `worktree-remove.sh` start fewer `git`
processes, which is a change to the product, not to its tests.
