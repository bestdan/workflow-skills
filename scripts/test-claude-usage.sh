#!/usr/bin/env bash
# Test harness for scripts/claude-usage.sh.
#
# Self-contained and offline: every case drives the script through its
# `--from-file` path (which skips the network + credential store), so the
# parser and fail-closed behavior are exercised against canned responses with
# no live query. Asserts exit code and, on the happy paths, the emitted output.
#
# Run directly: bash scripts/test-claude-usage.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/claude-usage.sh"

BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.cu-test.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT

pass=0
fail=0

# fixture <name> <json> — write a response file and echo its path.
fixture() { local p="$BASE/$1.json"; printf '%s' "$2" > "$p"; printf '%s' "$p"; }

# check <desc> <expected-rc> <cmd...> — run, capture stdout+rc, assert rc, and
# (optionally, via a following want_out call) assert output. Sets `out`/`rc`.
run_case() {
  local desc="$1" want_rc="$2"; shift 2
  out="$("$@" 2>/dev/null)"; rc=$?
  if [ "$rc" -eq "$want_rc" ]; then
    printf 'ok   - %s\n' "$desc"; pass=$((pass + 1))
  else
    printf 'FAIL - %s (rc=%s, want %s)\n' "$desc" "$rc" "$want_rc"; fail=$((fail + 1))
  fi
}

want_out() {  # assert the last run_case's stdout contains <substr>
  local desc="$1" substr="$2"
  case "$out" in
    *"$substr"*) printf 'ok   - %s\n' "$desc"; pass=$((pass + 1)) ;;
    *) printf 'FAIL - %s (output %q lacks %q)\n' "$desc" "$out" "$substr"; fail=$((fail + 1)) ;;
  esac
}

SESSION='{"limits":[{"kind":"session","percent":42.6,"resets_at":"2026-07-10T05:00:00Z"},{"kind":"weekly_all","percent":18,"resets_at":"2026-07-14T00:00:00Z"}],"spend":{"used":{"amount_minor":32261}}}'

# --- happy paths ----------------------------------------------------------
f="$(fixture session "$SESSION")"
run_case "valid session → rc 0"                   0 bash "$SCRIPT" --from-file "$f"
want_out "  emits session percent (rounded 42.6→43)" '"session":{"percent":43'
want_out "  emits session resets_at"               '"resets_at":"2026-07-10T05:00:00Z"'
want_out "  emits spend_used_minor"                '"spend_used_minor":32261'

run_case "--session-percent → bare integer, rc 0"  0 bash "$SCRIPT" --from-file "$f" --session-percent
want_out "  bare percent is 43"                    '43'

# --- fail-closed paths (rc 1 → caller falls back to the proxy) -------------
f="$(fixture nosession '{"limits":[{"kind":"weekly_all","percent":5}]}')"
run_case "missing session window → rc 1"           1 bash "$SCRIPT" --from-file "$f"

f="$(fixture nonnumeric '{"limits":[{"kind":"session","percent":"oops"}]}')"
run_case "non-numeric session percent → rc 1"      1 bash "$SCRIPT" --from-file "$f"

f="$(fixture badjson 'not json at all')"
run_case "invalid JSON → rc 1"                     1 bash "$SCRIPT" --from-file "$f"

f="$(fixture empty '')"
run_case "empty response → rc 1"                   1 bash "$SCRIPT" --from-file "$f"

run_case "unreadable --from-file → rc 1"           1 bash "$SCRIPT" --from-file "$BASE/does-not-exist.json"

# --- argument errors (rc 2) -----------------------------------------------
run_case "unknown argument → rc 2"                 2 bash "$SCRIPT" --nope
run_case "--from-file with no path → rc 2"         2 bash "$SCRIPT" --from-file

printf '\nclaude-usage tests: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
