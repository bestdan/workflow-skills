#!/usr/bin/env bash
# check-reproducibility.sh — re-run an analysis-pipeline directory and diff its
# committed, deterministic outputs against a fresh regeneration.
#
# Usage: scripts/analysis-pipeline/check-reproducibility.sh <dir>
#   prints REPRO: verdict=pass|fail|n/a  (plus the diff on fail)
#   exit 0 on pass/n/a, exit 1 on fail, exit 2 on usage error
#
# agents/fact-reviewer.md check 3 calls this instead of hand-walking the
# clean-check / re-run / diff / guaranteed-restore sequence.
#
# All git operations run with cwd inside <dir>, so <dir> may be given as any
# path (absolute, or relative to the caller's cwd) into any repo, not only
# one rooted at the caller's own repo root.
#
# n/a cases (never a fail — nothing was verified, so nothing is claimed):
#   - <dir> is not inside a git repo
#   - <dir> has uncommitted changes: a re-run would clobber them and any diff
#     would report false drift
#   - <dir> has no README.md, or its README's "## How to Run" section names
#     no "uv run" command to re-run
#   - <dir> has no committed model_output.json or memo.filled.md at HEAD: a
#     re-run would create an untracked file the diff step would silently skip
#     and the restore trap could not clean up
#
# The only files ever touched are model_output.json and memo.filled.md
# inside <dir> (fill_templates.py leaves both deterministic — see the
# example's README, which documents memo.final.md as the nondeterministic,
# narrative-filled artifact this script never touches); they are always
# restored via `git checkout --`, in a trap that fires on every exit path,
# including a failed re-run.
set -uo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/analysis-pipeline/check-reproducibility.sh <dir>" >&2
  exit 2
fi

dir="$1"
if [ ! -d "$dir" ]; then
  echo "check-reproducibility: not a directory: $dir" >&2
  exit 2
fi

model_output="model_output.json"
memo_filled="memo.filled.md"
readme="$dir/README.md"

# 1. Clean check — must run before anything else touches $dir. Paths are
# relative to $dir itself (cwd below), so this scopes to exactly $dir and
# everything under it regardless of repo nesting.
if ! status="$(cd "$dir" && git status --porcelain . 2>&1)"; then
  echo "REPRO: verdict=n/a (not inside a git repo: $dir)"
  exit 0
fi
if [ -n "$status" ]; then
  echo "REPRO: verdict=n/a (uncommitted changes under $dir)"
  exit 0
fi

if [ ! -f "$readme" ]; then
  echo "REPRO: verdict=n/a (no README.md in $dir)"
  exit 0
fi

# Pull the deterministic re-run commands out of the README's own "## How to
# Run" section: every "uv run ..." line inside a ```sh/```bash fence there.
# Scoped to that one section (not any fence in the file) so an unrelated
# example elsewhere in the README — a test command, a setup step — is never
# executed as a side effect of this check. This also picks up model.py +
# fill_templates.py while skipping an optional narrative-fill step piped
# through an LLM CLI, with no need to know that step's name — it never
# matches "uv run ".
commands="$(awk '
  /^## / { in_section = ($0 == "## How to Run") }
  in_section && /^```(sh|bash)[[:space:]]*$/ { fence = 1; next }
  in_section && /^```/ { fence = 0; next }
  fence && /^[[:space:]]*uv run / { print }
' "$readme")"

if [ -z "$commands" ]; then
  echo "REPRO: verdict=n/a (no uv run command found in $readme)"
  exit 0
fi

# A clean directory can still lack a committed baseline for one or both
# outputs. Require both in HEAD before running anything: otherwise the
# re-run creates an untracked file, the diff step below silently skips it
# (reporting pass instead of drift), and the cleanup trap's `git checkout --`
# cannot remove an untracked path, so it wouldn't even restore the other one.
if ! (cd "$dir" && git cat-file -e "HEAD:./$model_output") 2>/dev/null \
  || ! (cd "$dir" && git cat-file -e "HEAD:./$memo_filled") 2>/dev/null; then
  echo "REPRO: verdict=n/a (no committed $model_output or $memo_filled in $dir)"
  exit 0
fi

tmp_before="$(mktemp "${TMPDIR:-/tmp}/check-repro.XXXXXX")"
tmp_after="$(mktemp "${TMPDIR:-/tmp}/check-repro.XXXXXX")"
run_output="$(mktemp "${TMPDIR:-/tmp}/check-repro.XXXXXX")"
cleanup() {
  rm -f "$tmp_before" "$tmp_before.norm" "$tmp_after" "$tmp_after.norm" "$run_output"
  # Separate calls: a single pathspec that can't restore (e.g. one file went
  # missing) would otherwise fail the whole checkout atomically and restore
  # neither file.
  (cd "$dir" && git checkout -- "$model_output") 2>/dev/null
  (cd "$dir" && git checkout -- "$memo_filled") 2>/dev/null
}
trap cleanup EXIT

# 2. Re-run the pipeline.
run_failed=0
while IFS= read -r cmd; do
  [ -z "$cmd" ] && continue
  if ! (cd "$dir" && eval "$cmd") >>"$run_output" 2>&1; then
    run_failed=1
    break
  fi
done <<EOF
$commands
EOF

if [ "$run_failed" -ne 0 ]; then
  echo "REPRO: verdict=fail (pipeline re-run failed)"
  cat "$run_output" >&2
  exit 1
fi

diff_output=""

# 3. Normalized JSON diff of model_output.json (no jq dependency).
if [ -f "$dir/$model_output" ] && (cd "$dir" && git show "HEAD:./$model_output") >"$tmp_before" 2>/dev/null; then
  python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    json.dump(json.load(f), sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
' "$tmp_before" >"$tmp_before.norm" 2>/dev/null || cp "$tmp_before" "$tmp_before.norm"
  python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    json.dump(json.load(f), sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
' "$dir/$model_output" >"$tmp_after.norm" 2>/dev/null || cp "$dir/$model_output" "$tmp_after.norm"
  d="$(diff -u "$tmp_before.norm" "$tmp_after.norm" 2>&1)"
  if [ -n "$d" ]; then
    diff_output="$diff_output
--- $dir/$model_output (committed vs. re-run, normalized) ---
$d"
  fi
fi

# 4. Diff of memo.filled.md — fill_templates.py leaves it deterministic, but
# a markdown formatter run over the committed copy (e.g. `dprint fmt`) can
# repad a table's column widths to its final substituted values, which the
# raw regenerated file never repads to match. Collapse runs of padding
# whitespace and separator dashes, but ONLY on table rows/separators (lines
# starting with "|", after optional leading whitespace) — every other line
# is compared byte-exact, so a Markdown hard line break (two trailing
# spaces) or an indented code block (four leading spaces) still counts as
# drift, the same way the JSON diff above normalizes key order without
# touching values.
if [ -f "$dir/$memo_filled" ] && (cd "$dir" && git show "HEAD:./$memo_filled") >"$tmp_before" 2>/dev/null; then
  python3 -c '
import re, sys
def norm(path):
    out = []
    with open(path) as f:
        for line in f:
            if re.match(r"^\s*\|", line):
                line = re.sub(r" {2,}", " ", line)
                line = re.sub(r"-{3,}", "---", line)
            out.append(line)
    return "".join(out)
sys.stdout.write(norm(sys.argv[1]))
' "$tmp_before" >"$tmp_before.norm" 2>/dev/null || cp "$tmp_before" "$tmp_before.norm"
  python3 -c '
import re, sys
def norm(path):
    out = []
    with open(path) as f:
        for line in f:
            if re.match(r"^\s*\|", line):
                line = re.sub(r" {2,}", " ", line)
                line = re.sub(r"-{3,}", "---", line)
            out.append(line)
    return "".join(out)
sys.stdout.write(norm(sys.argv[1]))
' "$dir/$memo_filled" >"$tmp_after.norm" 2>/dev/null || cp "$dir/$memo_filled" "$tmp_after.norm"
  d="$(diff -u "$tmp_before.norm" "$tmp_after.norm" 2>&1)"
  if [ -n "$d" ]; then
    diff_output="$diff_output
--- $dir/$memo_filled (committed vs. re-run, padding-normalized) ---
$d"
  fi
fi

if [ -n "$diff_output" ]; then
  echo "REPRO: verdict=fail"
  echo "$diff_output"
  exit 1
fi

echo "REPRO: verdict=pass"
exit 0
