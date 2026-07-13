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

while IFS=$'\t' read -r skill prompt_file max_turns; do
  [[ -z "${skill// /}" || "$skill" == \#* ]] && continue
  # optional skill filter from argv
  if [[ -n "${1:-}" ]] && ! printf '%s\n' "$@" | grep -qx "$skill"; then
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

  if grep -q '"name":"Skill"' "$log" \
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
if [[ $fail -ne 0 ]]; then
  echo "failed: ${failed[*]}" >&2
  exit 1
fi
