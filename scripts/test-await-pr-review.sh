#!/usr/bin/env bash
# Test harness for scripts/await-pr-review.sh.
#
# Self-contained (no bats dependency): each test builds a stub `gh` on a
# temp PATH that emits canned `gh pr view --json reviews,reviewRequests`
# payloads, runs the fixture, and asserts the exit code and structured final
# line. Covers the four documented paths: already-landed, lands-after-N-polls,
# timeout, and multi-reviewer (--all waits for every reviewer, --any for one).
#
# Run directly: bash scripts/test-await-pr-review.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/await-pr-review.sh"

pass=0
fail=0

# make_gh_stub <dir> <response-file> [<response-file> ...]
# Writes a `gh` stub into <dir> that returns the Nth response payload on the
# Nth invocation, clamping to the last payload for any further calls. Each
# response arg is a path to a JSON file (the body printed for `gh pr view`).
make_gh_stub() {
  local dir="$1"; shift
  local i=1
  for body in "$@"; do
    cp "$body" "$dir/resp_$i.json"
    i=$((i + 1))
  done
  local last=$((i - 1))
  cat >"$dir/gh" <<STUB
#!/usr/bin/env bash
# Stub gh: ignores its args, returns the per-call response payload.
counter_file="$dir/.calls"
n=\$(cat "\$counter_file" 2>/dev/null || echo 0)
n=\$((n + 1))
echo "\$n" >"\$counter_file"
[ "\$n" -gt $last ] && n=$last
cat "$dir/resp_\$n.json"
STUB
  chmod +x "$dir/gh"
}

# write_json <path> <reviews-jq-array> <reviewRequests-jq-array>
write_json() {
  printf '{"reviews": %s, "reviewRequests": %s}\n' "$2" "$3" >"$1"
}

# review entry authored by the real Copilot login
COPILOT_REVIEW='[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]'
# reviewRequests entry uses the display name "Copilot" (the documented gotcha)
COPILOT_REQUESTED='[{"login":"Copilot"}]'
NONE='[]'

# run_case <name> <stub-dir> <expected-code> <expected-substr> [script-args...]
# The stub `gh` lives in <stub-dir>; prepend it to PATH so the fixture finds it.
run_case() {
  local name="$1"; shift
  local stub_dir="$1"; shift
  local expected_code="$1"; shift
  local expected_substr="$1"; shift
  local out code
  out="$(PATH="$stub_dir:$PATH" "$SCRIPT" "$@" 2>&1)"
  code=$?
  local final; final="$(printf '%s\n' "$out" | grep '^AWAIT_REVIEW:' | tail -1)"
  if [ "$code" = "$expected_code" ] && printf '%s' "$final" | grep -qF "$expected_substr"; then
    pass=$((pass + 1))
    echo "ok   - $name"
  else
    fail=$((fail + 1))
    echo "FAIL - $name"
    echo "       expected exit=$expected_code final~='$expected_substr'"
    echo "       got      exit=$code      final='$final'"
  fi
}

# 1. already-landed: review present on first poll, returns immediately, exit 0.
t1="$(mktemp -d)"
write_json "$t1/landed.json" "$COPILOT_REVIEW" "$NONE"
make_gh_stub "$t1" "$t1/landed.json"
run_case "already-landed returns immediately" "$t1" 0 "AWAIT_REVIEW: landed" \
  --pr 1 --repo o/r --interval 0 --timeout 5
rm -rf "$t1"

# 2. lands-after-N-polls: not landed for 2 polls, then landed on the 3rd.
t2="$(mktemp -d)"
write_json "$t2/wait.json" "$NONE" "$COPILOT_REQUESTED"
write_json "$t2/landed.json" "$COPILOT_REVIEW" "$NONE"
make_gh_stub "$t2" "$t2/wait.json" "$t2/wait.json" "$t2/landed.json"
run_case "lands after N polls" "$t2" 0 "AWAIT_REVIEW: landed" \
  --pr 1 --repo o/r --interval 0 --timeout 5
rm -rf "$t2"

# 3. timeout: never lands, exits non-zero with a timeout line.
t3="$(mktemp -d)"
write_json "$t3/wait.json" "$NONE" "$COPILOT_REQUESTED"
make_gh_stub "$t3" "$t3/wait.json"
run_case "timeout when review never lands" "$t3" 1 "AWAIT_REVIEW: timeout" \
  --pr 1 --repo o/r --interval 0 --timeout 1
rm -rf "$t3"

# 4a. multi-reviewer --all: both reviewers must land. Only one has → timeout.
t4="$(mktemp -d)"
write_json "$t4/one.json" \
  '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' \
  '[{"login":"gemini-code-assist"}]'
make_gh_stub "$t4" "$t4/one.json"
run_case "multi-reviewer --all waits for all (timeout when one missing)" "$t4" 1 "AWAIT_REVIEW: timeout" \
  --pr 1 --repo o/r --reviewer Copilot --reviewer gemini-code-assist \
  --mode all --interval 0 --timeout 1
rm -rf "$t4"

# 4b. multi-reviewer --any: first reviewer to land satisfies it → exit 0.
t5="$(mktemp -d)"
write_json "$t5/one.json" \
  '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' \
  '[{"login":"gemini-code-assist"}]'
make_gh_stub "$t5" "$t5/one.json"
run_case "multi-reviewer --any returns on first landed" "$t5" 0 "AWAIT_REVIEW: landed" \
  --pr 1 --repo o/r --reviewer Copilot --reviewer gemini-code-assist \
  --mode any --interval 0 --timeout 5
rm -rf "$t5"

# 5. fallback signal: reviewer requested on first poll, then drops out of
# reviewRequests[] with no reviews[] entry → counted as landed (it reviewed
# and was cleared). Guards the documented secondary precedence.
t6="$(mktemp -d)"
write_json "$t6/requested.json" "$NONE" "$COPILOT_REQUESTED"
write_json "$t6/cleared.json" "$NONE" "$NONE"
make_gh_stub "$t6" "$t6/requested.json" "$t6/cleared.json"
run_case "fallback: dropped from reviewRequests counts as landed" "$t6" 0 "AWAIT_REVIEW: landed" \
  --pr 1 --repo o/r --interval 0 --timeout 5
rm -rf "$t6"

echo
echo "await-pr-review tests: $pass passed, $fail failed"
[ "$fail" = 0 ]
