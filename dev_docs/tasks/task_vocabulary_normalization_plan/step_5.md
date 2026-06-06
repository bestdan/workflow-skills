← [[task_vocabulary_normalization_plan]]

# Step 5 — Fold plan-with-docs into the task system

## Title

Make plan-with-docs emit canonical task files into the shared `dev_docs/tasks/` tree, and migrate the repo's own example plan.

## Context

- `skills/plan-with-docs/SKILL.md` currently writes to `dev_docs/todo/<name>_plan/` with `step_N.md` files in a free-form shape (Title / Context / Changes / Acceptance / Dependencies). It is **not** renamed as a skill — only its output vocabulary, directory, and file format change.
- The repo dogfoods the old layout: `dev_docs/todo/evals_and_ci_plan/` (overview + `step_1.md`…`step_5.md`). That directory must move out of the way so it does not collide and so the repo models the new convention.
- The task file format and **Task size** definition now live in `skills/task/SKILL.md` (Step 1); reference them rather than redefining.
- Mapping from old step shape → task format: `Changes` → `## Task`, `Acceptance` (Code-enforced + User-run) → `## Acceptance Criteria`, `Dependencies` → `is_blocked_by` frontmatter, Title → frontmatter `title` + heading.

## Changes

`skills/plan-with-docs/SKILL.md`:

- Output layout → `dev_docs/tasks/<name>_plan/` containing `<name>_plan.md` (overview) + `task_1.md`, `task_2.md`, … (rename "step" → "task" throughout, including "one step = one PR" → "one task = one PR" and the granularity rule, which should cite the **Task size** budget in `skills/task/SKILL.md`).
- Each `task_N.md` is emitted in the **canonical task format** (YAML frontmatter + `## Context` / `## Task` / `## Acceptance Criteria`), with `status: new` so it flows through `/promote-tasks`. Chain ordered tasks via `is_blocked_by`. Required frontmatter fields (`title`, `priority`, `created`, `source_branch`, `related_files`, `expires`) must be populated; reference `skills/task/SKILL.md` for the field list instead of restating it.
- The overview `<name>_plan.md` carries **no** task frontmatter, so `/promote-tasks` (which only touches `status: new`) skips it. State this explicitly.
- Add the **Legacy migration** preflight: if `dev_docs/todo/` exists, run the prompt from `skills/task/SKILL.md` before writing.
- Update the description and the AGENTS-style trigger wording from "> ~3 steps" to "> ~3 tasks".

In-repo example migration (convert fully — the repo's example should model the new convention accurately):

- `git mv dev_docs/todo/evals_and_ci_plan dev_docs/tasks/evals_and_ci_plan`; rename `step_N.md` → `task_N.md`; fix the overview's internal links (`[[step_1]]` → `[[task_1]]`) and each file's back-link.
- **Convert each `task_N.md` to the canonical task format**: add full YAML frontmatter (`title`, `priority`, `created`, `source_branch`, `related_files`, `is_blocked_by` for the step chain, `expires`, `tags`) and map the existing body to `## Context` / `## Task` / `## Acceptance Criteria`. Pull real values from git history where possible (`created`/`source_branch` from the commits that landed these steps). Since this work is already merged, set `status` to reflect a completed record and note in the overview that it is a historical example, not an active backlog.
- Move the plan for _this_ refactor too: `git mv dev_docs/todo/task_vocabulary_normalization_plan dev_docs/tasks/task_vocabulary_normalization_plan` (it was written under the legacy path before the rename), then fix its internal `[[step_N]]` links if you also rename its files for consistency.
- Remove the now-empty `dev_docs/todo/` directory.

## Acceptance

### Code-enforced

- `uv run scripts/validate.py` → OK.
- `rg -n '\bstep_\d|one step = one PR|dev_docs/todo/' skills/plan-with-docs/SKILL.md` → no matches.
- `test ! -d dev_docs/todo` (the legacy plans dir is gone) and `ls dev_docs/tasks/evals_and_ci_plan/task_1.md` exists with valid task frontmatter (`rg -n '^status:' dev_docs/tasks/evals_and_ci_plan/task_1.md`).
- `dprint check skills/plan-with-docs/SKILL.md` passes.

### User-run

- Optional: run `/plan-with-docs` on a throwaway topic and confirm it writes `dev_docs/tasks/<name>_plan/task_1.md` with valid task frontmatter that `/promote-tasks` then scores.

## Dependencies

Step 1 (task format + Task size + Legacy migration live in `skills/task/SKILL.md`).
