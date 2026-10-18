#!/usr/bin/env bash
# test-handoffs-untracked.sh — fail when a handoff under dev_docs/.handoffs/ is
# tracked. Only the directory's README.md is.
#
# .gitignore keeps handoffs out of `git status`, but `git add -f` gets past it,
# and once a handoff is tracked it outlives its `expires:` condition with
# nothing marking it stale: the next session reads it as current. A tracked
# 2026-09-21 handoff did exactly that, sending a later session to the wrong
# repo's lint setup and a recipe that no longer existed. See
# dev_docs/.handoffs/README.md for what a handoff is and where its durable
# content belongs instead.
#
# Run directly: bash scripts/test-handoffs-untracked.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

tracked="$(git ls-files -- dev_docs/.handoffs)" || exit 2
extra="$(grep -vxF dev_docs/.handoffs/README.md <<<"$tracked")"

if [ -n "$extra" ]; then
  echo "test-handoffs-untracked: handoffs are tracked, but only README.md may be:" >&2
  sed 's/^/  /' <<<"$extra" >&2
  echo "  → move anything durable into a commit, PR body, dev_docs/designs/ or the tracker," >&2
  echo "    then: git rm --cached <file>" >&2
  exit 1
fi
echo "test-handoffs-untracked: OK"
