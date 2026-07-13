---
type: epic
title: Port spawn-orchestrator.sh to Python behind an unchanged CLI
status: active
owner: Daniel Egan
created: 2026-07-13
---

# Port `spawn-orchestrator.sh` to Python

## Goal

`scripts/spawn-orchestrator.sh` is **6,284 lines of Bash 3.2** with a **5,292-line** test
harness — together, ~82% of all shell in the repo. Every other script is 137–312 lines and
is fine as shell. This plan ports the orchestrator to Python incrementally, one CLI
subcommand at a time, behind a **byte-identical CLI contract**, using the existing harness
plus a captured golden-output corpus as the safety net.

The pain is not aesthetic. Evidence from the codebase:

- ShellCheck's first pass over the repo (PR #202) found real bugs **only** in this file:
  two `SC2155` defects (`local x="$(cmd)"` silently discards the command's exit status) and
  a file-level `SC2034` disable whose stated reason is *"state arrays are consumed
  indirectly by generated scripts."* A program with indirection and code generation has
  outgrown the language.
- It maintains a deliberate **Bash 3.2 compatibility floor** (macOS system bash), so it has
  **no associative arrays**, no namerefs, no `mapfile`. The source carries the scar tissue
  (`# empty-safe under bash 3.2 set -u`). It manages keyed run state and renders three
  file formats without a hash map.
- It shells out to `jq` 16 times to do JSON work that is one stdlib import in Python.

## Approach

**Strangler fig, not a rewrite.** `spawn-orchestrator.sh` stays the entrypoint and keeps
its 29-subcommand CLI. It becomes a thin dispatcher: subcommands on a `PORTED` list route
to Python, everything else falls through to the existing bash function. Each task ports one
group, deletes the bash it replaces, and leaves the suite green. The file shrinks
monotonically and is never in a broken intermediate state.

Two properties of the existing code make this unusually safe, and the plan leans on both:

1. **The CLI is already the seam.** `scripts/test-spawn-orchestrator.sh` and
   `scripts/smoke-confinement.sh` drive the orchestrator purely through its subcommands
   (`render-profile`, `check-profile`, `render-settings`, `write-launch`, …). They are a
   characterization suite pinned to a contract, not to an implementation.
2. **Most of the Tier A mass is pure.** The renderers are string-in / file-out. We capture
   golden outputs from today's bash **before** touching anything, then require the Python to
   reproduce them **byte-for-byte** (task 3).

### The load-bearing constraint: THREE constrained contexts

> **Corrected 2026-07-13 after co-review (PR #205).** This section originally claimed the only
> constraint was launchd's PATH, and listed `status`, `classify-exit`, `exit-reason`, and
> `doctor` as freely portable. That was **wrong** and would have broken the wake loop.

A subcommand is **constrained** if reachable from a context that cannot freely resolve a Python
interpreter. Three such paths (four entry points — the verify broker is a *second* launchd job,
`:5062`) — the original plan modelled only the first:

1. **The generated launch script → launchd's pinned minimal PATH.** `supervisor-scan →
   heartbeat → supervisor-gate → sandbox-exec claude → supervisor-check`, all under a minimal
   PATH (`write-launch` fail-closes without `--path` for this reason).
2. **In-process bash calls.** `status_report` calls `status` as a shell function
   (`spawn-orchestrator.sh:4233`); `supervisor_check` calls `classify_exit` (`:3195`). These
   never go through the CLI, so the CLI seam does **not** protect them — deleting the bash body
   breaks the caller outright.
3. **The jailed agent → the Seatbelt exec allowlist.** The run-phase agent, inside
   `sandbox-exec`, invokes `doctor` and `assert-run-head` **every loop iteration**,
   `exit-reason` on every termination, `heartbeat` at every sub-step, the
   `verify-request`/`verify-await` handshake on every task's verify, and `alarm-request` on a
   doctor halt (`skills/auto-pilot/SKILL.md`, `skills/deliver-task/SKILL.md:143`). The rendered
   profile carries a `(allow process-exec` allowlist (`:711-724`) — any interpreter used here
   must be **on it**, a constraint distinct from PATH resolvability.

**Traced by task 1** — the authoritative 29-row table is in
[`dev_docs/orchestrator-python-port.md`](../../orchestrator-python-port.md), regenerable with
`scripts/audit-orchestrator-reachability.py`. Do not restate it from memory here; this boundary
has been hand-drawn twice and been wrong twice.

**Tier A (unconstrained): 12 subcommands, 1,458 handler lines.** `write-launch` (332), `restack`
(320), `render-profile` (256), `launch` (142), `write-verify-broker` (130), `render-settings`
(62), `check-profile` (59), `smoke-test`, `clear-exit-state`, `record-handle`, `alarm-clear`,
`detach`. The renderers and generators — still the worst quoting/list-building code in the file.
Tasks 3–7.

**Tier B (constrained): 17 subcommands, 2,618 handler lines.** `doctor` (661, jail),
`status-report` (457, in-process ← launchd), `supervisor-check` (291, launchd), `status` (199,
in-process), `assert-run-head` (153, **jail, every iteration**), `supervisor-scan` (118, launchd),
`alarm` (103, in-process), `supervisor-gate` (91, launchd), `exit-reason` (79, jail),
`classify-exit` (76, in-process), `verify-broker` (74, **the second launchd job**),
`verify-request`/`verify-await` (115, **jail — the verify handshake**), `heartbeat` (63),
`alarm-request` (62, jail), `teardown` (60, **in-process ← the supervisor halt path**),
`report-tick` (16, launchd). All gated on the runtime decision (**task 2**).

The audit moved **eight** subcommands into Tier B that no hand-drawn list had caught — including
`teardown`, which looks like a human cleanup command and is in fact part of the supervisor's halt
path — and moved `alarm-clear` **out** (it is `--resume`-only, attended). The constrained tier is
2.6x the unconstrained one and holds the entire unattended runtime.

### Interpreter choice (open question — see below)

`scripts/validate.py` establishes the repo's Python convention: PEP 723 inline metadata,
`uv run`, hash-locked (`validate.py.lock`), `requires-python >=3.11`. That is the obvious
default for Tier A. It is a poor fit for Tier B, where `uv` typically lives in `~/.local/bin`
and would have to be added to the pinned launchd PATH. Recommendation and alternatives are
in **Open questions**.

## Scope / non-goals

- **Not** a rewrite. No behavior changes, no refactors-while-porting, no new features. Any
  bug found mid-port is written down, not fixed in the same PR.
- **Not** porting the other shell scripts. `claude-usage`, `await-pr-review`,
  `preflight-freshness` et al. are 137–312 lines, correct, and idiomatic shell. Leave them.
- **Not** porting `scripts/test-spawn-orchestrator.sh`. It is the safety net; rewriting the
  net while moving the trapeze defeats the point. It keeps running as bash against the CLI.
- **Not** touching the constrained tier before task 2 decides its runtime — and possibly not at all.
- **Not** adding a package manager, venv, or build step for plugin consumers.

## Tasks

**The runtime decision now comes first.** It determines how much of the file can ever move —
and whether Python is even the right target — so it is settled before a line is ported.

1. [[orch_py_task_1]] — **Audit the full reachability set** (which subcommands are actually constrained).
2. [[orch_py_task_2]] — **Decide the runtime** for the constrained tier. No code. May reopen Go.
3. [[orch_py_task_3]] — Golden-output corpus + the bash→Python dispatch seam.
4. [[orch_py_task_4]] — Port `render-profile` (+ network allowlist): the seatbelt renderer.
5. [[orch_py_task_5]] — Port `render-settings`: the layer-2 egress allowlist.
6. [[orch_py_task_6]] — Port `write-launch` + `write-verify-broker`: the generators.
7. [[orch_py_task_7]] — Port `restack`.
8. [[orch_py_task_8]] — `doctor` / the constrained tier — conditional on task 2's decision.
9. [[orch_py_task_9]] — Graduate into `dev_docs/orchestrator.md`; delete this plan folder.

## Open questions

1. **Which interpreter — and should the decision move to the front?** Any option for Tier B must
   satisfy **both** constraints: resolvable on launchd's pinned PATH **and** permitted by the
   Seatbelt `process-exec` allowlist.
   - **(a) `uv` for Tier A, bash stays for Tier B** *(recommended)*. Matches `validate.py`
     exactly, no new dependency, zero risk to the supervisor loop. Cost: **2,618 lines** of
     constrained bash survive — the entire unattended runtime.
   - **(b) Stdlib-only Python on absolute `/usr/bin/python3`**. Survives the minimal PATH and
     can be added to the exec allowlist as a literal. Cost: it is a **Command Line Tools shim**
     (not unconditionally usable), and it is **3.9 on this machine** while the repo's other
     Python requires ≥3.11; stdlib-only means no `pyyaml`.
   - **(c) `uv` everywhere** — on the pinned `--path` *and* the exec allowlist. Cleanest code,
     largest runtime-surface increase on the security-critical unattended path. Not recommended.

   **RESOLVED 2026-07-13: this decision moved to the front of the plan (task 2).** The task 1
   audit showed it gates 2,618 lines across 17 subcommands, so deciding it after building 1,458
   lines of Python would be deciding it too late to act on. Task 2 also explicitly reopens
   **(d) a compiled binary (Go)** — the only option satisfying both constraints at once — because
   `dev_docs/decisions/script_language.md` rejected Go while believing a premise that is now
   false.

2. **RESOLVED: PR #202 merged** (2026-07-13T11:55:57Z). It reformatted every line of
   `spawn-orchestrator.sh` with `shfmt`, so rebase onto `main` before starting — a branch cut
   before it will conflict badly.

3. **RESOLVED: `doctor` cannot be deleted.** The PR #205 co-review established it is invoked by
   the jailed agent on **every loop iteration** (HALT on exit 30). It is load-bearing, not
   accreted. It is also therefore *constrained*, so its port is gated on task 2.
