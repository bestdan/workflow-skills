#!/usr/bin/env bash
# cao-coder — adapt an orchestrate-coders custom command to a CAO worker.
#
# Usage:
#   cao-coder.sh <spec-file> <existing-worktree> <backend:model>
#
# The caller owns the worktree. This wrapper rejects an absent directory before
# invoking cao-run, so it can never cause cao-run's worktree-creation path to
# run. cao-run prints the harvested diff and its exit status is this command's
# exit status.
#
# Exit status:
#   0  CAO worker completed; cao-run printed the harvested diff.
#   1  dispatch failure (including a backend outside the CAO fleet).
#   2  usage or invalid caller-owned inputs.

set -euo pipefail

die() {
  printf 'cao-coder: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: cao-coder.sh <spec-file> <existing-worktree> <backend:model>\n' >&2
  exit 2
}

[ "$#" -eq 3 ] || usage

spec=$1
worktree=$2
coder_spec=$3

[ -f "$spec" ] || {
  printf "cao-coder: spec file '%s' not found\n" "$spec" >&2
  exit 2
}
[ -d "$worktree" ] || {
  printf "cao-coder: worktree '%s' must already exist; refusing to create one\n" "$worktree" >&2
  exit 2
}
[ "$(git -C "$worktree" rev-parse --is-inside-work-tree 2>/dev/null)" = true ] \
  || {
    printf "cao-coder: worktree '%s' is not a git worktree\n" "$worktree" >&2
    exit 2
  }

case "$coder_spec" in
  *:*)
    backend=${coder_spec%%:*}
    model=${coder_spec#*:}
    ;;
  *) usage ;;
esac
[ -n "$backend" ] && [ -n "$model" ] || usage

case "$backend" in
  codex) profile=dev-codex ;;
  agy) profile=dev-antigravity ;;
  *) die "backend '$backend' is not in the CAO fleet" ;;
esac

command -v cao-run >/dev/null 2>&1 || die "cao-run is required but not found in PATH"

exec cao-run "$profile" "$model" "$worktree" "@$spec"
