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
    printf '%s\n' "$touched" | grep -qxF -- "$file" && printf '%s\n' "$file"
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

# --- The Bash 3.2 floor, checked statically -----------------------------------
# AGENTS.md requires every script here to run under Bash 3.2, because scripts/
# ships runtime helpers that execute on installed users' machines, where stock
# macOS /bin/bash is 3.2.57.
#
# CI's macOS job enforces that by EXECUTING the suites under /bin/bash (see
# dev_docs/testing.md), which is the stronger check — it catches semantic
# differences no pattern can see, like empty-array "${arr[@]}" under set -u
# before 4.4. But it only reaches lines a test actually runs. A `declare -A` in
# a branch no assertion covers ships undetected. This check is the complement:
# every line, every PR, on the ubuntu job, with no dependence on which bash a
# runner image happens to put first.
#
# The `bash -n` below is not that complement, despite appearances: it parses
# with whatever bash is running the lint, which on the ubuntu job is Bash 5, and
# Bash 5 parses every construct banned here without complaint. It only rejects
# them on a developer's Mac — the machines that need the check least.
#
# It is deliberately approximate. It catches the accidental introduction of
# common bash-4+ constructs, which is the realistic failure; it is not a
# portability proof, and a green run here is not one either.
bash4_re=()
bash4_why=()
bash4_ban() {
  bash4_re+=("$1")
  bash4_why+=("$2")
}
# The escape hatch is a trailing `# bash4-lint: allow` on the offending line.
# The seven definitions below are its first users, and the reason it exists at
# all: a table of patterns for banned constructs necessarily contains those
# constructs, so without an opt-out this check's only finding would be itself.
# Use it for text that merely mentions a construct, never to keep one.
# `A` anywhere in an option word, after any number of earlier option words:
# `declare -Ar t` and `local -r -A t` are as bash-4 as `declare -A t`, and an
# anchored trailing `A` misses both. `declare -i A` stays clean — that `A` is a
# variable name, not a dash-prefixed option.
bash4_ban '(^|[^A-Za-z0-9_])(declare|typeset|local)[[:space:]]+(-[A-Za-z]+[[:space:]]+)*-[A-Za-z]*A[A-Za-z]*([[:space:]]|$)' 'associative array (declare -A) — bash 4.0' # bash4-lint: allow
bash4_ban '(^|[^A-Za-z0-9_])(mapfile|readarray)([^A-Za-z0-9_]|$)' 'mapfile/readarray — bash 4.0'                                                                         # bash4-lint: allow
bash4_ban '(^|[^A-Za-z0-9_])coproc([^A-Za-z0-9_]|$)' 'coproc — bash 4.0'                                                                                                 # bash4-lint: allow
# One class, not four alternatives: `^` is only literal inside brackets when it
# is not first, and every escaped form of it dies somewhere on the way through
# awk's -v assignment. `${v,` and `${v^` also cover the doubled forms.
bash4_ban '[$][{][A-Za-z_][A-Za-z0-9_]*[,^]' 'case modification ${var,,} / ${var^^} — bash 4.0' # bash4-lint: allow
bash4_ban '&>>' '&>> append redirection — bash 4.0'                                             # bash4-lint: allow
bash4_ban '[|]&' '|& pipe-stderr shorthand — bash 4.0'                                          # bash4-lint: allow
bash4_ban ';;?&' ';& / ;;& case fallthrough — bash 4.0'                                         # bash4-lint: allow

bash4_files=()
[ "${#shell_files[@]}" -gt 0 ] && bash4_files+=("${shell_files[@]}")
[ "${#bats_files[@]}" -gt 0 ] && bash4_files+=("${bats_files[@]}")
if [ "${#bash4_files[@]}" -gt 0 ]; then
  echo "→ bash 3.2 floor (no bash-4+ constructs)"
  for i in "${!bash4_re[@]}"; do
    # The awk pass re-tests the match against a comment-stripped copy of the
    # line, so prose ABOUT a banned construct doesn't fail the lint — there is
    # one such comment in scripts/pr-fix-guard.sh explaining why it hand-rolls
    # a `mapfile`. Stripping uses the shell's own rule (`#` at line start or
    # after whitespace), which means a `#` inside a quoted string ends the
    # scanned portion early. That direction is deliberate: it can hide a
    # violation, never invent one, and a lint that cries wolf gets disabled.
    # An explicit `# bash4-lint: allow` ENDING the line skips it outright. The
    # anchor is load-bearing: matched loosely, the marker would also fire from
    # inside a string, hiding a real violation that shares the line with prose
    # about the marker.
    #
    # `grep -H` because the awk below assumes `file:line:code` unconditionally.
    # Without it a single-file scan emits `line:code` — the common case under
    # --fast — and every field shifts: bogus locations, and a `sub` that eats
    # part of the code, which can turn a violation into a silent pass.
    while IFS= read -r hit; do
      [ -n "$hit" ] || continue
      echo "  $hit: ${bash4_why[$i]}" >&2
      fail=1
    done < <(grep -HnE -- "${bash4_re[$i]}" "${bash4_files[@]}" 2>/dev/null \
      | awk -v re="${bash4_re[$i]}" -F: '
        {
          file = $1; line = $2
          code = $0
          sub(/^[^:]*:[^:]*:/, "", code)
          if (code ~ /#[[:space:]]*bash4-lint:[[:space:]]*allow[[:space:]]*$/) next
          sub(/^[[:space:]]*#.*/, "", code)
          sub(/[[:space:]]#.*/, "", code)
          if (code ~ re) printf "%s:%s\n", file, line
        }')
  done
  [ "$fail" -eq 0 ] || echo "  → these are bash 4+ only; this repo's floor is 3.2 (see AGENTS.md)" >&2
fi

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
