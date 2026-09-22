---
title: Port render-profile — the seatbelt profile renderer
priority: high
size: 5
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_3
related_files:
  - scripts/spawn-orchestrator.sh:330 # render-profile docs
  - scripts/spawn-orchestrator.sh:711 # render_network_allowlist
  - scripts/smoke-confinement.sh
  - scripts/test-spawn-orchestrator.sh:100 # render happy path
tags: [orchestrator, python, port, security]
---

← [[orch_py_plan]]

## Context

`render_profile` (249 lines) plus `render_network_allowlist` (88 lines) turn resolved paths
and flags into a Seatbelt `.sb` profile — the layer-1 jail. This is the highest-value port
target: it is pure (flags in, one file out), it is the worst of the bash (path canonicalization,
list building, and quoting into an S-expression, all without associative arrays), and it is
the most safety-critical thing in the repo. A quoting bug here is a sandbox escape.

It is also the best-tested thing in the repo. `scripts/test-spawn-orchestrator.sh` covers it
from line 100 onward: the happy-path token blocks, exec-symlink resolution (the A1
regression), fail-closed bad inputs, `--confine-under` write-scope bounding, `--cred-ro`
keeping a credential file read-only inside an RW state dir, the `--workdir` ledger case, and
`--exec-dir`/`--toolchain` coarse exec mode. `scripts/smoke-confinement.sh` then compiles the
rendered profile with `sandbox-exec` and asserts real writes/reads/execs are actually denied.

Task 1's golden corpus pins the exact bytes of every one of those renders.

## Task

- Implement `render-profile` and its network-allowlist helper in
  `scripts/orchestrator/` (new module, e.g. `profile.py`), reproducing the current output
  **byte-for-byte** — same token blocks, same ordering, same quoting, same trailing newlines.
- Preserve the fail-closed contract exactly: bad input exits **2** and writes **nothing**
  (no partial `--out` file). Verify by asserting the output path does not exist after a
  failure.
- Preserve symlink resolution for `--exec` targets (the A1 regression): an exec allowlist
  entry must resolve to its real target, or the jail can be bypassed via a symlink.
- Add `render-profile` to the `PORTED` list; delete `render_profile` and
  `render_network_allowlist` from the bash.
- Do **not** change the profile's content, ordering, or semantics, even if something looks
  improvable. Any suspected bug goes in a follow-up task, not this PR — a behavior change
  here is indistinguishable from a port defect.

## Acceptance Criteria

**Code-enforced:**

- Every `render-profile` case in `test/golden/` reproduces byte-for-byte.
- `bash scripts/test-spawn-orchestrator.sh` passes unchanged, including the fail-closed,
  symlink, `--confine-under`, `--cred-ro`, `--workdir`, and `--exec-dir` sections.
- `bash scripts/smoke-confinement.sh` passes on macOS: the Python-rendered profile compiles
  under `sandbox-exec` and every denied/allowed assertion holds. **This is the real gate** —
  the golden corpus proves the bytes match, the smoke test proves the jail still holds.
- `just check` green.

**User-run:**

- Render a profile with the Python path and eyeball the `.sb` against one rendered by the
  bash on `main`; `diff` must be empty.
