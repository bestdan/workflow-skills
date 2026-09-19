#!/usr/bin/env bats
# scripts/dev-docs-layout.sh — the wrapper that runs the agent-guidance
# plugin's dev_docs layout checker as part of the gate.
#
# The contract worth pinning is that a missing or misconfigured plugin FAILS
# rather than skips: a check that skips is green while checking nothing, which
# is the whole reason this wrapper exists instead of a bare call. The happy
# path is exercised by every `just check`, so the cases below are the three
# failure classes plus a stub-checker run that pins which root gets checked.
#
# Fixtures live under a throwaway HOME so the suite never depends on which
# copies of the plugin the developer's machine holds — the same isolation
# test/agent-guidance-dir.bats uses on the resolver underneath.

setup() {
  setup_test
  SCRIPT="$REPO_ROOT/scripts/dev-docs-layout.sh"
  H="$TEST_TMPDIR/home"
  mkdir -p "$H"
  unset AGENT_GUIDANCE_DIR
}
teardown() { teardown_test; }
load test_helper

# A directory the resolver accepts as a plugin root.
mkroot() { # dir
  mkdir -p "$1/.claude-plugin"
  printf '{"name":"agent-guidance"}\n' >"$1/.claude-plugin/plugin.json"
  printf '# portable\n' >"$1/portable.md"
}

# A stub checker that reports the root it was handed and passes.
mkchecker() { # dir
  mkdir -p "$1/scripts"
  printf 'import sys\nprint("checked", sys.argv[1])\n' >"$1/scripts/dev-docs-layout.py"
}

layout() { run env HOME="$H" "$@" bash "$SCRIPT"; }

@test "no plugin installed anywhere fails, and never passes" {
  layout
  assert_failure 1
  assert_output --partial "the agent-guidance plugin is required for this check"
}

@test "AGENT_GUIDANCE_DIR naming something that is not a plugin root fails as a misconfiguration" {
  mkdir -p "$TEST_TMPDIR/not-a-root"
  layout AGENT_GUIDANCE_DIR="$TEST_TMPDIR/not-a-root"
  assert_failure 1
  assert_output --partial "does not name a plugin root"
  # The version complaint belongs to the next case; a resolution failure must
  # not borrow it.
  refute_output --partial "predates it"
}

@test "a plugin root without the checker reports its age, distinctly" {
  mkroot "$TEST_TMPDIR/old"
  layout AGENT_GUIDANCE_DIR="$TEST_TMPDIR/old"
  assert_failure 2
  assert_output --partial "predates it"
}

@test "a plugin shipping the checker runs it against this repo" {
  mkroot "$TEST_TMPDIR/new"
  mkchecker "$TEST_TMPDIR/new"
  layout AGENT_GUIDANCE_DIR="$TEST_TMPDIR/new"
  assert_success
  assert_output "checked $REPO_ROOT"
}

@test "the checker's own verdict is what the wrapper returns" {
  mkroot "$TEST_TMPDIR/failing"
  mkdir -p "$TEST_TMPDIR/failing/scripts"
  printf 'import sys\nprint("1 layout violation(s)")\nsys.exit(1)\n' \
    >"$TEST_TMPDIR/failing/scripts/dev-docs-layout.py"
  layout AGENT_GUIDANCE_DIR="$TEST_TMPDIR/failing"
  assert_failure 1
  assert_output --partial "1 layout violation(s)"
}
