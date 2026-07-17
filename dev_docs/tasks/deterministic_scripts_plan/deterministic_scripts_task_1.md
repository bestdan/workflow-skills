---
title: scripts/task-scan.py — repo-pr task scan/rank/readiness
priority: high
size: 3
status: ready
created: 2026-07-17
expires: 2026-08-16
source_branch: claude/sleepy-ride-8d4bjx
related_files:
  - scripts/task-scan.py # new file
  - scripts/validate.py # mirror its uv/pyyaml dependency profile + ROOT resolution
  - scripts/probe-coders.sh # the "replaces the ad-hoc X" header mold
  - scripts/check.sh # wire the paired test in here
  - commands/handlers/repo-pr-execute.md # §1-2 scan/epic-skip/readiness/rank consumer
  - commands/list-tasks.md # §2-3 scan + expired + dependency-blocked + epic rollup
  - commands/promote-tasks.md # §1 + deterministic 7/8 of the HIGH gate §2
  - commands/doctor.md # checks 4-5
  - skills/task/SKILL.md # Ranking, Field reference, Epics/Membership/Rollup (canonical rules)
parent: deterministic_scripts
impact: 5
tags: [scripts, repo-pr, task-loop, refactor]
---

# Task 1 — `scripts/task-scan.py` (repo-pr scan/rank/readiness)

Part of [[deterministic_scripts_plan]]. The audit's **top pick** (Finding #1):
highest-frequency × most re-derived arithmetic.

## Context

The single most-duplicated deterministic procedure in the plugin is *scan
`dev_docs/tasks/**/*.md` → parse frontmatter → classify by status → compute
readiness → rank*. It is restated, with local variations, in at least five
places (all cited in `related_files`), with the canonical rules specified once
in `skills/task/SKILL.md`. Every `/list-tasks`, `/do-tasks`, `/promote-tasks`,
`/archive-tasks`, or `/doctor` invocation on the default `repo-pr` handler makes
the agent read N task files and re-derive all of this by hand — a context cost
and a chance to apply the logic slightly differently each run.

The canonical rules to implement **verbatim** from `skills/task/SKILL.md` (do
not invent new semantics — this script is an executable copy of the existing
spec):

- **Ranking:** priority tier (`high`→`medium`→`low`) → `impact/size` descending
  → a card with no `impact` or a missing/invalid `size` has no score and ranks
  **last within its tier, never dropped** → tie-break oldest `created` first.
  `size`/`impact` are Fibonacci `1`/`2`/`3`/`5`.
- **Multi-blocker readiness:** a card is dependency-ready iff **every** entry in
  `is_blocked_by` resolves to a file that is **absent or `status: done`**.
  Slugs resolve globally across `dev_docs/tasks/**/*.md` (filename stem).
- **Epics:** `type: epic` files are never ranked/executed as tasks; they roll up
  their members (`parent: <epic-slug>`, recursive). Emit epic rollups.
- **Expired:** `expires < today && status` non-terminal.
- **Promote HIGH gate (deterministic 7/8):** fields present, `size ∈ {1,2,3,5}`,
  ≥1 AC bullet present, Open-Questions/TBD content flag,
  `human_approval_requested` flag. The 8th check — "scope fits size 5" — is
  explicitly NL judgment and **stays in prose**; do not implement it.

**Structural note:** this serves the `repo-pr` handler only. Linear/gh-issue/jira
scan/rank runs over MCP/`gh` responses in-session (`linear-scan.py` already
covers the Linear read) — out of scope here.

Follow the repo mold (`probe-coders.sh`, `await-pr-review.sh`,
`validate.py`): an explicit `# Replaces the ad-hoc <X> that <command> would
otherwise re-derive each run` header, structured/parseable JSON stdout,
fail-closed on malformed frontmatter, and a paired test wired into
`scripts/check.sh`. Match `validate.py`'s dependency profile (uv shebang +
pyyaml) and its `ROOT`/task-dir resolution — but take the task dir as an
**argument** (see task 3's path-bug lesson; don't hardcode a repo-root
assumption).

## Task

1. Create `scripts/task-scan.py` (executable, uv shebang, pyyaml), argument =
   task dir (default `dev_docs/tasks`), optional `--prs <json>` for
   tracker-issue merge (future-proofing; repo-pr ignores it). The
   `--archive-candidates` mode is **split out** into
   [[deterministic_scripts_task_7]] — leave a clean extension point (the
   `status: done` grouping this task already emits is what task 7 filters), but
   do not build the archive mode here.
2. Emit **one JSON document**: cards grouped by status, each with computed
   `rank`, `dependency_ready` + list of unresolved blockers, `expired` flag,
   epic rollups, and — for each `new` card — the deterministic promote-gate
   results (fields present, size valid, AC bullet present, open-questions/TBD
   content, `human_approval_requested`). Leave the NL-judgment bits (promote's
   scope-fit gate, rendering, dispatch decisions) to the agent.
3. Add `scripts/test-task-scan.sh` (or extend an existing test harness) with
   fixtures covering: the "none/missing impact sorts last-not-dropped" rank
   edge, the `task_1` vs `task_13` slug-resolution (no substring bleed),
   multi-blocker readiness (mixed absent/done/open), epic rollup, expired, and a
   malformed-frontmatter fail-closed case. Wire it into `scripts/check.sh`.
4. **Wire the consumers** to call the script and cite it as the source of truth
   for scan/rank/readiness (replace the re-derived prose in
   `repo-pr-execute.md` §1-2, `list-tasks.md` §2-3, `promote-tasks.md` §1,
   `doctor.md` checks 4-5 with a reference to the script's output).
   `repo-pr-archive.md` §2 is wired in [[deterministic_scripts_task_7]] with the
   archive mode. Keep the genuine judgment prose (promote scope-fit, rendering,
   dispatch).

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes (`dprint check`; `claude plugin validate . --strict`;
  `scripts/validate.py`; the new `test-task-scan.sh`).
- `scripts/task-scan.py dev_docs/tasks` emits valid JSON; the test fixtures
  assert the rank edge (no-impact last-in-tier), slug non-bleed, multi-blocker
  readiness, epic rollup, expired, and fail-closed-on-malformed cases.
- `rg -n "topolog|re-derive|rank the tasks" commands/handlers/repo-pr-execute.md`
  no longer shows a hand-derived rank/readiness procedure — only a reference to
  the script's output.

**User-run:**

- Run `scripts/task-scan.py` over a real `dev_docs/tasks/` tree and eyeball the
  ranking against `/list-tasks` output — confirm identical ordering and
  readiness classification (no regression from the prose behavior).

## Notes

The `--archive-candidates` mode was split into
[[deterministic_scripts_task_7]] to keep this task within the one-PR budget
(plan open-question 1, resolved). Emit the `status: done` grouping cleanly here
so task 7 only has to add the date filter on top.
