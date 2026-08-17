#!/usr/bin/env bash
# test-verify-fix.sh — fixture-based tests for scripts/verify-fix.sh.
#
# Hermetic: every case runs against a throwaway fixture tree under mktemp,
# each carrying its own COPY of verify-fix.sh (the script resolves its ROOT
# from its own path — see `ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/..")`
# in verify-fix.sh — so a fixture tree only exercises the real resolution
# logic if the script actually lives inside it) plus stub `check`/`dli`/`ps`/
# `smoke-confinement.sh` commands, never the real ~40s gate. Nothing here
# touches the network or the real repo tree.
#
# Run directly: bash scripts/test-verify-fix.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_SRC="$ROOT/scripts/verify-fix.sh"

[ -f "$SCRIPT_SRC" ] || {
  echo "test-verify-fix: $SCRIPT_SRC not found" >&2
  exit 2
}

BASE="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/test-verify-fix.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.test-verify-fix.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT
BASE="$(cd "$BASE" && pwd -P)" || exit 2

pass=0
fail=0
PASS() {
  pass=$((pass + 1))
  printf '  \033[32mPASS\033[0m  %s\n' "$1"
}
FAIL() {
  fail=$((fail + 1))
  printf '  \033[31mFAIL\033[0m  %s\n' "$1"
  [ -z "${2:-}" ] || printf '        %s\n' "$2"
}

# new_fixture <name> — a fresh fixture dir under BASE with its own
# scripts/verify-fix.sh copy and scripts/ dir. Echoes the fixture path.
new_fixture() {
  local dir="$BASE/$1"
  mkdir -p "$dir/scripts" "$dir/bin"
  cp "$SCRIPT_SRC" "$dir/scripts/verify-fix.sh"
  chmod +x "$dir/scripts/verify-fix.sh"
  echo "$dir"
}

# stub_check <fixture> <exit-code> — an executable scripts/check.sh that
# exits with the given code, printing a marker line so failure-path tests
# can confirm the captured output made it into the diagnostics dump.
stub_check() {
  local dir="$1" rc="$2"
  cat >"$dir/scripts/check.sh" <<EOF
#!/usr/bin/env bash
echo "stub-check-marker-line"
exit $rc
EOF
  chmod +x "$dir/scripts/check.sh"
}

run_verify_fix() {
  local dir="$1"
  shift
  (cd "$dir" && PATH="$dir/bin:$PATH" bash "$dir/scripts/verify-fix.sh" "$@")
}

# --- resolve_check_command precedence -------------------------------------

echo "== resolve_check_command precedence =="

d="$(new_fixture resolve-floor)"
stub_check "$d" 0
out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: floor" 2>&1)"
if echo "$out" | grep -q 'resolved gate command: scripts/check.sh'; then
  PASS "floor: scripts/check.sh chosen when no dli/just config present"
else
  FAIL "floor: scripts/check.sh chosen when no dli/just config present" "$out"
fi

d="$(new_fixture resolve-dli)"
stub_check "$d" 0
: >"$d/dli.toml"
cat >"$d/bin/dli" <<'EOF'
#!/usr/bin/env bash
[ "$1" = check ] || exit 2
echo "stub-dli-marker-line"
exit 0
EOF
chmod +x "$d/bin/dli"
out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: dli" 2>&1)"
if echo "$out" | grep -q 'resolved gate command: dli check'; then
  PASS "dli check outranks scripts/check.sh when dli.toml + dli on PATH"
else
  FAIL "dli check outranks scripts/check.sh when dli.toml + dli on PATH" "$out"
fi

if command -v just >/dev/null 2>&1; then
  d="$(new_fixture resolve-just)"
  stub_check "$d" 0
  printf 'check:\n\techo stub-just-marker-line\n' >"$d/justfile"
  out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: just" 2>&1)"
  if echo "$out" | grep -q 'resolved gate command: just check'; then
    PASS "just check outranks scripts/check.sh when justfile has a check recipe"
  else
    FAIL "just check outranks scripts/check.sh when justfile has a check recipe" "$out"
  fi
else
  echo "  (skip) just check precedence — 'just' not installed on this host"
fi

d="$(new_fixture resolve-none)"
out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: none" 2>&1)"
rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q 'no check command found'; then
  PASS "no check command found -> exit 2 with a clear message"
else
  FAIL "no check command found -> exit 2 with a clear message" "$out"
fi

# --- CLI validation ---------------------------------------------------------

echo "== CLI validation =="

d="$(new_fixture cli-no-description)"
stub_check "$d" 0
out="$(run_verify_fix "$d" --runs 1 2>&1)"
rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q 'usage:'; then
  PASS "missing <description> -> exit 2 with usage"
else
  FAIL "missing <description> -> exit 2 with usage" "$out"
fi

d="$(new_fixture cli-bad-runs)"
stub_check "$d" 0
out="$(run_verify_fix "$d" --runs 0 "desc" 2>&1)"
rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q -- '--runs must be'; then
  PASS "--runs 0 -> exit 2"
else
  FAIL "--runs 0 -> exit 2" "$out"
fi

d="$(new_fixture cli-nonnumeric-runs)"
stub_check "$d" 0
out="$(run_verify_fix "$d" --runs abc "desc" 2>&1)"
rc=$?
if [ "$rc" -eq 2 ] && echo "$out" | grep -q -- '--runs must be'; then
  PASS "--runs abc -> exit 2"
else
  FAIL "--runs abc -> exit 2" "$out"
fi

# --- pass/fail + diagnostics -------------------------------------------------

echo "== pass/fail + diagnostics =="

d="$(new_fixture happy-path)"
stub_check "$d" 0
out="$(run_verify_fix "$d" --runs 2 --skip-confinement "case: happy path" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q '✅ verify-fix passed: case: happy path'; then
  PASS "all rounds pass -> exit 0 with a summary line"
else
  FAIL "all rounds pass -> exit 0 with a summary line" "$out (rc=$rc)"
fi
rounds="$(echo "$out" | grep -c 'passed$')"
# 2 stability rounds + 1 final gate check round = 3 passing rounds.
if [ "$rounds" -ge 3 ]; then
  PASS "--runs 2 produces 2 stability rounds plus 1 final gate round"
else
  FAIL "--runs 2 produces 2 stability rounds plus 1 final gate round" "$out"
fi

d="$(new_fixture failing-path)"
stub_check "$d" 1
out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: failing" 2>&1)"
rc=$?
if [ "$rc" -eq 1 ] && echo "$out" | grep -q 'FAILED'; then
  PASS "a failing round -> exit 1"
else
  FAIL "a failing round -> exit 1" "$out (rc=$rc)"
fi
if echo "$out" | grep -q 'diagnostics:' && echo "$out" | grep -q 'stub-check-marker-line'; then
  PASS "failure dumps diagnostics including the captured output"
else
  FAIL "failure dumps diagnostics including the captured output" "$out"
fi
if echo "$out" | grep -q 'process snapshot'; then
  PASS "failure diagnostics include a process snapshot"
else
  FAIL "failure diagnostics include a process snapshot" "$out"
fi
if echo "$out" | grep -q '✅ verify-fix passed'; then
  FAIL "a failing round must not also print the success summary"
else
  PASS "a failing round does not print the success summary"
fi

# --- smoke-confinement wiring ------------------------------------------------

echo "== smoke-confinement wiring =="

d="$(new_fixture confinement-invoked)"
stub_check "$d" 0
cat >"$d/scripts/smoke-confinement.sh" <<EOF
#!/usr/bin/env bash
: >"$d/confinement-ran"
echo "stub-confinement-marker-line"
exit 0
EOF
chmod +x "$d/scripts/smoke-confinement.sh"
out="$(run_verify_fix "$d" --runs 1 "case: confinement runs" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && [ -f "$d/confinement-ran" ]; then
  PASS "smoke-confinement.sh is invoked when present and not skipped"
else
  FAIL "smoke-confinement.sh is invoked when present and not skipped" "$out (rc=$rc)"
fi

d="$(new_fixture confinement-skip-flag)"
stub_check "$d" 0
cat >"$d/scripts/smoke-confinement.sh" <<EOF
#!/usr/bin/env bash
: >"$d/confinement-ran"
exit 0
EOF
chmod +x "$d/scripts/smoke-confinement.sh"
out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: confinement skipped by flag" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && [ ! -f "$d/confinement-ran" ] && echo "$out" | grep -q 'skipped (--skip-confinement)'; then
  PASS "--skip-confinement prevents smoke-confinement.sh from running"
else
  FAIL "--skip-confinement prevents smoke-confinement.sh from running" "$out (rc=$rc)"
fi

d="$(new_fixture confinement-not-macos)"
stub_check "$d" 0
cat >"$d/scripts/smoke-confinement.sh" <<'EOF'
#!/usr/bin/env bash
echo "sandbox-exec required (macOS only)"
exit 2
EOF
chmod +x "$d/scripts/smoke-confinement.sh"
out="$(run_verify_fix "$d" --runs 1 "case: confinement not macOS" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q 'smoke-confinement skipped (macOS/sandbox-exec only'; then
  PASS "smoke-confinement's own macOS-only guard is treated as a skip, not a failure"
else
  FAIL "smoke-confinement's own macOS-only guard is treated as a skip, not a failure" "$out (rc=$rc)"
fi

d="$(new_fixture confinement-real-failure)"
stub_check "$d" 0
cat >"$d/scripts/smoke-confinement.sh" <<'EOF'
#!/usr/bin/env bash
echo "confinement wall did not hold"
exit 1
EOF
chmod +x "$d/scripts/smoke-confinement.sh"
out="$(run_verify_fix "$d" --runs 1 "case: confinement real failure" 2>&1)"
rc=$?
if [ "$rc" -eq 1 ] && echo "$out" | grep -q 'smoke-confinement FAILED' && echo "$out" | grep -q 'confinement wall did not hold'; then
  PASS "a real smoke-confinement failure fails the run and surfaces its output"
else
  FAIL "a real smoke-confinement failure fails the run and surfaces its output" "$out (rc=$rc)"
fi

# --- process/orphan watch ----------------------------------------------------

echo "== process/orphan watch =="

# A stub `ps` that reports one extra pid=1-parented process on its SECOND
# invocation (watch_processes calls it once before and once after the
# wrapped command), so the "before" snapshot from round 1 is clean and the
# "after" snapshot shows a newly-appeared orphan — deterministically, with no
# dependency on what the real host's process table happens to contain.
d="$(new_fixture process-watch-flags-new-orphan)"
stub_check "$d" 0
cat >"$d/bin/ps" <<EOF
#!/usr/bin/env bash
COUNT_FILE="$d/ps-call-count"
n=0
[ -f "\$COUNT_FILE" ] && n="\$(cat "\$COUNT_FILE")"
n=\$((n + 1))
echo "\$n" >"\$COUNT_FILE"
if [ "\$n" -ge 2 ]; then
  printf '99999 1\n'
fi
EOF
chmod +x "$d/bin/ps"
out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: orphan flagged" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q 'possible orphan'; then
  PASS "a process newly reparented to init during a round is flagged"
else
  FAIL "a process newly reparented to init during a round is flagged" "$out (rc=$rc)"
fi

# The stable case: `ps` reports the same pid=1-parented process (a
# long-running daemon, say) on every call, so it is present in both the
# "before" and "after" snapshots and comm -13 must not treat it as new.
d="$(new_fixture process-watch-stable)"
stub_check "$d" 0
cat >"$d/bin/ps" <<'EOF'
#!/usr/bin/env bash
printf '12345 1\n'
EOF
chmod +x "$d/bin/ps"
out="$(run_verify_fix "$d" --runs 1 --skip-confinement "case: no orphan" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && ! echo "$out" | grep -q 'possible orphan'; then
  PASS "a stable process table does not trigger a false orphan warning"
else
  FAIL "a stable process table does not trigger a false orphan warning" "$out (rc=$rc)"
fi

echo
echo "== Summary =="
printf '  %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
