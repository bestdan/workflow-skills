← [[task_vocabulary_normalization_plan]]

# Step 4 — Rename handler content, part 2

## Title

Convert "todo" → "task" wording in the jira and linear handlers.

## Context

- Same as Step 3: filenames stay; content only. These are the external-tracker handlers.
- Linear is a five-file family sharing `linear-common.md`; keep terminology consistent across all five so the per-verb files (`linear-add`, `linear-list`, `linear-claim`) agree with the common file.
- Several of these files use "step" as a _procedural_ word — leave those; only rename the unit-of-work noun and the loop nouns/paths/commands.

## Changes

Content rename (same substitutions as Step 3) in:

- `commands/handlers/jira.md`
- `commands/handlers/jira-config.md`
- `commands/handlers/linear-common.md`
- `commands/handlers/linear-add.md`
- `commands/handlers/linear-list.md`
- `commands/handlers/linear-claim.md`
- `commands/handlers/linear-config.md`

## Acceptance

### Code-enforced

- `rg -n '\btodos?\b|todo-loop|\.todo-config|/(add|claim|promote|process|list)-todo' commands/handlers/jira*.md commands/handlers/linear*.md` → no matches.
- `dprint check` passes on the seven files.
- `uv run scripts/validate.py` → OK.

### User-run

- None.

## Dependencies

Step 1. Independent of Steps 2–3 (no shared files); land in sequence for review coherence.
