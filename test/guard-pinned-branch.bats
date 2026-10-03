#!/usr/bin/env bats
# scripts/guard-pinned-branch.py — the PreToolUse guard that denies a branch
# switch aimed at the main worktree of a repo carrying hooks.pinnedBranch.
#
# This cannot work on command text alone: the verdict depends on the repo the
# command targets, which the guard learns by asking git. So every case runs
# against real throwaway repos, and the payload's `cwd` field is what points
# the guard at them. Branches carry DIFFERENT file contents, so a switch is
# a real change to the tree rather than a no-op.

setup() {
  setup_test
  # Pin git's config for the guard and every fixture call: a developer's
  # global config must not leak a pin or an alias into either.
  export GIT_CONFIG_GLOBAL=/dev/null
  HOOK="$REPO_ROOT/scripts/guard-pinned-branch.py"
  tmp="$TEST_TMPDIR"
  pinned="$(new_repo pinned main)"
  plain="$(new_repo plain)"
}
teardown() { teardown_test; }
load test_helper

# new_repo <name> [pinned-branch] — main commits file.txt "main"; `other`
# commits file.txt "other". A branch name opts the repo in to the pin.
new_repo() {
  local dir="$tmp/$1"
  git init -q -b main "$dir"
  git -C "$dir" config user.name test
  git -C "$dir" config user.email test@example.com
  echo main >"$dir/file.txt"
  git -C "$dir" add file.txt
  git -C "$dir" commit -q -m init
  git -C "$dir" checkout -q -b other
  echo other >"$dir/file.txt"
  git -C "$dir" commit -q -am other
  git -C "$dir" checkout -q main
  if [ -n "${2:-}" ]; then git -C "$dir" config hooks.pinnedBranch "$2"; fi
  printf %s "$dir"
}

# run_hook <cwd> <command> — prints the guard's stdout.
run_hook() {
  python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "cwd": sys.argv[1],
                  "tool_input": {"command": sys.argv[2]}}))
' "$1" "$2" | python3 "$HOOK"
}

denied() {
  local out
  out="$(run_hook "$1" "$2")"
  if [ -z "$out" ]; then
    echo "want denied, got allowed: $2" >&2
    return 1
  fi
}

allowed() {
  local out
  out="$(run_hook "$1" "$2")"
  if [ -n "$out" ]; then
    echo "want allowed, got: $out ($2)" >&2
    return 1
  fi
}

@test "a branch switch in a pinned main worktree is denied" {
  denied "$pinned" 'git checkout -b x'
  denied "$pinned" 'git switch other'
  denied "$pinned" 'git checkout other'
  denied "$pinned" 'git switch -c feature'
  denied "$pinned" 'git checkout --quiet other'
  denied "$pinned" 'git checkout -'
  denied "$pinned" 'git fetch && git checkout other'
  denied "$pinned" 'bash -c "git checkout other"'
  # An explicit detach still leaves the pin.
  denied "$pinned" 'git switch -d other'
}

# git takes a short option's value attached, so `-bfeature` is `-b feature`.
@test "a new-branch flag with its name attached is denied" {
  denied "$pinned" 'git checkout -bfeature'
  denied "$pinned" 'git checkout -Bfeature'
  denied "$pinned" 'git switch -cfeature'
  denied "$pinned" 'git switch -Cfeature'
  allowed "$pinned" 'git checkout -bmain'
}

@test "-C pointing at the pinned repo is denied, absolute or relative to the payload cwd" {
  denied "$plain" "git -C $pinned checkout other"
  denied "$tmp" "git -C pinned checkout other"
  denied "$tmp" "git -C ./pinned checkout other"
}

@test "returning to the pin is allowed" {
  git -C "$pinned" checkout -q other
  allowed "$pinned" 'git checkout main'
  allowed "$pinned" 'git checkout refs/heads/main'
}

@test "commands that move no HEAD are allowed" {
  allowed "$pinned" 'git checkout -- file.txt'
  allowed "$pinned" 'git checkout .'
  allowed "$pinned" 'git status'
  allowed "$pinned" 'git worktree add /tmp/wt -b x main'
}

# A bare `git checkout <path>` with no `--` is a file restore; an existing path
# wins the ambiguity, as it does in git.
@test "a bare existing path is a file restore, not a switch" {
  : >"$pinned/README.md"
  allowed "$pinned" 'git checkout README.md'
  allowed "$pinned" 'git checkout does-not-exist'
}

# A word that is both a branch and a path switches, in git, for checkout and
# switch alike; a path only matters when the word is not a ref, and only to
# checkout, which then refuses as ambiguous against a remote-only branch.
@test "a branch sharing its name with a path is still a switch" {
  mkdir "$pinned/docs" "$pinned/docs2"
  git -C "$pinned" branch docs
  git -C "$pinned" update-ref refs/remotes/origin/docs2 "$(git -C "$pinned" rev-parse main)"
  denied "$pinned" 'git checkout docs'
  denied "$pinned" 'git switch docs'
  denied "$pinned" 'git switch docs2'
  allowed "$pinned" 'git checkout docs2'
}

# A bare target only switches when it is the LAST positional.
@test "a tree-ish followed by paths, or patch mode, restores rather than switches" {
  allowed "$pinned" 'git checkout other -- file.txt'
  allowed "$pinned" 'git checkout other file.txt'
  allowed "$pinned" 'git checkout -p other'
  denied "$pinned" 'git checkout other'
}

# git's DWIM: no local `pushed-elsewhere` but one `<remote>/pushed-elsewhere`
# creates the local branch and switches to it, resolving no local ref.
@test "a remote-only branch via DWIM is denied" {
  git -C "$pinned" update-ref refs/remotes/origin/pushed-elsewhere "$(git -C "$pinned" rev-parse main)"
  denied "$pinned" 'git checkout pushed-elsewhere'
}

# `-t`/`--track` take an optional `=` value, never a separate token.
@test "-t and --track name no value token" {
  git -C "$pinned" update-ref refs/remotes/origin/pushed-elsewhere "$(git -C "$pinned" rev-parse main)"
  denied "$pinned" 'git checkout -t origin/pushed-elsewhere'
  denied "$pinned" 'git checkout --track origin/pushed-elsewhere'
  denied "$pinned" 'git switch -t origin/pushed-elsewhere'
}

@test "prose that merely names a checkout does not fire" {
  allowed "$pinned" "rg 'git checkout other' AGENTS.md"
}

@test "a repo with no pin is untouched" {
  allowed "$plain" 'git checkout other'
}

@test "a linked worktree of a pinned repo is not pinned" {
  git -C "$pinned" worktree add -q "$tmp/wt" -b wt-branch
  allowed "$tmp/wt" 'git switch other'
  allowed "$tmp/wt" 'git checkout -b y'
  allowed "$plain" "git -C $tmp/wt checkout other"
}

@test "the denial opens with the pin and carries the PreToolUse decision shape" {
  run run_hook "$pinned" 'git checkout -b x'
  assert_success
  run python3 -c '
import json, sys
h = json.load(sys.stdin)["hookSpecificOutput"]
print(h["hookEventName"], h["permissionDecision"])
print(h["permissionDecisionReason"])
' <<<"$output"
  assert_success
  assert_line --index 0 "PreToolUse deny"
  assert_line --index 1 "This checkout is pinned to 'main' — refusing to switch it to 'x'."
  assert_line --index 2 --partial "worktree"
  assert_line --index 3 "To unpin it for good: git config --unset hooks.pinnedBranch"
}

@test "an allowed command produces no output" {
  run run_hook "$pinned" 'git checkout main'
  assert_success
  assert_output ""
}

@test "a malformed payload fails open" {
  run bash -c 'printf "not json" | python3 "$1"' _ "$HOOK"
  assert_success
  assert_output ""
}
