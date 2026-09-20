#!/usr/bin/env bash
# Resolve the two values the worktree lifecycle is parameterised on: where
# worktrees are created, and what branch prefix their branches carry.
#
#   scripts/worktree-config.sh root     # absolute worktree root, exit 0
#   scripts/worktree-config.sh prefix   # branch prefix, exactly one trailing /
#   scripts/worktree-config.sh          # both, as `key=value` lines (diagnostic)
#
# Every other script and hook in the lifecycle reads these from here rather than
# hardcoding them, because both values are one person's convention rather than a
# fact about worktrees, and this plugin is installable by anyone. A literal
# `$HOME/src/worktrees` or `bestdan/` shipped in a helper is not a default — it
# is someone else's layout imposed on a fresh install.
#
# Precedence, per value, highest first:
#
#   1. An environment variable — WORKFLOW_SKILLS_WORKTREE_ROOT or
#      WORKFLOW_SKILLS_BRANCH_PREFIX. Exported-but-empty counts as unset, so a
#      caller that passes through an unset variable falls through rather than
#      resolving to nothing.
#   2. The repo's own worktree config file (see CONFIG_REL below). This plugin
#      already has a config convention and this file follows it rather than
#      inventing one: a git-excluded, repo-local, flat `key: value` YAML subset
#      under `dev_docs/<area>/`, exactly like `dev_docs/tasks/.task-config.yml`
#      and `dev_docs/orchestrate-coders/.coders.yml`. There is no user-level
#      config convention here to reuse, and adding one would have been a third
#      place to look.
#   3. For the branch prefix only: `branch_prefix` from the task handler's
#      config (TASK_CONFIG_REL below). A repo has one branch naming convention,
#      and that file already declares it — the gh-issue handler builds its claim
#      lock ref from it. Reading it here means a repo that has set it does not
#      restate it, and the two cannot drift into disagreeing about what a
#      branch of this repo is called. Tier 2 still outranks it, for the repo
#      that genuinely wants them different.
#   4. A derived default that assumes nothing about the machine — an XDG state
#      directory for the root, and the git identity for the prefix.
#
# Set-but-invalid is an ERROR at tier 1 rather than a reason to keep looking:
# someone who exported the variable meant it, and quietly resolving somewhere
# else hands them the wrong worktree while looking like it worked. An invalid
# value in the config file is reported the same way, for the same reason.

set -uo pipefail

CONFIG_REL="dev_docs/worktrees/.worktree-config.yml"
TASK_CONFIG_REL="dev_docs/tasks/.task-config.yml"
TASK_CONFIG_LOCAL_REL="dev_docs/tasks/.task-config.local.yml"

# The last-resort branch prefix. Deliberately impersonal: a prefix derived from
# nobody is better than one derived from the plugin author, which is the
# regression a copy-paste port produces and test/worktree-config.bats pins.
PREFIX_FALLBACK="worktree"

die() {
  printf 'worktree-config: %s\n' "$1" >&2
  exit 1
}

# Expand a leading `~/` (or a bare `~`) against $HOME. Config files are written
# by hand, so a tilde is the spelling people reach for; nothing else in a path
# is expanded, because a config value is data rather than shell.
expand_tilde() { # value
  # Held in a variable so the tilde is unmistakably a literal, to a reader and
  # to the linter alike — a quoted `~` is flagged as a tilde that will not
  # expand, which here is exactly the point.
  local tilde='~'
  case "$1" in
    "$tilde") printf '%s\n' "$HOME" ;;
    "$tilde"/*) printf '%s\n' "$HOME/${1#"$tilde"/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

# The MAIN checkout's root, which is where the config file lives. Not
# `--show-toplevel`: read from inside a linked worktree that resolves to the
# worktree, and the config file is git-excluded, so it is precisely the file
# that is NOT there. `--git-common-dir` points at the shared .git directory in
# every worktree of a repo, so its parent is the main checkout either way.
repo_root() {
  local common
  common=$(git rev-parse --git-common-dir 2>/dev/null) || return 1
  [ -n "$common" ] || return 1
  case "$common" in
    /*) ;;
    *) common="$PWD/$common" ;;
  esac
  (cd "$common/.." 2>/dev/null && pwd) || return 1
}

# One key out of the flat YAML subset the config file is restricted to:
# `key: value`, one per line, no nesting and no quoting. The same subset
# scripts/preflight.sh reads `handler:` out of, and for the same reason — a real
# parser would be a dependency, and this plugin's Python assets are stdlib-only.
#
# Leading indentation is tolerated so the key can sit inside a block, which is
# how `.task-config.yml` carries `branch_prefix` — under its `gh-issue:` key.
# Nothing here reads a key whose name repeats across blocks, so the first match
# is unambiguous.
config_get() { # relpath key
  local root file value
  root=$(repo_root) || return 1
  file="$root/$1"
  [ -f "$file" ] || return 1
  value=$(sed -n "s/^[[:space:]]*$2:[[:space:]]*//p" "$file" | head -1)
  # Trailing whitespace and a trailing comment are the two things a hand-edited
  # line carries that the value does not.
  value=${value%%#*}
  value=$(printf '%s' "$value" | sed 's/[[:space:]]*$//')
  [ -n "$value" ] || return 1
  printf '%s\n' "$value"
}

# ------------------------------------------------------------------- the root

validate_root() { # source value
  case "$(expand_tilde "$2")" in
    /*) ;;
    *) die "$1 must be an absolute path (or start with ~/): $2" ;;
  esac
}

resolve_root() {
  local value
  if [ -n "${WORKFLOW_SKILLS_WORKTREE_ROOT:-}" ]; then
    validate_root "WORKFLOW_SKILLS_WORKTREE_ROOT" "$WORKFLOW_SKILLS_WORKTREE_ROOT"
    expand_tilde "$WORKFLOW_SKILLS_WORKTREE_ROOT"
    return 0
  fi
  if value=$(config_get "$CONFIG_REL" root); then
    validate_root "$CONFIG_REL: root" "$value"
    expand_tilde "$value"
    return 0
  fi
  # No `~/src/worktrees`: that layout is one person's, and a fresh install has
  # no `~/src` at all. XDG_STATE_HOME is the standard answer for data a tool
  # creates and the user does not curate, and it is resolved rather than
  # assumed — the spec's own fallback is `~/.local/state`.
  #
  # A relative XDG_STATE_HOME is ignored rather than rejected, which is what the
  # spec says to do with one — and unlike the two tiers above, this value was
  # not set for this tool, so dying on it would be a new failure mode over
  # someone else's broken environment. Ignoring matters because the downstream
  # failure is silent: `git worktree add relative/worktrees/<repo>/<name>`
  # succeeds against whatever directory the hook happened to run in, registers
  # the worktree there, and every later lookup by the documented path misses it.
  case "${XDG_STATE_HOME:-}" in
    /*) printf '%s\n' "$XDG_STATE_HOME/worktrees" ;;
    *) printf '%s\n' "$HOME/.local/state/worktrees" ;;
  esac
}

# ----------------------------------------------------------------- the prefix

# Normalise to exactly one trailing slash, so `bestdan` and `bestdan/` are the
# same config and a caller can always write "$(prefix)$name".
#
# The whole run of trailing slashes goes, not one: `%` strips a single match,
# so `owner//` would otherwise come back unchanged and build `owner//name`,
# which git rejects as a ref with an empty path component — far from the
# config line that caused it.
normalise_prefix() { # value
  local v="$1"
  while [ "${v%/}" != "$v" ]; do v="${v%/}"; done
  printf '%s/\n' "$v"
}

validate_prefix() { # source value
  case "$2" in
    /*) die "$1 must not start with /: $2" ;;
    *[[:space:]]*) die "$1 must not contain whitespace: $2" ;;
  esac
}

# Turn a git identity into something that can sit in a ref name: lowercase, and
# every run of non-alphanumerics collapsed to a single dash.
slugify() { # value
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9]\{1,\}/-/g' -e 's/^-*//' -e 's/-*$//'
}

resolve_prefix() {
  local value slug file
  if [ -n "${WORKFLOW_SKILLS_BRANCH_PREFIX:-}" ]; then
    validate_prefix "WORKFLOW_SKILLS_BRANCH_PREFIX" "$WORKFLOW_SKILLS_BRANCH_PREFIX"
    normalise_prefix "$WORKFLOW_SKILLS_BRANCH_PREFIX"
    return 0
  fi
  if value=$(config_get "$CONFIG_REL" branch_prefix); then
    validate_prefix "$CONFIG_REL: branch_prefix" "$value"
    normalise_prefix "$value"
    return 0
  fi
  # The task handler's config, local override first — the same order
  # scripts/preflight.sh reads that pair in.
  for file in "$TASK_CONFIG_LOCAL_REL" "$TASK_CONFIG_REL"; do
    if value=$(config_get "$file" branch_prefix); then
      validate_prefix "$file: branch_prefix" "$value"
      normalise_prefix "$value"
      return 0
    fi
  done
  # Derived from whoever this machine's git says it is. The email's local part
  # first: it is an identifier, where user.name is a display name that more
  # often slugifies into something nobody would type.
  value=$(git config --get user.email 2>/dev/null)
  slug=$(slugify "${value%%@*}")
  if [ -z "$slug" ]; then
    value=$(git config --get user.name 2>/dev/null)
    slug=$(slugify "$value")
  fi
  normalise_prefix "${slug:-$PREFIX_FALLBACK}"
}

# ------------------------------------------------------------------ dispatch

case "${1-}" in
  root) resolve_root ;;
  prefix) resolve_prefix ;;
  '')
    # Resolved into variables first: an invalid value makes the resolver die in
    # a command substitution, which would otherwise leave the outer shell
    # printing `root=` and exiting 0.
    root=$(resolve_root) || exit 1
    prefix=$(resolve_prefix) || exit 1
    printf 'root=%s\n' "$root"
    printf 'branch_prefix=%s\n' "$prefix"
    ;;
  *) die "unknown argument: $1 (expected 'root', 'prefix', or none)" ;;
esac
