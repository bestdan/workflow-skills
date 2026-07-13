# workflow-skills dev tasks. Run `just <target>` from the repo root.
# These wrap the scripts/ entrypoints — the scripts remain the source of truth
# (CI calls scripts/check.sh directly, so `just` is not needed in CI).

# List available targets.
default:
    @just --list

# Run the full deterministic quality gate (exactly what CI runs).
check:
    scripts/check.sh

# Auto-format all files with dprint and shfmt.
fmt:
    dprint fmt --incremental=false
    @files=""; for file in $(git ls-files --cached --others --exclude-standard '*.sh'); do [ ! -f "$file" ] || files="$files $file"; done; [ -z "$files" ] || shfmt -i 2 -ci -bn -w $files
    @files=""; for file in $(git ls-files --cached --others --exclude-standard '*.bats'); do [ ! -f "$file" ] || files="$files $file"; done; [ -z "$files" ] || shfmt -ln bats -i 2 -ci -bn -w $files

# Check Bash syntax, formatting, and ShellCheck findings.
lint-shell:
    scripts/lint-shell.sh

# Run deterministic Bats suites and the retained orchestrator harness locally,
# including on macOS with the system Bash 3.2 compatibility floor.
test-shell:
    scripts/test-shell.sh

# Run credentialed integration checks (excluded from `just check`).
test-shell-live:
    scripts/test-shell-live.sh

# Run only the repo-native structural/consistency validator.
validate:
    uv run scripts/validate.py

# Run the gate plus behavioral skill-triggering evals (needs ANTHROPIC_API_KEY).
eval:
    scripts/check.sh --with-evals

# Preview the next Conventional-Commits version bump (no writes, no tags).
bump-preview:
    python3 scripts/bump-version.py
