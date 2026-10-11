---
title: Golden-output corpus and the bash→Python dispatch seam
priority: high
size: 5
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_2
related_files:
  - scripts/spawn-orchestrator.sh:6249 # the subcommand dispatch case block
  - scripts/test-spawn-orchestrator.sh
  - scripts/validate.py:1 # PEP 723 + uv convention to copy
  - scripts/check.sh
tags: [orchestrator, python, port, foundation]
---

← [[orch_py_plan]]

## Context

`scripts/spawn-orchestrator.sh` dispatches 29 subcommands from a single `case` block near
line 6249 (`render-profile) render_profile "$@" ;;` and so on). Everything that consumes the
orchestrator — the skills, `scripts/smoke-confinement.sh`, the generated launch script, and
the 5,292-line `scripts/test-spawn-orchestrator.sh` harness — goes through that CLI. Nothing
reaches into its internals.

That makes the CLI a **contract we can hold fixed while swapping the implementation**. This
task builds the two things every later task depends on, and ports one trivial subcommand to
prove the seam end-to-end.

**Prerequisite satisfied:** PR #202 (the `shfmt`/ShellCheck/Bats tooling) **merged on
2026-07-13**. Rebase onto `main` before starting — it reformatted every line of
`spawn-orchestrator.sh`, so any branch cut before it will conflict badly.

The repo already has a Python convention to copy verbatim — `scripts/validate.py` uses PEP
723 inline metadata (`# /// script`), runs via `uv run scripts/validate.py`, and hash-locks
its deps in `scripts/validate.py.lock`. Follow it; do not invent a new one.

## Task

**1. Capture the golden corpus (do this first, before any Python exists).**

Add `scripts/capture-golden.sh` that drives the *current bash* orchestrator over a matrix of
inputs and records stdout, stderr, exit code, and any `--out` file byte-for-byte into
`test/golden/<subcommand>/<case>/`. Cover at minimum the pure renderers, since they are what
tasks 2–4 replace:

- `render-profile` — with/without `--confine-under`, `--cred-ro`, `--workdir`, `--tmpdir`,
  multiple `--rw`/`--ro`, `--exec` vs `--exec-dir`/`--toolchain`, and the fail-closed
  bad-input cases (which must exit 2 and write nothing).
- `render-settings` — the egress allowlist narrowing cases.
- `check-profile` — a compiling profile and a non-compiling one.
- `write-launch` / `write-verify-broker` — full flag set; the generated script and plist.

Paths in the output must be normalized (the corpus is committed and must be
machine-independent — substitute `$HOME`, the repo root, and any tmpdir with stable tokens).

**2. Build the dispatch seam.**

- Create `scripts/orchestrator/__main__.py` (PEP 723 header, `argparse`, `uv run`), with a
  subcommand registry and a single `main()`.
- In `spawn-orchestrator.sh`, add a `PORTED` list and route matching subcommands to Python
  *before* the existing `case` block; everything not on the list falls through to the bash
  function unchanged. Preserve argv, stdin, stdout/stderr streams, and the exit code exactly.
- Port **`check-profile`** only (it is ~20 lines and calls `sandbox-exec -f`), and delete its
  bash function.

**3. Wire it into the gates.**

- The **`pyrefly` + `ruff` gate is not built here** — it is owned by
  [`py_toolchain_task_1`](../py_toolchain_plan/py_toolchain_task_1.md). The port lands *onto* a working toolchain rather than dragging one in with it.
  Just make sure the new Python is covered by the existing gate.
- Model closed state sets (e.g. the exit classification) as `Literal` + `match` +
  `assert_never`, so adding a variant without handling it is a **type error**. Verified to work
  under `pyrefly`; it is the main safety property the port is buying.
- Add `test/golden.bats` asserting the Python subcommand reproduces its golden files
  byte-for-byte.

## Acceptance Criteria

**Code-enforced:**

- `scripts/capture-golden.sh` run against the pre-port bash produces a committed
  `test/golden/` corpus that is stable across two consecutive runs and across machines
  (paths normalized).
- `test/golden.bats` asserts `check-profile`'s Python implementation matches its golden
  stdout, stderr, and exit code exactly. It fails if the implementation drifts.
- `bash scripts/test-spawn-orchestrator.sh` passes **unchanged** — not one assertion edited.
  If a test needs editing, the seam is wrong.
- `just check` is green, and covers the new Python file.
- `bash scripts/smoke-confinement.sh` still passes on macOS (it calls `check-profile`).

**User-run:**

- Confirm a real auto-pilot launch still works end-to-end: `spawn-orchestrator.sh launch
  --dry-run` produces the same script and plist as before the change.
