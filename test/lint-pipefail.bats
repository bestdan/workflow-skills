#!/usr/bin/env bats
#
# scripts/lint-pipefail.sh — keeps `<writer> | grep -q` out of the tree.
#
# The hazard: `grep -q` exits on its first match, its writer dies of SIGPIPE,
# and `set -o pipefail` turns the whole pipeline into 141 — so a match reports
# "not found". The repo documented that rule twice (scripts/test-verify-fix.sh,
# scripts/spawn-orchestrator.sh) before anything enforced it, and 53 sites
# drifted back to the piped form in the meantime. This check is the enforcement.
#
# Fixture inputs live in test/fixtures/lint-pipefail/*.txt, not inline: they
# carry real violations, and any fixture written as a `.sh`/`.bats` file would
# be swept up by lint-shell.sh's own tree-wide scan and fail the gate. See the
# README there.

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

lint() { run "$REPO_ROOT/scripts/lint-pipefail.sh" "$@"; }
fx() { printf '%s' "$REPO_ROOT/test/fixtures/lint-pipefail/$1"; }

@test "catches a piped grep -q in every option-word spelling" {
  # -q, -qF, -Eq, -qxF, -qiF all carry the same hazard, and a pattern anchoring
  # the q would miss most of them. Line 6 pins that the writer need not be
  # printf: any command feeding the pipe can lose the race.
  #
  # Lines 7-8 are the long spellings. Three reviewers independently found that
  # `--quiet` slipped past the first cut of this pattern; `--silent` is grep's
  # alias for the same flag and would have been the next one. Nothing in this
  # tree writes them today — the check is for the code that does not exist yet.
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
}

@test "the finding names the fix, not just the fault" {
  lint "$(fx violations.txt)"
  assert_output --partial 'grep -q PATTERN <<<"$var"'
}

@test "passes the here-string form and the greps that cannot race" {
  # -c, -v, -o and `grep -n | head -1` read to the end or discard their status
  # into an assignment. Flagging them would be noise, and a noisy lint gets
  # disabled.
  #
  # REGRESSION (last line of the fixture): `cmd || grep -q x <<<"$v"` is the
  # FIXED form, but a pattern matching a bare `|` sees the second pipe of `||`
  # and flags the repair as the defect. That false positive fired on
  # scripts/spawn-orchestrator.sh the first time this check ran.
  lint "$(fx clean.txt)"
  assert_success
  assert_output ''
}

@test "prose about the hazard does not fail the lint" {
  # scripts/test-verify-fix.sh and scripts/spawn-orchestrator.sh both explain
  # this rule in comments that necessarily quote the banned form. The lint must
  # not punish the documentation it exists to enforce.
  lint "$(fx prose.txt)"
  assert_success
}

@test "the allow marker exempts a line, but only as a trailing comment" {
  # Line 1 is the lint's own pattern table, which cannot be written without the
  # construct. Line 2 merely quotes the marker inside a string and must still
  # fail, or the escape hatch becomes a way to smuggle violations past.
  lint "$(fx marker.txt)"
  assert_failure 1
  refute_output --partial "marker.txt:1:"
  assert_output --partial "marker.txt:2:"
}

@test "a single-file scan still reports file and line correctly" {
  # Without `grep -H` a one-file scan emits `line:code`, every awk field shifts,
  # and the comment on line 1 gets reported in place of the violation on line 2.
  # One file is the common case when lint-shell.sh runs --fast.
  lint "$(fx single.txt)"
  assert_failure 1
  assert_output --partial "single.txt:2:"
  refute_output --partial "single.txt:1:"
}

@test "scanning many files at once reports each against its own path" {
  lint "$(fx first.txt)" "$(fx second.txt)"
  assert_failure 1
  assert_output --partial "first.txt:1:"
  assert_output --partial "second.txt:2:"
}

@test "no files is a usage error, not a silent pass" {
  lint
  assert_failure 2
  assert_output --partial "usage:"
}

@test "the repo's own shell files are clean" {
  # The check has to hold on the tree it ships with, or it is enforcing
  # nothing. This is what `just check` asserts, in miniature.
  run bash -c "cd '$REPO_ROOT' && git ls-files '*.sh' '*.bash' '*.bats' | xargs '$REPO_ROOT/scripts/lint-pipefail.sh'"
  assert_success
}
