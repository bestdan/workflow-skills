#!/usr/bin/env bash
# test-smoke-nono.sh — fixture tests for scripts/smoke-nono.sh's OS branches
# (#1361): macOS refuses to run without a token command, runs the launchd checks
# instead of the systemd ones, and bounds the token command without coreutils
# timeout. Offline: nono is a stub that refuses any command naming launchctl or
# open (as the profile's raw Seatbelt denies would) and runs everything else
# UNCONFINED, so only the lines asserted here mean anything — the write checks
# FAIL under it, by design. claude is a stub too. Real nono confinement is the
# smoke itself, run by pre-flight on a real host.
#
# Run directly: bash scripts/test-smoke-nono.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE="$REPO/scripts/smoke-nono.sh"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/test-smoke-nono.XXXXXX")" || exit 2
[ -n "$BASE" ] && [ -d "$BASE" ] || exit 2
trap 'rm -rf "$BASE"' EXIT
BASE="$(cd "$BASE" && pwd -P)"

pass=0
fail=0
ok() {
  pass=$((pass + 1))
  echo "ok   - $1"
}
bad() {
  fail=$((fail + 1))
  echo "FAIL - $1"
  [ -n "${2:-}" ] && echo "       $2"
  return 0
}
have() { if grep -qF -- "$2" <<<"$3"; then ok "$1"; else bad "$1" "missing: $2"; fi; }
lack() { if grep -qF -- "$2" <<<"$3"; then bad "$1" "unexpected: $2"; else ok "$1"; fi; }

mkdir -p "$BASE/bin" "$BASE/home"
cat >"$BASE/bin/nono" <<'STUB'
#!/bin/sh
if [ "$1" = profile ]; then
  echo "Result: valid"
  exit 0
fi
while [ $# -gt 0 ] && [ "$1" != -- ]; do shift; done
shift
case "$*" in
  *launchctl* | */usr/bin/open*)
    echo "sh: Operation not permitted" >&2
    exit 126
    ;;
esac
exec "$@"
STUB
cat >"$BASE/bin/claude" <<STUB
#!/bin/sh
printf 'token=%s\n' "\${CLAUDE_CODE_OAUTH_TOKEN:-<none>}" >>"$BASE/claude.calls"
printf '{"is_error":false,"result":"ok"}\n'
STUB
chmod +x "$BASE/bin/nono" "$BASE/bin/claude"

smoke() { # <os> [args...]
  local os="$1"
  shift
  env HOME="$BASE/home" GIT_CONFIG_GLOBAL=/dev/null AUTO_PILOT_HOST_OS="$os" \
    bash "$SMOKE" --nono-bin "$BASE/bin/nono" --claude-bin "$BASE/bin/claude" "$@" 2>&1
}

# --- macOS: no token command, no smoke -----------------------------------------
o="$(smoke Darwin)"
rc=$?
if [ "$rc" = 1 ]; then ok "macOS: no token command is a FAIL exit"; else bad "macOS: no token command is a FAIL exit" "rc=$rc"; fi
have "macOS: no token command names why" "SMOKE RENDER: FAIL (macOS needs --claude-token-cmd" "$o"
lack "macOS: no token command runs no check" "SMOKE VALIDATE" "$o"

# --- macOS: the launchd checks replace the systemd ones ------------------------
: >"$BASE/claude.calls"
o="$(smoke Darwin --claude-token-cmd 'printf tok-mac')"
have "macOS: the profile renders" "SMOKE VALIDATE: pass" "$o"
have "macOS: launchctl refused" "SMOKE LAUNCHCTL_DIRECT: pass" "$o"
have "macOS: a staged launchctl submits no job" "SMOKE LAUNCHCTL_STAGED: pass (no job was submitted" "$o"
have "macOS: open refused" "SMOKE OPEN: pass" "$o"
have "macOS: a here-doc works in the jail" "SMOKE HEREDOC: pass" "$o"
lack "macOS: no systemd bus checks" "SMOKE BUS_" "$o"
have "macOS: claude answers on the token (the token command bounded by timeout, or perl where there is none)" "SMOKE CLAUDE: pass" "$o"
have "macOS: the token reaches the jailed claude" "token=tok-mac" "$(cat "$BASE/claude.calls")"
lack "macOS: main_sum works with this host's stat" "stat: illegal option" "$o"
lack "macOS: main_sum works with this host's stat (GNU wording)" "stat: invalid option" "$o"

# --- Linux: the systemd checks, no launchd ones --------------------------------
o="$(smoke Linux)"
have "linux: the profile renders without a token" "SMOKE VALIDATE: pass" "$o"
have "linux: the bus checks run (or skip without systemctl)" "SMOKE BUS_DIRECT:" "$o"
lack "linux: no launchd checks" "SMOKE LAUNCHCTL_" "$o"
lack "linux: no here-doc check" "SMOKE HEREDOC" "$o"

echo
echo "test-smoke-nono: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
