#!/usr/bin/env bats
# Every tier of scripts/agent-guidance-dir.sh, each against fixtures under a
# throwaway HOME, so the suite never depends on which copies of the plugin the
# developer's machine happens to hold — the ambiguity the resolver exists to
# settle.

setup() {
  setup_test
  RESOLVER="$REPO_ROOT/scripts/agent-guidance-dir.sh"
  H="$TEST_TMPDIR/home"
  mkdir -p "$H"
  unset AGENT_GUIDANCE_DIR
}
teardown() { teardown_test; }
load test_helper

# A directory the resolver accepts: the plugin manifest plus portable.md.
mkroot() {
  mkdir -p "$1/.claude-plugin"
  printf '{"name":"agent-guidance"}\n' >"$1/.claude-plugin/plugin.json"
  printf '# portable\n' >"$1/portable.md"
}

mkmanifest() { # body
  mkdir -p "$H/.claude/plugins"
  printf '%s\n' "$1" >"$H/.claude/plugins/installed_plugins.json"
}

CACHE=".claude/plugins/cache/agent-guidance/agent-guidance"
MARKET=".claude/plugins/marketplaces/agent-guidance"

resolve() { run env HOME="$H" "$@" bash "$RESOLVER"; }

# ---------------------------------------------------- 1. the explicit override

@test "an explicit AGENT_GUIDANCE_DIR wins over every installed copy" {
  mkroot "$TEST_TMPDIR/checkout"
  mkroot "$H/$MARKET"
  resolve AGENT_GUIDANCE_DIR="$TEST_TMPDIR/checkout"
  assert_success
  assert_output "$TEST_TMPDIR/checkout"
}

@test "a set-but-invalid override exits 1 and does not fall through" {
  mkroot "$H/$MARKET"
  resolve AGENT_GUIDANCE_DIR="$TEST_TMPDIR/nonexistent"
  assert_failure 1
  assert_output --partial "$TEST_TMPDIR/nonexistent"
  refute_output --partial "$H/$MARKET"
}

@test "a directory without portable.md is not a plugin root" {
  mkdir -p "$TEST_TMPDIR/bare/.claude-plugin"
  printf '{}\n' >"$TEST_TMPDIR/bare/.claude-plugin/plugin.json"
  resolve AGENT_GUIDANCE_DIR="$TEST_TMPDIR/bare"
  assert_failure 1
}

# --------------------------------------------- 2. the install manifest wins

@test "the manifest's installPath outranks the version scan and the clone" {
  mkroot "$H/$MARKET"
  mkroot "$H/$CACHE/aaaa0000"
  mkroot "$H/$CACHE/ffff9999"
  mkmanifest "{\"plugins\":{\"agent-guidance@agent-guidance\":[{\"scope\":\"user\",\"installPath\":\"$H/$CACHE/aaaa0000\"}]}}"
  resolve
  assert_success
  assert_output "$H/$CACHE/aaaa0000"
}

@test "the manifest entry matches on plugin name, not the marketplace it came from" {
  mkroot "$TEST_TMPDIR/elsewhere"
  mkroot "$H/$MARKET"
  mkmanifest "{\"plugins\":{\"agent-guidance@some-other-marketplace\":[{\"installPath\":\"$TEST_TMPDIR/elsewhere\"}]}}"
  resolve
  assert_success
  assert_output "$TEST_TMPDIR/elsewhere"
}

@test "the user-scope record is preferred over an earlier project-scope one" {
  mkroot "$TEST_TMPDIR/project-install"
  mkroot "$TEST_TMPDIR/user-install"
  mkmanifest "{\"plugins\":{\"agent-guidance@agent-guidance\":[{\"scope\":\"project\",\"installPath\":\"$TEST_TMPDIR/project-install\"},{\"scope\":\"user\",\"installPath\":\"$TEST_TMPDIR/user-install\"}]}}"
  resolve
  assert_success
  assert_output "$TEST_TMPDIR/user-install"
}

# The manifest is an undocumented file, so every way of failing to read it must
# degrade to the next tier rather than break resolution. Each case keeps a
# valid clone present, so falling through is observable as the clone's path.

@test "an unparseable manifest falls through" {
  mkroot "$H/$MARKET"
  mkmanifest '{"plugins": {"agent-guidance@agent-guidance": [{"installPath":'
  resolve
  assert_success
  assert_output "$H/$MARKET"
}

@test "a manifest naming a vanished install falls through" {
  mkroot "$H/$MARKET"
  mkmanifest '{"plugins":{"agent-guidance@agent-guidance":[{"installPath":"/nonexistent/gone"}]}}'
  resolve
  assert_success
  assert_output "$H/$MARKET"
}

@test "a manifest with no agent-guidance entry falls through" {
  mkroot "$H/$MARKET"
  mkmanifest '{"plugins":{"workflow-skills@workflow-skills":[{"installPath":"/somewhere"}]}}'
  resolve
  assert_success
  assert_output "$H/$MARKET"
}

# ------------------------------------------------- 3. the version scan

@test "with no manifest, the version scan beats the marketplace clone" {
  mkroot "$H/$MARKET"
  mkroot "$H/$CACHE/0.1.0"
  resolve
  assert_success
  assert_output "$H/$CACHE/0.1.0"
}

@test "the highest version wins by version order, not lexical order" {
  for v in 0.1.0 0.9.0 0.10.0; do mkroot "$H/$CACHE/$v"; done
  resolve
  assert_success
  assert_output "$H/$CACHE/0.10.0"
}

@test "an incomplete newer version directory is skipped" {
  mkroot "$H/$CACHE/0.1.0"
  mkdir -p "$H/$CACHE/0.2.0"
  resolve
  assert_success
  assert_output "$H/$CACHE/0.1.0"
}

@test "a cache entry stamped .orphaned_at is skipped" {
  mkroot "$H/$CACHE/0.1.0"
  mkroot "$H/$CACHE/0.2.0"
  printf '1789098054479' >"$H/$CACHE/0.2.0/.orphaned_at"
  resolve
  assert_success
  assert_output "$H/$CACHE/0.1.0"
}

# ------------------------------------------------- 4. the marketplace clone

@test "the marketplace clone is the last resort" {
  mkroot "$H/$MARKET"
  resolve
  assert_success
  assert_output "$H/$MARKET"
}

# ------------------------------------------------- unresolvable

@test "no plugin anywhere exits 3 with an empty stdout" {
  run env HOME="$H" bash -c "'$RESOLVER' 2>/dev/null"
  assert_failure 3
  assert_output ""
}

@test "the unresolvable diagnostic goes to stderr and lists what was tried" {
  resolve
  assert_failure 3
  assert_output --partial 'installed copy'
  assert_output --partial 'marketplace clone'
  assert_output --partial 'claude plugin install agent-guidance@agent-guidance'
}

@test "an unknown argument is a usage error, exit 2" {
  run env HOME="$H" bash "$RESOLVER" --bogus
  assert_failure 2
}
