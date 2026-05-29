← [[evals_and_ci_plan|Overview]]

# Step 1 — Make the repo green

Fix the failures that exist _today_ so later steps can enforce them without the
first CI run being red. No new infrastructure in this PR — just corrections.

## Context

Baseline, captured from the repo root:

- `claude plugin validate . --strict` → **fails**: 1 warning, "No marketplace
  description provided" (`.claude-plugin/marketplace.json` has no `description`).
- `dprint check` → **fails**: "Found 15 not formatted files."
- Version drift: `.claude-plugin/plugin.json` is `0.6.0`;
  `.claude-plugin/marketplace.json` plugin entry is `0.5.1`.
- `README.md:14` claims "6 skills, **4 commands**, and 1 subagent" but
  `commands/*.md` has **6** top-level commands (add-todo, claim-todo,
  list-todos, process-todo, promote-todos, todo-config).

Relevant files:

- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `README.md:14`
- 15 unformatted files (let `dprint` enumerate them; do not hand-edit).

## Changes

1. **Format everything:** run `dprint fmt` from repo root. Review the diff — it
   should be whitespace/wrapping/table-alignment only (the failing files are
   markdown such as the todo skill docs). Do not let it rewrite code semantics.
2. **Add a marketplace description:** add a `description` field to
   `.claude-plugin/marketplace.json` (reuse/condense the plugin description).
3. **Resolve version drift:** **keep the version in both files and sync them** —
   set the `marketplace.json` plugin entry `version` to `0.6.0` to match
   `plugin.json`. (Claude Code lets `plugin.json` win when they differ, so the
   risk is a stale marketplace value; step 2's validator will assert they stay
   equal so they can't drift again.)
4. **Fix the stale README count:** update `README.md:14` to "6 commands" (or
   the accurate phrasing; note there are also 12 handler files under
   `commands/handlers/`). Keep the count consistent with what step 2's validator
   will assert.

## Acceptance

**Code-enforced:**

- `claude plugin validate . --strict` exits 0 with no warnings.
- `dprint check` exits 0 (no unformatted files).

**User-run:**

- Eyeball the `dprint fmt` diff to confirm it's formatting-only, no content
  regressions in skill text.
- Confirm the README command count matches the actual `commands/*.md` files.

## Dependencies

None. This is the first PR and unblocks the enforcement added in later steps.
