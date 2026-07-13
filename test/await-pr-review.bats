#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

make_gh() {
  local responses="$TEST_TMPDIR/responses"
  mkdir -p "$responses"
  local i=0 file
  for file in "$@"; do
    i=$((i + 1))
    cp "$file" "$responses/$i"
  done
  make_stub gh "n=\$(cat '$responses/count' 2>/dev/null || echo 0)" \
    'n=$((n + 1))' "echo \"\$n\" >'$responses/count'" \
    "[ \"\$n\" -gt $i ] && n=$i" "cat \"$responses/\$n\""
}

json() { printf '{"reviews":%s,"reviewRequests":%s}\n' "$2" "$3" >"$1"; }

@test "returns when Copilot review already landed" {
  json "$TEST_TMPDIR/landed" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[]'
  make_gh "$TEST_TMPDIR/landed"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout 5
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed'
}

@test "waits until a requested reviewer lands" {
  json "$TEST_TMPDIR/wait" '[]' '[{"login":"Copilot"}]'
  json "$TEST_TMPDIR/landed" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[]'
  make_gh "$TEST_TMPDIR/wait" "$TEST_TMPDIR/landed"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout 5
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed'
}

@test "times out when review never lands" {
  json "$TEST_TMPDIR/wait" '[]' '[{"login":"Copilot"}]'
  make_gh "$TEST_TMPDIR/wait"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout 1
  assert_failure 1
  assert_output --partial 'AWAIT_REVIEW: timeout'
}

@test "all requires every reviewer while any accepts one" {
  json "$TEST_TMPDIR/one" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[{"login":"gemini-code-assist"}]'
  make_gh "$TEST_TMPDIR/one"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --reviewer Copilot --reviewer gemini-code-assist --mode all --interval 0 --timeout 1
  assert_failure 1
  assert_output --partial 'AWAIT_REVIEW: timeout'
  rm -f "$TEST_TMPDIR/responses/count"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --reviewer Copilot --reviewer gemini-code-assist --mode any --interval 0 --timeout 5
  assert_success
}

@test "rejects invalid mode" {
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --mode neither
  assert_failure 2
}
