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
#   - <dir> has no README.md, or its README names no "uv run" command to
#     re-run
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

# Pull the deterministic re-run commands out of the README's own "How to
# Run" fence: every "uv run ..." line inside a ```sh/```bash block. This
# picks up model.py + fill_templates.py while skipping an optional
# narrative-fill step piped through an LLM CLI, with no need to know that
# step's name — it never matches "uv run ".
commands="$(awk '
  /^```(sh|bash)[[:space:]]*$/ { fence = 1; next }
  /^```/ { fence = 0; next }
  fence && /^[[:space:]]*uv run / { print }
' "$readme")"

if [ -z "$commands" ]; then
  echo "REPRO: verdict=n/a (no uv run command found in $readme)"
  exit 0
fi

tmp_before="$(mktemp "${TMPDIR:-/tmp}/check-repro.XXXXXX")"
tmp_after="$(mktemp "${TMPDIR:-/tmp}/check-repro.XXXXXX")"
run_output="$(mktemp "${TMPDIR:-/tmp}/check-repro.XXXXXX")"
cleanup() {
  rm -f "$tmp_before" "$tmp_before.norm" "$tmp_after" "$tmp_after.norm" "$run_output"
  (cd "$dir" && git checkout -- "$model_output" "$memo_filled") 2>/dev/null
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
# whitespace and separator dashes on both sides first, the same way the JSON
# diff above normalizes key order — real content differs char-for-char
# either way, only padding is insensitive to this.
if [ -f "$dir/$memo_filled" ] && (cd "$dir" && git show "HEAD:./$memo_filled") >"$tmp_before" 2>/dev/null; then
  python3 -c '
import re, sys
def norm(path):
    text = open(path).read()
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"-{3,}", "---", text)
    return text
sys.stdout.write(norm(sys.argv[1]))
' "$tmp_before" >"$tmp_before.norm" 2>/dev/null || cp "$tmp_before" "$tmp_before.norm"
  python3 -c '
import re, sys
def norm(path):
    text = open(path).read()
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"-{3,}", "---", text)
    return text
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
