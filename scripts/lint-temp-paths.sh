#!/usr/bin/env bash
# Reject a fixed file name in a machine-shared temp root in the skill, command
# and agent text named on the command line.
#
# Usage: scripts/lint-temp-paths.sh <file>...
#   exit 0  clean
#   exit 1  findings, one `  file:line: reason` line each on stderr
#   exit 2  no files given
#
# /tmp/claude/ is one directory shared by every Claude session on the machine,
# and $TMPDIR is shared by every process of one session (and differs sandboxed
# vs unsandboxed). A skill that writes `/tmp/claude/pr-body.md` has concurrent
# sessions or parallel workers overwrite each other's file (#1011, #1539). The
# fix is the directory scripts/work-tmp.sh prints, named by branch.
#
# Flagged:
#   /tmp/claude/<name>          anything INSIDE the root, literally. A bare
#                               `/tmp/claude/` followed by a backtick, space or
#                               punctuation is prose and passes; a dot or dash
#                               that starts a name (`.payload`, `-out`) is a name.
#   $TMPDIR/<name>, ${TMPDIR}/<name>, ${TMPDIR:-/tmp}/<name>
#                               unless the line carries an `XXX` mktemp
#                               template, which is unique.
# Not flagged: `/tmp/claude-<N>/`, the harness's own directory.
#
# A line that DESCRIBES such a path rather than instructing a write carries
# `temp-path-lint: allow` (typically as `<!-- temp-path-lint: allow -->`).
#
# Files come in as ARGUMENTS, as with scripts/lint-mktemp.sh, so
# test/lint-temp-paths.bats can run it over fixtures.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/lint-temp-paths.sh <file>..." >&2
  exit 2
fi

fail=0
while IFS= read -r hit; do
  [ -n "$hit" ] || continue
  echo "  $hit" >&2
  fail=1
done < <(awk '
  /temp-path-lint:[[:space:]]*allow/ { next }
  /\/tmp\/claude\/([A-Za-z0-9_<$-]|\.[A-Za-z0-9_.-])/ {
    printf "%s:%d: fixed name inside the shared /tmp/claude/ root\n", FILENAME, FNR
    next
  }
  /\$(\{TMPDIR(:-[^}]*)?\}|TMPDIR)"?\/([A-Za-z0-9_<*$-]|\.[A-Za-z0-9_.-])/ && !/XXX/ {
    printf "%s:%d: fixed name under $TMPDIR\n", FILENAME, FNR
  }
' "$@")

[ "$fail" -eq 0 ] || {
  echo "  → write it under the directory scripts/work-tmp.sh prints (see AGENTS.md)," >&2
  echo "  → or add <!-- temp-path-lint: allow --> if the line only describes the path." >&2
  exit 1
}
exit 0
