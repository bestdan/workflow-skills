#!/usr/bin/env bats
#
# scripts/lint-mktemp.sh — keeps template-less `mktemp` out of the tree.
#
# The hazard: on macOS `mktemp`, `mktemp -d` and `mktemp -t <prefix>` create
# under /var/folders and ignore $TMPDIR, which the Claude Code sandbox denies.
# Fixture inputs live in test/fixtures/lint-mktemp/*.txt so the tree-wide scan
# does not sweep up their deliberate violations.

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

lint() { run "$REPO_ROOT/scripts/lint-mktemp.sh" "$@"; }
fx() { printf '%s' "$REPO_ROOT/test/fixtures/lint-mktemp/$1"; }

@test "catches mktemp with no template in every spelling" {
  # Bare, -d, -t <prefix>, backticks, a statement followed by `;`, and bundled
  # flags all leave the file under /var/folders. Lines 7-8 pin that -t is
  # flagged whatever follows it: its argument is a prefix, so even a quoted
  # $TMPDIR path lands under /var/folders. Lines 9-10 pin that a redirection
  # ends the options: `2>/dev/null` silences the failure without avoiding it.
  lint "$(fx violations.txt)"
  assert_failure 1
  assert_output --partial "violations.txt:1:"
  assert_output --partial "violations.txt:2:"
  assert_output --partial "violations.txt:3:"
  assert_output --partial "violations.txt:4:"
  assert_output --partial "violations.txt:5:"
  assert_output --partial "violations.txt:6:"
  assert_output --partial "violations.txt:7:"
  assert_output --partial "violations.txt:8:"
  assert_output --partial "violations.txt:9:"
  assert_output --partial "violations.txt:10:"
}

@test "the finding names the fix, not just the fault" {
  lint "$(fx violations.txt)"
  assert_output --partial 'mktemp "${TMPDIR:-/tmp}/<name>.XXXXXX"'
}

@test "passes a template under TMPDIR and words that only contain mktemp" {
  lint "$(fx clean.txt)"
  assert_success
  assert_output ''
}

@test "prose about the hazard does not fail the lint" {
  lint "$(fx prose.txt)"
  assert_success
}

@test "the allow marker exempts a line, but only as a trailing comment" {
  lint "$(fx marker.txt)"
  assert_failure 1
  refute_output --partial "marker.txt:1:"
  assert_output --partial "marker.txt:2:"
}

@test "no files is a usage error, not a silent pass" {
  lint
  assert_failure 2
  assert_output --partial "usage:"
}

@test "the repo's own shell files are clean" {
  run bash -c "cd '$REPO_ROOT' && git ls-files '*.sh' '*.bash' '*.bats' | xargs '$REPO_ROOT/scripts/lint-mktemp.sh'"
  assert_success
}
