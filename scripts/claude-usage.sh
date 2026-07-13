#!/usr/bin/env bash
# claude-usage.sh — structured read of Claude Code rate-window usage, for the
# auto-pilot run-budget check (skills/auto-pilot/references/run-budget.md
# "Rate-window check"). It queries the same OAuth usage endpoint the in-app
# /usage view uses and emits the session (5-hour) window's consumed-percent and
# reset time as compact JSON — the first-best rate-window signal an orchestrator
# compares against its near-cap threshold.
#
# FAILS CLOSED. Any error — no token, unreachable network, unexpected response
# shape — exits non-zero so the caller falls back to the conservative
# time/dispatch proxy rather than proceeding blind. It never prints the OAuth
# token or the Authorization header: the token is passed to curl via a `-K -`
# stdin config so it never lands in argv (and thus never in `ps`) or in output.
#
# `percent` is percent CONSUMED (rises toward 100 as the window is spent), so
# real headroom is `100 - percent` and "near cap" fires as it approaches the
# threshold — do not invert the comparison.
#
# Usage:
#   scripts/claude-usage.sh                 # compact JSON of session/weekly/spend
#   scripts/claude-usage.sh --session-percent   # just the integer percent-consumed
#   scripts/claude-usage.sh --from-file <f>     # parse a saved response (no net/keychain)
#   scripts/claude-usage.sh --help
#
# Output (default), one JSON line on stdout:
#   {"session":{"percent":42,"resets_at":"2026-07-10T05:00:00Z"},
#    "weekly_all":{"percent":18,"resets_at":"..."},
#    "spend_used_minor":0}
#
# Exit status:
#   0  usage read OK; JSON (or the bare percent) on stdout.
#   1  usage UNAVAILABLE (no token, network failure, or unexpected shape) —
#      the caller falls back to the proxy. A one-line reason goes to stderr.
#   2  usage error (missing dependency, bad arguments).

set -uo pipefail

readonly ENDPOINT="https://api.anthropic.com/api/oauth/usage"
readonly OAUTH_BETA="anthropic-beta: oauth-2025-04-20"
readonly KEYCHAIN_SERVICE="Claude Code-credentials"
readonly LINUX_CREDS="${HOME}/.claude/.credentials.json"

die_unavail() {
  echo "claude-usage: $*" >&2
  exit 1
}
die_err() {
  echo "claude-usage: $*" >&2
  exit 2
}

MODE="json"
FROM_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --session-percent) MODE="percent" ;;
    --from-file)
      shift
      FROM_FILE="${1:-}"
      [ -n "$FROM_FILE" ] || die_err "--from-file needs a path"
      ;;
    --help | -h)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die_err "unknown argument: $1" ;;
  esac
  shift
done

command -v python3 >/dev/null 2>&1 || die_err "python3 is required but not found in PATH"

# --- Resolve the raw usage JSON -------------------------------------------
# Either a saved response (--from-file, for tests/debugging) or a live query.
if [ -n "$FROM_FILE" ]; then
  [ -r "$FROM_FILE" ] || die_unavail "cannot read --from-file: $FROM_FILE"
  usage_json="$(cat "$FROM_FILE")"
else
  command -v curl >/dev/null 2>&1 || die_err "curl is required but not found in PATH"

  # Resolve the OAuth access token, OS-appropriately, without ever echoing it
  # to a log. Prefer the macOS Keychain, but fall through to the Linux-style
  # credentials file when the Keychain is absent *or* the lookup fails — a
  # macOS box may keep the token in the file, so a failed Keychain read must
  # not short-circuit that fallback.
  creds_raw=""
  if command -v security >/dev/null 2>&1; then
    creds_raw="$(security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null)" || creds_raw=""
  fi
  if [ -z "$creds_raw" ] && [ -r "$LINUX_CREDS" ]; then
    creds_raw="$(cat "$LINUX_CREDS")"
  fi
  [ -n "$creds_raw" ] \
    || die_unavail "no OAuth token source (Keychain lookup failed and no readable ${LINUX_CREDS})"

  token="$(printf '%s' "$creds_raw" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["claudeAiOauth"]["accessToken"])' 2>/dev/null)" \
    || die_unavail "credentials present but no claudeAiOauth.accessToken field"
  [ -n "$token" ] || die_unavail "empty OAuth access token"

  # Pass the auth header via a curl `-K -` stdin config so the token stays out
  # of argv (and `ps`). The URL is not secret and rides on the command line.
  usage_json="$(printf 'header = "Authorization: Bearer %s"\nheader = "%s"\n' "$token" "$OAUTH_BETA" \
    | curl -sf --max-time 10 -K - "$ENDPOINT" 2>/dev/null)" \
    || die_unavail "usage query failed (network, auth, or non-2xx)"
  unset token creds_raw
fi

[ -n "$usage_json" ] || die_unavail "empty usage response"

# --- Parse + emit ----------------------------------------------------------
# A missing session window is fail-closed: the orchestrator needs the 5h read.
printf '%s' "$usage_json" | MODE="$MODE" python3 -c '
import json, os, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.stderr.write("claude-usage: response was not valid JSON\n"); sys.exit(1)

limits = {l.get("kind"): l for l in data.get("limits", []) if isinstance(l, dict)}
session = limits.get("session")
if not session or "percent" not in session:
    sys.stderr.write("claude-usage: no session window in usage response\n"); sys.exit(1)

try:
    session_pct = int(round(float(session["percent"])))
except (TypeError, ValueError):
    sys.stderr.write("claude-usage: session percent is not numeric\n"); sys.exit(1)

if os.environ.get("MODE") == "percent":
    print(session_pct); sys.exit(0)

def window(entry):
    if not entry:
        return None
    return {"percent": entry.get("percent"), "resets_at": entry.get("resets_at")}

out = {
    "session": {"percent": session_pct, "resets_at": session.get("resets_at")},
    "weekly_all": window(limits.get("weekly_all")),
    "spend_used_minor": ((data.get("spend") or {}).get("used") or {}).get("amount_minor", 0),
}
print(json.dumps(out, separators=(",", ":")))
'
# `set -o pipefail` propagates the python parser's exit status (1 on any
# unexpected shape — printed a specific reason above), so a parse failure
# fails the script closed with no extra message needed.
