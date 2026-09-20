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
# below is off by a doubled separator. `-P` because `git rev-parse
# --show-toplevel` reports the PHYSICAL path, and under $TMPDIR that is
# /private/var rather than /var; a logical $BASE would never match the run root.
BASE="$(cd "$BASE" && pwd -P)"

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

# The fixture root is what gets passed as --run-root, and preflight resolves that
# through `git rev-parse`, so it has to be a real checkout. GIT_CONFIG_GLOBAL is
# pinned per AGENTS.md: without it this fixture inherits the developer's global
# config (a machine-wide core.hooksPath, say) and fails only on their laptop.
GIT_CONFIG_GLOBAL=/dev/null git -C "$ROOT" init -q

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

# ProgramArguments, in order: /bin/bash <wrapper> <prof> <probe> <result> <specs...>
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
  case "$a" in
    *consent-probe.sh)
      seen_probe=1
      # Keep the generated probe and wrapper: preflight deletes its scratch dir
      # on the way out, and case 7 runs the probe for real.
      cp "$a" "$CAPTURE/probe.sh" 2>/dev/null || true
      ;;
    *consent-launch.sh) cp "$a" "$CAPTURE/wrapper.sh" 2>/dev/null || true ;;
  esac
done <<EOF
$args
EOF
[ -n "$result" ] || exit 1

: >"$result"
case "${CONSENT_FAKE_MODE:-clean}" in
  silent)
    # A job that dies mid-resource: the self-checks and one `attempting` line
    # land, then nothing. This is what a stalled read leaves behind, and it is
    # what the timeout blocker has to name.
    echo "CONSENT stdin_closed: ok" >>"$result"
    echo "CONSENT no_controlling_tty: ok" >>"$result"
    first_spec="$(printf '%s\n' "$specs" | sed -n '2p')"
    echo "CONSENT attempting ${first_spec%%=*}: ${first_spec#*=}" >>"$result"
    exit 0
    ;;
  terminal)
    echo "CONSENT stdin_closed: FAIL" >>"$result"
    echo "CONSENT no_controlling_tty: FAIL" >>"$result"
    ;;
  nomarker)
    # Every read answered; only the completion marker missing. Nothing stalled,
    # so the blocker must not name a resource.
    echo "CONSENT stdin_closed: ok" >>"$result"
    echo "CONSENT no_controlling_tty: ok" >>"$result"
    while IFS= read -r spec; do
      [ -n "$spec" ] || continue
      echo "CONSENT attempting ${spec%%=*}: ${spec#*=}" >>"$result"
      echo "CONSENT resource ${spec%%=*}: ok ${spec#*=}" >>"$result"
    done <<EOF
$specs
EOF
    exit 0
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
  if [ "${CONSENT_FAKE_MODE:-clean}" = "gated" ] && [ "${key#protected_}" != "$key" ]; then
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

run_preflight() { # run_preflight <fake-mode>
  CONSENT_FAKE_MODE="$1" \
    CAPTURE="$CAPTURE" \
    PATH="$FIXTURE_PATH" \
    PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" \
    PREFLIGHT_TCC_LOCATIONS="$PROTECTED" \
    PREFLIGHT_CONSENT_TICKS=4 \
    bash "$SCRIPT" --source plan --base main --run-root "$ROOT" 2>&1
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
  assert_contains "clean host: probes the passed --run-root, not the plugin dir" \
    "PREFLIGHT RUN_ROOT: $ROOT" "$out1"
  assert_contains "clean host: probes the run root's git-common-dir too" \
    "CONSENT resource git_common_dir: ok $ROOT/.git" "$out1"

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
  # ProgramArguments[0] is the one variable that decides which process launchd
  # makes responsible for the job, so it must be /bin/bash — what the production
  # plist (scripts/orchestrator.plist.tmpl) runs — and not sandbox-exec.
  prog="$(printf '%s' "$plist" | sed -n 's/^ *<string>\(.*\)<\/string>$/\1/p' | tail -n +2 | head -3 | tr '\n' ' ')"
  case "$prog" in
    "/bin/bash "*consent-launch.sh*consent.sb*) ok "job's program is /bin/bash, as the production job's is" ;;
    *) bad "job's ProgramArguments does not lead with /bin/bash <wrapper> (got: $prog)" ;;
  esac
  wrapper="$(cat "$CAPTURE/wrapper.sh" 2>/dev/null)"
  case "$wrapper" in
    *'/usr/bin/sandbox-exec -f "$1" /bin/bash "$2"'*) ok "wrapper runs sandbox-exec on the rendered profile, as a child" ;;
    *) bad "wrapper does not run sandbox-exec -f <profile> (got: $wrapper)" ;;
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
  # "see the log" is not an answer at 3am: the blocker must name the resource it
  # stalled on and carry the same remedy the denial branch does.
  assert_contains "silent probe: names the resource it stalled on" \
    ", and stalled on run_root: $ROOT" "$out4"
  assert_contains "silent probe: carries the Full Disk Access remedy" \
    "System Settings → Privacy & Security → Full Disk Access" "$out4"

  # The other half of the same branch: every read answered, only the completion
  # marker missing. Still a blocker — the property is unproven — but nothing
  # stalled, so naming a resource would send the operator after a path this very
  # file records as `ok`.
  out4b="$(run_preflight nomarker)"
  rc4b=$?
  assert_exit "probe with no completion marker: exits 1" 1 "$rc4b"
  assert_contains "probe with no completion marker: still blocks" \
    "PREFLIGHT BLOCKER: consent-gate probe did not complete" "$out4b"
  # Match the DYNAMIC clause only. The blocker also carries a constant sentence
  # ("If it stalled on a protected path, …"), which is present either way, so a
  # bare "stalled on" would match prose rather than the claim under test.
  case "$out4b" in
    *", and stalled on "*) bad "no completion marker: names a resource that returned ok" ;;
    *) ok "no completion marker: claims no stalled resource" ;;
  esac
fi

# --- Case 5: no launchctl — a logged skip, not a blocker -------------------
out5="$(CONSENT_FAKE_MODE=clean CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" \
  PREFLIGHT_LAUNCHCTL="$BASE/no-such-launchctl" \
  PREFLIGHT_TCC_LOCATIONS="$PROTECTED" \
  bash "$SCRIPT" --source plan --base main --run-root "$ROOT" 2>&1)"
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
  bash "$SCRIPT" --source plan --base main --run-root "$ROOT" 2>&1)"
assert_contains "run outside every protected location: records none" \
  "PREFLIGHT CONSENT_PROTECTED: none" "$out6"

# --- Case 7: the generated probe script, executed for real -----------------
# Every case above asserts preflight's PARSING of a result file the fake wrote;
# none of them runs the probe, which is the load-bearing half. This one does —
# no launchd needed, so it runs everywhere.
probe="$CAPTURE/probe.sh"
if [ ! -s "$probe" ]; then
  skipped "probe script: not captured (the launchd cases did not run)"
else
  readable="$BASE/probe-readable"
  unreadable="$BASE/probe-unreadable"
  mkdir -p "$readable" "$unreadable"
  chmod 000 "$unreadable"
  presult="$BASE/probe-result"
  bash "$probe" "$presult" "run_root=$readable" "protected_1=$unreadable" </dev/null
  chmod 755 "$unreadable"
  pout="$(cat "$presult" 2>/dev/null)"
  assert_contains "probe: reports a readable path ok" \
    "CONSENT resource run_root: ok $readable" "$pout"
  assert_contains "probe: reports an unreadable path denied" \
    "CONSENT resource protected_1: denied $unreadable" "$pout"
  assert_contains "probe: stdin-closed self-check passes on a closed stdin" \
    "CONSENT stdin_closed: ok" "$pout"
  assert_contains "probe: writes its completion marker last" "CONSENT done: 1" "$pout"
fi

# --- Case 8: a plist value carrying XML metacharacters stays well-formed ----
# An unescaped `&` or `<` in a path makes the plist malformed; launchctl then
# refuses the job and the whole check degrades to a logged skip while the
# verdict stays `go`. So the gate fails OPEN, which is what this guards.
if [ "$have_sandbox" = 0 ]; then
  skipped "plist escaping: requires sandbox-exec (macOS)"
elif ! command -v plutil >/dev/null 2>&1; then
  skipped "plist escaping: plutil not available"
else
  amp_protected="$BASE/a&b<c"
  amp_root="$amp_protected/fixture"
  mkdir -p "$amp_root"
  cp -R "$ROOT/scripts" "$ROOT/dev_docs" "$amp_root/"
  GIT_CONFIG_GLOBAL=/dev/null git -C "$amp_root" init -q
  # Every case shares $CAPTURE/plist, so clear it: otherwise a run that died
  # before bootstrapping would leave this asserting against a predecessor's
  # artifact, and `plutil -lint` alone would pass vacuously.
  rm -f "$CAPTURE/plist"
  CONSENT_FAKE_MODE=clean CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" \
    PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" \
    PREFLIGHT_TCC_LOCATIONS="$amp_protected" \
    PREFLIGHT_CONSENT_TICKS=4 \
    bash "$amp_root/scripts/preflight.sh" --source plan --base main --run-root "$amp_root" >/dev/null 2>&1
  if [ ! -s "$CAPTURE/plist" ]; then
    bad "plist escaping: no job was submitted, so nothing was tested"
  elif plutil -lint "$CAPTURE/plist" >/dev/null 2>&1; then
    ok "plist with & and < in its paths parses"
  else
    bad "plist with & and < in its paths is malformed: $(plutil -lint "$CAPTURE/plist" 2>&1)"
  fi
  assert_contains "plist escapes the metacharacters rather than dropping them" \
    "a&amp;b&lt;c" "$(cat "$CAPTURE/plist" 2>/dev/null)"
fi

# --- Case 9: a protected location with a space, and one with a glob ---------
# The PRODUCTION default list contains `$HOME/Library/Mobile Documents` — a path
# with a space — and every other case overrides it with a space-free fixture, so
# the colon-split loops' quoting is otherwise untested in the one configuration
# that actually ships. The `*` entry covers the other half: IFS makes the split
# happen on ':' only, but it does not stop the resulting words from being
# pathname-expanded.
if [ "$have_sandbox" = 0 ]; then
  skipped "space/glob in a protected location: requires sandbox-exec (macOS)"
else
  spaced="$BASE/with space"
  spaced_root="$spaced/fixture"
  mkdir -p "$spaced_root"
  cp -R "$ROOT/scripts" "$ROOT/dev_docs" "$spaced_root/"
  GIT_CONFIG_GLOBAL=/dev/null git -C "$spaced_root" init -q
  out9="$(CONSENT_FAKE_MODE=clean CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" \
    PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" \
    PREFLIGHT_TCC_LOCATIONS="$spaced" \
    PREFLIGHT_CONSENT_TICKS=4 \
    bash "$spaced_root/scripts/preflight.sh" --source plan --base main --run-root "$spaced_root" 2>&1)"
  assert_contains "space in a protected location survives the split intact" \
    "PREFLIGHT CONSENT_PROTECTED: $spaced" "$out9"
  assert_contains "space-containing location reaches the probe as one spec" \
    "CONSENT resource protected_1: ok $spaced" "$out9"

  # The glob fixture is built so expansion CHANGES the answer: `d[x]` as a
  # bracket expression matches the decoy `dx` and not its own literal name, so
  # an expanded $loc no longer contains the run root and the location drops out
  # of the inventory. Without `set -f` this reports `none`.
  globbed="$BASE/d[x]"
  globbed_root="$globbed/fixture"
  mkdir -p "$globbed_root" "$BASE/dx"
  cp -R "$ROOT/scripts" "$ROOT/dev_docs" "$globbed_root/"
  GIT_CONFIG_GLOBAL=/dev/null git -C "$globbed_root" init -q
  out9b="$(CONSENT_FAKE_MODE=clean CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" \
    PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" \
    PREFLIGHT_TCC_LOCATIONS="$globbed" \
    PREFLIGHT_CONSENT_TICKS=4 \
    bash "$globbed_root/scripts/preflight.sh" --source plan --base main --run-root "$globbed_root" 2>&1)"
  assert_contains "glob metacharacter in a protected location is not expanded" \
    "PREFLIGHT CONSENT_PROTECTED: $globbed" "$out9b"
fi

echo
echo "test-preflight-consent: $pass_count passed, $fail_count failed, $skip_count skipped"
[ "$fail" -eq 0 ] || exit 1
echo "test-preflight-consent: OK"
