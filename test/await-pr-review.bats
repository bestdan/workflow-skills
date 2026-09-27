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
  # `gh api` serves the timeline and commit fixtures (empty by default); every
  # `gh pr view` serves the next response in sequence, repeating the last.
  make_stub gh \
    "case \"\$*\" in *'/timeline'*) cat '$TEST_TMPDIR/timeline' 2>/dev/null || echo '[]'; exit ;; esac" \
    "case \"\$*\" in *'/commits/'*) cat '$TEST_TMPDIR/commit' 2>/dev/null || echo '{}'; exit ;; esac" \
    "n=\$(cat '$responses/count' 2>/dev/null || echo 0)" \
    'n=$((n + 1))' "echo \"\$n\" >'$responses/count'" \
    "[ \"\$n\" -gt $i ] && n=$i" "cat \"$responses/\$n\""
}

json() { printf '{"reviews":%s,"reviewRequests":%s}\n' "$2" "$3" >"$1"; }

# Safety net for the tests that assert a review LANDS, deliberately not a small
# number. Those tests are about the poll transition (wait -> landed), not about
# the deadline: with --interval 0 the loop is bounded by how fast it can poll,
# and --timeout only decides when to give up. Setting it low makes the success
# path race real elapsed time, because the timeout is checked only between
# polls -- so one slow poll ends the run before the landed fixture is ever
# fetched. That is not hypothetical: at --timeout 5 this suite failed under
# scripts/check.sh (13 checks running concurrently) with
# `AWAIT_REVIEW: timeout reviewer=copilot after=6s` and gh called exactly once,
# while passing whenever it was run on its own. Keep this comfortably above any
# plausible poll latency, and keep it BOUNDED so a genuine regression fails the
# suite instead of hanging it. The timeout-asserting tests below pass their own
# small --timeout: they never land, so elapsing is guaranteed and they are not
# racy.
LAND_TIMEOUT=60

@test "returns when Copilot review already landed" {
  json "$TEST_TMPDIR/landed" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[]'
  make_gh "$TEST_TMPDIR/landed"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT"
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed'
}

@test "waits until a requested reviewer lands" {
  json "$TEST_TMPDIR/wait" '[]' '[{"login":"Copilot"}]'
  json "$TEST_TMPDIR/landed" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[]'
  make_gh "$TEST_TMPDIR/wait" "$TEST_TMPDIR/landed"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT"
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed'
}

@test "a requested reviewer that drops out without a review has not landed" {
  # Copilot leaves reviewRequests[] when it starts work, minutes before its
  # review exists.
  json "$TEST_TMPDIR/requested" '[]' '[{"login":"Copilot"}]'
  json "$TEST_TMPDIR/cleared" '[]' '[]'
  make_gh "$TEST_TMPDIR/requested" "$TEST_TMPDIR/cleared"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout 1
  assert_failure 1
  assert_output --partial 'AWAIT_REVIEW: timeout reviewer=copilot'
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
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --reviewer Copilot --reviewer gemini-code-assist --mode any --interval 0 --timeout "$LAND_TIMEOUT"
  assert_success
}

@test "grace drops a reviewer that was never requested" {
  json "$TEST_TMPDIR/none" '[]' '[]'
  make_gh "$TEST_TMPDIR/none"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT" --grace 0
  assert_failure 3
  assert_output --partial 'AWAIT_REVIEW: not-requested reviewer=copilot'
}

@test "grace keeps waiting on a reviewer that was requested" {
  json "$TEST_TMPDIR/wait" '[]' '[{"login":"Copilot"}]'
  json "$TEST_TMPDIR/landed" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[]'
  make_gh "$TEST_TMPDIR/wait" "$TEST_TMPDIR/wait" "$TEST_TMPDIR/landed"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT" --grace 0
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed reviewer=copilot'
}

@test "grace keeps a reviewer the timeline shows at work" {
  printf '%s\n' '[{"event":"review_requested","created_at":"2026-01-02T00:00:01Z","requested_reviewer":{"login":"Copilot"}},{"event":"copilot_work_started","created_at":"2026-01-02T00:00:30Z"}]' >"$TEST_TMPDIR/timeline"
  json "$TEST_TMPDIR/working" '[]' '[]'
  json "$TEST_TMPDIR/landed" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[]'
  make_gh "$TEST_TMPDIR/working" "$TEST_TMPDIR/working" "$TEST_TMPDIR/landed"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT" --grace 0
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed reviewer=copilot'
}

@test "grace ignores a timeline request older than the commit" {
  printf '%s\n' '[{"event":"review_requested","created_at":"2026-01-01T00:00:00Z","requested_reviewer":{"login":"Copilot"}}]' >"$TEST_TMPDIR/timeline"
  printf '%s\n' '{"commit":{"committer":{"date":"2026-01-02T00:00:00Z"}}}' >"$TEST_TMPDIR/commit"
  json "$TEST_TMPDIR/none" '[]' '[]'
  make_gh "$TEST_TMPDIR/none"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT" --grace 0 --commit bbbb2222
  assert_failure 3
  assert_output --partial 'AWAIT_REVIEW: not-requested reviewer=copilot'
}

@test "grace reports a dropped reviewer beside the ones that landed" {
  json "$TEST_TMPDIR/one" '[{"author":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED"}]' '[]'
  make_gh "$TEST_TMPDIR/one"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --reviewer Copilot --reviewer gemini-code-assist --interval 0 --timeout "$LAND_TIMEOUT" --grace 0
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed reviewer=copilot'
  assert_output --partial 'not-requested=gemini-code-assist'
}

@test "commit ignores a review of an earlier push" {
  json "$TEST_TMPDIR/old" '[{"author":{"login":"copilot-pull-request-reviewer"},"commit":{"oid":"aaaa1111"}}]' '[{"login":"Copilot"}]'
  json "$TEST_TMPDIR/new" '[{"author":{"login":"copilot-pull-request-reviewer"},"commit":{"oid":"aaaa1111"}},{"author":{"login":"copilot-pull-request-reviewer"},"commit":{"oid":"bbbb2222"}}]' '[{"login":"Copilot"}]'
  make_gh "$TEST_TMPDIR/old" "$TEST_TMPDIR/new"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT" --commit bbbb2222
  assert_success
  assert_output --partial 'AWAIT_REVIEW: landed'
  [ "$(cat "$TEST_TMPDIR/responses/count")" = 2 ]
}

@test "commit with no new request is not-requested under grace" {
  json "$TEST_TMPDIR/old" '[{"author":{"login":"copilot-pull-request-reviewer"},"commit":{"oid":"aaaa1111"}}]' '[]'
  make_gh "$TEST_TMPDIR/old"
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --interval 0 --timeout "$LAND_TIMEOUT" --grace 0 --commit bbbb2222
  assert_failure 3
  assert_output --partial 'AWAIT_REVIEW: not-requested'
}

@test "rejects a commit that is not a hex sha" {
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --commit 'main'
  assert_failure 2
  assert_output --partial '--commit must be a hex commit sha'
}

@test "rejects invalid mode" {
  run "$REPO_ROOT/scripts/await-pr-review.sh" --pr 1 --repo o/r --mode neither
  assert_failure 2
  assert_output --partial "--mode must be 'all' or 'any'"
}
