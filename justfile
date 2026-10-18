# workflow-skills dev tasks. Run `just <target>` from the repo root.
# These wrap the scripts/ entrypoints — the scripts remain the source of truth
# (CI calls scripts/check.sh directly, so `just` is not needed in CI).

# Recipe arguments reach the recipe shell as "$@" rather than being
# interpolated as text, so a free-text argument containing `;`, backticks or
# `$()` is passed through instead of executed.
set positional-arguments

# List available targets.
default:
    @just --list

# Run the full deterministic quality gate (exactly what CI runs).
check:
    scripts/check.sh

# Skips the two long suites and lints only the files this branch touched, so it
# skips REAL coverage — `just check` must still pass before you push.
# (`just --list` shows only the last comment line, so keep the summary last.)
# Edit-loop gate: the full gate minus its three slowest parts (~8s, not ~66s).
check-fast:
    scripts/check.sh --fast

# Auto-format all files with dprint and shfmt.
fmt:
    dprint fmt --incremental=false
    @files=""; for file in $(git ls-files --cached --others --exclude-standard '*.sh' '*.bash'); do [ ! -f "$file" ] || files="$files $file"; done; [ -z "$files" ] || shfmt -i 2 -ci -bn -w $files
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

# Bundle the full post-fix verification sequence (full run, stability
# repeats, process/orphan watch, confinement smoke, final gate check) into
# one invocation instead of hand-composing it. `just verify-fix "fixed X"`,
# or pass flags through: `just verify-fix --runs 5 "fixed X"`.
verify-fix *args:
    scripts/verify-fix.sh "$@"

# Preview the next Conventional-Commits version bump (no writes, no tags).
bump-preview:
    python3 scripts/bump-version.py
