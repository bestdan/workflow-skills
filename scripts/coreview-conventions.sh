#!/usr/bin/env bash
# coreview-conventions.sh — name the review-convention files co-review appends
# to every reviewer's assembled <INPUT>.
#
# co-review's reviewers get no repo context by design, so the conventions they
# are held to have to travel inside <INPUT>. Those conventions live in the
# agent-guidance plugin, which is installed separately; agent-guidance-dir.sh
# (beside this script) finds it, and this script turns that root into the list
# of files the assembling `cat` takes as extra arguments.
#
# Usage:
#   scripts/coreview-conventions.sh
#
# Prints the absolute path of each convention file the installed plugin ships,
# one per line, in the order they are to be concatenated:
#   1. writing_about_code.md
#   2. reviewing.md
# and nothing else on stdout. The caller pastes these paths, double-quoted,
# into the `cat` that assembles <INPUT> — never into the reviewer's own command
# tail, which the exact-match allow rules pin byte-for-byte.
#
# Exit status:
#   0  at least one file is listed. A file the installed copy lacks (an older
#      release) is named on stderr; attach what was listed and carry the note
#   1  AGENT_GUIDANCE_DIR is set but does not name a plugin root. A
#      misconfiguration, not an absence: the resolver's diagnostic is on stderr
#      and the caller must surface it rather than dispatch as if nothing
#      happened
#   3  nothing to attach — the plugin is not installed anywhere the resolver
#      looks, or the installed copy ships neither file. stdout is empty and
#      stderr says which. A normal outcome: dispatch without the segment and
#      say so in the run summary
#
# The paths are read here, before dispatch, rather than by a script segment
# inside the dispatch line, on purpose. agy's and devin's assembly is chained
# with `&&`, so a segment that exited 3 there would cancel the dispatch — the
# opposite of failing soft — and a segment that always exited 0 could no longer
# tell a misconfigured override apart from an absent plugin.
set -uo pipefail

case "${1:-}" in
  "") ;;
  -h | --help)
    sed -n '2,38p' "$0"
    exit 0
    ;;
  *)
    echo "coreview-conventions: unknown argument: $1" >&2
    exit 2
    ;;
esac

# Concatenation order. writing_about_code.md carries the register every
# finding is written in; reviewing.md carries what a review checks and how
# each finding is shaped, and refers back to the first.
CONVENTION_FILES="writing_about_code.md reviewing.md"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$("$here/agent-guidance-dir.sh")"
rc=$?
if [ "$rc" -ne 0 ]; then
  # The resolver has already said why on stderr. Its codes are this script's
  # codes: 1 is loud, 3 is soft, anything else is a bug worth seeing as-is.
  exit "$rc"
fi

found=""
for name in $CONVENTION_FILES; do
  if [ -f "$root/$name" ]; then
    found="$found$root/$name"$'\n'
  else
    printf 'coreview-conventions: %s has no %s — the installed agent-guidance predates it\n' \
      "$root" "$name" >&2
  fi
done

if [ -z "$found" ]; then
  printf 'coreview-conventions: nothing to attach; update the plugin with:\n' >&2
  printf '  claude plugin update agent-guidance\n' >&2
  exit 3
fi

printf '%s' "$found"
