#!/usr/bin/env bats
# scripts/worktree-create-hook.sh — the WorktreeCreate hook, ported case-for-case
# from the suite it replaces.
#
# The guarantee under test is the one that keeps task start prompt-free: the
# worktree lands under <resolved root>/<repo>/<name> on <resolved prefix><name>,
# started from the LOCAL default branch, so nothing is written to the main
# checkout's .git/config. Two assertions carry most of the weight:
#
#   * the root and the prefix come from scripts/worktree-config.sh. A port that
#     copies the original's `$HOME/src/worktrees` and `bestdan/` passes every
#     behavioural check and still ships one person's layout to every install.
#   * the start point is the LOCAL default branch. A remote-tracking one would
#     pass every other check here and still need the unsandboxed retry this hook
#     exists to avoid — and the failure surfaces as a sandbox error nobody
#     connects back to the change.

# run --separate-stderr (below) is a 1.5.0 flag; without this bats warns on
# every call that uses it.
bats_require_minimum_version 1.5.0

setup() {
  setup_test
  HOOK="$REPO_ROOT/scripts/worktree-create-hook.sh"
  HOME="$TEST_TMPDIR/home"
  mkdir -p "$HOME"
  export HOME
  # Resolved, never assumed: every assertion below names this variable rather
  # than a path, so a hook that hardcoded a root would fail them all.
  WT_ROOT="$TEST_TMPDIR/worktrees"
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$WT_ROOT"
  export WORKFLOW_SKILLS_BRANCH_PREFIX="tester"
  export WT_ROOT

  # A "remote" and a clone of it, so origin/main exists and would be the
  # tempting start point. The clone's local main is deliberately left one commit
  # BEHIND origin/main, so the two start points are distinguishable by commit.
  REMOTE="$TEST_TMPDIR/remote.git"
  git init -q --bare -b main "$REMOTE"
  seed="$TEST_TMPDIR/seed"
  git clone -q "$REMOTE" "$seed"
  git -C "$seed" commit -q --allow-empty -m one
  git -C "$seed" push -q origin HEAD:main
  REPO="$TEST_TMPDIR/myrepo"
  git clone -q "$REMOTE" "$REPO"
  git -C "$seed" commit -q --allow-empty -m two
  git -C "$seed" push -q origin HEAD:main
  git -C "$REPO" fetch -q origin
  LOCAL_MAIN=$(git -C "$REPO" rev-parse main)
  REMOTE_MAIN=$(git -C "$REPO" rev-parse origin/main)
  export REMOTE REPO LOCAL_MAIN REMOTE_MAIN
}
teardown() { teardown_test; }
load test_helper

# run_hook <cwd> <name> [session] — feed the hook a WorktreeCreate payload.
# Python builds the JSON so the suite carries no jq dependency.
run_hook() {
  local cwd="$1" name="$2" session="${3-}"
  python3 - "$cwd" "$name" "$session" >"$TEST_TMPDIR/payload.json" <<'PY'
import json, sys
print(json.dumps({"hook_event_name": "WorktreeCreate", "cwd": sys.argv[1],
                  "name": sys.argv[2], "session_id": sys.argv[3]}))
PY
  # --separate-stderr because stdout IS the contract: the harness reads the
  # worktree path off it and aborts the creation on anything else, so git's own
  # chatter is deliberately sent to stderr. A merged capture cannot tell the
  # two apart, and would pass a hook that printed the chatter to stdout.
  run --separate-stderr bash -c '"$1" <"$2"' _ "$HOOK" "$TEST_TMPDIR/payload.json"
}

# The hook's refusals go to stderr, which the bats-assert vendored here has no
# matcher for.
assert_stderr_partial() {
  if [[ "$stderr" != *"$1"* ]]; then
    printf 'expected stderr to contain: %s\nstderr was: %s\n' "$1" "$stderr" >&2
    return 1
  fi
}

head_of() { git -C "$1" rev-parse HEAD; }
branch_of() { git -C "$1" rev-parse --abbrev-ref HEAD; }

@test "fixture: local main and origin/main differ" {
  refute [ "$LOCAL_MAIN" = "$REMOTE_MAIN" ]
}

# ------------------------------------------------------------- the happy path

@test "prints <resolved root>/<repo>/<name> and nothing else" {
  run_hook "$REPO" feat-a
  assert_success
  assert_output "$WT_ROOT/myrepo/feat-a"
}

@test "the worktree is created on <resolved prefix><name>" {
  run_hook "$REPO" feat-a
  assert_success
  assert [ -d "$WT_ROOT/myrepo/feat-a" ]
  run branch_of "$WT_ROOT/myrepo/feat-a"
  assert_output "tester/feat-a"
}

@test "the root comes from the resolver, not a hardcoded \$HOME/src/worktrees" {
  # The regression a copy-paste port produces: someone else's layout, shipped
  # to every install. Point the resolver somewhere unmistakable and check both
  # that the hook followed it and that the original literal is nowhere in the
  # answer.
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$TEST_TMPDIR/elsewhere"
  run_hook "$REPO" feat-r
  assert_success
  assert_output "$TEST_TMPDIR/elsewhere/myrepo/feat-r"
  refute_output --partial "src/worktrees"
  assert [ -d "$TEST_TMPDIR/elsewhere/myrepo/feat-r" ]
  assert [ ! -e "$HOME/src/worktrees" ]
}

@test "the branch prefix comes from the resolver too" {
  export WORKFLOW_SKILLS_BRANCH_PREFIX="acme"
  run_hook "$REPO" feat-p
  assert_success
  run branch_of "$WT_ROOT/myrepo/feat-p"
  assert_output "acme/feat-p"
  run git -C "$REPO" show-ref --verify --quiet refs/heads/bestdan/feat-p
  assert_failure
}

# --------------------------------------------------------------- the start point

@test "starts from LOCAL main, never origin/main" {
  run_hook "$REPO" feat-a
  assert_success
  run head_of "$WT_ROOT/myrepo/feat-a"
  assert_output "$LOCAL_MAIN"
  refute_output "$REMOTE_MAIN"
}

@test "no upstream is recorded in the main checkout's .git/config" {
  # The whole reason the start point is local: a remote-tracking one makes git
  # write `[branch "…"]` into a path the sandbox denies.
  run_hook "$REPO" feat-a
  assert_success
  run git -C "$REPO" config --get "branch.tester/feat-a.remote"
  assert_failure
  run git -C "$REPO" config --get-regexp '^branch\.tester/'
  assert_failure
}

@test "a repo whose default branch is not main starts from THAT branch" {
  # A port that hardcodes `main` passes every other case in this file. Here the
  # repo has a local `main` as well, pointing somewhere else, so hardcoding it
  # produces a worktree at the wrong commit rather than an error.
  git -C "$REPO" branch -q trunk "$REMOTE_MAIN"
  git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/trunk
  git -C "$REPO" update-ref refs/remotes/origin/trunk "$REMOTE_MAIN"
  run_hook "$REPO" feat-t
  assert_success
  run head_of "$WT_ROOT/myrepo/feat-t"
  assert_output "$REMOTE_MAIN"
  # …and it is the LOCAL trunk that was used, not refs/remotes/origin/trunk:
  # the remote-tracking form would have recorded an upstream here.
  run git -C "$REPO" config --get "branch.tester/feat-t.remote"
  assert_failure
}

@test "a default branch with no local counterpart fails rather than guessing" {
  git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/nope
  run_hook "$REPO" feat-n
  assert_failure
  assert_stderr_partial "no local branch nope"
  assert [ ! -e "$WT_ROOT/myrepo/feat-n" ]
}

@test "a repo with no remote at all falls back to main" {
  lone="$TEST_TMPDIR/lone"
  git init -q -b main "$lone"
  git -C "$lone" commit -q --allow-empty -m init
  run_hook "$lone" x
  assert_success
  assert_output "$WT_ROOT/lone/x"
  run branch_of "$WT_ROOT/lone/x"
  assert_output "tester/x"
}

@test "a repo with neither origin/HEAD nor a local main fails" {
  nomain="$TEST_TMPDIR/nomain"
  git init -q -b trunk "$nomain"
  git -C "$nomain" commit -q --allow-empty -m init
  run_hook "$nomain" z
  assert_failure
  assert [ ! -e "$WT_ROOT/nomain/z" ]
}

# ------------------------------------------------------------------ re-entry

@test "a cwd inside a worktree still resolves the repo name" {
  run_hook "$REPO" feat-a
  assert_success
  run_hook "$WT_ROOT/myrepo/feat-a" feat-b
  assert_success
  assert_output "$WT_ROOT/myrepo/feat-b"
}

@test "an existing worktree on the same branch is handed back as-is" {
  run_hook "$REPO" feat-a
  assert_success
  run_hook "$REPO" feat-a
  assert_success
  assert_output "$WT_ROOT/myrepo/feat-a"
}

@test "a branch that outlived its directory is re-attached at its own tip" {
  # The routine case: worktree-remove.sh keeps the branch when no merged PR
  # vouches for it. The branch carries a commit main does not, so a re-branch
  # from main would show up here as a lost commit.
  run_hook "$REPO" feat-b
  assert_success
  git -C "$WT_ROOT/myrepo/feat-b" commit -q --allow-empty -m work
  tip=$(git -C "$REPO" rev-parse tester/feat-b)
  git -C "$REPO" worktree remove "$WT_ROOT/myrepo/feat-b"
  run_hook "$REPO" feat-b
  assert_success
  run head_of "$WT_ROOT/myrepo/feat-b"
  assert_output "$tip"
}

@test "a foreign directory sitting at the path is refused" {
  mkdir -p "$WT_ROOT/myrepo/taken"
  run_hook "$REPO" taken
  assert_failure
  assert_stderr_partial "exists and is not a worktree of this repo"
}

# ----------------------------------------------------------------- refusals

@test "a name that is not one safe path segment is refused, and creates nothing" {
  for bad in "" "a/b" ".." "-x" "a b" "x..y"; do
    run_hook "$REPO" "$bad"
    assert_failure
    assert_stderr_partial "refusing worktree name"
  done
  run git -C "$REPO" worktree list --porcelain
  refute_output --partial "$WT_ROOT"
}

@test "a cwd outside any repository fails" {
  mkdir -p "$TEST_TMPDIR/nogit"
  run_hook "$TEST_TMPDIR/nogit" y
  assert_failure
  assert_stderr_partial "is not inside a git repository"
}

@test "an invalid resolver value fails the creation rather than resolving elsewhere" {
  # worktree-config.sh dies on a set-but-invalid value. The hook must not
  # swallow that and fall back to some other root.
  export WORKFLOW_SKILLS_WORKTREE_ROOT="relative/path"
  run_hook "$REPO" feat-bad
  assert_failure
  assert [ ! -e "$WT_ROOT/myrepo/feat-bad" ]
}

# ------------------------------------------------------------ ownership marker

@test "the created worktree records the session that created it" {
  run_hook "$REPO" owned session-1
  assert_success
  gd=$(git -C "$WT_ROOT/myrepo/owned" rev-parse --path-format=absolute --git-dir)
  run cat "$gd/claude-session-owner"
  assert_output "session-1"
}

@test "a second session entering an existing worktree marks it shared" {
  run_hook "$REPO" pair session-1
  assert_success
  gd=$(git -C "$WT_ROOT/myrepo/pair" rev-parse --path-format=absolute --git-dir)
  run_hook "$REPO" pair session-2
  assert_success
  run cat "$gd/claude-session-owner"
  assert_output "shared"
}

@test "entering an unmarked worktree leaves it unmarked" {
  # The remove hook keeps an unmarked worktree because it predates this scheme.
  # Entering must not defeat that by claiming it — the worktree may hold a live
  # session, and a claim would let this session's exit delete its checkout.
  run_hook "$REPO" unclaimed session-1
  assert_success
  gd=$(git -C "$WT_ROOT/myrepo/unclaimed" rev-parse --path-format=absolute --git-dir)
  rm -f "$gd/claude-session-owner"
  run_hook "$REPO" unclaimed session-2
  assert_success
  assert [ ! -e "$gd/claude-session-owner" ]
}

# ------------------------------------------------------------- registration

@test "hooks.json registers the hook at the plugin root" {
  assert [ -f "$REPO_ROOT/hooks/hooks.json" ]
  assert [ ! -e "$REPO_ROOT/.claude-plugin/hooks.json" ]
  run python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))["hooks"]
e = d["WorktreeCreate"][0]["hooks"][0]
print(e["type"], e["command"], e.get("timeout", "-"))
' "$REPO_ROOT/hooks/hooks.json"
  assert_success
  assert_output 'command ${CLAUDE_PLUGIN_ROOT}/scripts/worktree-create-hook.sh -'
}
