#!/usr/bin/env bats
#
# scripts/lint-temp-paths.sh — keeps fixed names in the machine-shared temp
# roots (/tmp/claude/, $TMPDIR) out of skill, command and agent text (#1539).
# Fixture inputs live in test/fixtures/lint-temp-paths/*.txt so the tree-wide
# scan does not sweep up their deliberate violations.

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

lint() { run "$REPO_ROOT/scripts/lint-temp-paths.sh" "$@"; }
fx() { printf '%s' "$REPO_ROOT/test/fixtures/lint-temp-paths/$1"; }

@test "catches a fixed name in the shared root and under TMPDIR, in every spelling" {
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
  assert_output --partial "violations.txt:11:"
  assert_output --partial "violations.txt:12:"
}

@test "the finding names the resolver" {
  lint "$(fx violations.txt)"
  assert_output --partial "scripts/work-tmp.sh"
}

@test "passes a resolver path, a bare root, a template and the harness's own dir" {
  lint "$(fx clean.txt)"
  assert_success
  assert_output ''
}

@test "the allow marker exempts only its own line" {
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

@test "the repo's own skill, command and agent text is clean" {
  run bash -c "cd '$REPO_ROOT' && git ls-files 'skills/*.md' 'commands/*.md' 'agents/*.md' dev_docs/external-agents.md | xargs '$REPO_ROOT/scripts/lint-temp-paths.sh'"
  assert_success
}
