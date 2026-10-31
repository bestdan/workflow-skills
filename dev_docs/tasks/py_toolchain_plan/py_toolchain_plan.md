---
type: epic
title: Adopt the Python toolchain (pyrefly + ruff) across the existing codebase
status: active
owner: Daniel Egan
created: 2026-07-13
---

# Adopt `pyrefly` + `ruff` across the existing Python

## Goal

[`dev_docs/decisions/script_language.md`](../../decisions/script_language.md) settled that
non-trivial scripting here is Python, gated by **`pyrefly` (strict) + `ruff`** — a sub-500 ms
pre-run signal fast enough to sit in an agent's edit→check loop.

Right now that gate does not exist. The repo has **1,845 lines of Python across 8 files** and
**no `ruff`, `pyrefly`, or `mypy` config at all**. `just check` runs `uv run scripts/validate.py`
and nothing else Python-side — so today, a type error in this repo's Python is caught at
runtime, by a user.

This plan builds the gate and brings the existing code up to it. It is deliberately **separate
from — and a prerequisite of — the orchestrator port**: that port adds ~5,000 lines of new
Python, and it should land onto a toolchain that already works, not carry the toolchain in on
its back.

## The existing surface

Not all 1,845 lines are equal, and the plan is sequenced by **who gets hurt when they break**:

| Files                                                                                  | Lines | Who runs it                                     |
| -------------------------------------------------------------------------------------- | ----- | ----------------------------------------------- |
| `commands/handlers/assets/linear-{relations,ready,scan,archive}.py`                     | 1,105 | **Plugin consumers, at runtime** — highest risk |
| `scripts/validate.py`, `scripts/bump-version.py`                                        | 537   | Dev/CI tooling                                  |
| `skills/analysis-pipeline/example/{model,fill_templates}.py`                            | 203   | **Templates users copy** — a judgment call      |

The four Linear handler assets are the sharp end: they are consumer-facing runtime code, they
talk to a remote API (so they handle dynamically-shaped JSON, exactly where types earn their
keep), and a defect there fails in a user's session. They go first after the gate exists.

## Approach

**Land the gate first, then climb to it — never the reverse.** Adopting a strict type checker
over 1,845 unannotated lines will surface a pile of findings at once. The failure mode is a
sprawling PR that changes the toolchain *and* 8 files *and* is impossible to review.

So: task 1 wires `ruff` + `pyrefly` into `just check` and CI at a **baseline** — the gate runs
and is green from day one, with existing violations explicitly suppressed per-file rather than
fixed. Every subsequent task removes one file group's suppression and fixes what falls out.
The gate is never red on `main`, and each PR is small and reviewable.

Task 4 removes the last suppression and asserts the baseline mechanism itself is gone — so the
scaffolding can't quietly become permanent.

## Tasks

1. [[py_toolchain_task_1]] — Land `ruff` + `pyrefly` in `just check` and CI, at a suppressed baseline.
2. [[py_toolchain_task_2]] — Strict-clean the four Linear handler assets (1,105 lines, consumer-facing).
3. [[py_toolchain_task_3]] — Strict-clean the dev tooling: `validate.py`, `bump-version.py`.
4. [[py_toolchain_task_4]] — Remove the baseline; enforce strict repo-wide with no suppressions.
5. [[py_toolchain_task_5]] — Graduate to `dev_docs/python-toolchain.md`; delete this plan folder.

## Scope / non-goals

- **Not** rewriting any bash in Python. The decision explicitly keeps short scripts
  (`claude-usage.sh`, `await-pr-review.sh`, `preflight-freshness.sh`) in shell.
- **Not** the orchestrator port. That is [its own plan](../../orchestrator-python-port.md) and
  depends on this one landing.
- **Not** changing what any existing Python *does*. This is annotation and type-correctness
  work; behavior changes are out of scope and any bug found is filed, not fixed inline.
- **Not** adding `mypy` or `pyright` alongside. One type checker. (`pyright` is explicitly
  disqualified on speed — see the decision doc.)

## Open questions

1. **Do the `analysis-pipeline/example/*.py` files get gated?** They are **templates users copy
   into their own projects**, not library code. Arguments both ways: gating them means the
   examples we hand people are type-correct (good — they're the pattern people imitate);
   but strict annotations may add noise that obscures the pedagogical point, and the files are
   deliberately illustrative. **Recommendation: gate them** — an example that doesn't pass our
   own gate is an example we shouldn't be shipping — but this is a real call, decided in task 1.
2. **Does `pyrefly` need a `pyproject.toml` at the repo root?** It required one in testing (a
   bare `pyrefly check <dir>` silently checked *nothing* and reported "0 errors" — a genuinely
   dangerous failure mode). The repo currently has no root `pyproject.toml`. Task 1 must add
   one and **prove the checker actually fails on a deliberately broken file** before trusting a
   green result.
3. **How strict is "strict"?** `pyrefly` has strictness levels. The intent is the strictest
   setting that the codebase can realistically hold, but the exact knob set is task 1's call —
   with the constraint that the exhaustiveness property (`Literal` + `match` + `assert_never`)
   must be enforced, since that is the specific safety the decision doc bought.
