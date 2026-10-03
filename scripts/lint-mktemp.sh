#!/usr/bin/env bash
# Reject a `mktemp` with no template path in the shell files named on the
# command line.
#
# Usage: scripts/lint-mktemp.sh <file>...
#   exit 0  clean
#   exit 1  findings, one `  file:line: reason` line each on stderr
#   exit 2  no files given
#
# On macOS, `mktemp`, `mktemp -d` and `mktemp -t <prefix>` all create under the
# per-user `/var/folders/.../T/` directory and ignore `$TMPDIR`. Claude Code's
# Bash sandbox remaps `$TMPDIR` to a writable directory and denies
# `/var/folders`, so every one of them fails there with
# `mkstemp failed on /var/folders/...: Operation not permitted`. Inside the gate
# that reads as a test failure in whichever suite reached the call first, which
# cost agents a 6-7 minute rerun each time before the cause was found.
#
# The fix is a template under `$TMPDIR`:
#   mktemp "${TMPDIR:-/tmp}/<name>.XXXXXX"     (add -d for a directory)
#
# Two shapes are flagged: a `mktemp` followed only by option words and then the
# end of the command or a redirection (`2>/dev/null` hides the failure, it does
# not avoid it), and any `mktemp` carrying `-t` (alone or bundled, as in
# `-dt`). `-t` is flagged whatever its argument, because its argument is a
# prefix, not a path: `mktemp -t "$TMPDIR/x.XXXXXX"` still creates under
# /var/folders. Any other argument is taken to be a template.
#
# It takes its files as ARGUMENTS rather than discovering them, for the reason
# scripts/lint-bash4.sh gives: test/lint-mktemp.bats runs it over fixtures.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/lint-mktemp.sh <file>..." >&2
  exit 2
fi

mt_re='(^|[^[:alnum:]_-])mktemp([[:space:]]+-[A-Za-z]+)*([[:space:]]*($|[)&;|`]|[0-9]*[<>])|[[:space:]]+-[A-Za-z]*t[A-Za-z]*([[:space:]]|$))' # mktemp-lint: allow

fail=0
# Same comment handling as scripts/lint-pipefail.sh: prose about the rule does
# not fail the lint, and a trailing `# mktemp-lint: allow` exempts a line.
while IFS= read -r hit; do
  [ -n "$hit" ] || continue
  echo "  $hit: mktemp with no template ignores \$TMPDIR on macOS" >&2
  fail=1
done < <(grep -HnE -- "$mt_re" "$@" 2>/dev/null \
  | awk -v re="$mt_re" -F: '
      {
        file = $1; line = $2
        code = $0
        sub(/^[^:]*:[^:]*:/, "", code)
        if (code ~ /#[[:space:]]*mktemp-lint:[[:space:]]*allow[[:space:]]*$/) next
        sub(/^[[:space:]]*#.*/, "", code)
        sub(/[[:space:]]#.*/, "", code)
        if (code ~ re) printf "%s:%s\n", file, line
      }')

[ "$fail" -eq 0 ] || {
  echo "  → the Claude Code sandbox denies /var/folders, so this fails there." >&2
  echo "  → write it as: mktemp \"\${TMPDIR:-/tmp}/<name>.XXXXXX\"" >&2
  exit 1
}
exit 0
