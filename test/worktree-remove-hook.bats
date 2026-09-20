#!/usr/bin/env bats
# scripts/worktree-remove-hook.sh — the WorktreeRemove hook, ported case-for-case
# from the suite it replaces.
#
# The guarantee under test is ownership, not deletion. The hook is registered
# plugin-wide, so the harness hands it every worktree it removes; each gate
# answers "did worktree-create-hook.sh make this, for this session?" and a no
# keeps the checkout. The load-bearing case is the foreign-owner one: the create
# hook hands a re-used name back rather than refusing it, so two sessions can
# share one checkout, and without that gate the first to exit would delete the
# second's live working tree.
#
# `gh` is stubbed to fail throughout. The teardown's last step asks it whether
# the branch merged, and a real one would reach the network and answer about
# whichever repo it guessed from a fixture that has no remote. Failing is the
# keep-the-branch path, which is what a session exit wants anyway.

setup() {
  setup_test
  CREATE="$REPO_ROOT/scripts/worktree-create-hook.sh"
  REMOVE="$REPO_ROOT/scripts/worktree-remove-hook.sh"
  HOME="$TEST_TMPDIR/home"
  mkdir -p "$HOME"
  export HOME
  WT_ROOT="$TEST_TMPDIR/worktrees"
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$WT_ROOT"
  export WORKFLOW_SKILLS_BRANCH_PREFIX="tester"
  export WT_ROOT

  REPO="$TEST_TMPDIR/myrepo"
  git init -q -b main "$REPO"
  git -C "$REPO" commit -q --allow-empty -m init
  # The branch gate inside scripts/branch-remove.sh resolves the default branch
  # from origin/HEAD, so the fixture needs one even with no remote.
  git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  export REPO

  printf '#!/bin/sh\nexit 1\n' >"$BIN_DIR/gh"
  chmod +x "$BIN_DIR/gh"
}
teardown() { teardown_test; }
load test_helper

# make_worktree <name> <session> — run the create hook, echo the worktree path.
# A fixture that failed to build must not be handed on: every path below feeds
# `git -C`, and an empty one does NOT fail — git falls back to the caller's own
# repository, which silently retargets the assertions at this checkout.
make_worktree() {
  local name="$1" session="$2" dir
  python3 - "$REPO" "$name" "$session" >"$TEST_TMPDIR/create.json" <<'PY'
import json, sys
print(json.dumps({"hook_event_name": "WorktreeCreate", "cwd": sys.argv[1],
                  "name": sys.argv[2], "session_id": sys.argv[3]}))
PY
  dir=$("$CREATE" <"$TEST_TMPDIR/create.json" 2>/dev/null) || return 1
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  printf '%s\n' "$dir"
}

# run_remove <path> [session] — omit the session to send a payload without one.
run_remove() {
  local path="$1"
  python3 - "$path" "${2-}" "$#" >"$TEST_TMPDIR/remove.json" <<'PY'
import json, sys
d = {"hook_event_name": "WorktreeRemove", "cwd": sys.argv[1],
     "worktree_path": sys.argv[1]}
if int(sys.argv[3]) > 1:
    d["session_id"] = sys.argv[2]
print(json.dumps(d))
PY
  run bash -c 'cd / && exec "$1" <"$2"' _ "$REMOVE" "$TEST_TMPDIR/remove.json"
}

owner_file_of() {
  printf '%s/claude-session-owner\n' "$(git -C "$1" rev-parse --path-format=absolute --git-dir)"
}

# ------------------------------------------------------------ ownership gates

@test "a worktree owned by another session is kept, and the hook says whose" {
  dir=$(make_worktree keep-me session-1)
  run_remove "$dir" session-2
  assert_success
  assert_output --partial "owned by session session-1"
  assert [ -d "$dir" ]
}

@test "a worktree two sessions entered is kept whichever exits first" {
  # The reverse exit order is the one last-writer-wins stamping cannot survive:
  # session 2 enters after session 1, exits first, and would match its own stamp
  # while session 1 is still standing in the checkout.
  dir=$(make_worktree shared-name session-1)
  make_worktree shared-name session-2 >/dev/null
  run cat "$(owner_file_of "$dir")"
  assert_output "shared"
  run_remove "$dir" session-2
  assert_success
  assert [ -d "$dir" ]
  run_remove "$dir" session-1
  assert_success
  assert [ -d "$dir" ]
}

@test "a worktree with no owner marker is kept" {
  dir=$(make_worktree no-owner session-1)
  rm -f "$(owner_file_of "$dir")"
  run_remove "$dir" session-1
  assert_success
  assert_output --partial "no session owner recorded"
  assert [ -d "$dir" ]
}

@test "a payload that carries no session_id keeps the worktree" {
  # Ownership gates a destructive operation, so an unidentifiable session is a
  # refusal, not a pass. Without this the gate fails open and tears down a live
  # session's checkout.
  dir=$(make_worktree no-session session-1)
  run_remove "$dir"
  assert_success
  assert_output --partial "no session_id"
  assert [ -d "$dir" ]
}

@test "a re-pointed branch is kept, and the message names the resolved prefix" {
  dir=$(make_worktree moved session-1)
  git -C "$dir" checkout -q -b tester/something-else
  run_remove "$dir" session-1
  assert_success
  assert_output --partial "not tester/moved"
  assert [ -d "$dir" ]
}

@test "anything outside the RESOLVED worktree root is kept" {
  # Named against the resolver, not against \$HOME/src/worktrees: this gate is
  # the one a copy-paste port breaks silently, by comparing every worktree
  # against a root this install never uses.
  run_remove "$REPO" session-1
  assert_success
  assert_output --partial "not under $WT_ROOT/<repo>/"
  assert [ -d "$REPO" ]
}

@test "a directory under the root that is not a linked worktree is kept" {
  mkdir -p "$WT_ROOT/myrepo/plain"
  run_remove "$WT_ROOT/myrepo/plain" session-1
  assert_success
  assert_output --partial "not a linked worktree"
  assert [ -d "$WT_ROOT/myrepo/plain" ]
}

# ----------------------------------------------------------------- the teardown

@test "an owned worktree is removed, leaving no stranded registration" {
  dir=$(make_worktree mine session-9)
  run_remove "$dir" session-9
  assert_success
  assert [ ! -d "$dir" ]
  # The distinction that makes this worth more than `rm -rf`: the admin entry
  # in the main checkout must be gone too.
  run git -C "$REPO" worktree list --porcelain
  refute_output --partial "worktree $dir"
  assert [ ! -e "$REPO/.git/worktrees/mine" ]
}

@test "the branch survives the teardown" {
  # No merged PR vouches for it, and at session exit there usually is none. The
  # worktree goes, the work does not.
  dir=$(make_worktree mine session-9)
  run_remove "$dir" session-9
  assert_success
  run git -C "$REPO" show-ref --verify --quiet refs/heads/tester/mine
  assert_success
}

@test "the hook runs the teardown from the main checkout, not the worktree" {
  # worktree-remove.sh refuses to delete the directory its caller is standing
  # in. The harness dispatches this hook with the worktree as its cwd, so a
  # hook that did not cd out would be refused by its own teardown.
  dir=$(make_worktree cwdcase session-9)
  python3 - "$dir" session-9 >"$TEST_TMPDIR/cwd.json" <<'PY'
import json, sys
print(json.dumps({"hook_event_name": "WorktreeRemove", "cwd": sys.argv[1],
                  "worktree_path": sys.argv[1], "session_id": sys.argv[2]}))
PY
  run bash -c 'cd "$3" && exec "$1" <"$2"' _ "$REMOVE" "$TEST_TMPDIR/cwd.json" "$dir"
  assert_success
  refute_output --partial "cd out first"
  assert [ ! -d "$dir" ]
}

@test "removing a worktree that is already gone is a silent success" {
  dir=$(make_worktree mine session-9)
  run_remove "$dir" session-9
  assert_success
  run_remove "$dir" session-9
  assert_success
  assert_output ""
}

@test "a payload with no worktree_path fails" {
  echo '{"hook_event_name":"WorktreeRemove"}' >"$TEST_TMPDIR/empty.json"
  run bash -c '"$1" <"$2"' _ "$REMOVE" "$TEST_TMPDIR/empty.json"
  assert_failure
  assert_output --partial "no worktree_path"
}

# ------------------------------------------------------------- registration

@test "hooks.json registers the remove hook with its 120s timeout" {
  run python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))["hooks"]
e = d["WorktreeRemove"][0]["hooks"][0]
print(e["type"], e["command"], e["timeout"])
' "$REPO_ROOT/hooks/hooks.json"
  assert_success
  assert_output 'command ${CLAUDE_PLUGIN_ROOT}/scripts/worktree-remove-hook.sh 120'
}
