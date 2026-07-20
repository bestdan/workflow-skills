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
# Safety / exit contract:
#   - DRY RUN by default. An unresolved key/team in dry-run SKIPS and exits 0
#     (loud WARNING outside CI, quiet in CI) — the full-account key must never
#     live in CI secrets (see commands/handlers/linear-config.md "Archive key").
#   - An --apply run with no resolvable key/team FAILS (exit 1). An unattended
#     apply cron must alert, not record a false-green run while the active-issue
#     cap keeps growing. The soft exit-0 skip is a dry-run affordance only.
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
    --older-than)
      shift
      OLDER_THAN="${1:-3}"
      ;;
    --older-than=*) OLDER_THAN="${1#*=}" ;;
    --no-canceled) INCLUDE_CANCELED="" ;;
    --include-canceled) INCLUDE_CANCELED="1" ;;
    -h | --help)
      sed -n '2,32p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
  shift
done

# Strip a trailing 'd' so `--older-than 7d` and `--older-than 7` both work
# (linear-archive.py wants a bare integer).
OLDER_THAN="${OLDER_THAN%d}"

# Fail an --apply run loudly; skip a dry run softly. Centralizes the exit
# contract so a missing key and a missing team behave identically (C4/C5): an
# unattended apply cron must alert instead of recording a false-green no-op.
give_up() {
  local what="$1"
  shift
  if [ -n "$APPLY" ]; then
    echo "ERROR: nightly-linear-tidy --apply cannot proceed — $what." >&2
    printf '%s\n' "$@" >&2
    exit 1
  fi
  if [ -n "${CI:-}" ]; then
    echo "nightly-linear-tidy: SKIP — $what (dry-run; expected in CI)"
  else
    echo "WARNING: nightly-linear-tidy DID NOT RUN — $what (dry-run)." >&2
    printf '%s\n' "$@" >&2
  fi
  exit 0
}

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
      kill "$pid" 2>/dev/null
      sleep 0.2
      kill -9 "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      rm -f "$tmp"
      return 1
    fi
    sleep 0.1
  done
  if wait "$pid"; then
    cat "$tmp"
    rm -f "$tmp"
    return 0
  fi
  rm -f "$tmp"
  return 1
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
      REF="${REF%\"}"
      REF="${REF#\"}"
      REF="${REF%\'}"
      REF="${REF#\'}"
      [ -n "$REF" ] && REF_SRC="linear.api_key_ref (${cfg##*/})"
    fi
  done
  [ -n "$REF" ] && KEY="$(op_read_bounded "$REF" || true)"
fi

if [ -z "$KEY" ]; then
  if [ -n "$REF_SRC" ]; then
    give_up "$REF_SRC is set but 'op read' could not resolve it non-interactively" \
      "Sign in op in this shell, set \$OP_SERVICE_ACCOUNT_TOKEN, or export \$LINEAR_API_KEY directly."
  else
    give_up "no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config"
  fi
fi
export LINEAR_API_KEY="$KEY"

# --- resolve the team ($LINEAR_TEAM, else linear.team from config) ------------
TEAM="${LINEAR_TEAM:-}"
if [ -z "$TEAM" ] && [ -f "$CONFIG" ]; then
  TEAM="$(sed -n 's/^[[:space:]]*team:[[:space:]]*\([^#[:space:]]*\).*/\1/p' "$CONFIG" | head -1)"
  TEAM="${TEAM%\"}"
  TEAM="${TEAM#\"}"
  TEAM="${TEAM%\'}"
  TEAM="${TEAM#\'}"
fi
[ -z "$TEAM" ] && give_up "a key resolved but no team (\$LINEAR_TEAM unset, none in $CONFIG)"

# --- run ----------------------------------------------------------------------
CANCELED_FLAG=""
[ -n "$INCLUDE_CANCELED" ] && CANCELED_FLAG="--include-canceled"

MODE="dry-run"
[ -n "$APPLY" ] && MODE="APPLY"
echo "nightly-linear-tidy: archive team=$TEAM older-than=${OLDER_THAN}d${INCLUDE_CANCELED:+ +canceled} ($MODE)"
# shellcheck disable=SC2086
python3 "$SCRIPT" --team "$TEAM" --older-than "$OLDER_THAN" $CANCELED_FLAG $APPLY
