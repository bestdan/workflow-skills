---
title: scripts/claim-scan.sh — orchestrator-side WIP/claim query + whole-line dedupe
priority: medium
size: 3
status: ready
created: 2026-07-17
expires: 2026-08-16
source_branch: claude/sleepy-ride-8d4bjx
related_files:
  - scripts/claim-scan.sh # new file
  - scripts/await-pr-review.sh # style/mold to mirror (bash, gh, structured stdout)
  - scripts/check.sh
  - commands/handlers/repo-pr-execute.md # claim protocol step 1 (~49-81), WIP count 4.2 (~179-197), remote prompt copy (~251-255)
  - commands/do-tasks.md # ~183-195
  - commands/doctor.md # stale-claim check (~147-152)
  - skills/task/SKILL.md # Race conditions
parent: deterministic_scripts
impact: 3
tags: [scripts, repo-pr, claim, wip]
---

# Task 4 — `scripts/claim-scan.sh` (orchestrator-side claim/WIP dedupe)

Part of [[deterministic_scripts_plan]]. Audit Finding #4. Independent build.
**Medium impact** — deliberately narrower than #1/#2 (see the remote-VM limit).

## Context

The whole-line `Claims-task: <slug>` matching rule — including the documented
`task_1` vs `task_13` **substring bug the repo has already been burned by once**
— is restated in at least four places (all in `related_files`). Every
restatement is a chance to reintroduce that exact bug. A single tested script
running the claim/WIP queries and doing whole-line slug matching would serve the
WIP count, the local pre-claim check, `--local` mode, and doctor's stale-claim
check from one implementation.

**Hard limit (why this is medium, not high impact):** the highest-risk consumer
is the **remote dispatch prompt** — and the remote VM explicitly does **not**
have the plugin installed (`repo-pr-execute.md` ~line 234; `skills/task/SKILL.md`
"Remote session notes"), so it cannot call plugin scripts. That copy **stays
inline prose** (it already embeds the exact `grep -Fxq` whole-line semantics —
the next best thing). This script hardens only the **orchestrator-side** paths.
Do not attempt to route the remote prompt through it.

Mirror `await-pr-review.sh`: bash, `gh` calls, structured/parseable stdout,
fail-closed, paired test.

## Task

1. Create `scripts/claim-scan.sh` (executable, bash, `await-pr-review.sh` style).
   Run the three `gh pr list --label task-{claim,loop,blocked} --limit 100`
   queries, extract claimed slugs by **whole-line** `Claims-task:` marker
   (`grep -Fxq`-equivalent, never substring) or `headRefName == task/<slug>`,
   and dedupe against `in_progress` task files. Emit parseable stdout (claimed
   slugs, WIP count, stale claims).
2. Structure the output so one invocation serves: the WIP count
   (`repo-pr-execute.md` 4.2), the local pre-claim check (claim protocol step
   1), `--local` mode, and doctor's stale-claim check.
3. Add `scripts/test-claim-scan.sh` with fixtures asserting the **`task_1` vs
   `task_13` non-bleed** (the load-bearing case), a clean no-WIP state, and a
   stale-claim (labeled PR with no matching `in_progress` file). Wire into
   `scripts/check.sh`. Prefer a `gh` stub/fixture over live calls so the test is
   hermetic.
4. **Wire the orchestrator-side consumers** (`repo-pr-execute.md` step 1 & 4.2,
   `do-tasks.md`, `doctor.md` stale-claim) to call the script and cite it.
   **Explicitly leave the remote-dispatch prompt copy (~251-255) as prose** and
   add a one-line note there saying so, so nobody "helpfully" wires the script
   into the remote path later.

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes including `test-claim-scan.sh`.
- The `task_1`/`task_13` fixture proves whole-line matching (claiming `task_1`
  does **not** mark `task_13` claimed, and vice-versa).
- `rg -n "Claims-task" commands/handlers/repo-pr-execute.md` shows the
  orchestrator-side paths referencing the script; the remote-prompt block still
  contains its inline `grep -Fxq` copy (unchanged) plus the new "stays prose"
  note.

**User-run:**

- Against a repo with a couple of live `task-claim`/`task-loop` PRs, run
  `claim-scan.sh` and confirm the claimed-slug set and WIP count match what
  `/do-tasks`'s pre-claim gate computes by hand today.
