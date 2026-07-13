---
title: Strict-clean the dev tooling — validate.py and bump-version.py
priority: medium
size: 3
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: py_toolchain
is_blocked_by: py_toolchain_task_2
related_files:
  - scripts/validate.py # 290 lines
  - scripts/bump-version.py # 247 lines
tags: [python, toolchain]
---

← [[py_toolchain_plan]]

## Context

`scripts/validate.py` (290 lines) is the repo-native structural validator that `just check`
runs; `scripts/bump-version.py` (247 lines) drives releases. Together, 537 lines of dev/CI
tooling.

Lower risk than the Linear handlers — these fail in CI, in front of us, not in front of a
user — which is exactly why they come second. `validate.py` is also the file that already
established the repo's Python convention (PEP 723 + `uv` + a hash-locked `validate.py.lock`),
so it should be exemplary of the standard the decision doc sets.

## Task

- Remove the per-file suppressions for both files.
- Annotate and fix until `pyrefly` (strict) and `ruff` pass clean.
- `validate.py` parses YAML frontmatter into loosely-typed dicts — model the frontmatter shape
  with `TypedDict` rather than threading `dict[str, Any]` through. It is the repo's own schema;
  it should be typed.
- Preserve the PEP 723 inline metadata and the lockfile discipline. If a dependency is added
  (it shouldn't need one), re-lock.
- **Do not change behavior.** `validate.py` gates every PR — a behavior change here silently
  changes what the repo accepts. Any bug found is filed, not fixed inline.

## Acceptance Criteria

**Code-enforced:**

- `pyrefly` (strict) and `ruff` pass with zero suppressions for both files.
- `uv run scripts/validate.py` still reports `validate.py: OK` on a clean tree and still
  **fails** on a deliberately malformed task file — prove both, since this file is the gate the
  rest of the repo trusts.
- `just check` green.

**User-run:**

- Dry-run `bump-version.py` and confirm it computes the same version bump as before the change.
