---
title: "Classify Probe 5b and record the outcome and redirect in §7a row 5b"
priority: high
size: 2
status: new
created: 2026-07-27
expires: 2026-08-26
source_branch: bestdan/autopilot-e-lite-design
parent: probe5b
is_blocked_by: [probe5b_task_3, probe5b_task_4, probe5b_task_5]
related_files:
  - dev_docs/elite-spike/fixtures/runaway/results.json
  - dev_docs/elite-spike/fixtures/runaway/probe5b-runaway.md
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md:810
tags: [spike, probe5b, evidence]
---

Plan: [[probe5b_plan]]

## Context

**Row 5b is already classified.** It was falsified by the inventory on
2026-07-28, the redirect was taken, and priority 6 was stopped — all before any
leg ran. So this task is **not** "classify the probe"; it is **record the
measurement under the already-taken classification**. Nothing here can return
row 5b to `confirmed`, and a task that finds itself arguing otherwise has
drifted into the repair rule 5 forbids.

§7a rule 3 still governs the measurement: at the cap, each leg is `confirmed` /
`falsified` / `inconclusive`, and "nearly done" is not a fourth state.

Expected shape, given the inventory: leg 2 establishes the ledger's authority
(`LedgerWrite` → **`EPERM`** under the rendered profile, with its control write
succeeding — `EACCES` belongs to the filesystem substitute, not this boundary),
leg 1's forgery variants falsify the blown-`--until` halt with `done-forgery`
destroying supervision outright, leg 3 falsifies the parallelism family and
yields the number that sizes the ceiling, leg 4 is partial with the token
boundary blocked. The usage and
continuation families carry `falsified — no enforcement exists; not exercised`
rows with no leg.

## Task

- Write `dev_docs/elite-spike/fixtures/runaway/results.json`: one row per armed
  leg, each carrying fixture revision, sha256 per file, verdict, measured damage
  bound, and the halt condition id. Plus the two **no-leg** rows —
  `falsified — no enforcement exists; not exercised` for the usage and
  continuation families — and the `blocked` token-boundary row. No secrets.
- Update `probe5b-runaway.md` with the outcome per leg, using Probe 5's framing:
  state explicitly what a per-leg CONFIRMED does **not** cover (unjailed
  execution where the profile could not be applied, no privileged domain, no real
  model calls, no token boundary, per-machine evidence that does not transfer to
  another host) — and that no leg outcome bears on row 5b's already-recorded
  falsification.
- **Verify, don't re-write, the design doc.** §7a row 5b, Risk #1, Decision #5
  and row 6's stop marker were updated on 2026-07-28. Confirm they still match
  the evidence and correct them only if a leg contradicts them; do not restate
  the classification.
- Record the measured ceiling inputs the redirect needs: `halt-bounded` worker
  count, survivors after teardown, and wall-clock overshoot past `--until`.

## Acceptance Criteria

**Code-enforced:**

- `results.json` has exactly one row per armed leg; an armed leg with no row is
  a hard error. **`inconclusive — not run at cap` is a valid row**: rule 3
  requires classification at the cap, so an unfinished leg is recorded, not
  treated as a missing row.
- The roll-up from row verdicts (`confirmed` / `falsified` / `inconclusive` /
  `error` / `blocked`) to rule 3's three probe classifications ships **as a
  script** and is asserted total over every combination by enumeration — the
  same move as the re-hash check. An `error` row never yields `confirmed`.
- The sha256 manifest covers the fixture and evidence files **excluding
  `results.json` itself** — a file cannot contain its own digest. Ship the
  re-hash check as a script in the fixture rather than leaving "verify by
  re-hashing" as an instruction. Do **not** run dprint or shfmt over
  `dev_docs/elite-spike/fixtures/`; that tree is deliberately excluded and
  formatting it breaks the evidence chain.
- `results.json` carries the two no-leg family rows and the `blocked`
  token-boundary row, not only the run legs.
- §7a row 5b still reads `FALSIFIED` with its redirect; row 6 still records the
  stop.
- `scripts/check.sh` passes.

**User-run:**

- The maintainer agrees the per-leg verdicts match the evidence, and that
  nothing was upgraded from `falsified` to `confirmed` by narrowing what was
  claimed to be tested.
