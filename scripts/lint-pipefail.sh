#!/usr/bin/env bash
# Reject `<writer> | grep -q` in the shell files named on the command line.
#
# Usage: scripts/lint-pipefail.sh <file>...
#   exit 0  clean
#   exit 1  findings, one `  file:line: reason` line each on stderr
#   exit 2  no files given
#
# `grep -q` exits the moment it matches. Its writer is then still writing, dies
# of SIGPIPE, and `set -o pipefail` — which every script here sets — turns the
# pipeline's status into 141. So a SUCCESSFUL match reports failure, and the
# code takes the branch for "not found".
#
# The symptom is unmistakable once seen and baffling until then: an assertion
# reports that a string is absent, then prints the haystack containing it. It is
# also load-bearing outside the tests. scripts/spawn-orchestrator.sh:1698
# documents a measurement — that capture is ~30KB, and the piped form starts
# failing between 32KB and 64KB — so the margin was one growth spurt wide, and a
# false miss there refuses to detach and blames the jail.
#
# The fix is a here-string: `grep -q PATTERN <<<"$var"`. Bash writes it to a
# temp file, so there is no pipe and no writer to kill. It is not a workaround;
# it is strictly simpler than the pipeline it replaces.
#
# Scope is deliberately narrow. Only `-q` is flagged, because `grep -q` produces
# no output and therefore exists ONLY for its exit status — so a finding here is
# always a status that something consumes. `-c`, `-o`, `-v` and friends read
# their input to the end and cannot lose the race. `| head -1` does exit early,
# but in this tree it appears inside `$(...)` assignments whose status nothing
# reads, so flagging it would be noise.
#
# It takes its files as ARGUMENTS rather than discovering them, for the reason
# scripts/lint-bash4.sh gives: test/lint-pipefail.bats runs it over fixtures in
# a temp dir, which is the only way to test a lint against code that must not
# exist in the tree.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/lint-pipefail.sh <file>..." >&2
  exit 2
fi

# A pipe into grep whose option words carry a `q` anywhere: `-q`, `-qF`, `-Eq`,
# `-qiF`, `-Fxq` are all the same hazard. `-e q` is not an option word and does
# not match; `grep -q` with no preceding pipe is fine and is how the fix reads
# once the here-string moves the input off the pipeline.
#
# The leading `([^|]|^)` is what keeps `cmd || grep -q x <<<"$v"` clean: that is
# the FIXED form, and matching the second `|` of `||` would flag the repair as
# the defect. It cost a false positive on scripts/spawn-orchestrator.sh before
# it was added.
pf_re='([^|]|^)[|][[:space:]]*grep[[:space:]]+(-[A-Za-z]+[[:space:]]+)*-[A-Za-z]*q' # pipefail-lint: allow

fail=0
# The awk pass re-tests the match against a comment-stripped copy of the line,
# so prose ABOUT the hazard does not fail the lint — scripts/test-verify-fix.sh
# and scripts/spawn-orchestrator.sh both carry such comments, and they are the
# documentation this check enforces. An explicit `# pipefail-lint: allow` ENDING
# the line skips it outright; the anchor is load-bearing for the same reason it
# is in lint-bash4.sh, and the pattern above is its first user.
#
# `grep -H` because the awk assumes `file:line:code` unconditionally — without
# it a single-file scan emits `line:code` and every field shifts.
while IFS= read -r hit; do
  [ -n "$hit" ] || continue
  echo "  $hit: piped into grep -q; use a here-string" >&2
  fail=1
done < <(grep -HnE -- "$pf_re" "$@" 2>/dev/null \
  | awk -v re="$pf_re" -F: '
      {
        file = $1; line = $2
        code = $0
        sub(/^[^:]*:[^:]*:/, "", code)
        if (code ~ /#[[:space:]]*pipefail-lint:[[:space:]]*allow[[:space:]]*$/) next
        sub(/^[[:space:]]*#.*/, "", code)
        sub(/[[:space:]]#.*/, "", code)
        if (code ~ re) printf "%s:%s\n", file, line
      }')

[ "$fail" -eq 0 ] || {
  echo "  → under pipefail a matching grep -q can still report 141 (SIGPIPE on the writer)." >&2
  echo "  → write it as: grep -q PATTERN <<<\"\$var\"" >&2
  exit 1
}
exit 0
