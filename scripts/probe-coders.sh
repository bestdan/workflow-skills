#!/usr/bin/env bash
# probe-coders.sh — deterministic availability probe for select-coder.
#
# Replaces the ad-hoc `command -v` / config-grep probes the select-coder
# skill would otherwise re-derive each time. Probes the coder CLI backends
# (codex, agy, devin) and emits a ready-to-merge `availability:` YAML block
# on stdout. It does NOT write any file — the caller merges the block into
# dev_docs/orchestrate-coders/.coders.yml.
#
# The `opus:` backend is intentionally emitted with an empty models list:
# the available Claude models come from the calling session's
# `availableModels`, which a shell script cannot see. The skill fills it in.
#
# Usage:
#   scripts/probe-coders.sh
#
# Exit status: always 0 (a missing coder is data, not an error).
# Network: `devin auth status` and `agy models` touch the network (both are
# cheap auth probes — no inference, no quota); everything else is local. Run
# unsandboxed if agy or devin is installed.

set -u

probed_at=$(date +%Y-%m-%d)

codex_installed=false
codex_model=unknown
if command -v codex >/dev/null 2>&1; then
  codex_installed=true
  m=$(codex config get model 2>/dev/null)
  if [ -z "$m" ] && [ -f "$HOME/.codex/config.toml" ]; then
    m=$(sed -n 's/^model[[:space:]]*=[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$HOME/.codex/config.toml" | head -1)
  fi
  [ -n "$m" ] && codex_model=$m
fi

agy_installed=false
agy_logged_in=false
if command -v agy >/dev/null 2>&1; then
  agy_installed=true
  # `agy models` is a free metadata list (no inference, ~1s) that returns
  # non-zero with "Please sign in" when the token is dead — so it doubles as
  # a quota-free auth probe. Over SSH agy uses a file-based token store that
  # GUI logins don't refresh, so presence alone does NOT imply usable auth.
  # (Same probe co-review's pre-flight uses — keep the two in sync.)
  agy models >/dev/null 2>&1 && agy_logged_in=true
fi

devin_installed=false
devin_logged_in=false
devin_tier=unknown
if command -v devin >/dev/null 2>&1; then
  devin_installed=true
  auth=$(devin auth status 2>/dev/null)
  case "$auth" in
    *"Logged in"*) devin_logged_in=true ;;
  esac
  # Tier is not printed directly; infer from model gating if reported.
  case "$auth" in
    *[Ff]ree*) devin_tier=free ;;
    *[Pp]ro* | *[Tt]eam* | *[Mm]ax*) devin_tier=pro ;;
  esac
fi

cat <<EOF
availability:
  probed_at: $probed_at
  opus:
    models: [] # filled by the skill from the session's availableModels
  codex:
    installed: $codex_installed
    default_model: $codex_model
  agy:
    installed: $agy_installed
    logged_in: $agy_logged_in # false when the token is dead (e.g. stale file-store over SSH)
  devin:
    installed: $devin_installed
    logged_in: $devin_logged_in
    tier: $devin_tier # unknown → verify with a cheap swe-1.6 call; /upgrade error → free
EOF
