#!/usr/bin/env bats
# scripts/guard-enter-by-path.py — the PreToolUse hook that refuses
# EnterWorktree `path` when worktree-name-for.sh gives the worktree a name.
#
# A wrong deny blocks the only way into a worktree; a wrong silence costs a
# prompt nobody may be there to answer. So every "no decision" case asserts
# empty stdout, not just a zero exit: the hook fails open by printing nothing.

bats_require_minimum_version 1.5.0

setup() {
  setup_test
  HOOK="$REPO_ROOT/scripts/guard-enter-by-path.py"
  WT_ROOT="$TEST_TMPDIR/worktrees"
  export WORKFLOW_SKILLS_WORKTREE_ROOT="$WT_ROOT"
  export WORKFLOW_SKILLS_BRANCH_PREFIX="tester/"

  MAIN="$TEST_TMPDIR/main"
  git init -q "$MAIN"
  git -C "$MAIN" commit --allow-empty -qm one
  mkdir -p "$WT_ROOT/main"
}
teardown() { teardown_test; }
load test_helper

# hook_worktree <name> [branch] — a worktree where the create hook would put it.
hook_worktree() {
  git -C "$MAIN" worktree add -q -b "${2:-tester/$1}" "$WT_ROOT/main/$1" main
}

# payload <tool> <key> <value> <cwd> — one hook payload as JSON.
payload() {
  python3 -c '
import json, sys
tool, key, value, cwd = sys.argv[1:5]
print(json.dumps({"tool_name": tool, "tool_input": {key: value}, "cwd": cwd}))
' "$@"
}

# fire <hook> <tool> <key> <value> <cwd> — run <hook> on that payload.
fire() {
  local hook="$1"
  shift
  run --separate-stderr bash -c 'printf "%s" "$1" | python3 "$2"' _ "$(payload "$@")" "$hook"
}

# field <name> — one hookSpecificOutput field of the last run's stdout.
field() {
  printf '%s' "$output" | python3 -c '
import json, sys
print(json.load(sys.stdin)["hookSpecificOutput"][sys.argv[1]])
' "$1"
}

@test "a path with a name is denied, and the reason quotes <n> and both calls" {
  hook_worktree feat-a
  fire "$HOOK" EnterWorktree path "$WT_ROOT/main/feat-a" "$MAIN"
  assert_success
  [ "$(field hookEventName)" = PreToolUse ]
  [ "$(field permissionDecision)" = deny ]
  reason=$(field permissionDecisionReason)
  [[ "$reason" == *'`EnterWorktree` with `name: feat-a`'* ]]
  [[ "$reason" == *'`ExitWorktree` with `action: keep`'* ]]
}

@test "the exit step is conditional on already being in a worktree" {
  # ExitWorktree outside a worktree is a no-op that reports is_error (#1001),
  # so the reason must not tell the agent to run it unconditionally.
  hook_worktree feat-a
  fire "$HOOK" EnterWorktree path "$WT_ROOT/main/feat-a" "$MAIN"
  assert_success
  reason=$(field permissionDecisionReason)
  [[ "$reason" == *'If this session is already in a worktree, call `ExitWorktree`'* ]]
  [[ "$reason" == *'otherwise skip that step'* ]]
}

@test "a relative path is resolved against the payload's cwd" {
  hook_worktree feat-a
  fire "$HOOK" EnterWorktree path "../worktrees/main/feat-a" "$MAIN"
  assert_success
  [ "$(field permissionDecision)" = deny ]
  [[ "$(field permissionDecisionReason)" == *'`name: feat-a`'* ]]
}

@test "the same path is denied from inside another worktree of the repo" {
  hook_worktree feat-a
  hook_worktree feat-b
  fire "$HOOK" EnterWorktree path "$WT_ROOT/main/feat-a" "$WT_ROOT/main/feat-b"
  assert_success
  [ "$(field permissionDecision)" = deny ]
}

@test "a path with no name gets no decision: unprefixed branch" {
  hook_worktree feat-a other/feat-a
  fire "$HOOK" EnterWorktree path "$WT_ROOT/main/feat-a" "$MAIN"
  assert_success
  assert_output ""
}

@test "a path with no name gets no decision: outside the layout" {
  git -C "$MAIN" worktree add -q -b tester/feat-x "$TEST_TMPDIR/elsewhere" main
  fire "$HOOK" EnterWorktree path "$TEST_TMPDIR/elsewhere" "$MAIN"
  assert_success
  assert_output ""
}

@test "a path with no name gets no decision: not a worktree at all" {
  mkdir -p "$TEST_TMPDIR/plain"
  fire "$HOOK" EnterWorktree path "$TEST_TMPDIR/plain" "$MAIN"
  assert_success
  assert_output ""
}

@test "a name call gets no decision" {
  hook_worktree feat-a
  fire "$HOOK" EnterWorktree name feat-a "$MAIN"
  assert_success
  assert_output ""
}

@test "another tool carrying a path gets no decision" {
  hook_worktree feat-a
  fire "$HOOK" Read path "$WT_ROOT/main/feat-a" "$MAIN"
  assert_success
  assert_output ""
}

@test "malformed stdin fails open" {
  run --separate-stderr bash -c 'printf "not json" | python3 "$1"' _ "$HOOK"
  assert_success
  assert_output ""
}

@test "a payload that is not an object fails open" {
  run --separate-stderr bash -c 'printf "[]" | python3 "$1"' _ "$HOOK"
  assert_success
  assert_output ""
}

@test "a non-string path fails open" {
  run --separate-stderr bash -c 'printf "%s" "{\"tool_name\":\"EnterWorktree\",\"tool_input\":{\"path\":7}}" | python3 "$1"' _ "$HOOK"
  assert_success
  assert_output ""
}

@test "an unreadable config fails open" {
  hook_worktree feat-a
  export WORKFLOW_SKILLS_WORKTREE_ROOT="relative/root"
  fire "$HOOK" EnterWorktree path "$WT_ROOT/main/feat-a" "$MAIN"
  assert_success
  assert_output ""
}

@test "a missing resolver fails open" {
  hook_worktree feat-a
  mkdir -p "$TEST_TMPDIR/lone"
  cp "$HOOK" "$TEST_TMPDIR/lone/"
  fire "$TEST_TMPDIR/lone/guard-enter-by-path.py" EnterWorktree path "$WT_ROOT/main/feat-a" "$MAIN"
  assert_success
  assert_output ""
}

# stub_resolver <body> — a copy of the hook beside a stub resolver.
stub_resolver() {
  mkdir -p "$TEST_TMPDIR/stub"
  cp "$HOOK" "$TEST_TMPDIR/stub/"
  printf '#!/usr/bin/env bash\n%s\n' "$1" >"$TEST_TMPDIR/stub/worktree-name-for.sh"
  chmod +x "$TEST_TMPDIR/stub/worktree-name-for.sh"
}

@test "control: a stub resolver that names feat-a is honoured" {
  # Without this, every stub case below would pass on a hook that never ran
  # the sibling resolver at all.
  stub_resolver 'echo feat-a'
  fire "$TEST_TMPDIR/stub/guard-enter-by-path.py" EnterWorktree path "$MAIN" "$MAIN"
  assert_success
  [ "$(field permissionDecision)" = deny ]
}

@test "a crashing resolver fails open" {
  stub_resolver 'echo boom >&2; kill -SEGV $$'
  fire "$TEST_TMPDIR/stub/guard-enter-by-path.py" EnterWorktree path "$MAIN" "$MAIN"
  assert_success
  assert_output ""
}

@test "a resolver usage/config error (exit 2) fails open" {
  stub_resolver 'echo feat-a; exit 2'
  fire "$TEST_TMPDIR/stub/guard-enter-by-path.py" EnterWorktree path "$MAIN" "$MAIN"
  assert_success
  assert_output ""
}

@test "a resolver that prints something other than a name fails open" {
  stub_resolver 'printf "feat a\nmore\n"; exit 0'
  fire "$TEST_TMPDIR/stub/guard-enter-by-path.py" EnterWorktree path "$MAIN" "$MAIN"
  assert_success
  assert_output ""
}

@test "a non-executable resolver fails open" {
  stub_resolver 'echo feat-a'
  chmod -x "$TEST_TMPDIR/stub/worktree-name-for.sh"
  fire "$TEST_TMPDIR/stub/guard-enter-by-path.py" EnterWorktree path "$MAIN" "$MAIN"
  assert_success
  assert_output ""
}

@test "the hook is registered for PreToolUse on EnterWorktree" {
  run python3 -c '
import json, sys
h = json.load(open(sys.argv[1]))["hooks"]["PreToolUse"]
matchers = [g["matcher"] for g in h
            if any(e["command"] == sys.argv[2] for e in g["hooks"])]
print(matchers)
' "$REPO_ROOT/hooks/hooks.json" '"${CLAUDE_PLUGIN_ROOT}/scripts/guard-enter-by-path.py"'
  assert_success
  assert_output "['EnterWorktree']"
}
