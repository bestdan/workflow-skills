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

### The load-bearing constraint: the launchd boundary

`write-launch` generates a launch script that embeds `$self` (the path to
`spawn-orchestrator.sh`) and calls **back into it** on every supervisor wake:

```
supervisor-scan → heartbeat → supervisor-gate → claude (sandbox-exec'd) → supervisor-check
```

That generated script runs under **launchd with a pinned, fingerprint-resolved minimal
`PATH`** (`write-launch` fail-closes without `--path` precisely because a launchd job has a
minimal environment). And `supervisor-scan` internally calls `status_report` — the
second-largest function in the file at 443 lines.

So the boundary that matters is **not** the seatbelt sandbox — it is launchd's minimal PATH.
Any subcommand reachable from the generated script needs its interpreter resolvable there.
This splits the work cleanly:

- **Tier A — outside launchd** (invoked by skills, humans, and the launch-time path):
  `render-profile`, `render-settings`, `check-profile`, `write-launch`,
  `write-verify-broker`, `status`, `doctor`, `restack`, `classify-exit`, `exit-reason`, …
  These carry most of the pain (`doctor` 659, `write-launch` 327, `restack` 303,
  `render_profile` 249, `status` 180) and port with **zero** runtime-surface risk.
- **Tier B — inside the launchd wake loop**: `supervisor-scan`, `supervisor-gate`,
  `supervisor-check`, `heartbeat`, and `status_report`. Porting these adds an interpreter
  dependency to a security-critical supervisor loop. **Deferred to task 8**, gated on the
  runtime decision below, and explicitly acceptable to leave in bash forever.

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
5. [[orch_py_task_5]] — Port the read-only reporters: `status`, `classify-exit`, `exit-reason`.
6. [[orch_py_task_6]] — Port `doctor` (659 lines, the single worst function).
7. [[orch_py_task_7]] — Port `restack`.
8. [[orch_py_task_8]] — Decide the launchd boundary: port Tier B, or freeze it in bash and document why.
9. [[orch_py_task_9]] — Graduate the architecture into `dev_docs/orchestrator.md`; delete this plan folder.

## Open questions

1. **Which interpreter, and does Tier B ever move?** Three options:
   - **(a) `uv` for Tier A, bash stays for Tier B** *(recommended)*. Matches `validate.py`
     exactly, no new dependency, zero risk to the supervisor loop. Cost: the file never
     goes to zero — ~900–1,200 lines of bash survive in the wake loop.
   - **(b) Stdlib-only Python on absolute `/usr/bin/python3`**. Always present on macOS at a
     stable absolute path, so it survives launchd's minimal PATH and can be added to the
     seatbelt exec allowlist as a literal. Would let Tier B port too, killing the file
     entirely. Cost: `/usr/bin/python3` is 3.9 on older macOS (the repo's other Python
     requires ≥3.11), and stdlib-only means no `pyyaml`.
   - **(c) `uv` everywhere**, adding its bin dir to the pinned `--path`. Cleanest code,
     largest runtime-surface increase on the security-critical path. Not recommended.

   This decision only *binds* at task 8; tasks 1–7 are identical under (a) and (c) and
   near-identical under (b). It is listed first because it changes what "done" means.

2. **Sequencing against PR #202.** That PR reformats every line of `spawn-orchestrator.sh`
   with `shfmt`. Any port work started before it lands will conflict catastrophically. This
   plan assumes **#202 merges first**. It is not encoded as `is_blocked_by` (that resolves
   task slugs, not PRs) — it is a hard prerequisite on task 1.

3. **Is `doctor` (659 lines) worth porting, or worth deleting?** It is a diagnostic
   read-only command. Before porting it wholesale, worth asking whether it is actually used
   or whether it accreted. A port is ~a day; a deletion is ten minutes.
