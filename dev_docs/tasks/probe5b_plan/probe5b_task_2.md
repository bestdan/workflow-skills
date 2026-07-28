---
title: Build the disposable runaway fixture harness
priority: high
size: 3
status: new
created: 2026-07-27
expires: 2026-08-26
source_branch: bestdan/autopilot-e-lite-design
parent: probe5b
is_blocked_by: probe5b_task_1
related_files:
  - dev_docs/elite-spike/fixtures/runaway/
  - scripts/spawn-orchestrator.sh:1693
  - scripts/test-spawn-orchestrator.sh:4893
tags: [spike, probe5b, fixture]
---

Plan: [[probe5b_plan]]

## Context

The probe drives the **real** supervisor path — `spawn-orchestrator.sh`'s
`supervisor-scan`, `supervisor-gate`, `supervisor-check`, `alarm` — against a
scratch run directory. Only the agent is a surrogate. Testing a reimplementation
of the supervisor would prove nothing (§7a rule 2: exercise the assumption
through the real boundary).

`scripts/test-spawn-orchestrator.sh` already fakes `claude` with canned
stream-json and fakes `claude-usage.sh` with canned session JSON
(`:4893`) — reuse those shapes rather than inventing new ones. The fixture is
disposable and is **never** promoted into production by renaming (§0a rule 4).

### Containment rules — non-negotiable, and why

Probe 5's incident record (`dev_docs/tasks/probe5-incident-evidence/`) is a
supervisor bootstrapped in uid mode against the **maintainer's own** uid, which
reaped every SSH login for four days. Probe 5b spawns processes and asserts they
get reaped: the exact same hazard class. So:

- **Unprivileged only.** No `sudo`, no root-owned helpers, no sudoers fragment.
  Probe 5's teardown removed all of that and it stays removed.
- **No launchd bootstrap into a real uid domain.** Wakes are driven by the
  fixture's own loop, not by `launchctl`.
- **Every surrogate runs in a process group the fixture created**, and the
  fixture only ever signals **that** group. Fail closed at construction if the
  group is not the fixture's own. **"The fixture's own group" is not enough on
  its own** — a child spawned without `setpgid`/`setsid` inherits the *driver's*
  group, and recording that pgid satisfies a naive check while `kill -- -$pgid`
  takes out the driver, the invoking shell, and the SSH session (Probe 3's exact
  bug). Assert both halves before any signal:
  `pgid(surrogate) != pgid(driver)` **and** `pgid(surrogate) == pid(surrogate)`,
  i.e. the surrogate is its own group leader.
- **Re-validate identity immediately before every signal.** A saved pgid names a
  slot, not a process; once the fixture's group dies the number can be reused.
  Probe 2's `p_uniqueid` reader (`fixtures/process-binding/incarnation.py`)
  transfers here — the cross-uid `EPERM` that forced Probe 2 to run privileged
  does not apply, because the fixture and its surrogates share one uid. Assert
  `same_incarnation(recorded, live)` immediately before each `kill -- -$pgid`;
  on mismatch or `ESRCH`, **skip the signal** and record
  `escaped` / `already-dead`.
- **Never resolve a uid numerically.** If the fixture needs the `agent` account
  at all, resolve it **by name** (its uid differs per host; 502 on the mini).
  The `agent` account stays — nothing here deletes or recreates it.
- **An armed injection point with no recorded row is a hard error**, per Probe
  5's construction rule.
- **Every signal call validates its target pgid** is non-empty, numeric,
  positive, and equal to the group the fixture created — failing closed
  otherwise. An empty variable in `kill -- -$pgid` signals the caller's own
  group, which is the outage's exact symptom shape.
- **Unique random launchd label per run, asserted absent from `launchctl list`
  before use.** `spawn-orchestrator.sh`'s `teardown` runs `launchctl bootout
  gui/<uid>/<label>` unconditionally whenever `launchctl` exists (`:1693`) — a
  colliding label would boot out a live job the fixture never created.
- **The deadline does not depend on a healthy driver.** "The driver enforces its
  own absolute deadline and self-terminates" is too weak: a crashed or wedged
  driver enforces nothing, and the surrogate below is deliberately long-lived and
  backgrounded so that it does not cooperate. Two independent mechanisms, both
  asserted: a **parent-death channel** (the driver holds the write end of a pipe;
  every surrogate and worker holds the read end and exits on EOF) **and** a
  **watchdog** — a separate process in its own group, spawned first, that sleeps
  to the absolute deadline and then signals the surrogate group through the same
  validated path. Predicate: `kill -9` the driver mid-run and every surrogate and
  worker exits within N seconds. The fixture that probes runaways must not be
  able to become one.
- **Escaped descendants are detected and reported, never chased.** Record every
  spawned PID with its start identity; at teardown, report any tracked PID that
  left the process group rather than widening what the fixture is willing to
  signal. Sweeping by name or heuristic is how this repo reaped every SSH login
  for four days. The `setsid`/double-fork escapee is a **documented scope
  limit**, not a target.

Do not touch `dev_docs/tasks/probe5-incident-evidence/` — it outlives the spike.

## Task

Create `dev_docs/elite-spike/fixtures/runaway/`:

- `runaway.py` — the surrogate agent. Configurable per leg: keeps `RUN.md`
  healthy (heartbeat, `status: running`), and optionally burns wall-clock,
  rewrites `paused_until`, forks workers, or attempts a merge.

  **It runs as a long-lived background process, not as a per-wake callback.**
  A surrogate invoked once per wake and returning is *cooperating* — it yields
  to the supervisor on schedule, which is the opposite of the thing under test.
  It also makes leg 2's far-future variant inexpressible: that forgery closes
  `supervisor-gate`, and on a per-wake topology the surrogate is never invoked
  on a gate-closed wake, so it cannot keep rewriting `paused_until` and spawning
  workers *behind* the pause. Start the surrogate before the first wake, in the
  fixture's own process group; the driver only fires supervisor wakes at it and
  observes.
- `driver.sh` — creates the scratch rundir + run-state branch, renders the
  supervisor's own state via `spawn-orchestrator.sh`, starts the surrogate, then
  loops wakes: `supervisor-scan` → gate → `supervisor-check`, recording state
  after each. Enforces the containment rules above and fails closed at
  construction.

  **Render the real sandbox profile** with `render-profile --workdir <scratch
  rundir>` and run the surrogate under it. This is what makes the pause-exempt
  ledger's authority *tested* rather than assumed — the profile emits the
  `supervisor-state` write-deny (`spawn-orchestrator.sh:880`), and without it
  leg 2 measures the ledger's arithmetic while its enforcement sits unexercised.
  It is also the only layer that can refuse leg 4. If the profile cannot be
  applied inside the time box, say so in the kill sheet and cap the affected
  legs at `inconclusive — boundary not in force`; do not run them and report a
  pass.

  **Pin an interpreter that can actually exec under that profile, and smoke-test
  it before any leg.** `/usr/bin/python3` re-execs into
  `CommandLineTools/Library/Frameworks/Python3.framework/...`, and Seatbelt
  matches the **resolved** path (the renderer documents this defect class at
  `spawn-orchestrator.sh:828`–`:840`), which falls outside the granted
  `CommandLineTools/usr` subpath — so a Python surrogate under this profile is
  refused at exec. Use a Homebrew build (it resolves into the granted
  `/opt/homebrew/Cellar`) and assert the exec succeeds under the rendered
  profile as a pre-leg check. Otherwise this construction-time wall consumes the
  time box and yields `inconclusive — boundary not in force` for a reason that
  is neither the boundary nor the fixture logic.
- `common.py` — shared evidence emitter: append-only JSONL per wake, sha256 of
  every fixture file, fixture git revision, non-secret environment metadata.
- `scenarios.py` — leg registry with one entry per armed injection, so a leg
  that produces no row is a hard error.

Legs themselves land in tasks 3–5; this task delivers the harness plus one
**smoke leg** — the false-positive floor Probe 3 made load-bearing. A quiescent
healthy agent is too weak a floor on its own: it shows the harness doesn't fire
on nothing, not that it declines to fire on things that *resemble* a runaway. So
the smoke leg carries two **near-misses** as well: a short legitimate pause well
inside `--pause-exempt-max` that must NOT trip the ledger (this also exercises
the ledger's `exempt_since` clear path, which no other leg touches), and a run
that completes just before `--until` that must NOT trip the deadline halt.

## Acceptance Criteria

**Code-enforced:**

- `driver.sh` refuses to run — non-zero, with a named reason — when invoked with
  privilege, when the process group is not its own, or when any uid is given
  numerically.
- The smoke leg runs N wakes against a healthy surrogate and produces **zero**
  halts, zero alarms, and an empty `ALARM` sentinel path — and both near-misses
  likewise halt nothing.
- Every fixture file's sha256 and the fixture git revision appear in the
  emitted JSONL.
- Evidence metadata is an **explicit allowlist of field names**, not a denylist
  of token substrings — grepping for `Authorization`/`Bearer`/`accessToken`
  cannot establish that no credential was emitted. Assert that no environment
  *value* and no credential path is copied into evidence.
- No real `claude` process is launched and no network is reachable during a leg
  run — enforced by a mechanism (a no-network run, or a stub resolver that
  hard-errors), not asserted.
- `scripts/check.sh` passes.

**User-run:**

- After the smoke leg, `ps` shows zero surviving surrogate processes and the
  scratch rundir is the only thing the fixture wrote outside its own tree.
