#!/usr/bin/env bats
#
# The guard in scripts/eval.sh that fails a row which wrote into the repo under
# test (#829). The claude CLI is stubbed, so nothing here costs a token or
# needs a login — what is exercised is the harness's own bookkeeping.

setup() {
  setup_test
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t
  export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

  # A miniature copy of the repo layout eval.sh expects: scripts/eval.sh plus
  # an evals/ tree. It is a real git checkout, because the guard reads
  # `git status --porcelain` against it.
  FAKE="$TEST_TMPDIR/repo"
  mkdir -p "$FAKE/scripts" "$FAKE/evals/prompts"
  cp "$REPO_ROOT/scripts/eval.sh" "$FAKE/scripts/eval.sh"
  chmod +x "$FAKE/scripts/eval.sh"
  echo "trigger the thing" >"$FAKE/evals/prompts/demo.txt"
  printf 'demo\tprompts/demo.txt\t6\n' >"$FAKE/evals/manifest.tsv"

  git -c init.defaultBranch=main init -q --template= "$FAKE"
  git -C "$FAKE" add -A
  git -C "$FAKE" commit -qm base

  export FAKE
}
teardown() { teardown_test; }

# A stub that "passes" the routing check: it emits a Skill invocation for the
# skill named in the manifest.
stub_claude_clean() {
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"demo"}}]}}'
SH
  chmod +x "$BIN_DIR/claude"
}

# The same stub, except it also writes into the checkout it was pointed at —
# the behaviour #829 observed in the wild.
stub_claude_dirty() {
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"demo"}}]}}'
printf 'residue\n' >"$FAKE/evals/leftover.md"
git -C "$FAKE" add evals/leftover.md
SH
  chmod +x "$BIN_DIR/claude"
}

@test "a case that leaves the tree alone still passes" {
  stub_claude_clean
  run bash "$FAKE/scripts/eval.sh"
  assert_success
  assert_output --partial "✅ PASS"
  assert_output --partial "1 passed, 0 failed"
}

@test "a case that writes into the repo under test fails the row" {
  stub_claude_dirty
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "wrote into the repo under test"
  assert_output --partial "evals/leftover.md"
  assert_output --partial "0 passed, 1 failed"
}

@test "the dirtying row is named in the summary, not just counted" {
  stub_claude_dirty
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "dirtied the repo under test: demo"
}

# The guard must not convict a row for edits the developer already had in
# flight. This is the case that decides between a rolling baseline and a
# "tree must be clean" assertion, and the suite is routinely run this way.
@test "pre-existing uncommitted work does not fail a clean row" {
  stub_claude_clean
  printf 'work in progress\n' >"$FAKE/evals/prompts/wip.txt"
  git -C "$FAKE" add evals/prompts/wip.txt
  run bash "$FAKE/scripts/eval.sh"
  assert_success
  assert_output --partial "1 passed, 0 failed"
  refute_output --partial "wrote into the repo under test"
}

# Porcelain reports status codes and paths, not content, so a case that edits a
# file already showing as modified leaves the porcelain line byte-identical.
# That is the likeliest miss, because the rolling baseline exists precisely to
# support running with work already in flight.
@test "editing a path that was already dirty fails the row" {
  printf 'in flight\n' >>"$FAKE/evals/prompts/demo.txt"
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"demo"}}]}}'
printf 'and the case appended this too\n' >>"$FAKE/evals/prompts/demo.txt"
SH
  chmod +x "$BIN_DIR/claude"
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "wrote into the repo under test"
  assert_output --partial "already modified"
}

# Reverting someone's in-flight edit is a write too, and reading it as clean
# would be worse than the bug this guard was added for.
@test "reverting a pre-existing edit fails the row" {
  printf 'in flight\n' >>"$FAKE/evals/prompts/demo.txt"
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"demo"}}]}}'
git -C "$FAKE" checkout -- evals/prompts/demo.txt
SH
  chmod +x "$BIN_DIR/claude"
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "wrote into the repo under test"
}

# A ROOT that is not a git checkout (a tarball install) must degrade to a
# no-op rather than failing every row.
@test "a non-git checkout degrades to a no-op instead of failing rows" {
  stub_claude_clean
  rm -rf "$FAKE/.git"
  run bash "$FAKE/scripts/eval.sh"
  assert_success
  assert_output --partial "1 passed, 0 failed"
}

load test_helper
