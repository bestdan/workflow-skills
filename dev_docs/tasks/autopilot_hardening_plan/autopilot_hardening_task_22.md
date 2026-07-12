---
title: "doctor I3: park a vanished PR for real — teach the gh readers NOT_FOUND vs transient"
priority: high
size: 3
status: new
created: 2026-07-12
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
  - skills/auto-pilot/references/run-state.md
is_blocked_by: [autopilot_hardening_task_14]
parent: autopilot_hardening
tags: [auto-pilot, doctor, gh, test-fidelity, p1]
---

[[autopilot_hardening_plan]]

## Context

Found co-reviewing task 14 (#189). Invariant 3 claims "every `pr-open`/`in-review`/`iterating`/`handed-off` task has a PR that actually exists," parking the task when the PR is gone. **That park is unreachable in production.**

The park branch requires `_doctor_pr_state` to return rc 0 with an **empty** state. Real `gh pr view <n> --json state` on a nonexistent PR **exits non-zero**, which the D2 guard correctly classifies as "undetermined → skip." The rc-0-empty shape exists **only in the test's fake gh**, which does `cat "$db/$num.state" 2>/dev/null; true`. So the test `doctor I3: nonexistent PR row parked` passes against the stub while production skips a deleted PR on **every pass, forever**.

It fails safe — no wrong parks, ever — so this is a correctness-of-claim bug, not a data-loss bug. But an invariant that cannot fire is not an invariant, and a green test asserting otherwise is worse than no test.

This is the repo's recurring failure shape: **the stub does not reproduce the real binary's failure semantics** (exit codes, not just stdout). See `dev_docs/auto-pilot-developer-review-feedback.md`.

## Task

- Teach the `gh` reader path to distinguish **"not found"** (GraphQL `NOT_FOUND` / `Could not resolve to a PullRequest`) from a **transient** failure (401, rate limit, network, `gh` missing). Only the former is a positive "this PR is gone."
- Keep the D2 posture intact for everything else: a transient/unreadable result stays **undetermined → skip**, never a park and never a destructive action.
- Fix the stub to reproduce real `gh`'s semantics — non-zero exit plus the real error text on a missing PR — and keep the existing tests passing against the corrected stub.
- Audit the other `gh` readers (`_pr_number`, I5's open-PR check, restack's) for the same rc-0-empty assumption.

## Acceptance Criteria

- Verified **once against the real binary** and the check written down in the test: `gh pr view 999999 --json state; echo $?` is non-zero, with the not-found error distinguishable from a transient one.
- A deleted PR on a `handed-off` row is **parked** — driven through the corrected stub, whose failure semantics now match the real thing.
- A transient `gh` failure (401 / rate limit) on the same row is **skipped**, not parked.
- Mutation check: removing the not-found detection makes the new park test fail (it must not pass by default).
- `bash scripts/check.sh` green.
