#!/usr/bin/env bats
# scripts/worktree-teardown-reminder.py — the PostToolUse hook that reminds the
# agent teardown ends when ExitWorktree returns, ported case-for-case from the
# suite it replaces (bestdan/dotfiles agents/worktree_teardown_reminder.test.sh).
#
# The load-bearing case is the content one. Non-emptiness alone would pass on a
# message that said the opposite of what this hook exists to say, so the
# prohibition itself is asserted — as a substring, because the wording is
# allowed to change and the prohibition is not.

setup() {
  setup_test
  HOOK="$REPO_ROOT/scripts/worktree-teardown-reminder.py"
}
teardown() { teardown_test; }
load test_helper

# fire <tool_name> — run the hook on a payload naming that tool, echo stdout.
fire() {
  printf '{"tool_name": "%s"}' "$1" | python3 "$HOOK"
}

@test "ExitWorktree fires the reminder" {
  run fire ExitWorktree
  assert_success
  refute_output ""
}

@test "an unrelated tool stays silent" {
  run fire Bash
  assert_success
  assert_output ""
}

@test "WorktreeRemove (the hook event, not the tool) stays silent" {
  run fire WorktreeRemove
  assert_success
  assert_output ""
}

@test "the reminder carries hookEventName PostToolUse" {
  run bash -c 'printf "{\"tool_name\": \"ExitWorktree\"}" | python3 "$1" |
    python3 -c "import json,sys; print(json.load(sys.stdin)[\"hookSpecificOutput\"][\"hookEventName\"])"' _ "$HOOK"
  assert_success
  assert_output "PostToolUse"
}

@test "additionalContext carries the prohibition" {
  run bash -c 'printf "{\"tool_name\": \"ExitWorktree\"}" | python3 "$1" |
    python3 -c "import json,sys; print(json.load(sys.stdin)[\"hookSpecificOutput\"][\"additionalContext\"])"' _ "$HOOK"
  assert_success
  assert_output --partial "don't enumerate"
  assert_output --partial "other worktrees"
}

@test "a malformed payload fails open" {
  run bash -c 'printf "not json" | python3 "$1"' _ "$HOOK"
  assert_success
  assert_output ""
}

@test "a payload that is not an object fails open" {
  run bash -c 'printf "[]" | python3 "$1"' _ "$HOOK"
  assert_success
  assert_output ""
}

@test "the hook is registered for PostToolUse on ExitWorktree" {
  run python3 -c '
import json, sys
h = json.load(open(sys.argv[1]))["hooks"]["PostToolUse"]
entries = [e for g in h for e in g["hooks"]
           if e["command"].endswith("worktree-teardown-reminder.py")]
matchers = [g["matcher"] for g in h
            if any(e["command"].endswith("worktree-teardown-reminder.py")
                   for e in g["hooks"])]
print(len(entries), matchers)
' "$REPO_ROOT/hooks/hooks.json"
  assert_success
  assert_output "1 ['ExitWorktree']"
}
