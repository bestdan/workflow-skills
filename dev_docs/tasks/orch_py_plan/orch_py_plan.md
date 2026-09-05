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

**Tier A (unconstrained): 12 subcommands, 1,422 handler lines.** `write-launch` (332), `restack`
(320), `render-profile` (256), `launch` (142), `write-verify-broker` (130), `render-settings`
(62), `check-profile` (23), `smoke-test`, `clear-exit-state`, `record-handle`, `alarm-clear`,
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
1.8x the unconstrained one and holds the entire unattended runtime.

### Interpreter choice — RESOLVED (task 2, 2026-07-13)

**Stdlib-only Python on a pinned, pre-flight-resolved absolute interpreter (≥3.11). Go
re-rejected. Nothing is frozen in bash — the whole file is portable, Tier B included.**

The premise this plan rested on — that no interpreter could satisfy launchd's pinned PATH *and*
the Seatbelt exec allowlist, so Tier B was stuck in shell — **was never tested, and is false.**
A uv-managed CPython 3.11 invoked by *absolute path* runs inside the jail under one exec grant,
with no cache writes and no PATH lookup. (`uv run` genuinely does fail — it needs `~/.cache/uv`
writable — but `uv run` is a package manager, not the interpreter it installs. That distinction
is the whole decision.)

Rationale, the collapse of Go's differentiator, and the three requirements the port inherits:
[`decisions/script_language.md`](../../decisions/script_language.md) → "The constrained tier's
runtime".

## Scope / non-goals

- **Not** a rewrite. No behavior changes, no refactors-while-porting, no new features. Any
  bug found mid-port is written down, not fixed in the same PR.
- **Not** porting the other shell scripts. `claude-usage`, `await-pr-review`,
  `preflight-freshness` et al. are 137–312 lines, correct, and idiomatic shell. Leave them.
- **Not** porting `scripts/test-spawn-orchestrator.sh`. It is the safety net; rewriting the
  net while moving the trapeze defeats the point. It keeps running as bash against the CLI.
- **Not** touching the constrained tier before task 2 decides its runtime. (Task 2 decided: it
  **ports**. An earlier draft added "and possibly not at all" — that is no longer true.)
- **Not** adding a package manager, venv, or build step for plugin consumers.

## Tasks

**The runtime decision now comes first.** It determines how much of the file can ever move —
and whether Python is even the right target — so it is settled before a line is ported.

1. [[orch_py_task_1]] — **Audit the full reachability set** (which subcommands are actually constrained).
2. [[orch_py_task_2]] — **Decide the runtime** — ✅ **done 2026-07-13:** pinned stdlib Python
   ≥3.11; Go re-rejected; **nothing frozen in bash**.
3. [[orch_py_task_3]] — Golden-output corpus + the bash→Python dispatch seam.
4. [[orch_py_task_4]] — Port `render-profile` (+ network allowlist): the seatbelt renderer.
5. [[orch_py_task_5]] — Port `render-settings`: the layer-2 egress allowlist.
6. [[orch_py_task_6]] — Port `write-launch` + `write-verify-broker`: the generators.
7. [[orch_py_task_7]] — Port `restack`.
8. [[orch_py_task_8]] — `doctor` / the constrained tier — **unblocked** by task 2; it ports.
9. [[orch_py_task_9]] — Graduate into `dev_docs/orchestrator.md`; delete this plan folder.
10. [[orch_py_task_10]] — **BUG:** `git` cannot exec inside the jail. The `/usr/bin/git` **shim is
    already granted** by `(subpath "/usr/bin")`; what is ungranted is its **re-exec target** under
    the active developer dir. Found while testing task 2; independent of the port; blocks nothing
    in it.
11. [[orch_py_task_11]] — **PROCESS:** enforce *exercising*. Four times on this plan a
    load-bearing claim about runtime behavior was asserted, propagated into a spec, and never
    executed — each time it was one command to check. Includes the archetype in the code:
    `smoke-confinement.sh` grants `git` to the jail and never runs it.
12. [[orch_py_task_12]] — **BUG (security):** layer 2 does not enforce. The launch script's
    `--permission-mode bypassPermissions` disables the harness's inner sandbox — which *is* layer
    2 — so the jailed agent has open network egress: a raw socket to an arbitrary host connects.
    Found while fixing task 10, whose exec fixes let the smoke's §2 run at all. Task 11's thesis,
    demonstrated. Gates task 5: do not port `render-settings` until it is known whether anything
    reads the allowlist it emits.

(Tasks 1–9 are the port. Tasks 10, 11 and 12 rode along: two bugs and a process fix, all found
*by* the port rather than *in* it.)

## Open questions

1. **Which interpreter? — RESOLVED 2026-07-13 (task 2): stdlib-only Python on a pinned,
   pre-flight-resolved absolute interpreter (≥3.11). Go re-rejected. Nothing frozen in bash.**

   The options this question used to list are kept only as a record of what was believed, because
   **the premise under all of them was false**: it was assumed no interpreter could satisfy
   launchd's pinned PATH *and* the Seatbelt exec allowlist at once, which made "(a) bash stays for
   Tier B" the recommendation and made a Go binary "the only option satisfying both constraints at
   once."

   **Tested, that premise collapsed.** A uv-managed CPython invoked by absolute path runs in the
   jail under one exec grant, with no cache writes and no PATH lookup — so an interpreter *does*
   satisfy both, and Go's differentiator is gone. (`uv run` genuinely does fail — it needs
   `~/.cache/uv` writable — but `uv run` is a package manager, not the interpreter it installs.
   That distinction is the whole decision.) The stale options — "(a) recommended", "(b) on
   `/usr/bin/python3`" (a 3.9.6 CLT shim), "(c) uv everywhere", "(d) Go, the only option that
   can" — are all superseded.

   See "Interpreter choice — RESOLVED" above and
   [`decisions/script_language.md`](../../decisions/script_language.md) → "The constrained tier's
   runtime" for the rationale and the three requirements the port inherits.

2. **RESOLVED: PR #202 merged** (2026-07-13T11:55:57Z). It reformatted every line of
   `spawn-orchestrator.sh` with `shfmt`, so rebase onto `main` before starting — a branch cut
   before it will conflict badly.

3. **RESOLVED: `doctor` cannot be deleted.** The PR #205 co-review established it is invoked by
   the jailed agent on **every loop iteration** (HALT on exit 30). It is load-bearing, not
   accreted. It is also therefore *constrained*, so its port is gated on task 2.
