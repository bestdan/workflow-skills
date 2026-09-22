#!/usr/bin/env bash
# Behavioral skill-triggering eval harness (opt-in, non-blocking).
#
# For each row in evals/manifest.tsv, run the naive prompt through the claude
# CLI headless and assert Claude auto-invoked the expected Skill. Adapted from
# obra/superpowers' tests/skill-triggering. This checks *routing* (did the right
# skill fire), not output quality — see evals/README.md for the LLM-judge
# extension point.
#
# Costs API tokens and is nondeterministic — that's why it's flag-gated
# (`scripts/check.sh --with-evals` / `just eval`) and never a blocking PR check.
# Needs an authenticated claude CLI: in CI via the ANTHROPIC_API_KEY secret;
# locally a logged-in CLI (OAuth) works too.
#
# Usage: scripts/eval.sh [skill ...]   # default: every row in the manifest
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

MANIFEST="evals/manifest.tsv"
MAX_TURNS_DEFAULT=6

command -v claude >/dev/null || {
  echo "✘ claude CLI not found on PATH" >&2
  exit 2
}
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "note: ANTHROPIC_API_KEY not set — relying on a logged-in claude CLI." >&2
fi

# `timeout` is GNU coreutils — present on Linux/CI, but not on a stock macOS
# (where it's `gtimeout` via `brew install coreutils`, if at all). Find whichever
# exists; if neither does, run without a wall-clock cap rather than failing every
# row with "command not found".
TIMEOUT="$(command -v timeout || command -v gtimeout || true)"
if [[ -z "$TIMEOUT" ]]; then
  echo "note: no timeout/gtimeout on PATH — running without a per-eval time cap." >&2
fi

pass=0
fail=0
failed=()
dirtied=()
dirty_count=0

# Each case runs against a writable checkout with permissions skipped, so a
# realistic prompt can write into the repo under test — and one did: the
# review-facts case wrote a report under skills/analysis-pipeline/example/ and
# staged it, while the suite reported the row as a pass. Detect that and fail
# the row, so residue is never folded silently into whatever is in flight.
#
# Compared per case against a rolling baseline rather than against "clean".
# The suite is routinely run from a worktree with work already in progress, so
# only the delta a case introduces is the harness's doing; blaming a row for
# pre-existing edits would make the check useless exactly where it is needed.
# A ROOT that is not a git checkout yields empty on both sides, so this
# degrades to a no-op rather than failing every row.
#
# Two signals, because `git status --porcelain` reports status codes and paths
# but not content. A case that edits a file already showing as ` M` leaves the
# porcelain line byte-identical, and that is the likeliest miss here: the
# rolling baseline exists to support running with work in flight, which is
# exactly when paths are already dirty. The content hash catches it.
#
# Known limit: rewriting a file that was already untracked moves neither
# signal, since `diff HEAD` does not cover untracked content. New untracked
# files — the case actually observed — do show up in the porcelain list.
tree_paths() {
  git -C "$ROOT" status --porcelain 2>/dev/null
}
tree_hash() {
  git -C "$ROOT" diff HEAD 2>/dev/null | shasum 2>/dev/null
}
baseline_paths="$(tree_paths)"
baseline_hash="$(tree_hash)"

while IFS=$'\t' read -r skill prompt_file max_turns; do
  [[ -z "${skill// /}" || "$skill" == \#* ]] && continue
  # optional skill filter from argv
  if [[ -n "${1:-}" ]] && ! grep -qx "$skill" <<<"$(printf '%s\n' "$@")"; then
    continue
  fi
  mt="${max_turns:-$MAX_TURNS_DEFAULT}"
  prompt="$(cat "evals/$prompt_file")"
  log="$(mktemp)"
  echo "→ ${skill}  (prompt: ${prompt_file}, max-turns: ${mt})"
  ${TIMEOUT:+"$TIMEOUT" 300} claude -p "$prompt" \
    --plugin-dir "$ROOT" \
    --dangerously-skip-permissions \
    --max-turns "$mt" \
    --output-format stream-json --verbose \
    >"$log" 2>&1 || true

  after_paths="$(tree_paths)"
  after_hash="$(tree_hash)"
  residue=""
  if [[ "$after_paths" != "$baseline_paths" || "$after_hash" != "$baseline_hash" ]]; then
    # Symmetric (comm -3), not just additions: a case that *reverts* an entry
    # has also written to the checkout, and reading that as clean would let it
    # delete work someone had in flight. comm prefixes its second column with a
    # tab, which the sed strips.
    residue="$(comm -3 <(printf '%s\n' "$baseline_paths" | sort -u) \
      <(printf '%s\n' "$after_paths" | sort -u) | sed -e 's/^\t//' -e '/^$/d')"
    if [[ -z "$residue" ]]; then
      residue="(content changed under a path that was already modified)"
    fi
    # Roll the baseline forward either way, so one dirtying case does not
    # convict every row after it.
    baseline_paths="$after_paths"
    baseline_hash="$after_hash"
  fi

  if [[ -n "$residue" ]]; then
    # Reported ahead of the routing verdict on purpose: a case that wrote into
    # the checkout has already broken the run, because a later case sees a tree
    # this one changed. Whether it also picked the right skill is beside that.
    echo "  ❌ FAIL — wrote into the repo under test:"
    printf '%s\n' "$residue" | sed 's/^/       /'
    fail=$((fail + 1))
    failed+=("$skill")
    dirtied+=("$skill")
    dirty_count=$((dirty_count + 1))
  elif grep -q '"name":"Skill"' "$log" \
    && grep -qE "\"(skill|name)\":\"([^\"]*:)?${skill}\"" "$log"; then
    echo "  ✅ PASS"
    pass=$((pass + 1))
  else
    invoked="$(grep -oE '"skill":"[^"]*"' "$log" | sort -u | tr '\n' ' ')"
    echo "  ❌ FAIL — skills invoked: ${invoked:-none}"
    fail=$((fail + 1))
    failed+=("$skill")
  fi
  rm -f "$log"
done <"$MANIFEST"

echo
echo "evals: ${pass} passed, ${fail} failed"
if [[ $dirty_count -ne 0 ]]; then
  echo "dirtied the repo under test: ${dirtied[*]}" >&2
  echo "those changes are still in the checkout — inspect and remove them before" >&2
  echo "committing, and treat any row after the first as an unreliable result." >&2
fi
if [[ $fail -ne 0 ]]; then
  echo "failed: ${failed[*]}" >&2
  exit 1
fi
