#!/usr/bin/env bash
# test-preflight-consent.sh — fixture-based tests for scripts/preflight.sh's
# consent-gate probe (the launchd-attribution check).
#
# Everything runs against a FIXTURE root: preflight.sh is copied into a temp
# tree beside fake `probe-coders.sh`, `preflight-freshness.sh` and
# `spawn-orchestrator.sh`, and the fixture PATH holds fake `gh` and `claude`
# binaries. So no case here touches the network, the real repo, or the real
# launchd — the fake `launchctl` (PREFLIGHT_LAUNCHCTL) captures the job the
# probe submits and synthesizes the result the probe would have read back.
#
# The two properties the probe exists to hold:
#   - it runs the entry path AS LAUNCHD WILL — bootstrapped into the user's
#     launchd domain (so: detached, no controlling TTY), stdin on /dev/null,
#     exec'd through the rendered Seatbelt profile. Asserted against the
#     captured job, and asserted to be load-bearing: when the probe reports it
#     ran with a terminal attached, the pre-flight blocks.
#   - a resource that reads fine FROM THIS TERMINAL but is gated under launchd
#     attribution is a no-go, with a message naming the resolved binary path
#     and the Full Disk Access remedy.
#
# Host dependence: the consent probe only runs where `sandbox-exec` exists
# (TCC is macOS-only, and preflight.sh degrades to a logged skip elsewhere), so
# cases 1-4 skip on Linux and are covered by CI's macOS job. See
# dev_docs/testing.md.
#
# Run directly: bash scripts/test-preflight-consent.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BASE="$(mktemp -d "${TMPDIR:-/tmp}/test-preflight-consent.XXXXXX")"
[ -n "$BASE" ] && [ -d "$BASE" ] || {
  echo "test-preflight-consent: could not create a temp dir" >&2
  exit 2
}
trap 'rm -rf "$BASE"' EXIT
# $TMPDIR often carries a trailing slash, and preflight.sh normalizes its own
# ROOT through `cd … && pwd` — so normalize here too, or every path comparison
# below is off by a doubled separator.
BASE="$(cd "$BASE" && pwd)"

fail=0
pass_count=0
fail_count=0
skip_count=0

ok() {
  pass_count=$((pass_count + 1))
  echo "  ✔ $1"
}

bad() {
  fail_count=$((fail_count + 1))
  fail=1
  echo "  ✘ $1" >&2
}

skipped() {
  skip_count=$((skip_count + 1))
  echo "  – skip: $1"
}

assert_exit() {
  # assert_exit <description> <expected-rc> <actual-rc>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected exit $2, got $3)"
  fi
}

assert_contains() {
  # assert_contains <description> <needle> <haystack>
  case "$3" in
    *"$2"*) ok "$1" ;;
    *) bad "$1 (missing: $2)" ;;
  esac
}

# --- Fixture root ----------------------------------------------------------
# PROTECTED stands in for a TCC-protected location (~/Documents and friends);
# the fixture repo lives INSIDE it, which is what makes the run "need" it.
PROTECTED="$BASE/protected"
ROOT="$PROTECTED/fixture"
mkdir -p "$ROOT/scripts" "$ROOT/dev_docs/tasks"
cp "$REPO/scripts/preflight.sh" "$ROOT/scripts/preflight.sh"
SCRIPT="$ROOT/scripts/preflight.sh"

cat >"$ROOT/scripts/preflight-freshness.sh" <<'FRESH'
#!/bin/bash
echo "FRESHNESS: fresh refs=main"
exit 0
FRESH

cat >"$ROOT/scripts/probe-coders.sh" <<'CODERS'
#!/bin/bash
echo "coders:"
echo "  codex:"
echo "    installed: false"
echo "  agy:"
echo "    installed: false"
echo "  devin:"
echo "    installed: false"
exit 0
CODERS

# Fake spawn-orchestrator: render-profile emits a REAL, minimal Seatbelt
# profile (so the confinement smoke above the consent section still passes on a
# macOS host rather than adding blockers that muddy these assertions), and
# render-settings emits the shape the smoke greps for.
cat >"$ROOT/scripts/spawn-orchestrator.sh" <<'SPAWN'
#!/bin/bash
sub="$1"
shift
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out)
      out="$2"
      shift 2
      ;;
    *) shift ;;
  esac
done
[ -n "$out" ] || exit 2
case "$sub" in
  render-profile)
    printf '(version 1)\n(allow default)\n(deny file-write* (subpath "%s"))\n' "$HOME" >"$out"
    ;;
  render-settings)
    printf '{"allowedDomains":[],"enabled":true}\n' >"$out"
    ;;
  *) exit 2 ;;
esac
exit 0
SPAWN

printf 'handler: gh-issue\n' >"$ROOT/dev_docs/tasks/.task-config.yml"
chmod +x "$ROOT/scripts"/*.sh

# --- Fixture PATH ----------------------------------------------------------
# `claude` is a SYMLINK to a version-named bare executable, exactly as the real
# install is — the resolved target is what the blocker message must name.
FIXBIN="$BASE/bin"
FIXTURE_PATH="$FIXBIN:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$FIXBIN" "$BASE/versions"
printf '#!/bin/sh\nexit 0\n' >"$BASE/versions/9.9.9"
chmod +x "$BASE/versions/9.9.9"
ln -s "$BASE/versions/9.9.9" "$FIXBIN/claude"
# preflight.sh reports the symlink's fully-resolved target, so the expected
# value is the physical path (under $TMPDIR that means /private/var, not /var).
RESOLVED_CLAUDE="$(cd "$BASE/versions" && pwd -P)/9.9.9"

cat >"$FIXBIN/gh" <<'GH'
#!/bin/bash
case "$*" in
  "auth status") exit 0 ;;
  *viewerPermission*) echo "ADMIN" ;;
esac
exit 0
GH
chmod +x "$FIXBIN/gh"

# --- Fake launchctl --------------------------------------------------------
# Captures the submitted job (domain + plist) for inspection, then writes the
# result file the probe polls for. $CONSENT_FAKE_MODE picks what it reports.
CAPTURE="$BASE/capture"
mkdir -p "$CAPTURE"
cat >"$FIXBIN/fake-launchctl" <<'LAUNCHCTL'
#!/bin/bash
# Fake launchctl. bootstrap: record the job, synthesize the probe's result.
[ "$1" = "bootstrap" ] || exit 0
echo "$2" >"$CAPTURE/domain"
cp "$3" "$CAPTURE/plist"

# ProgramArguments, in order: sandbox-exec -f <prof> /bin/bash <probe> <result> <specs...>
# Read only what's inside <array>, or the trailing StandardInPath/log strings
# would be mistaken for probe specs.
args="$(awk '/<array>/{a=1;next} /<\/array>/{a=0} a' "$3" | sed -n 's/^ *<string>\(.*\)<\/string>$/\1/p')"
result=""
specs=""
seen_probe=0
while IFS= read -r a; do
  if [ "$seen_probe" = 2 ]; then
    specs="$specs
$a"
  elif [ "$seen_probe" = 1 ]; then
    result="$a"
    seen_probe=2
  fi
  case "$a" in *consent-probe.sh) seen_probe=1 ;; esac
done <<EOF
$args
EOF
[ -n "$result" ] || exit 1

: >"$result"
case "${CONSENT_FAKE_MODE:-clean}" in
  silent) exit 0 ;; # job never reports back
  terminal)
    echo "CONSENT stdin_closed: FAIL" >>"$result"
    echo "CONSENT no_controlling_tty: FAIL" >>"$result"
    ;;
  *)
    echo "CONSENT stdin_closed: ok" >>"$result"
    echo "CONSENT no_controlling_tty: ok" >>"$result"
    ;;
esac
while IFS= read -r spec; do
  [ -n "$spec" ] || continue
  key="${spec%%=*}"
  path="${spec#*=}"
  if [ "${CONSENT_FAKE_MODE:-clean}" = "gated" ] && [ "$key" != "run_root" ]; then
    echo "CONSENT resource $key: denied $path — Operation not permitted" >>"$result"
  else
    echo "CONSENT resource $key: ok $path" >>"$result"
  fi
done <<EOF
$specs
EOF
echo "CONSENT done: 1" >>"$result"
exit 0
LAUNCHCTL
chmod +x "$FIXBIN/fake-launchctl"

run_preflight() { # run_preflight <fake-mode> [extra env assignments...]
  CONSENT_FAKE_MODE="$1" \
    CAPTURE="$CAPTURE" \
    PATH="$FIXTURE_PATH" \
    PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" \
    PREFLIGHT_TCC_LOCATIONS="$PROTECTED" \
    PREFLIGHT_CONSENT_TICKS=4 \
    bash "$SCRIPT" --source plan --base main 2>&1
}

have_sandbox=0
command -v sandbox-exec >/dev/null 2>&1 && have_sandbox=1

# --- Case 1: clean host — the probe passes, and the submitted job is right --
if [ "$have_sandbox" = 0 ]; then
  skipped "clean host: requires sandbox-exec (macOS)"
  skipped "submitted job runs through launchd, stdin closed, via sandbox-exec"
else
  out1="$(run_preflight clean)"
  rc1=$?
  assert_exit "clean host: exits 0" 0 "$rc1"
  assert_contains "clean host: CONSENT_GATE is pass" "PREFLIGHT CONSENT_GATE: pass" "$out1"
  assert_contains "clean host: verdict is go" "PREFLIGHT VERDICT: go" "$out1"
  assert_contains "clean host: names the needed protected location" \
    "PREFLIGHT CONSENT_PROTECTED: $PROTECTED" "$out1"
  assert_contains "clean host: fingerprint records the RESOLVED binary" \
    "PREFLIGHT ATTRIBUTION_BIN: $RESOLVED_CLAUDE" "$out1"

  # The job itself: launchd domain (hence detached, no controlling TTY),
  # stdin on /dev/null, and exec'd through the rendered Seatbelt profile.
  domain="$(cat "$CAPTURE/domain" 2>/dev/null)"
  plist="$(cat "$CAPTURE/plist" 2>/dev/null)"
  assert_contains "job is bootstrapped into the user's launchd domain" \
    "gui/$(id -u)" "$domain"
  plist_flat="$(printf '%s' "$plist" | tr -d ' \n')"
  if grep -q '<key>StandardInPath</key><string>/dev/null</string>' <<<"$plist_flat"; then
    ok "job closes stdin (StandardInPath /dev/null)"
  else
    bad "job does not set StandardInPath to /dev/null"
  fi
  prog="$(printf '%s' "$plist" | sed -n 's/^ *<string>\(.*\)<\/string>$/\1/p' | tail -n +2 | head -3 | tr '\n' ' ')"
  case "$prog" in
    "/usr/bin/sandbox-exec -f "*consent.sb*) ok "job execs through the rendered Seatbelt profile" ;;
    *) bad "job does not exec through sandbox-exec -f <profile> (got: $prog)" ;;
  esac
  if grep -q '<key>RunAtLoad</key><true/>' <<<"$plist_flat"; then
    ok "job runs at load"
  else
    bad "job does not set RunAtLoad"
  fi
fi

# --- Case 2: gated under launchd, readable from this terminal --------------
if [ "$have_sandbox" = 0 ]; then
  skipped "consent gate: requires sandbox-exec (macOS)"
else
  # The asymmetry this whole probe exists for: the terminal can read it.
  if ls -- "$PROTECTED" >/dev/null 2>&1; then
    ok "gated resource reads fine from this terminal"
  else
    bad "fixture protected dir is unreadable from the terminal — test is not measuring the asymmetry"
  fi
  out2="$(run_preflight gated)"
  rc2=$?
  assert_exit "consent gate: exits 1" 1 "$rc2"
  assert_contains "consent gate: CONSENT_GATE reports the gate" \
    "PREFLIGHT CONSENT_GATE: FAIL (interactive consent gate)" "$out2"
  blocker2="$(printf '%s\n' "$out2" | grep '^PREFLIGHT BLOCKER: interactive consent gate')"
  assert_contains "consent gate: blocker names the gated path" "$PROTECTED" "$blocker2"
  assert_contains "consent gate: blocker names the RESOLVED binary path" \
    "$RESOLVED_CLAUDE" "$blocker2"
  assert_contains "consent gate: blocker names the Full Disk Access remedy" \
    "System Settings → Privacy & Security → Full Disk Access" "$blocker2"
  assert_contains "consent gate: blocker warns the dialog shows only a version number" \
    "'9.9.9'" "$blocker2"
fi

# --- Case 3: the no-TTY/stdin self-check is load-bearing -------------------
if [ "$have_sandbox" = 0 ]; then
  skipped "terminal-attached probe: requires sandbox-exec (macOS)"
else
  out3="$(run_preflight terminal)"
  rc3=$?
  assert_exit "terminal-attached probe: exits 1" 1 "$rc3"
  assert_contains "terminal-attached probe: blocks with the reason" \
    "PREFLIGHT BLOCKER: consent-gate probe ran with a terminal attached" "$out3"
fi

# --- Case 4: a probe that never reports back fails closed ------------------
if [ "$have_sandbox" = 0 ]; then
  skipped "silent probe: requires sandbox-exec (macOS)"
else
  out4="$(run_preflight silent)"
  rc4=$?
  assert_exit "silent probe: exits 1" 1 "$rc4"
  assert_contains "silent probe: blocks as unproven" \
    "PREFLIGHT BLOCKER: consent-gate probe did not complete" "$out4"
fi

# --- Case 5: no launchctl — a logged skip, not a blocker -------------------
out5="$(CONSENT_FAKE_MODE=clean CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" \
  PREFLIGHT_LAUNCHCTL="$BASE/no-such-launchctl" \
  PREFLIGHT_TCC_LOCATIONS="$PROTECTED" \
  bash "$SCRIPT" --source plan --base main 2>&1)"
if [ "$have_sandbox" = 0 ]; then
  assert_contains "no sandbox-exec: skips rather than blocking" \
    "PREFLIGHT CONSENT_GATE: skip (sandbox-exec not available" "$out5"
else
  assert_contains "no launchctl: skips rather than blocking" \
    "PREFLIGHT CONSENT_GATE: skip (launchctl not available" "$out5"
fi
case "$out5" in
  *"BLOCKER: interactive consent gate"*) bad "no launchctl: must not synthesize a consent blocker" ;;
  *) ok "no launchctl: no consent blocker" ;;
esac

# --- Case 6: a run outside every protected location needs no grant ---------
mkdir -p "$BASE/elsewhere"
out6="$(CONSENT_FAKE_MODE=clean CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" \
  PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" \
  PREFLIGHT_TCC_LOCATIONS="$BASE/elsewhere" \
  PREFLIGHT_CONSENT_TICKS=4 \
  bash "$SCRIPT" --source plan --base main 2>&1)"
assert_contains "run outside every protected location: records none" \
  "PREFLIGHT CONSENT_PROTECTED: none" "$out6"

echo
echo "test-preflight-consent: $pass_count passed, $fail_count failed, $skip_count skipped"
[ "$fail" -eq 0 ] || exit 1
echo "test-preflight-consent: OK"
