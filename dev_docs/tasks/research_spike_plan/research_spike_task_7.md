---
title: "research-spike: the ledgers — derive, store, scoped freshness, --strict tier"
priority: high
size: 5
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - scripts/research-spike.py
  - scripts/test-research-spike.sh
  - dprint.json
is_blocked_by: research_spike_task_6
parent: research_spike
expires: 2026-08-31
tags: [research-spike, script, ledger]
---

[[research_spike_plan]]

## Context

The ledger is the number that was missing. It is **stored**, not computed on
demand, because a number you must remember to run reproduces the original
failure — invisible unless someone thinks to look. The gate's job is to verify
the stored number is not stale.

Two traps the reference implementation paid for:

1. **The generated ledger must survive the repo's formatter.** If `dprint` (or
   prettier) rewrites the generated block, the freshness check and the
   formatter fight forever. Use constructs the formatter leaves alone — a
   bullet list rather than an aligned table — and **verify it explicitly**.
2. **The check will catch its own staleness during development.** That is
   correct behaviour and should be left in place; it is the thing that keeps
   the number honest when someone adds a record and forgets to regenerate.

Stored form, per track, at the top of `questions.md` (so parallel agents never
contend on a shared file):

```markdown
- **Questions:** 8 answered, 4 open, 1 retired
- **Obligations:** 3 discharged, 10 open (2 blocking, 1 stub, 1 external)
```

## Task

Implement `ledger`, `write-ledger`, and freshness validation.

- `ledger` prints the **derived** ledger without writing. `write-ledger`
  rewrites the stored blocks in place. Rewriting is always an **explicit act**:
  `validate` must **never** auto-repair a stale ledger, or the number stops
  being checked and starts being generated.
- Per-track ledgers live between markers at the top of each `questions.md`;
  `LEDGER.md` is the project roll-up — the same lines per track, per-decision
  blocker status, and project totals, reusing task 6's derivation (one
  implementation, not two).
- **Scoped freshness.** A track's stored ledger is checked against **that track
  only**, so a parallel agent fails validation solely for its own forgotten
  `write-ledger` — another track's activity can never fail it. Track agents
  gate on `validate --track <theirs>`.
- **`LEDGER.md` has its own tier.** Staleness is a **warning** in plain
  `validate` — otherwise every track PR would fail on a file it is forbidden to
  touch — and an **error** under `validate --strict`, the organizer's gate, run
  on merge to the mainline. Warning-only everywhere would let the roll-up rot;
  the strict tier is what keeps the number checked rather than decorative.
- **`validate` gets its full scoping surface here** — task 1 built whole-tree
  scanning and this card owns the flags, so per-project scoping belongs to it
  rather than falling through the gap:
  `validate [<project>] [--track <t>] [--strict]`. A bare project name scopes
  the whole gate to one project (records, referential integrity, ledger
  freshness, `LEDGER.md`), mirroring `status <project>`; `--track` narrows it
  further; omitting both scans every project. An unknown project name exits `2`
  listing known projects (task 1's classification). Without this the organizer
  has no way to gate one project on a root the design explicitly allows to hold
  several.
- `write-ledger --track <t>` rewrites one track; without `--track` it rewrites
  every track plus `LEDGER.md`.
- Missing markers in a file that should carry them is an error naming `init`
  (task 2 installs them) — never silently insert them.
- **Concurrency is handled by git, not by new machinery.** One agent at a time
  per track is the convention; if two collide, the stored ledger block produces
  an ordinary git merge conflict, which is the intended detection. Do not add
  hashing or locking.

## Acceptance Criteria

**Code-enforced:**

- New fixtures in `scripts/test-research-spike.sh`:
  - a stale stored track ledger fails `validate`, and `write-ledger` repairs it
    to a passing state;
  - `validate` on a stale ledger leaves the file **byte-identical** (no
    auto-repair);
  - a stale **foreign** track does not fail `validate --track <mine>`;
  - `validate <project>` ignores a violation in a **sibling project**, while
    bare `validate` catches it; an unknown project name exits `2` listing the
    known ones;
  - a stale `LEDGER.md` **warns** under plain `validate` (exit 0) and **fails**
    under `validate --strict` (exit 1);
  - `write-ledger --track t` rewrites only `t`;
  - a `questions.md` missing its markers errors naming `init`;
  - counts are correct across a two-track project: answered/open/retired and
    discharged/open with blocking/stub/external subtotals;
  - **`dprint fmt` over a written ledger produces no diff** — the explicit
    formatter-stability assertion (skip cleanly if `dprint` is absent);
  - round-trip: `write-ledger` then `validate` is clean; mutate one record and
    `validate` fails again.
- `bash scripts/check.sh` green.

**User-run:**

- Add an obligation by hand, run `validate` (must fail on staleness), run
  `write-ledger`, run `validate` again (clean) — and confirm `just fmt` after
  that leaves no diff.
