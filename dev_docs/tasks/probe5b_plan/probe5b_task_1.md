---
title: Write the Probe 5b kill sheet and breaker-gap inventory
priority: high
size: 2
status: done
created: 2026-07-27
expires: 2026-08-26
source_branch: bestdan/autopilot-e-lite-design
parent: probe5b
related_files:
  - dev_docs/elite-spike/fixtures/runaway/probe5b-runaway.md
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
  - skills/auto-pilot/references/run-budget.md
  - scripts/spawn-orchestrator.sh
  - scripts/claude-usage.sh
tags: [spike, probe5b, kill-sheet]
---

Plan: [[probe5b_plan]]

## Context

§7a rule 1: before writing the fixture, write the **six** rule-1 elements —
falsifier, pass threshold, `inconclusive` condition, time cap, dependent work,
redirect — plus rule 4's evidence requirements. A result is useful only if it
changes what gets built next. This task is that document, plus the breaker
inventory that reshaped the probe (see the
plan overview's finding table — it belongs in the kill sheet too, because it is
the reason the probe's shape differs from row 5b's literal text).

The kill sheet is the **approval gate**. No fixture code is written until the
maintainer approves it.

Prior kill sheets to match in form: `dev_docs/elite-spike/fixtures/crash-kernel/probe5-crash-kernel.md`
and `dev_docs/elite-spike/fixtures/async-skeleton/probe3-async-skeleton.md`.

## Task

Create `dev_docs/elite-spike/fixtures/runaway/probe5b-runaway.md` containing:

- **The breaker inventory** — the four-row table from the plan overview, with
  file:line citations for each "real enforcement" claim.
- **Assumption under test** (narrowed — row 5b's own assumption is already
  falsified by the inventory, and this kill sheet does not re-litigate it): _the
  two existing out-of-process halting breakers bound a runaway's damage, and the
  measurement sizes the ceiling the redirect must add._ Say explicitly that no
  outcome here can return row 5b to `confirmed`.
- **Mandatory rows for the families that get no leg:**
  `falsified — no enforcement exists; not exercised` for the usage and
  continuation families, each citing the inventory's evidence. A fake invocation
  counter would be fixture-created enforcement, not the real boundary rule 2
  demands, so no leg can rescue them.
- **Falsifier:** a runaway surrogate that keeps `RUN.md` reading healthy (fresh
  heartbeat, `status: running`, gate open) while burning wall-clock past
  `--until` and spawning unbounded workers **survives past the stated damage
  bound**, or reaches a merge.
- **The four legs** with per-leg pass thresholds (see tasks 3–5), including
  **separate expected outcomes for leg 2's two forgery variants** — the
  recent-past variant leaves `supervisor-gate` open, the far-future variant
  closes it, so they exercise different paths and cannot share one threshold.
- **A falsifier and pass threshold per breaker family, not one composite.** The
  single conjunction ("survives past the damage bound, or reaches a merge") is
  incoherent across the legs: leg 3 declares the mere absence of worker-count
  observation a falsification, which no composite threshold expresses. State
  the roll-up rule explicitly — **any falsified family falsifies the probe**,
  and the redirect is taken per family.
- **Inconclusive conditions**, enumerated rather than singular:
  - a halt that depends on an agent-written value the fixture did not
    independently corroborate;
  - **a boundary that was not in force** — the unjailed case task 3 anticipates;
  - **the fixture's own ceiling binding before any real bound** (leg 3);
  - a leg not run at the time cap;
  - missing or truncated evidence.

  Distinct from all of these: a **fixture defect** gets its own `error` verdict.
  Probe 4's driver bug was a false _pass_ on an unrelated non-zero exit; a
  category that quietly absorbs fixture bugs as "inconclusive" is the
  mirror-image failure.
- **What the changed kill sheet would be** if this probe returns inconclusive.
  Rule 6 forbids carrying the same probe across tranches as open work and
  requires a changed kill sheet naming the new evidence or method before any
  repeat — and task 3 already pre-concedes an inconclusive leg-2 if the fixture
  runs unjailed. Name the method now, or the probe is spent on its first run.
- **Evidence required:** `results.json` with per-leg fixture revision,
  wake-by-wake supervisor state, halt condition id, measured damage bound
  (wall-clock past deadline, worker count at halt, survivors after teardown),
  and sha256 per file. No tokens, no credentials, no secret env values (rule 4).
- **Time cap:** half a day, no override requested. The clock covers fixture
  build + legs + classification; the kill sheet and task 7 sit outside it. Record
  the run order — leg 2, leg 1, leg 3, leg 4 — as the degradation order, and that
  unrun legs become `inconclusive — not run at cap`.
- **Dependent work:** priority 6, the thin vertical run — **already stopped** at
  its start line by row 5b's falsification, with "not started" as its durable
  state and no external write to reconcile.
- **Redirect — already taken, not contingent on this measurement:** a hard
  control-plane ceiling — wall-clock, action-count, or live worker count —
  specified in **§5.1 + §4.2**, not in `spawn-orchestrator.sh` (§6:
  delete-do-not-port), with inputs pinned to the three agent-unforgeable sources
  (control-plane wall clock, process table, provider-side usage query). This
  measurement sizes that ceiling; it does not decide whether to build it. If leg
  4 escapes, fix the server-side ruleset (Probe 4) first, not the caps.

State explicitly in the document that **CONFIRMED here would mean only
"not falsified in the tested unprivileged shell environment"** — the ceiling,
not proof — matching the framing Probe 5 adopted.

## Acceptance Criteria

**User-run:**

- The maintainer has read the kill sheet and approved it. No fixture code exists
  before that approval.
- The kill sheet carries the resolved decisions (A)–(D) from the plan overview,
  not drafting assumptions.

**Code-enforced:**

- `dev_docs/elite-spike/fixtures/runaway/probe5b-runaway.md` exists and names
  the six rule-1 elements (falsifier, pass threshold, inconclusive condition,
  time cap, dependent work, redirect) plus rule 4's evidence requirements.
- A falsifier and pass threshold exist **per breaker family**, plus a stated
  roll-up rule; leg 2's two variants have separate expected outcomes.
- Every "real enforcement in repo today" cell cites a real `path:line`; each
  citation resolves.
- `scripts/check.sh` passes. The new file lives under
  `dev_docs/elite-spike/fixtures/`, which is deliberately excluded from dprint
  and shfmt — do **not** "fix" that exclusion (`dprint.json`,
  `scripts/lint-shell.sh`); `results.json` pins sha256s of files in that tree.
