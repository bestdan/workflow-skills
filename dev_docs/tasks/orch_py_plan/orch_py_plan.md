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
2. **Most of the mass is pure.** The renderers are string-in / file-out. We capture golden
   outputs from today's bash **before** touching anything, then require the Python to
   reproduce them **byte-for-byte** (task 1).

### The load-bearing constraint: TWO constrained contexts

> **Corrected 2026-07-13 after co-review (PR #205).** This section originally claimed the only
> constraint was launchd's PATH, and listed `status`, `classify-exit`, `exit-reason`, and
> `doctor` as freely portable. That was **wrong** and would have broken the wake loop.

A subcommand is **constrained** if reachable from a context that cannot freely resolve a Python
interpreter. Three such paths — the original plan modelled only the first:

1. **The generated launch script → launchd's pinned minimal PATH.** `supervisor-scan →
   heartbeat → supervisor-gate → sandbox-exec claude → supervisor-check`, all under a minimal
   PATH (`write-launch` fail-closes without `--path` for this reason).
2. **In-process bash calls.** `status_report` calls `status` as a shell function
   (`spawn-orchestrator.sh:4233`); `supervisor_check` calls `classify_exit` (`:3195`). These
   never go through the CLI, so the CLI seam does **not** protect them — deleting the bash body
   breaks the caller outright.
3. **The jailed agent → the Seatbelt exec allowlist.** The run-phase agent, inside
   `sandbox-exec`, invokes `doctor` **every loop iteration** and `exit-reason` on every
   termination (`skills/auto-pilot/SKILL.md`), plus `heartbeat`/`alarm-clear`. The rendered
   profile carries a `(allow process-exec` allowlist (`:711-724`) — any interpreter used here
   must be **on it**, a constraint distinct from PATH resolvability.

**Tier A (unconstrained, ~1,200 lines):** `render-profile` (337), `write-launch` (327),
`restack` (374), `write-verify-broker` (126), `render-settings`, `check-profile`. The renderers
and generators — still the worst quoting/list-building code in the file. Tasks 1–4, 7.

**Tier B (constrained, ~2,000+ lines):** `doctor` (659, jail), `status_report` (443, launchd),
`supervisor_check`/`_supervisor_halt` (414, launchd), `status` (180, in-process),
`supervisor_scan`/`supervisor_gate` (174, launchd), `classify_exit` (73, in-process),
`exit_reason` (69, jail), `heartbeat`/`alarm-clear`. All gated on the interpreter decision
(task 8) — a materially bigger share than first assumed.

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
- **Not** touching Tier B (the launchd wake loop) before task 8, and possibly not at all.
- **Not** adding a package manager, venv, or build step for plugin consumers.

## Tasks

1. [[orch_py_task_1]] — Golden-output corpus + the bash→Python dispatch seam, proved on `check-profile`.
2. [[orch_py_task_2]] — Port `render-profile` (+ `render_network_allowlist`): the seatbelt renderer.
3. [[orch_py_task_3]] — Port `render-settings`: the layer-2 egress allowlist.
4. [[orch_py_task_4]] — Port `write-launch` + `write-verify-broker`: the generators.
5. [[orch_py_task_5]] — **Audit the full reachability set** (was: port the reporters — they are Tier B).
6. [[orch_py_task_6]] — `doctor`: **blocked on task 8** (jail-invoked every loop; was: port or delete).
7. [[orch_py_task_7]] — Port `restack`.
8. [[orch_py_task_8]] — Decide the launchd boundary: port Tier B, or freeze it in bash and document why.
9. [[orch_py_task_9]] — Graduate the architecture into `dev_docs/orchestrator.md`; delete this plan folder.

## Open questions

1. **Which interpreter — and should the decision move to the front?** Any option for Tier B must
   satisfy **both** constraints: resolvable on launchd's pinned PATH **and** permitted by the
   Seatbelt `process-exec` allowlist.
   - **(a) `uv` for Tier A, bash stays for Tier B** *(recommended)*. Matches `validate.py`
     exactly, no new dependency, zero risk to the supervisor loop. Cost: **~2,000+ lines** of
     constrained bash survive — more than this plan originally assumed.
   - **(b) Stdlib-only Python on absolute `/usr/bin/python3`**. Survives the minimal PATH and
     can be added to the exec allowlist as a literal. Cost: it is a **Command Line Tools shim**
     (not unconditionally usable), and it is **3.9 on this machine** while the repo's other
     Python requires ≥3.11; stdlib-only means no `pyyaml`.
   - **(c) `uv` everywhere** — on the pinned `--path` *and* the exec allowlist. Cleanest code,
     largest runtime-surface increase on the security-critical unattended path. Not recommended.

   **This no longer binds only at task 8.** The corrected reachability analysis means it gates
   tasks **5, 6, and 8** (~2,000 lines). Tasks 1–4 and 7 stay unconditionally safe. Consider
   moving the decision earlier — it also determines whether a compiled-binary answer (which
   satisfies both constraints at once; see `dev_docs/decisions/script_language.md`) should be
   revisited *before* 1,200 lines of Python exist.

2. **Sequencing against PR #202.** That PR reformats every line of `spawn-orchestrator.sh`
   with `shfmt`. Any port work started before it lands will conflict catastrophically. This
   plan assumes **#202 merges first**. It is not encoded as `is_blocked_by` (that resolves
   task slugs, not PRs) — it is a hard prerequisite on task 1.

3. **Is `doctor` (659 lines) worth porting, or worth deleting?** It is a diagnostic
   read-only command. Before porting it wholesale, worth asking whether it is actually used
   or whether it accreted. A port is ~a day; a deletion is ten minutes.
