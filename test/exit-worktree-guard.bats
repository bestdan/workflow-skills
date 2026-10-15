#!/usr/bin/env bats
# scripts/exit-worktree-guard.sh — the PreToolUse hook on ExitWorktree. The
# guarantee: `discard_changes: true` on a worktree the create hook made for this
# session is denied, with a reason, unless the tree is clean and its tip is the
# head of a PR merged into the default branch. Every other call passes silently.
#
# `gh` is stubbed. The landed check asks it for merged PRs, so each case sets
# the verdict it needs; the default stub fails, which is the deny path.

setup() {
  setup_test
  CREATE="$REPO_ROOT/scripts/worktree-create-hook.sh"
  GUARD="$REPO_ROOT/scripts/exit-worktree-guard.sh"
  HOME="$TEST_TMPDIR/home"
  mkdir -p "$HOME"
  export HOME
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$TEST_TMPDIR/worktrees"
  export WORKFLOW_SKILLS_BRANCH_PREFIX="tester/"

  REPO="$TEST_TMPDIR/myrepo"
  git init -q -b main "$REPO"
  git -C "$REPO" commit -q --allow-empty -m init
  git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  export REPO

  gh_says 1
}
teardown() { teardown_test; }
load test_helper

gh_says() { # exit-code [stdout]
  printf '#!/bin/sh\nprintf %%s "%s"\nexit %s\n' "${2:-}" "$1" >"$BIN_DIR/gh"
  chmod +x "$BIN_DIR/gh"
}

# make_worktree <name> <session> — run the create hook, echo the worktree path.
make_worktree() {
  local dir
  python3 - "$REPO" "$1" "$2" >"$TEST_TMPDIR/create.json" <<'PY'
import json, sys
print(json.dumps({"hook_event_name": "WorktreeCreate", "cwd": sys.argv[1],
                  "name": sys.argv[2], "session_id": sys.argv[3]}))
PY
  dir=$("$CREATE" <"$TEST_TMPDIR/create.json" 2>/dev/null) || return 1
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  printf '%s\n' "$dir"
}

# run_guard <cwd> <session> <tool_input-json> [tool_name]
run_guard() {
  python3 - "$1" "$2" "$3" "${4:-ExitWorktree}" >"$TEST_TMPDIR/pre.json" <<'PY'
import json, sys
print(json.dumps({"hook_event_name": "PreToolUse", "cwd": sys.argv[1],
                  "session_id": sys.argv[2], "tool_name": sys.argv[4],
                  "tool_input": json.loads(sys.argv[3])}))
PY
  run bash -c 'cd "$1" && exec "$2" <"$3"' _ "$1" "$GUARD" "$TEST_TMPDIR/pre.json"
}

DISCARD='{"action": "remove", "discard_changes": true}'

assert_denied() {
  assert_success
  assert_output --partial '"permissionDecision": "deny"'
}

# ------------------------------------------------------------ silent passes

@test "a remove without the flag is left to the harness" {
  dir=$(make_worktree plain s1)
  touch "$dir/untracked"
  run_guard "$dir" s1 '{"action": "remove"}'
  assert_success
  assert_output ""
}

@test "keep is never touched" {
  dir=$(make_worktree kept s1)
  touch "$dir/untracked"
  run_guard "$dir" s1 '{"action": "keep", "discard_changes": true}'
  assert_success
  assert_output ""
}

@test "another tool's payload is ignored" {
  dir=$(make_worktree other s1)
  touch "$dir/untracked"
  run_guard "$dir" s1 "$DISCARD" Bash
  assert_success
  assert_output ""
}

@test "a worktree another session owns is out of scope" {
  dir=$(make_worktree theirs s1)
  touch "$dir/untracked"
  run_guard "$dir" s2 "$DISCARD"
  assert_success
  assert_output ""
}

@test "the main checkout is out of scope" {
  touch "$REPO/untracked"
  run_guard "$REPO" s1 "$DISCARD"
  assert_success
  assert_output ""
}

@test "a malformed payload passes rather than blocking" {
  dir=$(make_worktree junk s1)
  run bash -c 'cd "$1" && printf "not json" | "$2"' _ "$dir" "$GUARD"
  assert_success
  assert_output ""
}

# ------------------------------------------------------------ the two checks

@test "an untracked file is denied, and the reason counts it" {
  dir=$(make_worktree dirty s1)
  touch "$dir/a" "$dir/b"
  run_guard "$dir" s1 "$DISCARD"
  assert_denied
  assert_output --partial "2 uncommitted or untracked path(s)"
}

@test "a git status that fails is a deny, not a pass" {
  dir=$(make_worktree corrupt s1)
  printf 'garbage' >"$(git -C "$dir" rev-parse --path-format=absolute --git-dir)/index"
  run_guard "$dir" s1 "$DISCARD"
  assert_denied
  assert_output --partial "git status failed"
}

@test "a detached HEAD is denied with its own reason" {
  dir=$(make_worktree detached s1)
  git -C "$dir" switch -q --detach
  run_guard "$dir" s1 "$DISCARD"
  assert_denied
  assert_output --partial "detached HEAD"
}

@test "a clean tree whose branch never merged is denied with the gate's reason" {
  dir=$(make_worktree unmerged s1)
  git -C "$dir" commit -q --allow-empty -m work
  gh_says 0 ""
  run_guard "$dir" s1 "$DISCARD"
  assert_denied
  assert_output --partial "tester/unmerged has not landed"
  assert_output --partial "no merged PR has it as head"
}

@test "an unreachable gh is a deny, not a pass" {
  dir=$(make_worktree offline s1)
  run_guard "$dir" s1 "$DISCARD"
  assert_denied
  assert_output --partial "could not reach gh"
}

@test "a PR merged into a parent branch is not landed" {
  dir=$(make_worktree stacked s1)
  tip=$(git -C "$dir" rev-parse HEAD)
  gh_says 0 "$tip parent-branch"
  run_guard "$dir" s1 "$DISCARD"
  assert_denied
  assert_output --partial "merged into parent-branch"
}

@test "a clean tree whose tip merged into the default passes silently" {
  dir=$(make_worktree landed s1)
  tip=$(git -C "$dir" rev-parse HEAD)
  gh_says 0 "$tip main"
  run_guard "$dir" s1 "$DISCARD"
  assert_success
  assert_output ""
  # The check must not delete anything: the branch is the remove hook's call.
  git -C "$REPO" show-ref --quiet --verify refs/heads/tester/landed
}
