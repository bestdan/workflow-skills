#!/usr/bin/env bash
# test-grok-telemetry-gate.sh — tests for scripts/grok-telemetry-gate.py.
#
# The two regression cases at the bottom are the fail-opens that the previous
# `grep`-based gate actually had; both were reproduced before the script
# existed, so they are regressions, not hypotheticals.
#
# Run directly: bash scripts/test-grok-telemetry-gate.sh
# Also run under macOS's stock bash: /bin/bash scripts/test-grok-telemetry-gate.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/grok-telemetry-gate.py"

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

tmp="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/test-grok-telemetry-gate.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.test-grok-telemetry-gate.XXXXXX")"
# Fail closed: an empty tmp would make every fixture path absolute-from-root,
# and the EXIT trap's `rm -rf ""` would silently do nothing while the suite
# reported failures for the wrong reason. Same guard as the other harnesses —
# see test/harness-temp-dir-guard.bats for the bug this shape exists to stop.
[ -n "$tmp" ] && [ -d "$tmp" ] || {
  echo "test-grok-telemetry-gate: could not create a temp dir" >&2
  exit 2
}
tmp="$(cd "$tmp" && pwd -P)" || exit 2
trap 'rm -rf "$tmp"' EXIT

# Keep the harness's own environment from deciding the answer.
unset GROK_TELEMETRY_ENABLED GROK_TELEMETRY_TRACE_UPLOAD GROK_HOME

write_pinned() {
  cat >"$1" <<'TOML'
[cli]
installer = "internal"

[features]
telemetry = false

[telemetry]
trace_upload = false

[harness]
disable_codebase_upload = true
TOML
}

# --- The happy path ---------------------------------------------------------

write_pinned "$tmp/pinned.toml"
python3 "$SCRIPT" "$tmp/pinned.toml" >/dev/null 2>&1
assert_eq "fully pinned config: exits 0" 0 "$?"

# --- Missing and malformed --------------------------------------------------

python3 "$SCRIPT" "$tmp/does-not-exist.toml" >/dev/null 2>&1
assert_eq "missing config: exits 2" 2 "$?"

# Garbage is readable, so it is not a "2" (missing/unreadable): the scanner
# simply recognises nothing in it, every required key is absent, and the gate
# reports "not pinned". Both codes are a SKIP for co-review, so what this
# asserts is that garbage fails CLOSED, not which of the two codes it picks.
printf 'this is not = valid = toml\n' >"$tmp/broken.toml"
python3 "$SCRIPT" "$tmp/broken.toml" >/dev/null 2>&1
assert_eq "unparseable config: fails closed at 1" 1 "$?"

printf '\n' >"$tmp/empty.toml"
python3 "$SCRIPT" "$tmp/empty.toml" >/dev/null 2>&1
assert_eq "empty config: fails closed at 1" 1 "$?"

# A bare key before any table header belongs to no table and must not count.
printf 'trace_upload = false\ntelemetry = false\ndisable_codebase_upload = true\n' >"$tmp/notable.toml"
python3 "$SCRIPT" "$tmp/notable.toml" >/dev/null 2>&1
assert_eq "keys with no table header: exits 1" 1 "$?"

# A trailing comment on a real assignment is still a real assignment.
cat >"$tmp/trailing.toml" <<'TOML'
[features]
telemetry = false # pinned by co-review

[telemetry]
trace_upload = false

[harness]
disable_codebase_upload = true
TOML
python3 "$SCRIPT" "$tmp/trailing.toml" >/dev/null 2>&1
assert_eq "trailing comment on an assignment: exits 0" 0 "$?"

# --- Each key individually missing ------------------------------------------

for key in telemetry trace_upload disable_codebase_upload; do
  write_pinned "$tmp/drop.toml"
  grep -v "^$key = " "$tmp/drop.toml" >"$tmp/drop2.toml"
  mv "$tmp/drop2.toml" "$tmp/drop.toml"
  python3 "$SCRIPT" "$tmp/drop.toml" >/dev/null 2>&1
  assert_eq "missing $key: exits 1" 1 "$?"
done

# --- Regression 1: a commented-out key must not satisfy the gate ------------
# The old `grep -c` substring form counted this line and printed 3.

cat >"$tmp/commented.toml" <<'TOML'
[features]
telemetry = false

[telemetry]
# trace_upload = false

[harness]
disable_codebase_upload = true
TOML
python3 "$SCRIPT" "$tmp/commented.toml" >/dev/null 2>&1
assert_eq "regression: commented-out key exits 1" 1 "$?"

# --- Regression 2: a key in the WRONG table must not satisfy the gate -------
# Anchoring the grep fixed regression 1 but not this: the assignment is real
# and uncommented, and grok ignores it because the table is wrong.

cat >"$tmp/wrongtable.toml" <<'TOML'
[features]
telemetry = false

[marketplace]
trace_upload = false

[harness]
disable_codebase_upload = true
TOML
python3 "$SCRIPT" "$tmp/wrongtable.toml" >/dev/null 2>&1
assert_eq "regression: key in the wrong table exits 1" 1 "$?"

# --- A wrong VALUE is not the same as a missing key -------------------------

cat >"$tmp/inverted.toml" <<'TOML'
[features]
telemetry = false

[telemetry]
trace_upload = true

[harness]
disable_codebase_upload = true
TOML
python3 "$SCRIPT" "$tmp/inverted.toml" >/dev/null 2>&1
assert_eq "trace_upload = true exits 1" 1 "$?"

# --- The env layer outranks the file ----------------------------------------

write_pinned "$tmp/pinned.toml"

GROK_TELEMETRY_TRACE_UPLOAD=1 python3 "$SCRIPT" "$tmp/pinned.toml" >/dev/null 2>&1
assert_eq "truthy env var over a pinned config: exits 1" 1 "$?"

GROK_TELEMETRY_ENABLED=true python3 "$SCRIPT" "$tmp/pinned.toml" >/dev/null 2>&1
assert_eq "GROK_TELEMETRY_ENABLED=true: exits 1" 1 "$?"

GROK_TELEMETRY_TRACE_UPLOAD=0 python3 "$SCRIPT" "$tmp/pinned.toml" >/dev/null 2>&1
assert_eq "falsey env var agrees with the config: exits 0" 0 "$?"

GROK_TELEMETRY_TRACE_UPLOAD=off python3 "$SCRIPT" "$tmp/pinned.toml" >/dev/null 2>&1
assert_eq "env var spelled 'off': exits 0" 0 "$?"

# --- GROK_HOME selects the default config -----------------------------------

mkdir -p "$tmp/home"
write_pinned "$tmp/home/config.toml"
GROK_HOME="$tmp/home" python3 "$SCRIPT" >/dev/null 2>&1
assert_eq "GROK_HOME picks up its config.toml: exits 0" 0 "$?"

# --- Usage ------------------------------------------------------------------

python3 "$SCRIPT" a b >/dev/null 2>&1
assert_eq "too many args: exits 2" 2 "$?"

echo
echo "test-grok-telemetry-gate: $pass_count passed, $fail_count failed"
exit "$fail"
