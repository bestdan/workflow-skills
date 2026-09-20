#!/usr/bin/env bats
# Every tier of scripts/worktree-config.sh, against a throwaway HOME and a
# fixture repo, so the suite never reads the developer's own worktree layout or
# git identity — which are exactly the two things the resolver exists to stop
# the plugin from assuming.

setup() {
  setup_test
  RESOLVER="$REPO_ROOT/scripts/worktree-config.sh"
  H="$TEST_TMPDIR/home"
  mkdir -p "$H"
  REPO="$TEST_TMPDIR/repo"
  mkdir -p "$REPO"
  git -C "$REPO" init --quiet
  unset WORKFLOW_SKILLS_WORKTREE_ROOT WORKFLOW_SKILLS_BRANCH_PREFIX XDG_STATE_HOME
}
teardown() { teardown_test; }
load test_helper

CONFIG_REL="dev_docs/worktrees/.worktree-config.yml"

# Write the repo-local config file the second tier reads.
write_config() { # body...
  mkdir -p "$REPO/$(dirname "$CONFIG_REL")"
  printf '%s\n' "$@" >"$REPO/$CONFIG_REL"
}

# Run the resolver from inside the fixture repo, under the throwaway HOME.
resolve() { # [env=val ...] -- [arg]
  cd "$REPO" || return 1
  run env HOME="$H" "$@"
}

# ------------------------------------------------- 1. the environment variable

@test "the env var wins over the config file for the root" {
  write_config "root: /from/config"
  resolve WORKFLOW_SKILLS_WORKTREE_ROOT=/from/env bash "$RESOLVER" root
  assert_success
  assert_output "/from/env"
}

@test "the env var wins over the config file for the branch prefix" {
  write_config "branch_prefix: fromconfig"
  resolve WORKFLOW_SKILLS_BRANCH_PREFIX=fromenv bash "$RESOLVER" prefix
  assert_success
  assert_output "fromenv/"
}

@test "an exported-but-empty env var falls through: empty means unset" {
  write_config "root: /from/config"
  resolve WORKFLOW_SKILLS_WORKTREE_ROOT="" bash "$RESOLVER" root
  assert_success
  assert_output "/from/config"
}

@test "a relative root in the env var is an error, not a reason to fall through" {
  write_config "root: /from/config"
  resolve WORKFLOW_SKILLS_WORKTREE_ROOT=not/absolute bash "$RESOLVER" root
  assert_failure 1
  assert_output --partial "not/absolute"
  refute_output --partial "/from/config"
}

@test "a prefix with whitespace in the env var is an error" {
  resolve WORKFLOW_SKILLS_BRANCH_PREFIX="two words" bash "$RESOLVER" prefix
  assert_failure 1
  assert_output --partial "whitespace"
}

@test "a leading ~/ in the env var expands against HOME" {
  resolve WORKFLOW_SKILLS_WORKTREE_ROOT='~/elsewhere/wt' bash "$RESOLVER" root
  assert_success
  assert_output "$H/elsewhere/wt"
}

# ------------------------------------------------------- 2. the config file

@test "the config file is used when the env var is unset" {
  write_config "root: /cfg/wt" "branch_prefix: cfgowner"
  resolve bash "$RESOLVER"
  assert_success
  assert_line "root=/cfg/wt"
  assert_line "branch_prefix=cfgowner/"
}

@test "a config value keeps its trailing comment and whitespace out of the value" {
  write_config "root: /cfg/wt   # where worktrees go" "branch_prefix: cfgowner  "
  resolve bash "$RESOLVER"
  assert_success
  assert_line "root=/cfg/wt"
  assert_line "branch_prefix=cfgowner/"
}

@test "a key present but empty in the config file falls through to the default" {
  write_config "root:" "branch_prefix: cfgowner"
  resolve XDG_STATE_HOME="$H/state" bash "$RESOLVER" root
  assert_success
  assert_output "$H/state/worktrees"
}

@test "an invalid config value is an error rather than a silent default" {
  write_config "root: relative/path"
  resolve bash "$RESOLVER" root
  assert_failure 1
  assert_output --partial "relative/path"
}

@test "the config file is read from the MAIN checkout, not the linked worktree" {
  # The file is git-excluded by convention, so it does not exist in a linked
  # worktree at all — resolving against --show-toplevel would lose it exactly
  # where the lifecycle scripts run.
  write_config "root: /cfg/wt"
  git -C "$REPO" commit --quiet --allow-empty -m init
  git -C "$REPO" worktree add --quiet -b linked "$TEST_TMPDIR/linked"
  cd "$TEST_TMPDIR/linked"
  run env HOME="$H" bash "$RESOLVER" root
  assert_success
  assert_output "/cfg/wt"
}

# --------------------------------------------- 3. the task handler's config

# The `branch_prefix` the gh-issue handler already declares, under its own key.
write_task_config() { # rel body...
  local rel="$1"
  shift
  mkdir -p "$REPO/dev_docs/tasks"
  printf '%s\n' "$@" >"$REPO/$rel"
}

@test "the branch prefix falls back to the task config rather than to git" {
  write_task_config "dev_docs/tasks/.task-config.yml" \
    "handler: gh-issue" "gh-issue:" "  branch_prefix: fromtasks/"
  resolve bash "$RESOLVER" prefix
  assert_success
  assert_output "fromtasks/"
}

@test "the worktree config outranks the task config for the branch prefix" {
  write_config "branch_prefix: fromworktree"
  write_task_config "dev_docs/tasks/.task-config.yml" \
    "gh-issue:" "  branch_prefix: fromtasks/"
  resolve bash "$RESOLVER" prefix
  assert_success
  assert_output "fromworktree/"
}

@test "the task config's local override outranks the committed task config" {
  write_task_config "dev_docs/tasks/.task-config.yml" \
    "gh-issue:" "  branch_prefix: committed/"
  write_task_config "dev_docs/tasks/.task-config.local.yml" \
    "gh-issue:" "  branch_prefix: local/"
  resolve bash "$RESOLVER" prefix
  assert_success
  assert_output "local/"
}

@test "the task config is not consulted for the worktree root" {
  # It has no root to declare, and a `root:` key there would belong to a
  # different schema than this one.
  write_task_config "dev_docs/tasks/.task-config.yml" "root: /tasks/root"
  resolve XDG_STATE_HOME="$H/state" bash "$RESOLVER" root
  assert_success
  assert_output "$H/state/worktrees"
}

# ----------------------------------------------------------- 4. the defaults

@test "the default root is the XDG state dir, not a ~/src layout" {
  resolve XDG_STATE_HOME="$H/state" bash "$RESOLVER" root
  assert_success
  assert_output "$H/state/worktrees"
}

@test "the default root falls back to ~/.local/state when XDG_STATE_HOME is unset" {
  resolve bash "$RESOLVER" root
  assert_success
  assert_output "$H/.local/state/worktrees"
  refute_output --partial "/src/worktrees"
}

@test "the default branch prefix is NOT the plugin author's" {
  # The regression that would make this plugin unusable by anyone else, and
  # exactly what survives a copy-paste port of the dotfiles original.
  resolve bash "$RESOLVER" prefix
  assert_success
  refute_output "bestdan/"
}

@test "the default branch prefix derives from the git email's local part" {
  # test_helper pins the fixture identity to test@example.com.
  resolve bash "$RESOLVER" prefix
  assert_success
  assert_output "test/"
}

@test "the default branch prefix falls back to user.name when there is no email" {
  printf '[user]\n\tname = Ada B. Lovelace\n' >"$TEST_TMPDIR/noemail"
  resolve GIT_CONFIG_GLOBAL="$TEST_TMPDIR/noemail" bash "$RESOLVER" prefix
  assert_success
  assert_output "ada-b-lovelace/"
}

@test "the default branch prefix is an impersonal literal when git knows nobody" {
  resolve GIT_CONFIG_GLOBAL=/dev/null bash "$RESOLVER" prefix
  assert_success
  assert_output "worktree/"
}

@test "outside a git repo the config file is skipped and the defaults hold" {
  cd "$TEST_TMPDIR"
  run env HOME="$H" XDG_STATE_HOME="$H/state" GIT_CEILING_DIRECTORIES="$TEST_TMPDIR" \
    bash "$RESOLVER" root
  assert_success
  assert_output "$H/state/worktrees"
}

# ------------------------------------------------------------ 5. the interface

@test "a prefix already ending in a slash is not doubled" {
  resolve WORKFLOW_SKILLS_BRANCH_PREFIX=owner/ bash "$RESOLVER" prefix
  assert_success
  assert_output "owner/"
}

@test "an unknown argument is an error, not a silent both-values print" {
  resolve bash "$RESOLVER" branch
  assert_failure 1
  assert_output --partial "unknown argument"
}
