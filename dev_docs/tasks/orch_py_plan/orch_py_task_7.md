---
title: Port restack — post-merge restack of stacked PRs
priority: medium
size: 5
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_6
related_files:
  - scripts/spawn-orchestrator.sh:258 # restack docs
  - scripts/spawn-orchestrator.sh # _restack_orphan_scan (71 lines)
tags: [orchestrator, python, port, git]
---

← [[orch_py_plan]]

## Context

`restack` (303 lines, plus a 71-line `_restack_orphan_scan` helper) reads RUN.md and
restacks stacked PRs after a merge. It is the most `git`/`gh`-heavy function in the file —
the orchestrator invokes `git` 107 times and `gh` 107 times overall, and a large share lands
here.

This one is a **judgment call on value**, and unlike the renderers it is genuinely mutating:
it rewrites branches. Two honest considerations:

- The port's payoff is smaller than elsewhere. Shelling out to `git`/`gh` from Python is not
  obviously nicer than shelling out from bash — this is bash's actual home turf. The win is
  in the surrounding logic (parsing RUN.md, tracking the stack, the orphan scan), not in the
  subprocess calls.
- The risk is higher. A defect here rewrites git history on real branches, and the golden
  corpus can only pin the *plan* it prints, not the mutations it performs.

So: port the **decision logic** (parse RUN.md → compute the restack plan → detect orphans)
and keep it well-separated from the **execution** (the `git`/`gh` calls). Prefer a `--dry-run`
that prints the computed plan, and pin *that* in the golden corpus.

If, on reading it, the port looks like it buys little, say so and defer — this task is
explicitly allowed to conclude "leave it in bash." That is a legitimate outcome, not a
failure.

## Task

- Implement `restack` in `scripts/orchestrator/restack.py`, with a clean split between plan
  computation (pure, testable) and mutation (subprocess).
- Reproduce the computed plan and all output byte-for-byte.
- Preserve the orphan-scan behavior and every fail-closed guard around destructive git
  operations — an undetermined signal must never green-light a branch rewrite.
- Add to `PORTED`; delete the bash function and its helper.
- **Or**: conclude the port is not worth it, document why in the PR, and leave the bash in
  place. Update the plan's task list accordingly.

## Acceptance Criteria

**Code-enforced:**

- Golden corpus reproduces the computed restack plan byte-for-byte, including the orphan-scan
  cases.
- `bash scripts/test-spawn-orchestrator.sh` passes unchanged.
- Tests cover the destructive-guard paths: an undetermined or ambiguous signal must abort
  rather than rewrite a branch.
- `just check` green.

**User-run:**

- Against a real stacked-PR run (or a fixture repo), confirm `restack --dry-run` prints the
  identical plan to the bash version, and that a real restack produces the same branch
  topology.
