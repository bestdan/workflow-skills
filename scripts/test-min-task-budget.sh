#!/usr/bin/env bash
# test-min-task-budget.sh — fixture-free tests for scripts/min-task-budget.sh.
#
# Run directly: bash scripts/test-min-task-budget.sh
# Also run under macOS's stock bash: /bin/bash scripts/test-min-task-budget.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/min-task-budget.sh"

fail=0
pass_count=0
fail_count=0

ok() {
  pass_count=$((pass_count + 1))
  echo "  OK $1"
}

bad() {
  fail_count=$((fail_count + 1))
  fail=1
  echo "  FAIL $1" >&2
}

assert_eq() {
  # assert_eq <description> <expected> <actual>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected '$2', got '$3')"
  fi
}

# --- Anchor cases -----------------------------------------------------------

out="$("$SCRIPT")"
rc=$?
assert_eq "no args: exits 0" 0 "$rc"
assert_eq "no args: prints 18m" 18m "$out"

out="$("$SCRIPT" codex)"
assert_eq "codex: prints 24m" 24m "$out"

out="$("$SCRIPT" codex agy)"
assert_eq "codex agy: prints 63m" 63m "$out"

out="$("$SCRIPT" devin)"
assert_eq "devin: prints 63m" 63m "$out"

out="$("$SCRIPT" agy codex)"
assert_eq "agy codex (order-independent): prints 63m" 63m "$out"

out="$("$SCRIPT" gemini)"
assert_eq "gemini alone: prints 18m" 18m "$out"

out="$("$SCRIPT" gemini codex)"
assert_eq "gemini codex: prints 24m" 24m "$out"

# --- Unknown reviewer: fails closed ------------------------------------------

STDERR_FILE="${TMPDIR:-/tmp}/test-min-task-budget-stderr.$$"
out="$("$SCRIPT" frobnicator 2>"$STDERR_FILE")"
rc=$?
assert_eq "unknown reviewer: exits 2" 2 "$rc"
assert_eq "unknown reviewer: stdout empty" "" "$out"
if grep -q "frobnicator" "$STDERR_FILE"; then
  ok "unknown reviewer: stderr mentions frobnicator"
else
  bad "unknown reviewer: stderr did not mention frobnicator: $(cat "$STDERR_FILE")"
fi
rm -f "$STDERR_FILE"

# --- --help ------------------------------------------------------------------

"$SCRIPT" --help >/dev/null 2>&1
rc=$?
assert_eq "--help: exits 0" 0 "$rc"

# --- Output shape -------------------------------------------------------------

for args in "" "codex" "codex agy" "devin" "agy codex" "gemini" "gemini codex"; do
  # shellcheck disable=SC2086
  out="$("$SCRIPT" $args)"
  digits="${out%m}"
  case "$out" in
    *m)
      case "$digits" in
        '' | *[!0-9]*) bad "output shape for '$args': '$out' does not match ^[0-9]+m\$" ;;
        *) ok "output shape for '$args': '$out' matches ^[0-9]+m\$" ;;
      esac
      ;;
    *) bad "output shape for '$args': '$out' does not match ^[0-9]+m\$" ;;
  esac
done

echo
echo "test-min-task-budget: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-min-task-budget: OK"
