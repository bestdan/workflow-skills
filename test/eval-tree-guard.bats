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
  # eval.sh shells out to this sibling for the routing verdict, so the fixture
  # is not a working copy of the harness without it.
  cp "$REPO_ROOT/scripts/eval-triage.py" "$FAKE/scripts/eval-triage.py"
  echo "trigger the thing" >"$FAKE/evals/prompts/demo.txt"
  printf 'demo\tprompts/demo.txt\t6\n' >"$FAKE/evals/manifest.tsv"

  git -c init.defaultBranch=main init -q --template= "$FAKE"
  git -C "$FAKE" add -A
  git -C "$FAKE" commit -qm base

  export FAKE
}
teardown() { teardown_test; }

# A stub that "passes" the routing check: it emits a Skill invocation for the
# skill named in the manifest, then the terminal `result` event a completed run
# ends with. That second line is not decoration — a log with no `result` event
# is how a run killed by the wall-clock cap looks, and the harness reports such
# a row as a truncated pass rather than a clean one.
stub_claude_clean() {
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"demo"}}]}}'
echo '{"type":"result","subtype":"success","num_turns":1,"result":"done"}'
SH
  chmod +x "$BIN_DIR/claude"
}

# A stub whose run is cut off: the skill fired, but no `result` event ever
# arrives. This is what `timeout` leaves behind, and the row passes only
# because the Skill call happened to land first.
stub_claude_truncated() {
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
echo '{"type":"result","subtype":"success","num_turns":1,"result":"done"}'
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

# Porcelain collapses an untracked directory to one `?? dir/` entry by default,
# so a file written inside an already-untracked directory leaves the output
# byte-identical — and `diff HEAD` does not cover untracked content either.
# This is the shape of the case #829 actually observed, so it is the one that
# most needs a regression.
@test "a new file inside an already-untracked directory fails the row" {
  mkdir -p "$FAKE/scratch"
  printf 'pre-existing\n' >"$FAKE/scratch/kept.md"
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"demo"}}]}}'
printf 'generated\n' >"$FAKE/scratch/report.md"
SH
  chmod +x "$BIN_DIR/claude"
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "wrote into the repo under test"
  assert_output --partial "scratch/report.md"
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

# --- the routing verdict and what a failing row leaves behind (#840) ---
#
# Before #840 a failing row deleted its own log, so `skills invoked: none` was
# the entire record of a failure the suite cannot reproduce on demand.

@test "a passing row leaves no log behind" {
  stub_claude_clean
  run bash "$FAKE/scripts/eval.sh"
  assert_success
  refute_output --partial "log: temp/evals"
  [ ! -d "$FAKE/temp/evals" ]
}

@test "a row that fired nothing keeps its log and names a cause" {
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"system","subtype":"init","skills":["demo"],"slash_commands":[],"plugins":[]}'
echo '{"type":"result","subtype":"success","num_turns":1,"result":"I just answered."}'
SH
  chmod +x "$BIN_DIR/claude"
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "skills invoked: none"
  # The whole point: surfaced but unchosen is a different defect from a run that
  # never had the skill, and the cause says which.
  assert_output --partial "cause: no-skill-chosen"
  assert_output --partial "causes: 1×no-skill-chosen"
  assert_output --partial "log: temp/evals"
  run find "$FAKE/temp/evals" -name 'demo-demo.jsonl'
  assert_output --partial "demo-demo.jsonl"
}

@test "a skill the session never listed is reported as not-surfaced, not a routing miss" {
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"system","subtype":"init","skills":["something-else"],"slash_commands":[],"plugins":[]}'
echo '{"type":"result","subtype":"success","num_turns":1,"result":"no idea"}'
SH
  chmod +x "$BIN_DIR/claude"
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "cause: not-surfaced"
}

@test "a pass on a truncated run is flagged and keeps its log" {
  stub_claude_truncated
  run bash "$FAKE/scripts/eval.sh"
  # Still a pass — the skill did fire — but not a clean one.
  assert_success
  assert_output --partial "1 passed, 0 failed"
  assert_output --partial "PASS on a truncated run"
  assert_output --partial "passed on a truncated run (at the 300s cap): demo"
  assert_output --partial "log: temp/evals"
}

@test "a row that dirtied the repo still reports what it routed to" {
  stub_claude_dirty
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "wrote into the repo under test"
  # The residue verdict does not cost us the routing one.
  assert_output --partial "also PASS"
  assert_output --partial "causes: 1×dirtied-repo"
  assert_output --partial "log: temp/evals"
}

@test "an alternatives row passes on either name and argv selects it by either" {
  printf 'demo|other\tprompts/demo.txt\t6\n' >"$FAKE/evals/manifest.tsv"
  git -C "$FAKE" add -A
  git -C "$FAKE" commit -qm alt
  stub_claude_clean # fires `demo`, the first alternative

  run bash "$FAKE/scripts/eval.sh"
  assert_success
  assert_output --partial "1 passed, 0 failed"

  # argv must select the row by either alternative, not by the literal field.
  run bash "$FAKE/scripts/eval.sh" other
  assert_success
  assert_output --partial "1 passed, 0 failed"

  run bash "$FAKE/scripts/eval.sh" unrelated
  assert_success
  assert_output --partial "0 passed, 0 failed"
}

@test "an alternatives row's kept log has no pipe in its filename" {
  printf 'demo|other\tprompts/demo.txt\t6\n' >"$FAKE/evals/manifest.tsv"
  git -C "$FAKE" add -A
  git -C "$FAKE" commit -qm alt
  cat >"$BIN_DIR/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"system","subtype":"init","skills":["demo"],"slash_commands":[],"plugins":[]}'
echo '{"type":"result","subtype":"success","num_turns":1,"result":"nope"}'
SH
  chmod +x "$BIN_DIR/claude"
  run bash "$FAKE/scripts/eval.sh"
  assert_failure
  assert_output --partial "demo-or-other-demo.jsonl"
}

load test_helper
