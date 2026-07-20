#!/usr/bin/env bash
# Nightly Linear tidy — the GitHub-free archive step, as a standalone cron job.
#
# The full nightly pipeline (detect false closures -> sweep complete -> archive)
# is documented in dev_docs/nightly-tidy.md and runs as a scheduled agent
# session, because two of its three steps need GitHub PR merge-checks that, in
# the cloud sandbox, only the GitHub MCP can serve. This script covers the ONE
# step that needs no GitHub at all — archiving terminal-state issues past an age
# threshold — so it can also run as a plain key-only cron/GitHub Action to keep
# the workspace under Linear's free-plan 250 active-issue cap.
#
# It is a thin wrapper over commands/handlers/assets/linear-archive.py that:
#   - resolves a Linear personal API key the same opt-in way the test-*-live.sh
#     harnesses do ($LINEAR_API_KEY, else an op:// ref, else linear.api_key_ref),
#   - resolves the team the same way ($LINEAR_TEAM, else linear.team from config),
#   - defaults to a DRY RUN and only mutates with --apply,
#   - archives completed AND canceled issues older than N days (default 3).
#
# Safety: DRY RUN by default. With no resolvable key it SKIPS and exits 0 (loud
# WARNING outside CI, quiet in CI) — the full-account key must never live in CI
# secrets (see commands/handlers/linear-config.md "Archive key").
#
# Usage:
#   scripts/nightly-linear-tidy.sh                 # dry-run, > 3 days
#   scripts/nightly-linear-tidy.sh --apply         # archive them
#   scripts/nightly-linear-tidy.sh --older-than 7 --apply
#   scripts/nightly-linear-tidy.sh --no-canceled   # completed only
#   LINEAR_API_KEY=… LINEAR_TEAM=PreThink scripts/nightly-linear-tidy.sh --apply
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/commands/handlers/assets/linear-archive.py"
CONFIG="$ROOT/dev_docs/tasks/.task-config.yml"
LOCAL_CONFIG="$ROOT/dev_docs/tasks/.task-config.local.yml" # gitignored personal override

# --- args ---------------------------------------------------------------------
OLDER_THAN="${ARCHIVE_AFTER:-3}"
APPLY=""
INCLUDE_CANCELED="1" # completed + canceled by default (matches the routine)
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY="--apply" ;;
    --older-than) shift; OLDER_THAN="${1:-3}" ;;
    --older-than=*) OLDER_THAN="${1#*=}" ;;
    --no-canceled) INCLUDE_CANCELED="" ;;
    --include-canceled) INCLUDE_CANCELED="1" ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

# Strip a trailing 'd' so `--older-than 7d` and `--older-than 7` both work
# (linear-archive.py wants a bare integer).
OLDER_THAN="${OLDER_THAN%d}"

# --- resolve an op:// ref without hanging (same guard as test-linear-scan-live) -
# `op read` BLOCKS on a locked 1Password desktop session; bound it to ~6s so an
# unattended cron never wedges.
op_read_bounded() {
  command -v op >/dev/null 2>&1 || return 1
  local tmp
  tmp="$(mktemp)"
  op read "$1" >"$tmp" 2>/dev/null &
  local pid=$! i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
      kill "$pid" 2>/dev/null; sleep 0.2; kill -9 "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null; rm -f "$tmp"; return 1
    fi
    sleep 0.1
  done
  if wait "$pid"; then cat "$tmp"; rm -f "$tmp"; return 0; fi
  rm -f "$tmp"; return 1
}

# --- resolve a key (opt-in) ---------------------------------------------------
# Precedence: raw $LINEAR_API_KEY; else op:// ref in $LINEAR_API_KEY_REF; else
# linear.api_key_ref from the gitignored local override, then the committed config.
KEY="${LINEAR_API_KEY:-}"
REF_SRC=""
if [ -z "$KEY" ]; then
  REF="${LINEAR_API_KEY_REF:-}"
  [ -n "$REF" ] && REF_SRC="\$LINEAR_API_KEY_REF"
  for cfg in "$LOCAL_CONFIG" "$CONFIG"; do
    if [ -z "$REF" ] && [ -f "$cfg" ]; then
      REF="$(sed -n 's/^[[:space:]]*api_key_ref:[[:space:]]*\([^#[:space:]]*\).*/\1/p' "$cfg" | head -1)"
      REF="${REF%\"}"; REF="${REF#\"}"; REF="${REF%\'}"; REF="${REF#\'}"
      [ -n "$REF" ] && REF_SRC="linear.api_key_ref (${cfg##*/})"
    fi
  done
  [ -n "$REF" ] && KEY="$(op_read_bounded "$REF" || true)"
fi

if [ -z "$KEY" ]; then
  if [ -n "${CI:-}" ]; then
    echo "nightly-linear-tidy: SKIP — no key (expected in CI; keeps CI keyless)"
  elif [ -n "$REF_SRC" ]; then
    echo "WARNING: nightly-linear-tidy DID NOT RUN — $REF_SRC is set but 'op read' could not resolve it non-interactively." >&2
    echo "         Sign in op in this shell, set \$OP_SERVICE_ACCOUNT_TOKEN, or export \$LINEAR_API_KEY directly." >&2
  else
    echo "WARNING: nightly-linear-tidy DID NOT RUN — no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config." >&2
  fi
  exit 0
fi
export LINEAR_API_KEY="$KEY"

# --- resolve the team ($LINEAR_TEAM, else linear.team from config) ------------
TEAM="${LINEAR_TEAM:-}"
if [ -z "$TEAM" ] && [ -f "$CONFIG" ]; then
  TEAM="$(sed -n 's/^[[:space:]]*team:[[:space:]]*\([^#[:space:]]*\).*/\1/p' "$CONFIG" | head -1)"
  TEAM="${TEAM%\"}"; TEAM="${TEAM#\"}"; TEAM="${TEAM%\'}"; TEAM="${TEAM#\'}"
fi
if [ -z "$TEAM" ]; then
  echo "WARNING: nightly-linear-tidy has a key but no team (\$LINEAR_TEAM unset, none in $CONFIG) — skipping." >&2
  exit 0
fi

# --- run ----------------------------------------------------------------------
CANCELED_FLAG=""
[ -n "$INCLUDE_CANCELED" ] && CANCELED_FLAG="--include-canceled"

MODE="dry-run"; [ -n "$APPLY" ] && MODE="APPLY"
echo "nightly-linear-tidy: archive team=$TEAM older-than=${OLDER_THAN}d${INCLUDE_CANCELED:+ +canceled} ($MODE)"
# shellcheck disable=SC2086
python3 "$SCRIPT" --team "$TEAM" --older-than "$OLDER_THAN" $CANCELED_FLAG $APPLY
