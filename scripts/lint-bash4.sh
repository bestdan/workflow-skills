#!/usr/bin/env bash
# Reject bash-4+ constructs in the shell files named on the command line.
#
# Usage: scripts/lint-bash4.sh <file>...
#   exit 0  clean
#   exit 1  findings, one `  file:line: construct` line each on stderr
#   exit 2  no files given
#
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
# `bash -n` is not that complement, despite living next door in lint-shell.sh:
# it parses with whatever bash runs the lint, which on the ubuntu job is Bash 5,
# and Bash 5 parses every construct banned here without complaint. It rejects
# them only on a developer's Mac — the machines that need the check least.
#
# It is deliberately approximate. It catches the accidental introduction of
# common bash-4+ constructs, which is the realistic failure; it is not a
# portability proof, and a green run here is not one either.
#
# It takes its files as ARGUMENTS rather than discovering them, which is what
# makes it testable: test/lint-bash4.bats runs it over fixtures in a temp dir.
# The first cut of this check was inlined in lint-shell.sh, discovered its own
# file list, and could only be exercised by writing a fixture into the repo —
# so it shipped with two false passes that a real test would have caught.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/lint-bash4.sh <file>..." >&2
  exit 2
fi

bash4_re=()
bash4_why=()
bash4_ban() {
  bash4_re+=("$1")
  bash4_why+=("$2")
}
# The escape hatch is a trailing `# bash4-lint: allow` on the offending line.
# The definitions below are its first users, and the reason it exists at all: a
# table of patterns for banned constructs necessarily contains those
# constructs, so without an opt-out this check's only finding would be itself.
# Use it for text that merely mentions a construct, never to keep one.
#
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

fail=0
for i in "${!bash4_re[@]}"; do
  # The awk pass re-tests the match against a comment-stripped copy of the
  # line, so prose ABOUT a banned construct doesn't fail the lint — there is
  # one such comment in scripts/pr-fix-guard.sh explaining why it hand-rolls a
  # `mapfile`. Stripping uses the shell's own rule (`#` at line start or after
  # whitespace), which means a `#` inside a quoted string ends the scanned
  # portion early. That direction is deliberate: it can hide a violation, never
  # invent one, and a lint that cries wolf gets disabled.
  #
  # An explicit `# bash4-lint: allow` ENDING the line skips it outright. The
  # anchor is load-bearing: matched loosely, the marker would also fire from
  # inside a string, hiding a real violation that shares the line with prose
  # about the marker.
  #
  # `grep -H` because the awk below assumes `file:line:code` unconditionally.
  # Without it a single-file scan emits `line:code` — the common case when
  # lint-shell.sh runs --fast — and every field shifts: bogus locations, and a
  # `sub` that eats part of the code, which can turn a violation into a silent
  # pass. Both of those were real bugs here, caught in review, and both are
  # pinned by test/lint-bash4.bats.
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    echo "  $hit: ${bash4_why[$i]}" >&2
    fail=1
  done < <(grep -HnE -- "${bash4_re[$i]}" "$@" 2>/dev/null \
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

[ "$fail" -eq 0 ] || {
  echo "  → these are bash 4+ only; this repo's floor is 3.2 (see AGENTS.md)" >&2
  exit 1
}
exit 0
