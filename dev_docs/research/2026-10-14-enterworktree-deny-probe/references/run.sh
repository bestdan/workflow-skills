#!/usr/bin/env bash
# Run one probe phase: run.sh <phase-name> <prompt> [extra claude args...]
# Output: <phase-name>.jsonl (stream-json transcript) next to this script.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
phase="$1"
prompt="$2"
shift 2
cd "$here/probe1001" || exit 1
timeout 600 claude -p "$prompt" \
  --max-turns 6 \
  --output-format stream-json --verbose \
  --allowedTools "EnterWorktree,ExitWorktree" \
  "$@" \
  </dev/null >"$here/$phase.jsonl" 2>"$here/$phase.stderr"
echo "exit=$?"
