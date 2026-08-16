#!/usr/bin/env bats
#
# scripts/lint-bash4.sh — the static half of the Bash 3.2 floor.
#
# Two cases here pin bugs the first cut of this check actually shipped, both of
# them FALSE PASSES: a violation the lint saw and let through. That is the
# failure a lint least survives, and neither was reachable while the check
# discovered its own file list — which is why it now takes files as arguments.
#
# Fixture inputs live in test/fixtures/lint-bash4/*.txt, not inline: they carry
# real bash-4 constructs, and any fixture written as a `.sh`/`.bats` file would
# be swept up by lint-shell.sh's own tree-wide scan and fail the gate. See the
# README there. Assertions that quote a construct by name carry the lint's own
# `# bash4-lint: allow` marker, which is exactly what that marker is for.

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

lint() { run "$REPO_ROOT/scripts/lint-bash4.sh" "$@"; }
fx() { printf '%s' "$REPO_ROOT/test/fixtures/lint-bash4/$1"; }

@test "catches every banned construct, and names the line it is on" {
  lint "$(fx violations.txt)"
  assert_failure 1
  assert_output --partial "violations.txt:1: associative array"
  assert_output --partial "violations.txt:2: mapfile/readarray" # bash4-lint: allow
  assert_output --partial "violations.txt:3: mapfile/readarray" # bash4-lint: allow
  assert_output --partial "violations.txt:4: coproc"            # bash4-lint: allow
  assert_output --partial "violations.txt:5: case modification"
  assert_output --partial "violations.txt:6: case modification"
  assert_output --partial "violations.txt:7: &>> append"     # bash4-lint: allow
  assert_output --partial "violations.txt:8: |& pipe-stderr" # bash4-lint: allow
  assert_output --partial "violations.txt:9: ;& / ;;& case"  # bash4-lint: allow
  assert_output --partial "violations.txt:10: ;& / ;;& case" # bash4-lint: allow
}

@test "an associative array is caught in every option-word spelling" {
  # `declare -Ar` and `local -r -A` are as bash-4 as the plain form. A pattern
  # anchoring the option word's `A` at the end misses both — and did.
  lint "$(fx spellings.txt)"
  assert_failure 1
  assert_output --partial "spellings.txt:1:"
  assert_output --partial "spellings.txt:2:"
  assert_output --partial "spellings.txt:3:"
}

@test "passes clean code, including look-alikes that are not bash 4" {
  # The fixture holds `declare -i A=1`, which reads like the associative-array
  # form but whose `A` is a variable name, not a dash-prefixed option. A false
  # positive there would make the whole check unwelcome.
  lint "$(fx clean.txt)"
  assert_success
  assert_output ''
}

@test "prose about a construct does not fail the lint" {
  # scripts/pr-fix-guard.sh carries exactly such a comment, explaining why it
  # hand-rolls a bash-4 builtin. The lint must not punish the explanation.
  lint "$(fx prose.txt)"
  assert_success
}

@test "the allow marker exempts a line, but only as a trailing comment" {
  # REGRESSION: the marker was once matched anywhere on the line, so a line
  # that merely quoted it smuggled a live construct past the check.
  lint "$(fx marker.txt)"
  assert_failure 1
  refute_output --partial "marker.txt:1:"
  assert_output --partial "marker.txt:2:"
}

@test "a single-file scan still reports file and line correctly" {
  # REGRESSION: without `grep -H`, a one-file scan emits `line:code` instead of
  # `file:line:code` and every awk field shifts — bogus locations, a code
  # snapshot truncated at the next colon, and the comment on the line above
  # reported in place of the real violation. One file is the common case when
  # lint-shell.sh runs --fast, so this is the everyday path, not an edge case.
  lint "$(fx single.txt)"
  assert_failure 1
  assert_output --partial "single.txt:2:"
  refute_output --partial "single.txt:1:"
}

@test "scanning many files at once reports each against its own path" {
  lint "$(fx first.txt)" "$(fx second.txt)"
  assert_failure 1
  assert_output --partial "first.txt:1: associative array"
  assert_output --partial "second.txt:2: coproc" # bash4-lint: allow
}

@test "no files is a usage error, not a silent pass" {
  lint
  assert_failure 2
  assert_output --partial "usage:"
}

@test "the repo's own shell files are clean" {
  # The check has to hold on the tree it ships with, or it is enforcing
  # nothing. This is what `just check` asserts, in miniature.
  run bash -c "cd '$REPO_ROOT' && git ls-files '*.sh' '*.bash' '*.bats' | xargs '$REPO_ROOT/scripts/lint-bash4.sh'"
  assert_success
}
