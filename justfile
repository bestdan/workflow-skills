# workflow-skills dev tasks. Run `just <target>` from the repo root.
# These wrap the scripts/ entrypoints — the scripts remain the source of truth
# (CI calls scripts/check.sh directly, so `just` is not needed in CI).

# List available targets.
default:
    @just --list

# Run the full deterministic quality gate (exactly what CI runs).
check:
    scripts/check.sh

# Auto-format all files with dprint.
fmt:
    dprint fmt

# Run only the repo-native structural/consistency validator.
validate:
    uv run scripts/validate.py

# Run the gate plus behavioral skill-triggering evals (needs ANTHROPIC_API_KEY).
eval:
    scripts/check.sh --with-evals

# Preview the next Conventional-Commits version bump (no writes, no tags).
bump-preview:
    python3 scripts/bump-version.py
