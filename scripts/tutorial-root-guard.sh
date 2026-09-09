#!/usr/bin/env bash
# tutorial-root-guard.sh — the three path predicates
# skills/research-spike-tutorial/SKILL.md hand-walks before trusting `$WORK`.
#
# Usage: scripts/tutorial-root-guard.sh <path>
#   exit 0  <path> is non-empty, absolute, exists, and is not the caller's
#           repo root (git rev-parse --show-toplevel) or anywhere under it
#   exit 1  the failing predicate, printed to stderr
#
# This exists because an unset/empty $WORK expands to "", Path("") resolves
# to the current directory, and research-spike.py accepts that silently —
# scaffolding dev_docs/research/ into whatever directory the agent happens to
# be standing in. It has already happened once, during this skill's own
# development. See dev_docs/research_spike.md and the SKILL.md callout.
set -uo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/tutorial-root-guard.sh <path>" >&2
  exit 2
fi

path="$1"

if [ -z "$path" ]; then
  echo "tutorial-root-guard: path is empty" >&2
  exit 1
fi

case "$path" in
  /*) ;;
  *)
    echo "tutorial-root-guard: path is not absolute: $path" >&2
    exit 1
    ;;
esac

if [ ! -e "$path" ]; then
  echo "tutorial-root-guard: path does not exist: $path" >&2
  exit 1
fi

# Canonicalize both sides before comparing so a symlinked tmp dir (macOS's
# /tmp -> /private/tmp, say) doesn't produce a false "outside the repo".
real_path="$(cd "$path" 2>/dev/null && pwd -P)" || real_path="$path"

toplevel="$(git rev-parse --show-toplevel 2>/dev/null)" || toplevel=""
if [ -z "$toplevel" ]; then
  # Not inside any git repo — nothing for $path to be "under", so it passes.
  exit 0
fi
real_toplevel="$(cd "$toplevel" && pwd -P)" || real_toplevel="$toplevel"

if [ "$real_path" = "$real_toplevel" ]; then
  echo "tutorial-root-guard: path is the caller's repo root: $path" >&2
  exit 1
fi

case "$real_path/" in
  "$real_toplevel"/*)
    echo "tutorial-root-guard: path is under the caller's repo root: $path" >&2
    exit 1
    ;;
esac

exit 0
