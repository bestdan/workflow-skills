---
title: "Leg 3 — worker-spawn storm (designed to falsify)"
priority: medium
size: 2
status: new
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

- `breaker-bounded` — something in `supervisor-scan` observed the count.
- `halt-bounded` — leg 1's deadline halt or leg 2's ledger halt arrived first.
  **This is the number that sizes the redirect.**
- `fixture-bounded / CENSORED` — the fixture ceiling hit first, so no real
  bound was measured. Not a result about the system.

Tune for the useful case: set the fixture ceiling high and the `--until` /
`--pause-exempt-max` caps low, so a real halt is expected to win the race. Do
**not** remove the fixture ceiling to get a cleaner number — this repo's fixture
became the runaway once already.

Classify honestly: "no breaker observed the worker count" is the expected result
and is a **falsification**, not an inconclusive.

## Acceptance Criteria

**Code-enforced:**

- The fixture's own worker ceiling is enforced and its value is recorded.
- Evidence records live worker count per wake, the count at halt, and survivors
  after teardown.
- The recorded outcome is one of `breaker-bounded` / `halt-bounded` /
  `fixture-bounded (CENSORED)`; a `CENSORED` run is **not** reported as a
  measurement of the system.
- The leg's recorded verdict is `falsified` when no out-of-process component
  bounded the worker count — not softened to `inconclusive`.
- `scripts/check.sh` passes.

**User-run:**

- After the leg, `ps` shows zero surviving workers; if any survive, that is
  itself the headline result and is recorded as such rather than cleaned up
  silently.
