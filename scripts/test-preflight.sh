#!/usr/bin/env bash
# Test harness for scripts/preflight.sh.
#
# Self-contained: stubs the coder probe (scripts/probe-coders.sh) via the
# PREFLIGHT_PROBE_CODERS env override so the logged-out-required-coder case is
# deterministic and offline. Does not stub gh/git/freshness — those run for
# real (read-only), matching how the script behaves in normal use.
#
# Run directly: bash scripts/test-preflight.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/preflight.sh"

export TMPDIR="${TMPDIR:-$HOME/.cache/ap-test-preflight-tmp}"
mkdir -p "$TMPDIR"
BASE="$(mktemp -d "$TMPDIR/test-preflight.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT

pass=0
fail=0
ok()  { pass=$((pass + 1)); echo "ok   - $1"; }
bad() { fail=$((fail + 1)); echo "FAIL - $1"; [ -n "${2:-}" ] && echo "       $2"; return 0; }
have() { if grep -qF -- "$2" <<<"$3"; then ok "$1"; else bad "$1" "$3"; fi; }

# --- the VERDICT line is always emitted ---------------------------------------
out="$(bash "$SCRIPT" --source plan --base main 2>&1)"; code=$?
have "verdict line emitted" "PREFLIGHT VERDICT:" "$out"

# --- a logged-out REQUIRED coder yields non-zero with a NAMED blocker --------
fake_probe="$BASE/fake-probe-coders.sh"
cat >"$fake_probe" <<'EOF'
#!/usr/bin/env bash
cat <<'YAML'
availability:
  probed_at: 2026-01-01
  opus:
    models: []
  codex:
    installed: false
    default_model: unknown
  agy:
    installed: true
    logged_in: false
  devin:
    installed: false
    logged_in: false
    tier: unknown
YAML
EOF

lo_out="$(PREFLIGHT_PROBE_CODERS="$fake_probe" bash "$SCRIPT" --source plan --base main 2>&1)"; lo_code=$?
if [ "$lo_code" != 0 ]; then ok "logged-out required coder: non-zero exit"; else bad "logged-out required coder: non-zero exit" "$lo_out"; fi
have "logged-out required coder: names agy" "PREFLIGHT BLOCKER: coder 'agy'" "$lo_out"
have "logged-out required coder: verdict is no-go" "PREFLIGHT VERDICT: no-go" "$lo_out"

# --- a fully logged-in coder set never blocks on that axis --------------------
ok_probe="$BASE/ok-probe-coders.sh"
cat >"$ok_probe" <<'EOF'
#!/usr/bin/env bash
cat <<'YAML'
availability:
  probed_at: 2026-01-01
  opus:
    models: []
  codex:
    installed: true
    default_model: gpt-test
  agy:
    installed: true
    logged_in: true
  devin:
    installed: true
    logged_in: true
    tier: pro
YAML
EOF
ok_out="$(PREFLIGHT_PROBE_CODERS="$ok_probe" bash "$SCRIPT" --source plan --base main 2>&1)"
if grep -qF "PREFLIGHT BLOCKER: coder 'agy'" <<<"$ok_out" || grep -qF "PREFLIGHT BLOCKER: coder 'devin'" <<<"$ok_out"; then
  bad "logged-in coders: no coder blocker" "$ok_out"
else
  ok "logged-in coders: no coder blocker"
fi

# --- a crashed coder probe fails closed with a named blocker ------------------
crash_probe="$BASE/crash-probe-coders.sh"
cat >"$crash_probe" <<'EOF'
#!/usr/bin/env bash
echo boom >&2
exit 3
EOF
chmod +x "$crash_probe"
crash_out="$(PREFLIGHT_PROBE_CODERS="$crash_probe" bash "$SCRIPT" --source plan --base main 2>&1)"; crash_code=$?
if [ "$crash_code" != 0 ]; then ok "crashed coder probe: non-zero exit"; else bad "crashed coder probe: non-zero exit" "$crash_out"; fi
have "crashed coder probe: names the blocker" "PREFLIGHT BLOCKER: coder probe failed" "$crash_out"

# --- the emitted PATH/exec-dir lines are always present, absolute and existent -
count=0
missing=0
while IFS= read -r d; do
  count=$((count + 1))
  case "$d" in
    /*) [ -d "$d" ] || missing=1 ;;
    *) missing=1 ;;
  esac
done < <(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT PATH_DIR: //p')
[ "$count" -gt 0 ] && [ "$missing" = 0 ] && ok "PATH_DIR lines are emitted, absolute and existent" || bad "PATH_DIR lines are emitted, absolute and existent" "$out"

count=0
missing=0
while IFS= read -r d; do
  count=$((count + 1))
  case "$d" in
    /*) [ -d "$d" ] || missing=1 ;;
    *) missing=1 ;;
  esac
done < <(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT EXEC_DIR: //p')
[ "$count" -gt 0 ] && [ "$missing" = 0 ] && ok "EXEC_DIR lines are emitted, absolute and existent" || bad "EXEC_DIR lines are emitted, absolute and existent" "$out"

# --- DEST_HOST is a bare hostname, not the parenthesized handler tuple --------
dest_host_line="$(printf '%s\n' "$out" | sed -n 's/^PREFLIGHT DEST_HOST: //p')"
if [ -n "$dest_host_line" ] && [[ "$dest_host_line" != *" "* ]] && [[ "$dest_host_line" != *"(handler="* ]]; then
  ok "DEST_HOST is a bare hostname"
else
  bad "DEST_HOST is a bare hostname" "$dest_host_line"
fi

# --- usage: missing --source fails closed --------------------------------------
uo="$(bash "$SCRIPT" --base main 2>&1)"; uc=$?
[ "$uc" = 2 ] && printf '%s' "$uo" | grep -qF -- '--source' \
  && ok "usage: missing --source fails closed" || bad "usage: missing --source fails closed" "exit=$uc msg=$uo"

# --- the confinement smoke degrades to a logged SKIP, never a hard failure, only
# for the two legitimate environmental reasons (sandbox-exec absent, or nested
# apply denied) — a render-profile failure is a real defect and must NOT read as
# this skip ----------------------------------------------------------------------
if printf '%s\n' "$out" | grep -qE "PREFLIGHT SMOKE: skip \((sandbox-exec not available|nested sandbox-exec denied)"; then
  ok "confinement smoke: degrades to a logged SKIP only for legitimate environmental reasons"
else
  have "confinement smoke: ran a real check when it could" "PREFLIGHT SMOKE_EXEC: pass" "$out"
  have "confinement smoke: HOME-write check ran and passed" "PREFLIGHT SMOKE_HOME_WRITE: pass" "$out"
  have "confinement smoke: egress check ran and passed" "PREFLIGHT SMOKE_EGRESS: pass" "$out"
fi

echo "test-preflight: $pass passed, $fail failed"
[ "$fail" = 0 ]
