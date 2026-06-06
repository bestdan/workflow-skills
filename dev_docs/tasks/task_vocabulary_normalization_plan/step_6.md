← [[task_vocabulary_normalization_plan]]

# Step 6 — Consistency sweep (docs, manifests, evals, validator)

## Title

Finish the rename across README, CONTRIBUTING, manifests (with version bump), evals, and the validator comment; assert no stray "todo" remains.

## Context

- `README.md` describes the loop ("repo-native todo loop", `todo` ×11) and contains the `validate.py`-checked sentence "N skills, M commands, and K subagents" (`scripts/validate.py:161`) — counts are unchanged, but keep the sentence intact.
- `plugin.json` and `marketplace.json` carry "todo" in descriptions and `keywords`, and must stay version-synced (`scripts/validate.py:151`). A behavioral change warrants a version bump.
- `evals/manifest.tsv:8` has a `todo` row pointing at `prompts/todo.txt`; the eval asserts auto-invocation of the skill by name, so the row must point at `task` / `prompts/task.txt`.
- `scripts/validate.py:103` has a comment "bundled into the todo skill".

## Changes

- `README.md`: `todo`/`todos` → `task`/`tasks`; "repo-native todo loop" → "repo-native task loop"; all command names, `dev_docs/tasks/`, `.task-config.yml`, branch/label references. Preserve the component-count sentence.
- `CONTRIBUTING.md`: any todo references → task.
- `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`: update descriptions ("todo loop" → "task loop"); `keywords` `"todo"` → `"task"`; bump `version` `0.7.0` → `1.0.0` in **both** (keep synced — this is a breaking rename of every command name).
- `scripts/validate.py:103`: comment "bundled into the todo skill" → "bundled into the task skill".
- `git mv evals/prompts/todo.txt evals/prompts/task.txt`; `evals/manifest.tsv`: row `todo\tprompts/todo.txt\t6` → `task\tprompts/task.txt\t6`; update the prompt body if it names the old dir/commands (keep it a naive prompt that does not name the skill).
- `evals/README.md`: any `todo` references → task.

## Acceptance

### Code-enforced

- `uv run scripts/validate.py` → OK (counts match; manifest versions synced at 1.0.0).
- Repo-wide stray check: `rg -n '\btodos?\b|todo-loop|\.todo-config|/(add|claim|promote|process|list)-todo|dev_docs/todo\b' -g '!dev_docs/**' -g '!**/*.lock'` → only intentional matches inside `skills/task/SKILL.md` and command **Legacy migration** sections (legacy path strings). No other hits.
- `dprint check` passes repo-wide.
- `bash scripts/check.sh` (or the CI entrypoint) passes, including `claude plugin validate` if wired.

### User-run

- Trigger the `task` eval locally (`bash scripts/eval.sh task` or per `evals/README.md`) and confirm the naive prompt auto-invokes the renamed `task` skill.

## Dependencies

Steps 1–5 (everything must be renamed before the stray-`todo` assertion can pass).
