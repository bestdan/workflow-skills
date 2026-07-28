---
title: "Leg 3 — worker-spawn storm (designed to falsify)"
priority: medium
size: 2
status: done
created: 2026-07-27
expires: 2026-08-26
source_branch: bestdan/autopilot-e-lite-design
parent: probe5b
is_blocked_by: probe5b_task_2
related_files:
  - dev_docs/elite-spike/fixtures/runaway/scenarios.py
  - skills/auto-pilot/references/run-budget.md:471
tags: [spike, probe5b, injection]
---

Plan: [[probe5b_plan]]

## Context

The inventory found **no parallelism cap anywhere in code** — no numeric cap, no
semaphore, no live dispatch counter. Risk #1 names one and Decision #1 calls it
a client-side substitute for the absent server-side spend cap; neither exists.
The nearest artifact, the paid-agent dispatch cap, is agent-side prose and is
about cost rather than concurrency.

So this leg is expected to falsify. It is kept anyway because the redirect —
"add a hard control-plane ceiling" — needs a **number**: how many workers a
runaway reaches before anything stops it, and whether the halt that eventually
arrives (from leg 1's deadline or leg 2's ledger) reaps them or leaves them
running. A ceiling proposed without that measurement is a guess.

If open question (B) is resolved as "cut", this task is dropped and the plan's
overview records why.

## Task

Add `Storm` to `scenarios.py`: the surrogate forks surrogate workers in a tight
loop while keeping `RUN.md` healthy, with a hard fixture-side ceiling on worker
count so the probe cannot itself become the runaway. Workers are cheap sleepers
in the fixture's own process group.

Record per wake: live worker count, whether any supervisor component observed
the count at all, and — critically — the worker count **immediately after** the
eventual halt and teardown, i.e. how many survived. Probe 5 established reap
convergence for its own topology; this leg asks whether that convergence covers
processes a runaway created after the supervisor last looked.

**Record one of three outcomes, not two.** The naive two-state version is
unfalsifiable: the fixture imposes its own worker ceiling, so "nothing bounded
the count" is trivially true because the *fixture* bounded it first, and the
recorded number is an artifact of the test apparatus rather than a property of
the system.

- `breaker-bounded` — something on the `supervisor_scan` call graph **bounded**
  the count. Not "observed": observation is not a bound, and a pass threshold
  that accepts observation lets a reported-but-unbounded storm read as
  contained. **Statically unreachable at this revision** — the only
  `ps`/`pgrep`/`proc_pidinfo` call sites in `spawn-orchestrator.sh` are `:1584`
  (launch) and `:5178` (status), so nothing on that call graph (`:3019`–`:3133`)
  reads the process table at all. Recorded as a live branch only if that
  inventory turns out to be wrong, which would itself be the headline result.
- `halt-bounded` — leg 1's deadline halt or leg 2's ledger halt arrived first.
  **This is the number that sizes the redirect.**
- `fixture-bounded / CENSORED` — the fixture ceiling hit first, so no real
  bound was measured. Not a result about the system.

**The family verdict and the measurement are separate records.** The verdict
("no out-of-process component bounds the worker count") is decidable by the
inventory predicate above, before the leg runs and independent of any race; the
measurement is the number. A `CENSORED` run censors the **number only** — it
never returns the verdict to inconclusive. Collapsing the two is what let one
outcome satisfy `falsified` and `inconclusive` simultaneously.

Tune for the useful case: set the fixture ceiling high and the `--until` /
`--pause-exempt-max` caps low, so a real halt is expected to win the race. Do
**not** remove the fixture ceiling to get a cleaner number — this repo's fixture
became the runaway once already.

**Assert the margin at construction rather than discovering it at write-up.**
State the predicted time-to-real-halt (`until_delta`, or `pause_exempt_cap`) and
the predicted time-to-ceiling (`ceiling ÷ measured_spawn_rate`) before running,
record both in `results.json`, and assert

```
time_to_halt × 5  ≤  time_to_ceiling
```

A failing assertion means the tuning provably cannot let the real halt win — the
run would be `CENSORED` before it starts, caught by arithmetic instead of by
burning the leg. This is also the rule-6 discriminator for any repeat: lowering
the fixture ceiling proportionally with `--until` preserves the ratio and
therefore the race, so it names no new evidence; restating the margin with the
*measured* spawn rate from the censored run does.

Classify honestly: "no breaker observed the worker count" is the expected result
and is a **falsification**, not an inconclusive.

## Acceptance Criteria

**Code-enforced:**

- The fixture's own worker ceiling is enforced and its value is recorded.
- Evidence records live worker count per wake, the count at halt, and survivors
  after teardown.
- The row carries `family_verdict` and `measurement` as **separate fields**. The
  measurement is one of `breaker-bounded` / `halt-bounded` /
  `fixture-bounded (CENSORED)`; a `CENSORED` run is **not** reported as a
  measurement of the system, and does **not** change the family verdict.
- The leg's recorded family verdict is `falsified` when no out-of-process
  component bounded the worker count — not softened to `inconclusive`, and not
  satisfied by a component that merely observed the count.
- The margin assertion's inputs and result are recorded, and a run that fails it
  is not started.
- `scripts/check.sh` passes.

**User-run:**

- After the leg, `ps` shows zero surviving workers; if any survive, that is
  itself the headline result and is recorded as such rather than cleaned up
  silently.
