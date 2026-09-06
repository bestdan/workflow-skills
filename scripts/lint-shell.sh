#!/usr/bin/env bash
# Repository-wide deterministic syntax, formatting, and shell lint checks.
#
# Usage: scripts/lint-shell.sh [--fast]
#   --fast  lint only the files you have touched (see narrow_to_touched below).
#           NOT the gate — CI and `just check` always lint the whole tree.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

fast=0
for arg in "$@"; do
  case "$arg" in
    --fast) fast=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

for tool in shfmt shellcheck; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "lint-shell: $tool is required" >&2
    exit 2
  fi
done

scripts/ensure-bats.sh || exit 2

shell_files=()
bats_files=()
while IFS= read -r file; do
  case "$file" in
    *.sh | *.bash) shell_files+=("$file") ;;
    *.bats) bats_files+=("$file") ;;
  esac
done < <(git ls-files --cached --others --exclude-standard '*.sh' '*.bash' '*.bats' | while IFS= read -r file; do
  [ -f "$file" ] && printf '%s\n' "$file"
done)

# --fast narrows the file set to what this branch has touched. ShellCheck is the
# reason: it costs ~23s over the whole tree, and its cost is superlinear in the
# size of a file's top-level scope, so a handful of long files carry nearly all
# of it while most edits never go near them. Re-linting them on every loop is
# the difference between a lint you run constantly and one you run once.
#
# The corollary, for anyone reading a surprising --fast time: this narrowing is
# by TOUCHED FILE, so a branch that adds several long shell files pays their
# full lint cost on every --fast run. That is the mechanism working, not a
# regression — see dev_docs/gate-performance.md.
#
# "Touched" = everything differing from the merge-base with the default branch,
# UNION the dirty working tree — so it still covers work you have already
# committed locally, not just unstaged edits. If no base resolves (a detached
# clone, a fork with no `main`), this FALLS BACK to the full list: slower than
# the caller asked for, never quieter than they asked for.
touched_paths() {
  local base="" ref
  for ref in origin/main main origin/master master; do
    base="$(git merge-base HEAD "$ref" 2>/dev/null)" && [ -n "$base" ] && break
    base=""
  done
  [ -n "$base" ] || return 1
  # `git diff --name-only <base>` spans base..working-tree, so committed and
  # uncommitted changes both land here; untracked files need the second call.
  git diff --name-only "$base" 2>/dev/null
  git ls-files --others --exclude-standard 2>/dev/null
  # Explicit, because the caller distinguishes "no base resolved" (fall back to
  # the full tree) from "base resolved, nothing touched" (lint nothing) — and
  # without this the function would instead return the status of the last `git`
  # call above, which says nothing about whether a base was found.
  return 0
}

narrow_to_touched() { # <touched-list> <file...> -> the files that appear in the list
  local touched="$1" file
  shift
  for file in "$@"; do
    grep -qxF -- "$file" <<<"$touched" && printf '%s\n' "$file"
  done
}

narrowed=0
if [ "$fast" -eq 1 ]; then
  # Branch on touched_paths' STATUS, never on whether "$touched" is empty: a
  # clean branch sitting exactly on the merge-base touches nothing, and an
  # emptiness test would read that as "no base found" and lint the whole tree —
  # making --fast slowest in precisely the case there is least to check.
  if touched="$(touched_paths)"; then
    narrowed=1
    kept_shell=()
    kept_bats=()
    if [ "${#shell_files[@]}" -gt 0 ]; then
      while IFS= read -r file; do
        [ -n "$file" ] && kept_shell+=("$file")
      done < <(narrow_to_touched "$touched" "${shell_files[@]}")
    fi
    if [ "${#bats_files[@]}" -gt 0 ]; then
      while IFS= read -r file; do
        [ -n "$file" ] && kept_bats+=("$file")
      done < <(narrow_to_touched "$touched" "${bats_files[@]}")
    fi
    echo "→ --fast: linting ${#kept_shell[@]} shell + ${#kept_bats[@]} bats file(s) this branch touched (of ${#shell_files[@]} + ${#bats_files[@]})"
    shell_files=()
    bats_files=()
    [ "${#kept_shell[@]}" -gt 0 ] && shell_files=("${kept_shell[@]}")
    [ "${#kept_bats[@]}" -gt 0 ] && bats_files=("${kept_bats[@]}")
  else
    echo "→ --fast: no merge-base with a default branch — linting everything" >&2
  fi
fi

fail=0
run() {
  echo "→ $*"
  "$@" || fail=1
}

# The file list both pattern lints scan. Each owns its own patterns and the
# reasoning behind them, and each takes its files as ARGUMENTS rather than
# discovering them — which is what lets test/lint-bash4.bats and
# test/lint-pipefail.bats exercise them against fixtures in a temp dir instead
# of only through this script's whole-tree scan.
lint_files=()
[ "${#shell_files[@]}" -gt 0 ] && lint_files+=("${shell_files[@]}")
[ "${#bats_files[@]}" -gt 0 ] && lint_files+=("${bats_files[@]}")

# The Bash 3.2 floor: bash-4-only constructs, which parse fine on the ubuntu
# job's Bash 5 and fail only on a contributor's Mac.
[ "${#lint_files[@]}" -eq 0 ] || run scripts/lint-bash4.sh "${lint_files[@]}"

# `<writer> | grep -q` under pipefail: a match can report 141 and read as a
# miss.
[ "${#lint_files[@]}" -eq 0 ] || run scripts/lint-pipefail.sh "${lint_files[@]}"

if [ "${#shell_files[@]}" -gt 0 ]; then
  run bash -n "${shell_files[@]}"
  run shfmt -i 2 -ci -bn -d "${shell_files[@]}"
  # SC1090/SC1091: runtime-resolved includes are validated by syntax checks and
  # the Bats suite; ShellCheck cannot follow paths assembled from repo roots.
  # Warning-and-error findings are blocking. Lower-severity style suggestions
  # remain available to contributors without forcing behavior-risking rewrites.
  run shellcheck -s bash --severity=warning -e SC1090,SC1091 "${shell_files[@]}"
fi
if [ "${#bats_files[@]}" -gt 0 ]; then
  run shfmt -ln bats -i 2 -ci -bn -d "${bats_files[@]}"
  run test/vendor/bats-core/bin/bats --count "${bats_files[@]}"
fi

[ "$fail" -eq 0 ] || exit 1
if [ "$narrowed" -eq 1 ]; then
  echo "lint-shell: OK (--fast: touched files only)"
else
  # Covers a plain run AND a --fast run that fell back to the full list — the
  # message has to track what was actually linted, not what was asked for.
  echo "lint-shell: OK"
fi
