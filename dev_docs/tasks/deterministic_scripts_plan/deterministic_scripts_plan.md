---
type: epic
title: Deterministic-script extraction — the audit's remaining recommendations
status: active
owner: Daniel Egan
created: 2026-07-17
---

# Deterministic-script extraction

## Goal

Execute the still-open recommendations from the deterministic-code opportunity
audit ([[deterministic-code-opportunity|../deterministic-code-opportunity.md]]):
extract the deterministic procedures the plugin's prose re-derives on every
invocation into tested scripts, so the hot **`repo-pr`** paths run the same way
every time instead of being re-reasoned from markdown. The Linear analogues
already shipped (fast-path/floor); this plan closes the **repo-pr** and
**push-plan** side, plus a real path defect in `/doctor`.

## Scope / non-goals

- **In scope:** four script builds (`task-scan.py`, `plan-graph.py`,
  `claim-scan.sh`, a `validate.py` extension) each with a paired test wired into
  `scripts/check.sh`, plus a docs close-out of the audit's Finding #5.
- **Out of scope — deliberately staying prose** (per the audit's "stay as prose"
  list): everything MCP-bound (Linear/Jira claim locks, mutations, archive
  writes beyond the existing GraphQL script), the config wizards, select-coder /
  assess-task routing judgment, promote's scope-fit gate,
  `linear-reoptimize` Dimensions 1/2/4, the crash-reconciliation table, and
  `fact-reviewer`.
- **Out of scope — the remote-dispatch copy.** `claim-scan.sh` (task 4) hardens
  only orchestrator-side paths; the remote VM has no plugin installed, so its
  inline `grep -Fxq` copy stays prose.
- **No new dependencies.** Python scripts match `validate.py`'s uv/pyyaml
  profile; bash scripts match `await-pr-review.sh`. Every script follows the
  repo mold: an explicit "replaces the ad-hoc X" header, structured/parseable
  stdout, fail-closed behavior, and a paired test in `scripts/check.sh`.

## Approach

Each script is a standalone extraction of one deterministic procedure, shaped
after an existing precedent in the repo so it reads like its neighbors:

- `task-scan.py` ← mirrors `validate.py` (uv/pyyaml) and the `linear-scan.py`
  read-extraction pattern, but over local `dev_docs/tasks/**/*.md`.
- `plan-graph.py` ← the push-side analogue of the graph load `linear-relations.py`
  already does for reoptimize.
- `validate.py` extension ← extends the script `/doctor` already delegates to,
  and fixes the consumer-repo path bug found alongside it.
- `claim-scan.sh` ← mirrors `await-pr-review.sh` (bash, `gh` + structured stdout).

The main tradeoff, shared with the shipped Linear work: a script is a *second*
implementation of logic the prose also describes, so each task names one
canonical source (the SKILL.md rule block or the script itself) and has the
prose **cite** it rather than re-derive it — the same single-source-of-truth
discipline the fast-path/floor pattern used.

Tasks 1–4 are independent builds (no ordering between them). Task 7 (the
`--archive-candidates` mode absorbing Findings #3/#6) is split out of task 1 to
keep each within one PR, and is `is_blocked_by` task 1. Task 5 is docs-only.
Task 6 is the graduate-then-delete cleanup, blocked by all the others.

## Tasks

1. [[deterministic_scripts_task_1]] — **`scripts/task-scan.py`**: file-path task
   scan/rank/readiness for the `repo-pr` handler (the audit's top pick).
2. [[deterministic_scripts_task_2]] — **`scripts/plan-graph.py`**: topological
   sort + cycle detection + id-shape edge classification for `/push-plan`.
3. [[deterministic_scripts_task_3]] — **Extend `scripts/validate.py`** for
   `/doctor`'s field/expiry checks and **fix the consumer-repo path bug**.
4. [[deterministic_scripts_task_4]] — **`scripts/claim-scan.sh`**: orchestrator-side
   WIP/claim query + whole-line slug dedupe.
5. [[deterministic_scripts_task_5]] — **Close out Finding #5** (auto-pilot
   supervisor): verify the shipped supervisor covers it, mark it delivered in
   the audit. Docs-only.
7. [[deterministic_scripts_task_7]] — **`task-scan.py --archive-candidates`**:
   the repo-pr-archive candidate-selection mode (Findings #3/#6), split out of
   task 1; `is_blocked_by` task 1.
6. [[deterministic_scripts_task_6]] — **Graduate & clean up**: fold durable
   decisions into `dev_docs/deterministic-code-opportunity.md`, delete the plan
   scaffolding. Do last, once tasks 1–5 and 7 are done.

## Resolved decisions

1. **Task 1 size** — **split.** The `--archive-candidates` mode was moved out of
   task 1 into [[deterministic_scripts_task_7]] to keep each within the one-PR
   budget.
2. **Canonical rank/readiness rules** — `skills/task/SKILL.md` (Ranking / Field
   reference / Epics) stays the single source of truth; `task-scan.py`
   implements it verbatim and the consuming commands cite the script. No new
   rule block.
3. **`validate.py` vs. `task-scan.py`** — **kept separate.** `validate.py` stays
   the frontmatter-shape authority (task 3); `task-scan.py` stays the scan/rank
   authority (task 1). Not folded.
4. **Wiring** — **build + wire together.** Each build task lands its script
   *and* updates the consuming command(s) to cite it, rather than deferring the
   prose cut-over to a separate pass.
