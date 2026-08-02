#!/usr/bin/env bats

setup() { setup_test; }
teardown() { teardown_test; }
load test_helper

@test "archive builder creates upload-ready skill zips" {
  command -v zip >/dev/null || skip "zip is not installed"
  run bash "$REPO_ROOT/scripts/build-claude-ai-zips.sh" "$TEST_TMPDIR/out"
  assert_success
  assert_dir_exists "$TEST_TMPDIR/out"
  assert_file_exists "$TEST_TMPDIR/out/task.zip"
  assert_file_exists "$TEST_TMPDIR/out/review-facts.zip"
}

@test "the research-spike zip carries the script its every procedure calls" {
  # The default skills/<name> copy would ship a skill whose every instruction
  # points at a file that was never bundled, and nothing above would notice:
  # the zip exists and unpacks fine. Assert the contents, not the artifact.
  command -v zip >/dev/null || skip "zip is not installed"
  command -v unzip >/dev/null || skip "unzip is not installed"
  run bash "$REPO_ROOT/scripts/build-claude-ai-zips.sh" "$TEST_TMPDIR/out"
  assert_success
  run unzip -Z1 "$TEST_TMPDIR/out/research-spike.zip"
  assert_success
  assert_output --partial 'research-spike/SKILL.md'
  assert_output --partial 'research-spike/references/record-grammar.md'
  assert_output --partial 'research-spike/scripts/research-spike.py'
}

@test "the research-spike-tutorial zip carries the script its walkthrough calls" {
  # Same reasoning as the research-spike zip test above: the tutorial's every
  # command shells out to research-spike.py too, so a default skills/<name>
  # copy would ship a walkthrough whose every command points at a file that
  # was never bundled.
  command -v zip >/dev/null || skip "zip is not installed"
  command -v unzip >/dev/null || skip "unzip is not installed"
  run bash "$REPO_ROOT/scripts/build-claude-ai-zips.sh" "$TEST_TMPDIR/out"
  assert_success
  run unzip -Z1 "$TEST_TMPDIR/out/research-spike-tutorial.zip"
  assert_success
  assert_output --partial 'research-spike-tutorial/SKILL.md'
  assert_output --partial 'research-spike-tutorial/scripts/research-spike.py'
}

@test "coder probe reports missing tools as data" {
  make_stub date 'echo 2026-01-01'
  make_stub sed 'exec /usr/bin/sed "$@"'
  make_stub head 'exec /usr/bin/head "$@"'
  make_stub cat 'exec /bin/cat "$@"'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/probe-coders.sh"
  assert_success
  assert_output --partial 'codex:'
  assert_output --partial 'installed: false'
}

@test "coder probe classifies codex auth from stderr" {
  make_stub date 'echo 2026-01-01'
  make_stub sed 'exec /usr/bin/sed "$@"'
  make_stub head 'exec /usr/bin/head "$@"'
  make_stub cat 'exec /bin/cat "$@"'
  # `codex login status` prints to STDERR. If the probe ever drops its 2>&1 it
  # reads empty and silently classifies every install as `unknown`, so assert
  # the stderr path specifically.
  make_stub codex 'if [ "$1 $2" = "login status" ]; then echo "Logged in using ChatGPT" >&2; fi'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/probe-coders.sh"
  assert_success
  assert_output --partial 'auth: chatgpt'
}

@test "pr-fix-guard check: open PR is safe to push" {
  make_stub gh 'echo OPEN'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/pr-fix-guard.sh" check --pr 1
  assert_success
  assert_output --partial 'PRGUARD: state=open'
}

@test "pr-fix-guard check: merged PR is a dead branch (exit 4)" {
  make_stub gh 'echo MERGED'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/pr-fix-guard.sh" check --pr 1
  assert_failure 4
  assert_output --partial 'PRGUARD: state=merged'
}

@test "pr-fix-guard check: gh failure is unknown, never fatal (exit 3)" {
  make_stub gh 'exit 1'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/pr-fix-guard.sh" check --pr 1
  assert_failure 3
  assert_output --partial 'PRGUARD: state=unknown'
}

# --- verify: content-diff detection, exercised against a REAL throwaway repo ---
# Stubbing git would make these vacuous — the content-diff logic is the thing
# under test — so build an actual repo. `origin` is a second local repo the
# fixture fetches from, so `git fetch origin <base>` in the script works offline.
_prg_make_repo() {
  REPO="$TEST_TMPDIR/work"
  ORIGIN="$TEST_TMPDIR/origin.git"
  # Hermetic: block inherited global/system git config so this repo's own
  # core.hooksPath (whose pre-commit hook blocks commits to main) can't leak in
  # and fail the fixture's commits.
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
  git init -q --bare "$ORIGIN"
  git init -q "$REPO"
  git -C "$REPO" config core.hooksPath /dev/null
  git -C "$REPO" config user.email t@t.t
  git -C "$REPO" config user.name t
  git -C "$REPO" config commit.gpgsign false
  git -C "$REPO" remote add origin "$ORIGIN"
  printf 'base\n' >"$REPO/f.txt"
  git -C "$REPO" add f.txt
  git -C "$REPO" commit -qm base
  git -C "$REPO" push -q origin HEAD:main
  git -C "$REPO" fetch -q origin # establish the origin/main tracking ref
}

@test "pr-fix-guard verify: open PR needs no verification" {
  _prg_make_repo
  make_stub gh 'echo OPEN'
  local sha
  sha="$(git -C "$REPO" rev-parse HEAD)"
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash -c "cd '$REPO' && '$REPO_ROOT/scripts/pr-fix-guard.sh' verify --pr 1 --commit $sha"
  assert_success
  assert_output --partial 'PRGUARD: verdict=open'
}

@test "pr-fix-guard verify: fix content present in base is landed" {
  _prg_make_repo
  # A fix commit whose content is then also pushed to base.
  printf 'fixed\n' >"$REPO/f.txt"
  git -C "$REPO" commit -qam fix
  local sha
  sha="$(git -C "$REPO" rev-parse HEAD)"
  git -C "$REPO" push -q origin HEAD:main
  make_stub gh 'echo MERGED'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash -c "cd '$REPO' && '$REPO_ROOT/scripts/pr-fix-guard.sh' verify --pr 1 --commit $sha"
  assert_success
  assert_output --partial 'PRGUARD: verdict=landed'
}

@test "pr-fix-guard verify: fix content absent from base is orphaned (exit 5)" {
  _prg_make_repo
  # Fix committed locally but base never receives it — the race we guard against.
  printf 'fixed\n' >"$REPO/f.txt"
  git -C "$REPO" commit -qam fix
  local sha
  sha="$(git -C "$REPO" rev-parse HEAD)"
  make_stub gh 'echo MERGED'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash -c "cd '$REPO' && '$REPO_ROOT/scripts/pr-fix-guard.sh' verify --pr 1 --commit $sha"
  assert_failure 5
  assert_output --partial 'PRGUARD: orphaned-file=f.txt'
  assert_output --partial 'PRGUARD: verdict=orphaned'
}

@test "pr-fix-guard verify: squash-merge is landed even though commit is not an ancestor of base" {
  # THE design-constraint regression test. Base gets the fix CONTENT via a fresh
  # squashed commit; the branch's own commit is unreachable from base. An
  # ancestry check (--is-ancestor) would wrongly call this orphaned. Content
  # diff must call it landed. If this fails, someone rewrote verify as ancestry.
  _prg_make_repo
  printf 'fixed\n' >"$REPO/f.txt"
  git -C "$REPO" commit -qam 'fix (branch commit)'
  local sha
  sha="$(git -C "$REPO" rev-parse HEAD)"
  # Simulate the squash merge: base advances to a NEW commit with the same
  # content but a different SHA, with the branch commit not in its history.
  git -C "$REPO" checkout -q -B squashed origin/main
  printf 'fixed\n' >"$REPO/f.txt"
  git -C "$REPO" commit -qam 'squashed PR (new sha)'
  git -C "$REPO" push -q origin HEAD:main
  # Prove the premise: the branch commit is NOT an ancestor of base.
  run git -C "$REPO" merge-base --is-ancestor "$sha" origin/main
  assert_failure
  make_stub gh 'echo MERGED'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash -c "cd '$REPO' && '$REPO_ROOT/scripts/pr-fix-guard.sh' verify --pr 1 --commit $sha"
  assert_success
  assert_output --partial 'PRGUARD: verdict=landed'
}

@test "eval rejects a missing claude dependency" {
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/eval.sh"
  assert_failure 2
  assert_output --partial 'claude CLI not found'
}

@test "confinement smoke rejects non-macOS environments" {
  command -v sandbox-exec >/dev/null && skip "requires a host without sandbox-exec"
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$REPO_ROOT/scripts/smoke-confinement.sh"
  assert_failure 2
  assert_output --partial 'sandbox-exec required'
}

# ensure-bats.sh resolves its root from its own location, so these run a copy
# planted in a fixture tree rather than the real repo — where the submodules are
# already present and every case would take the no-op path.
plant_ensure_bats() {
  mkdir -p "$TEST_TMPDIR/root/scripts"
  cp "$REPO_ROOT/scripts/ensure-bats.sh" "$TEST_TMPDIR/root/scripts/"
}

# Mirrors what a completed `git submodule update` leaves behind: the runner plus
# the three helpers test_helper.bash loads.
plant_all_bats_artifacts() {
  local root="$1"
  mkdir -p "$root/test/vendor/bats-core/bin"
  touch "$root/test/vendor/bats-core/bin/bats"
  chmod +x "$root/test/vendor/bats-core/bin/bats"
  local helper
  for helper in bats-support bats-assert bats-file; do
    mkdir -p "$root/test/vendor/$helper"
    touch "$root/test/vendor/$helper/load.bash"
  done
}

@test "ensure-bats leaves an initialized checkout alone" {
  plant_ensure_bats
  plant_all_bats_artifacts "$TEST_TMPDIR/root"
  # Any git call here would be a wasted network round-trip on every check run.
  make_stub git 'echo "unexpected git call" >&2; exit 1'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$TEST_TMPDIR/root/scripts/ensure-bats.sh"
  assert_success
  refute_output --partial 'unexpected git call'
}

@test "ensure-bats initializes missing submodules" {
  plant_ensure_bats
  make_stub git 'mkdir -p test/vendor/bats-core/bin
touch test/vendor/bats-core/bin/bats
chmod +x test/vendor/bats-core/bin/bats
for helper in bats-support bats-assert bats-file; do
  mkdir -p "test/vendor/$helper"
  touch "test/vendor/$helper/load.bash"
done'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$TEST_TMPDIR/root/scripts/ensure-bats.sh"
  assert_success
  assert_output --partial 'initializing'
  assert_file_exists "$TEST_TMPDIR/root/test/vendor/bats-core/bin/bats"
}

@test "ensure-bats does not call a partial init a success" {
  # A clone that dies after bats-core but before the helpers used to exit 0 —
  # and because the same check gates the next run, the tree stayed broken and
  # only failed later, at helper-load time, with nothing actionable.
  plant_ensure_bats
  make_stub git 'mkdir -p test/vendor/bats-core/bin
touch test/vendor/bats-core/bin/bats
chmod +x test/vendor/bats-core/bin/bats
echo "fatal: clone of bats-file failed" >&2
exit 128'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$TEST_TMPDIR/root/scripts/ensure-bats.sh"
  assert_failure 2
  assert_output --partial 'git submodule update --init --recursive'
}

@test "ensure-bats falls back to the manual command when init cannot run" {
  # The clone needs network, so a sandboxed or offline run must still say what
  # to do by hand instead of failing bare.
  plant_ensure_bats
  make_stub git 'echo "fatal: could not resolve host" >&2; exit 128'
  PATH="$BIN_DIR:/bin:/usr/bin"
  run bash "$TEST_TMPDIR/root/scripts/ensure-bats.sh"
  assert_failure 2
  assert_output --partial 'git submodule update --init --recursive'
}
