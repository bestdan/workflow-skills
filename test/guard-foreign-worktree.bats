#!/usr/bin/env bats
# scripts/guard-foreign-worktree.py — the PreToolUse guard that refuses a write
# into a worktree the session has not entered, and warns once on the first
# write in a main checkout sitting on its default branch. Ported case-for-case
# from the suite it replaces (bestdan/dotfiles#911,
# agents/guard_foreign_worktree.test.sh).
#
# This cannot work on command text alone: the verdict depends on which
# worktree of which repo a path lands in, which the guard learns by asking git.
# So every case runs against real throwaway repos, and the payload's `cwd`
# field — the one Claude Code fills with the session's working directory — is
# what points the guard at them.

setup() {
  setup_test
  HOOK="$REPO_ROOT/scripts/guard-foreign-worktree.py"
  tmp="$TEST_TMPDIR"
  # Each case gets its own stamp directory unless it is explicitly testing
  # the once-per-session behaviour, so the warning is never suppressed by a
  # previous case's stamp.
  stamps="$tmp/stamps"
  mkdir -p "$stamps"
  repo="$(new_repo repo)"
  wt="$tmp/wt"
  git -C "$repo" worktree add -q -b feature "$wt"
  echo body >"$repo/file.txt"
  echo body >"$wt/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit -q -m file
}
teardown() { teardown_test; }
load test_helper

new_repo() {
  local dir="$tmp/$1"
  git init -q -b main "$dir"
  git -C "$dir" commit -q --allow-empty -m init
  printf %s "$dir"
}

# verdict <tool> <cwd> <payload-field> <value> [session] -> deny|warn|allow
verdict() {
  local tool=$1 cwd=$2 key=$3 value=$4 session=${5:-$RANDOM.$RANDOM} out
  out=$(python3 -c '
import json, sys
tool, cwd, session, key, value = sys.argv[1:]
print(json.dumps({"tool_name": tool, "cwd": cwd, "session_id": session,
                  "tool_input": {key: value}}))
' "$tool" "$cwd" "$session" "$key" "$value" \
    | TMPDIR="$stamps/$session" python3 "$HOOK" 2>/dev/null)
  if [ -z "$out" ]; then
    printf 'allow'
    return
  fi
  printf %s "$out" | python3 -c '
import json, sys
h = json.load(sys.stdin).get("hookSpecificOutput", {})
if h.get("permissionDecision") == "deny":
    print("deny", end="")
elif h.get("additionalContext"):
    print("warn", end="")
else:
    print("unexpected", end="")
'
}

# expect <want> <tool> <cwd> <payload-field> <value> [session]
expect() {
  local want=$1 got
  shift
  got=$(verdict "$@")
  if [ "$got" != "$want" ]; then
    echo "want $want, got $got: $*" >&2
    return 1
  fi
}

# bash_case <want> <cwd> <command>
bash_case() { expect "$1" Bash "$2" command "$3"; }

@test "a mutating git call into a foreign worktree is denied" {
  bash_case deny "$repo" "git -C $wt commit -m x"
  bash_case deny "$repo" "git -C $wt add ."
  bash_case deny "$repo" "git -C $wt push"
  bash_case deny "$repo" "git -C $wt reset --hard"
  bash_case deny "$repo" "git -C $wt branch -d feature" # a mutating branch flag
  bash_case deny "$repo" "git -C $wt tag v1"            # a positional creates a tag
  bash_case deny "$repo" "git -C ../wt commit -m x"     # a relative -C resolving foreign
}

@test "cd, redirects, writers and in-place editors into a foreign worktree are denied" {
  bash_case deny "$repo" "cd $wt && git commit -m x"
  bash_case deny "$repo" "cd $wt; touch a"
  bash_case deny "$repo" "echo hi > $wt/file.txt"
  bash_case deny "$repo" "echo hi >> $wt/file.txt"
  bash_case deny "$repo" "cp $repo/file.txt $wt/copy.txt"
  bash_case deny "$repo" "rm $wt/file.txt"
  bash_case deny "$repo" "sed -i '' s/a/b/ $wt/file.txt"
}

# The #827 shape: auto mode steers the edit into a heredoc, so the path is
# inside opaque code and no Edit/Write hook ever sees it.
@test "an interpreter fed inline code naming a foreign path is denied" {
  bash_case deny "$repo" "$(printf 'python3 - <<%s\nopen("%s/file.txt", "w").write("x")\nPY\n' "'PY'" "$wt")"
  bash_case deny "$repo" "python3 -c \"open('$wt/file.txt','w')\""
}

# The reverse direction matters as much: a session standing in a worktree must
# not reach back into the main checkout either.
@test "a worktree session reaching back into the main checkout is denied" {
  bash_case deny "$wt" "git -C $repo commit -m x"
  bash_case deny "$wt" "echo x > $repo/file.txt"
}

@test "Edit, Write and NotebookEdit into a foreign worktree are denied" {
  expect deny Edit "$repo" file_path "$wt/file.txt"
  expect deny Write "$repo" file_path "$wt/new.txt"
  expect deny NotebookEdit "$repo" notebook_path "$wt/nb.ipynb"
  # $HOME is the recommended spelling, and no shell has expanded it by the
  # time a hook sees it.
  HOME="$tmp" expect deny Write "$repo" file_path '$HOME/wt/new.txt'
}

# Per-command path flags are the recommended way to read another tree, so
# denying these would false-deny a taught idiom.
@test "reads into a foreign worktree are allowed" {
  bash_case allow "$repo" "git -C $wt status"
  bash_case allow "$repo" "git -C $wt log --oneline"
  bash_case allow "$repo" "git -C $wt diff"
  bash_case allow "$repo" "git -C $wt worktree list"
  bash_case allow "$repo" "git -C $wt stash list"
  bash_case allow "$repo" "git -C $wt config --get user.name"
  bash_case allow "$repo" "git -C $wt branch --show-current"
  bash_case allow "$repo" "git -C $wt branch --contains HEAD" # a value flag is not a new branch
  bash_case allow "$repo" "git -C $wt tag -l"
  bash_case allow "$repo" "git -C $wt fetch" # fetch writes only the shared object store
  bash_case allow "$repo" "rg pattern $wt"
  bash_case allow "$repo" "cat $wt/file.txt"
  bash_case allow "$repo" "diff $repo/file.txt $wt/file.txt"
}

@test "the bypass, another repo, no repo, and a path outside every worktree are allowed" {
  local other
  other="$(new_repo other)"
  bash_case allow "$repo" "env WORKFLOW_SKILLS_ALLOW_FOREIGN_WRITE=1 git -C $wt commit -m x"
  bash_case allow "$other" "echo x > $wt/file.txt"
  bash_case allow "$tmp" "echo x > $wt/file.txt"
  bash_case allow "$repo" "echo x > $tmp/scratch.txt"
}

# A deleted worktree stays listed with a `prunable` line. If it won the holder
# lookup, the guard would deny a command and name a path that no longer exists.
@test "a prunable worktree is not a holder" {
  local gone="$tmp/gone"
  git -C "$repo" worktree add -q -b gone-branch "$gone"
  rm -rf "$gone"
  bash_case allow "$repo" "echo x > $gone/file.txt"
}

@test "a tab in a worktree path is read intact" {
  local tabbed
  tabbed="$(printf '%s/ta\tb' "$tmp")"
  git -C "$repo" worktree add -q -b tabbed "$tabbed"
  bash_case deny "$repo" "$(printf 'echo x > "%s/file.txt"' "$tabbed")"
}

# A NEWLINE is the case `--porcelain -z` is actually for: without `-z` the
# record splits, the guard learns a root that is a PREFIX of the real one, and
# misses everything under it. Asserted through a Write payload, because a
# command carrying a literal newline is split into segments long before this.
@test "a newline in a worktree path is read intact" {
  local newlined
  newlined="$(printf '%s/new\nline' "$tmp")"
  git -C "$repo" worktree add -q -b newlined "$newlined"
  expect deny Write "$repo" file_path "$newlined/file.txt"
}

@test "the first write in a main checkout on the default branch warns, once" {
  expect warn Write "$repo" file_path "$repo/new.txt" warn-once
  expect allow Write "$repo" file_path "$repo/second.txt" warn-once
  bash_case warn "$repo" "echo x > $repo/new.txt"
  bash_case warn "$repo" 'git commit -m x'
}

@test "reads, bare interpreters, linked worktrees and off-default checkouts do not warn" {
  local off_default
  bash_case allow "$repo" 'git status'
  bash_case allow "$repo" 'python3 scripts/foo.py'
  expect allow Write "$wt" file_path "$wt/new.txt"
  off_default="$(new_repo off-default)"
  git -C "$off_default" checkout -q -b topic
  expect allow Write "$off_default" file_path "$off_default/new.txt"
}

@test "a malformed payload fails open" {
  run bash -c 'printf "not json" | python3 "$1"' _ "$HOOK"
  assert_success
  assert_output ""
}

@test "the hook is registered for PreToolUse on Bash and on Edit|Write|NotebookEdit" {
  run python3 -c '
import json, sys
h = json.load(open(sys.argv[1]))["hooks"]["PreToolUse"]
print(sorted(g["matcher"] for g in h
             if any(e["command"].endswith("guard-foreign-worktree.py") for e in g["hooks"])))
' "$REPO_ROOT/hooks/hooks.json"
  assert_success
  assert_output "['Bash', 'Edit|Write|NotebookEdit']"
}
