---
title: Land ruff + pyrefly in just check and CI, at a suppressed baseline
priority: high
size: 3
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: py_toolchain
related_files:
  - justfile
  - scripts/check.sh:38 # currently the only python step
  - .github/workflows/ci.yml:25 # uv is already set up in CI
  - dev_docs/decisions/script_language.md
tags: [python, toolchain, foundation]
---

← [[py_toolchain_plan]]

## Context

The repo has 1,845 lines of Python and **no type checker or linter config of any kind**.
`scripts/check.sh:38` runs `uv run scripts/validate.py` and that is the entire Python gate. CI
already installs `uv` (`astral-sh/setup-uv@v5`), so the runtime is in place — only the tools
are missing.

Per [`dev_docs/decisions/script_language.md`](../../decisions/script_language.md), the gate is
**`pyrefly` (strict) + `ruff`**, chosen because together they check 6,000 lines in under 500 ms
— fast enough for an agent's inner loop. Do **not** substitute `pyright` (1,014 ms, 5x slower
than the TypeScript compiler the decision declined) or `ty` (faster, but beta at ~53–67%
typing-spec conformance).

**Land the gate green, don't land a mountain of findings.** Adopting a strict checker over
1,845 unannotated lines will surface many errors at once. This task's job is to make the gate
*exist and pass*, with existing violations suppressed per-file. Tasks 2–3 then remove
suppressions one group at a time, each a small reviewable PR. Task 4 removes the mechanism.

## Task

- Add a root `pyproject.toml` configuring `ruff` and `pyrefly`.
- Wire both into `scripts/check.sh` (so `just check` covers them) and confirm CI picks them up
  via the existing `uv` setup. Invoke them via `uvx` so no new install step is needed.
- **Pin exact tool versions** — `uvx ruff@<X.Y.Z>`, `uvx pyrefly@<X.Y.Z>` (or dev-dependency
  pins in the new `pyproject.toml`). Never bare `uvx ruff`. Unpinned, every run floats to the
  latest release, so a new `ruff` rule or a `pyrefly` strictness change turns `just check` and
  CI red on an unrelated PR. Worse, **mid-port that is indistinguishable from a port defect** —
  the exact failure mode the orchestrator plan treats as its hardest problem. Pinning also
  matches the repo's existing hash-locked convention (`scripts/validate.py.lock`), which this
  plan cites as the model to copy. Tool bumps become deliberate, reviewable PRs.
- Add a `just lint-py` target mirroring `just lint-shell`, so the fast Python gate can be run
  alone in an agent loop.
- Configure `pyrefly` at the **strictest setting the codebase can hold**, with one hard
  requirement: the exhaustiveness property must be enforced — `Literal` + `match` +
  `assert_never` must reject an unhandled variant. That specific safety is what the decision
  doc bought; a config that doesn't enforce it has missed the point.
- Suppress existing violations **per-file** (not globally, not by weakening the rules), so the
  gate is green on landing and every suppression is a visible, greppable TODO naming the file
  that owes work.
- Resolve open question 1: whether `skills/analysis-pipeline/example/*.py` is gated.
  Recommendation is yes — an example that fails our own gate is one we shouldn't ship — but
  make the call and record it.

## Acceptance Criteria

**Code-enforced:**

- **Prove the checker actually checks.** Add a test (or a documented `just` recipe) that runs
  `pyrefly` against a deliberately broken file and asserts it **fails**. This is not
  paranoia: during evaluation, a misconfigured `pyrefly` silently checked *nothing* and
  cheerfully reported `0 errors` on a file returning `int` from a `-> str` function. A green
  gate that isn't running is worse than no gate.
- A second test asserts the **exhaustiveness** property: a `Literal` union with an unhandled
  variant under `match` + `assert_never` is rejected.
- `just check` is green on a clean tree, and runs `ruff` + `pyrefly`.
- `just lint-py` completes in **under 500 ms** on the current codebase (it is ~1,845 lines;
  the 500 ms budget was measured at 6,000). If it doesn't, the config is wrong.
- CI is green.
- Every suppression is per-file and greppable — no global rule disables, no blanket ignores.

**User-run:**

- Introduce a type error in one of the Linear handler assets, run `just lint-py`, confirm it is
  caught and the message is actionable.
