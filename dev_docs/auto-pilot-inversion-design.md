---
title: "Auto-pilot: invert the authority boundary"
status: draft — for review
created: 2026-07-12
supersedes_approach_in: dev_docs/tasks/autopilot_hardening_plan/
related:
  - dev_docs/auto-pilot-developer-review-feedback.md
  - dev_docs/auto-pilot-design-review-codex.md
---

# Auto-pilot: invert the authority boundary

## The problem, stated once

Fifteen PRs of hardening have produced the same five defect shapes, roughly ten times:

1. **Unreachable guards** — correct logic, a trigger that cannot fire in production (doctor I3's park; `--park-limit` never emitted; the status report's dead reconciliation; the restack diff-audit).
2. **Tests that cannot detect their own bypass** — a guard that counts what it catches cannot see what escaped.
3. **The gate seam** — three merge blockers from code landing below an `exit 0` that had to run above it.
4. **The agent corroborating itself** — `status: paused` "corroborated" by `paused_until`, both written by the agent, same file, same write.
5. **Rules written down but never enforced** — the plan's own indictment, reproduced inside its own fixes.

These are not five bug classes. They are one, seen from five angles:

> **The agent writes the facts that determine whether the agent is healthy, paused, finished, or entitled to run again — and the supervisor tries to reconstruct truth from those claims.**

`RUN.md` is documented as "cache + report; the tracker and Git are authoritative" ([run-state.md](../skills/auto-pilot/references/run-state.md)). It is used as a **privileged control plane**: `status`, `paused_until`, `exit_reason`, PID identity, deadline, and the phase table all directly drive relaunch, teardown, and park decisions. `run-state.md` states the rule outright — _"declaring beats inferring."_

That is the bug. Not a bug **in** the design; the bug **is** the design.

The verify broker makes it explicit. Its own reference concedes the verdict is forgeable:

> _"The sentinel dir lives in the run worktree, which the jailed orchestrator can write — so it could forge its own `<id>.result` (`code: 0`) and skip the broker entirely. This is deliberately out of scope."_

**The agent gates on its own verdict.** Every guard we have written is a detector for one specific lie, bolted onto a substrate where the guarded party authors the truth. That is why we keep shipping guards whose trigger cannot fire: we are enumerating lies instead of requiring evidence.

## The inversion

> **A deterministic controller owns time, graph state, retries, leases, reconciliation, verdicts, and alarms. The model receives one bounded task attempt and a writable worktree. Agent output is a _claim_ that causes no state transition until corroborated by evidence the agent cannot author.**

The founding thesis — _"auto-pilot's failure modes look like success"_ — collapses from a philosophical problem into a single policy:

> **Exit 0 without the externally required effect is no progress.**

Every guard we built is a special case of that one rule.

### This is already happening by accident

The fix for #196 (task 23) independently invented a **supervisor-owned ledger**: `SUPERVISOR_STATE_NAME` under `.auto-pilot/`, with a Seatbelt `(deny file-write* (literal …))` emitted after the RW block, so the jailed agent **cannot write it**. That is the new architecture arriving one field at a time, under pressure, because it was the only thing that worked.

Generalize it deliberately instead of rediscovering it five more times.

## Target architecture

### Two programs, not one gated script

Today a **generated** wrapper hand-maintains an above/below topology (`supervisor-scan`, `heartbeat`, `supervisor-gate` → possible `exit 0`, agent, `supervisor-check`), preserved only by load-bearing comments. Three merge blockers have come from a line landing on the wrong side, twice **without a conflict marker**.

Replace with two executables:

- **`autopilot-watch <run-id>`** — runs on **every** launchd tick, never gated. Owns reconciliation, time, budgets, alarms, state, scheduling. Decides whether an attempt is allowed.
- **`autopilot-attempt <attempt-id>`** — runs **only** when `watch` says so. Invokes the jailed model for **one bounded task attempt** and returns its raw result.

There is no "above the gate" because the watcher is not gated. The generated shell control flow, and the entire class of seam bugs, is **deleted**.

### The state substrate

Supervisor-owned, outside the agent's write authority, atomic:

| Record          | Written by | Contents                                                                                          |
| --------------- | ---------- | ------------------------------------------------------------------------------------------------- |
| **run**         | controller | deadline, pause budget, retry policy, verify command (pinned)                                     |
| **lease**       | controller | task, attempt N, child PID + start time, base SHA, attempt deadline — written **before** dispatch |
| **observation** | controller | branch SHA, PR state, tracker state, verify verdict, child exit code                              |
| **claim**       | agent      | "I completed" / "I paused" / "PR #123" — **never** causes a transition alone                      |

`RUN.md`, `REPORT.md`, `STATUS.md` become **projections** — rendered output, not memory. A human editing them steers nothing.

**Why a lease and not pure Git/PR derivation:** before a push, absence is ambiguous — "no commit" could mean "wedged" or "still working." The lease supplies the one fact Git cannot: _"I started worker X for task T at time C."_ Git, GitHub, the tracker, and the verify verdict then supply the outcome.

### What "progress" means (the load-bearing definition)

An attempt made progress iff **at least one** of these changed, as observed by the controller:

- a new commit on the task branch (local **or** pushed — a long implementation must not read as wedged);
- PR state (opened, updated, review state, mergeable);
- tracker state transition;
- a verify verdict recorded **by the broker**, in a location the agent cannot write.

`exit 0` with **none** of the above = **no progress**, regardless of what the agent claimed. That single rule subsumes the no-progress counter, the pause exemption, the exit contract, and most of the doctor.

## What gets deleted

Measured against `scripts/spawn-orchestrator.sh` (4,503 lines) and `scripts/test-spawn-orchestrator.sh` (3,982 lines).

**Deleted or subsumed by the controller (~1,600–1,800 lines of bash):**

| Function                                  | Lines | Why it goes                                                                                                                                     |
| ----------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `doctor()`                                | 587   | Seven invariants reconciling agent claims against reality. The controller **is** the reconciliation; there is nothing to repair after the fact. |
| `supervisor_check()`                      | 224   | Exit-code classification + no-progress counting → controller policy.                                                                            |
| `write_launch()`                          | 174   | The generated wrapper disappears entirely.                                                                                                      |
| `status()`                                | 164   | Becomes a projection of controller state.                                                                                                       |
| `_supervisor_halt()`                      | 133   | Controller terminal transition.                                                                                                                 |
| `assert_run_head()`                       | 74    | The run worktree stops being a shared mutable HEAD.                                                                                             |
| `exit_reason()` + `clear-exit-state`      | ~70   | The five-value exit vocabulary **ceases to exist**. Context exhaustion is a failed/incomplete attempt, not a declaration.                       |
| `supervisor_gate()` + `supervisor_scan()` | 91    | The seam is gone.                                                                                                                               |
| `_supervisor_alarm_scan()`                | 55    | Controller raises alarms directly.                                                                                                              |
| `heartbeat`, `alarm-request`              | ~40   | Agent-authored liveness is not evidence.                                                                                                        |

Plus the bulk of the test suite's **string-shape assertions against generated wrapper lines** — they test an artifact that no longer exists (est. **1,500–2,000 lines**).

**Kept, essentially unchanged (~600–700 lines of bash).** These are genuine OS/sandbox adapters, deterministic, and well-tested:
`render_profile` (191), `render_network_allowlist` (64), `emit_harness_runtime` (47), `canonicalize` (35), `render_settings` (32), `teardown` (35), `alarm` notifier delivery (75), `check-profile`, `smoke-test`, `detach`, `record-handle`.

**Rewritten elsewhere:** `restack()` (277) → a standalone **post-merge reconciler**, not a supervisor function. Per both reviews, **delete its line-survival diff-audit** — a clean 3-way rebase provably cannot drop a parent's review-added line (proven by construction: either the child touched the region → conflict → restack fails closed, or it didn't → the line survives). The real exposure is the **human hand-resolving** the conflict restack fails closed on. Re-run verification, co-review, and the PR's own acceptance criterion; do not manufacture another unreachable guard.

**New controller (est. 1,200–1,400 lines Python + SQLite):**

| Component                                           | Est. lines |
| --------------------------------------------------- | ---------- |
| Schema + atomic transactions                        | ~100       |
| Tick loop: reconcile → policy → dispatch → record   | ~250       |
| Task graph + readiness (`is_blocked_by` resolution) | ~150       |
| Lease + attempt lifecycle                           | ~150       |
| Observation adapters (git, `gh`, tracker, usage)    | ~300       |
| Policy (progress, pause, retry, deadline, alarm)    | ~200       |
| Projections (`RUN.md` / `REPORT.md` / `STATUS.md`)  | ~200       |

**Net:** roughly flat in total lines, but concentrated where correctness actually lives, in a typed language with atomic persistence — instead of Markdown parsing in shell.

Python + SQLite is chosen because the repo **already has Python infra** (`scripts/validate.py`, `scripts/bump-version.py`, `uv`), and because atomic multi-record transactions are the thing shell cannot do. It is not chosen because bash is ugly. **Bash is not what causes these bugs — authority is.**

## The test strategy has to change too

Six hundred shell assertions stayed green through every one of the ten defects. They prove **components**, not the **composed, generated program**.

Replace with a **scenario runner** driving the **real controller** with: real Git repos and worktrees, an **injected clock**, a fake `claude` executable, and **explicit fake adapters** for GitHub / tracker / launchd / notifier — _not_ ambient `PATH` shadowing (which is the mechanism that could not observe its own bypass).

The scenario set that would have caught our actual defect history:

1. Worker exits `0` repeatedly with no external effect → halt + alarm.
2. Valid pause / missing bound / expired pause / far-future pause → correct spawn count, bounded exemption.
3. Crash after **every** controller transaction → deterministic reconciliation; no duplicate PR; no live-worktree deletion.
4. GitHub `NOT_FOUND` vs transient 401/rate-limit → distinct outcomes, against a boundary contract matching **real** `gh` (measured: rc=1, `Could not resolve to a PullRequest`).
5. Parent merge → verify + co-review actually re-run before renewed hand-off.
6. Teardown partial failure → never "job gone, no terminal record, no alarm."
7. **One macOS canary**: real launchd, real Seatbelt, fake model, end-to-end on the actual detached artifact.

Keep: the sandbox-profile renderer tests, the jail smoke, and **one opt-in contract probe against real `gh`**.

## Staging (this is not a big-bang rewrite)

A single rewrite of 4,500 lines, mid-flight, with open PRs, trades a known-broken system for an unknown-broken one. Stage it so **every stage is independently valuable and independently shippable**.

**Stage 0 — stop digging (now).**
Land #199. Drop task 21's diff-audit. Freeze tasks 17/20/21/22/28/29 — they are guards on the substrate we are replacing.

**Stage 1 — supervisor-owned progress evidence.** _(highest value / lowest cost; doable in the current bash)_
Add the lease + the external-effect progress rule. Delete the pause exemption's self-corroboration. This alone subsumes the no-progress guard, the pause exemption, and the exit contract — the source of findings #22, #26, and task 23.

**Stage 2 — make the verdict unforgeable.**
Move `supervisor-state` and the **verify result path** _out of the run worktree entirely_ (see hazard below), so no `deny` rule is load-bearing.

**Stage 3 — split `watch` / `attempt`.**
Delete the generated wrapper and the gate seam as a category.

**Stage 4 — controller owns the graph.**
The agent becomes a one-task worker. Python + SQLite. This is the big one.

**Stage 5 — delete.**
`doctor`, `status`, `exit-reason`, the restack guard. Re-render as projections.

## Where this will hurt — the honest list

1. **The agent's contract changes fundamentally.** Today `claude -p` loops over _many_ tasks in one wake, reusing context. "One bounded attempt per task" means more process spawns and more cold starts — **a real token and latency cost**. It buys atomic, observable attempts. This trade needs a decision, not an assumption. It also means `SKILL.md` and `/deliver-task` shrink dramatically: the phase machine moves **out** of the agent's instructions and into the controller. That is a large doc rewrite and the most likely place to lose behavior silently.

2. **`supervisor-state` currently lives inside git-tracked `.auto-pilot/`.** It is protected by a Seatbelt `deny file-write*`, which works — but it sits in a directory the agent runs `git add -A` over. A `git checkout` / `reset` / `clean -xfd` touching that path hits the deny and **fails the git operation**, in a way that will be baffling to debug. Move supervisor state **outside the run root**. Flagged as a live hazard in the current code, not just the future design.

3. **Controller-owned task readiness needs a real dependency resolver.** Today the agent reads the plan and decides what's next. The controller must parse `is_blocked_by` across task front matter deterministically. Tractable (the front matter exists) but it is new, and it is exactly the kind of thing that acquires an unreachable guard if we are not careful.

4. **"Progress" has edge cases.** A legitimate long implementation attempt may produce no commit _within one attempt_. Mitigated by counting **local** commits and by attempt deadlines rather than instantaneous progress — but the threshold is a judgment call and must be scenario-tested, not reasoned about.

5. **Crash windows move, they do not vanish.** Lease-written-but-child-not-spawned; child-exited-but-observation-not-recorded. SQLite transactions make these tractable where the current write-order discipline cannot — but scenario 3 above (crash after _every_ transaction) is mandatory, not optional.

6. **Migration with five open PRs.** Stages 1–2 are compatible with the current bash and can land incrementally. Stage 3 invalidates most wrapper tests at once. Sequence so the deletion of the test suite coincides with the arrival of the scenario runner, or we will have a window with no safety net.

7. **We may be wrong about detachment.** Codex argues foreground should be the default and `--detach` an explicit deployment mode — which aligns with task 19's finding that the _partially attended_ human is the common case, not the sleeping one. This is worth deciding **before** Stage 3, because it changes how much of the launchd/TCC/jail complexity we must carry at all.

## The one-line test of whether this worked

> After the inversion, can a wedged agent that exits 0 forever, writes `status: paused` on every wake, forges a `code: 0` verify result, and never pushes a commit — **still be believed by the controller?**

Today the answer is yes, in four different ways. If the answer is not a flat **no**, we have not inverted anything.
