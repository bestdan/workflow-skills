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
#     run through the pinned nono under a rendered profile. Asserted against the
#     captured job, and asserted to be load-bearing: when the probe reports it
#     ran with a terminal attached, the pre-flight blocks.
#   - a resource that reads fine FROM THIS TERMINAL but is gated under launchd
#     attribution is a no-go, with a message naming the resolved binary path
#     and the Full Disk Access remedy.
#
# Host independence: the macOS cases pin PREFLIGHT_OS=Darwin and run against a
# stub nono (#1361: the macOS jail is nono), a fake launchctl and a fake
# renderer, so they run on Linux CI as well as on macOS.
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

assert_not_contains() {
  # assert_not_contains <description> <needle> <haystack>
  case "$3" in
    *"$2"*) bad "$1 (present: $2)" ;;
    *) ok "$1" ;;
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
export REAL_SPAWN="$REPO/scripts/spawn-orchestrator.sh"

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

# Fake spawn-orchestrator: render-nono-profile records its arguments (the
# consent probe's grants are asserted against them) and writes a stand-in
# profile; nothing here runs nono.
cat >"$ROOT/scripts/spawn-orchestrator.sh" <<'SPAWN'
#!/bin/bash
sub="$1"
shift
# The run-clone check is the real one: it only reads git.
[ "$sub" = check-run-clone ] && exec bash "$REAL_SPAWN" check-run-clone "$@"
[ "$sub" = render-nono-profile ] && printf '%s\n' "$@" >"$CAPTURE/render-args"
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
  render-nono-profile)
    [ "${FAKE_RENDER_FAIL:-}" = 1 ] && {
      echo "spawn-orchestrator: render failed (fixture)" >&2
      exit 2
    }
    printf '{"meta":{"name":"auto-pilot-orchestrator"}}\n' >"$out"
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

# --- The pinned nono, stubbed ------------------------------------------------
# The real nono-install.sh runs against a stub binary whose checksum the test
# pins, so each no-go is the installer's own text. The smoke is a stub that
# prints the real smoke's line shape and records its arguments. Both OSes use
# them: macOS (#1361) jails the run under nono too.
cp "$REPO/scripts/nono-install.sh" "$ROOT/scripts/nono-install.sh"
NH="$BASE/nono-home"
mkdir -p "$NH/0.81.0"
cat >"$NH/0.81.0/nono" <<'STUB'
#!/bin/sh
case "$1" in
  --version) echo "nono ${STUB_VERSION:-0.81.0}" ;;
  setup) echo "  * Landlock V${STUB_ABI:-8}" ;;
esac
exit 0
STUB
chmod 755 "$NH/0.81.0/nono"
if command -v sha256sum >/dev/null 2>&1; then
  NSUM="$(sha256sum "$NH/0.81.0/nono" | cut -d' ' -f1)"
else
  NSUM="$(shasum -a 256 "$NH/0.81.0/nono" | cut -d' ' -f1)"
fi
printf 'landlock\n' >"$BASE/lsm-ok"
printf 'yama\n' >"$BASE/lsm-none"
cat >"$FIXBIN/smoke-nono" <<'SMOKE'
#!/bin/bash
printf '%s\n' "$*" >"$SMOKE_ARGS"
echo "SMOKE WRITE_HOME: pass (a write to \$HOME is denied)"
echo "SMOKE CRED_READ: ${SMOKE_CRED:-pass} (the credential file)"
[ "${SMOKE_CRED:-pass}" = pass ]
SMOKE
chmod +x "$FIXBIN/smoke-nono"

# macOS: PREFLIGHT_OS pins the branch, so these cases run on every host — the
# launchd job is the fake's and the nono is the stub. Extra env first, then
# `--` and extra pre-flight args.
mac_preflight() { # <fake-mode> [VAR=value ...] [-- preflight args...]
  local mode="$1"
  shift
  local -a e=() a=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do
    e+=("$1")
    shift
  done
  [ "${1:-}" = -- ] && shift
  a=("$@")
  env CONSENT_FAKE_MODE="$mode" CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" PREFLIGHT_OS=Darwin \
    PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" PREFLIGHT_TCC_LOCATIONS="$PROTECTED" \
    PREFLIGHT_CONSENT_TICKS=4 \
    AUTO_PILOT_NONO_HOME="$NH" AUTO_PILOT_NONO_TARGET=aarch64-apple-darwin AUTO_PILOT_NONO_SHA256="$NSUM" \
    PREFLIGHT_NONO_SMOKE="$FIXBIN/smoke-nono" SMOKE_ARGS="$BASE/smoke-args" ${e[@]+"${e[@]}"} \
    bash "${PF_SCRIPT:-$SCRIPT}" --source plan --base main --run-root "${PF_RUN_ROOT:-$ROOT}" \
    --claude-token-cmd 'op read op://v/claude/token' ${a[@]+"${a[@]}"} 2>&1
}
run_preflight() { # run_preflight <fake-mode>
  mac_preflight "$1"
}

# A run worktree made by run-clone, inside the protected location, as a real
# run's is: pre-flight blocks on any other run worktree (both launchers).
CR="$PROTECTED/rc"
mkdir -p "$CR"
(
  export GIT_CONFIG_GLOBAL=/dev/null
  git init -q --bare "$CR/origin.git"
  git init -q -b main "$CR/main"
  git -C "$CR/main" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
  git -C "$CR/main" remote add origin "$CR/origin.git"
  git -C "$CR/main" push -q origin main 2>/dev/null
  bash "$REAL_SPAWN" run-clone --repo "$CR/main" --run-root "$CR/root" --run-id r >"$CR/clone.log" 2>&1
) || bad "run-clone fixture for the clean host builds ($(tail -1 "$CR/clone.log" 2>/dev/null))"
CRW="$CR/root/r"

# --- Case 1: clean host — the probe passes, and the submitted job is right --
: >"$BASE/smoke-args"
out1="$(PF_RUN_ROOT="$CRW" run_preflight clean)"
rc1=$?
assert_exit "clean host: exits 0" 0 "$rc1"
assert_contains "clean host: CONSENT_GATE is pass" "PREFLIGHT CONSENT_GATE: pass" "$out1"
assert_contains "clean host: verdict is go" "PREFLIGHT VERDICT: go" "$out1"
assert_contains "clean host: names the needed protected location" \
  "PREFLIGHT CONSENT_PROTECTED: $PROTECTED" "$out1"
assert_contains "clean host: fingerprint records the RESOLVED binary" \
  "PREFLIGHT ATTRIBUTION_BIN: $RESOLVED_CLAUDE" "$out1"
assert_contains "clean host: probes the passed --run-root, not the plugin dir" \
  "PREFLIGHT RUN_ROOT: $CRW" "$out1"
assert_contains "clean host: probes the run root's git-common-dir too" \
  "CONSENT resource git_common_dir: ok $CR/root/main.git" "$out1"
assert_contains "clean host: the run worktree is from a run clone" \
  "PREFLIGHT RUN_CLONE: $CR/root/main.git" "$out1"
assert_contains "macOS: the pinned nono is checked (Seatbelt, no Landlock)" \
  "PREFLIGHT NONO: ok $NH/0.81.0/nono (nono 0.81.0, Seatbelt)" "$out1"
assert_contains "macOS: the launcher is launchd + nono" "PREFLIGHT LAUNCHER: launchd + nono" "$out1"
assert_contains "macOS: the nono smoke runs, every assertion listed" "PREFLIGHT SMOKE_WRITE_HOME: pass" "$out1"
assert_contains "macOS: the smoke runs the pinned nono with the token command" \
  "--nono-bin $NH/0.81.0/nono --claude-token-cmd op read op://v/claude/token" "$(cat "$BASE/smoke-args" 2>/dev/null)"
assert_not_contains "macOS: no sandbox-exec smoke any more" "SMOKE_EXEC" "$out1"
# The consent profile grants every path the probe reads: nono's reads are not
# global, so an ungranted path would be refused by nono, not TCC.
rargs="$(cat "$CAPTURE/render-args" 2>/dev/null)"
for want in --claude-token --toolchain "$CRW" "$ROOT" "$CR/root/main.git" "$PROTECTED"; do
  if grep -qxF -- "$want" <<<"$rargs"; then
    ok "consent profile: rendered with $want"
  else
    bad "consent profile: not rendered with $want (args: $(tr '\n' ' ' <<<"$rargs"))"
  fi
done

# The job itself: launchd domain (hence detached, no controlling TTY),
# stdin on /dev/null, and run through nono under the rendered profile.
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
# plist (scripts/orchestrator.plist.tmpl) runs — and not nono.
prog="$(printf '%s' "$plist" | sed -n 's/^ *<string>\(.*\)<\/string>$/\1/p' | tail -n +2 | head -3 | tr '\n' ' ')"
case "$prog" in
  "/bin/bash "*consent-launch.sh*consent.json*) ok "job's program is /bin/bash, as the production job's is" ;;
  *) bad "job's ProgramArguments does not lead with /bin/bash <wrapper> (got: $prog)" ;;
esac
wrapper="$(cat "$CAPTURE/wrapper.sh" 2>/dev/null)"
case "$wrapper" in
  *"NONO_NO_UPDATE_CHECK=1 NONO_NO_MIGRATE=1 $NH/0.81.0/nono run --silent --profile \"\$1\" -- /bin/bash \"\$2\""*)
    ok "wrapper runs the pinned nono on the rendered profile, as a child, as write-launch does"
    ;;
  *) bad "wrapper does not run nono run --silent --profile <profile> (got: $wrapper)" ;;
esac
assert_not_contains "wrapper: no sandbox-exec" "sandbox-exec" "$wrapper"
if grep -q '<key>RunAtLoad</key><true/>' <<<"$plist_flat"; then
  ok "job runs at load"
else
  bad "job does not set RunAtLoad"
fi

# --- Case 2: gated under launchd, readable from this terminal --------------
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

# --- Case 3: the no-TTY/stdin self-check is load-bearing -------------------
out3="$(run_preflight terminal)"
rc3=$?
assert_exit "terminal-attached probe: exits 1" 1 "$rc3"
assert_contains "terminal-attached probe: blocks with the reason" \
  "PREFLIGHT BLOCKER: consent-gate probe ran with a terminal attached" "$out3"

# --- Case 4: a probe that never reports back fails closed ------------------
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

# --- Case 5: no launchctl — a logged skip for the probe, a launch blocker ---
out5="$(mac_preflight clean PREFLIGHT_LAUNCHCTL="$BASE/no-such-launchctl")"
assert_contains "no launchctl: the probe skips rather than blocking" \
  "PREFLIGHT CONSENT_GATE: skip (launchctl not available" "$out5"
case "$out5" in
  *"BLOCKER: interactive consent gate"*) bad "no launchctl: must not synthesize a consent blocker" ;;
  *) ok "no launchctl: no consent blocker" ;;
esac
# The skip is about the probe; the launch itself has no way to spawn on this
# host, so the verdict must be no-go (#1348), never `go` with skip notes.
assert_contains "no launchctl: reports no launcher" "PREFLIGHT LAUNCHER: none (macOS: no launchctl)" "$out5"
assert_contains "no launchctl: verdict is no-go" "PREFLIGHT VERDICT: no-go — no launchctl on this host" "$out5"

# A host that is neither macOS nor Linux has no launcher at all.
out5b="$(mac_preflight clean PREFLIGHT_OS=FreeBSD)"
assert_contains "no launcher: reports none" "PREFLIGHT LAUNCHER: none (FreeBSD)" "$out5b"
assert_contains "no launcher: verdict is no-go" \
  "PREFLIGHT VERDICT: no-go — no supported launcher on this host" "$out5b"
assert_contains "no launcher: reason names both supported launchers" "through launchd on macOS, or through systemd-run on Linux" "$out5b"
assert_contains "no launcher: the consent probe skips (TCC is macOS-only)" "PREFLIGHT CONSENT_GATE: skip (non-macOS host" "$out5b"
assert_contains "no launcher: the smoke skips" "PREFLIGHT SMOKE: skip (no supported launcher" "$out5b"
case "$out5b" in
  *"PREFLIGHT VERDICT: go"*) bad "no launcher: must not print VERDICT: go" ;;
  *) ok "no launcher: no go verdict" ;;
esac

# --- Case 5c: macOS — nono not ready, and no token command ------------------
out5c="$(mac_preflight clean AUTO_PILOT_NONO_HOME="$BASE/no-nono-here")"
assert_contains "macOS: a missing nono reports no launcher" "PREFLIGHT LAUNCHER: none (macOS: nono not ready)" "$out5c"
assert_contains "macOS: a missing nono is a no-go naming the install" \
  "PREFLIGHT VERDICT: no-go — nono jail not ready: nono 0.81.0 is not installed" "$out5c"
assert_contains "macOS: no smoke without a ready nono" "PREFLIGHT SMOKE: skip (nono is not ready" "$out5c"
assert_contains "macOS: no consent probe without a ready nono" "PREFLIGHT CONSENT_GATE: skip (nono not ready — see PREFLIGHT NONO)" "$out5c"
out5c="$(mac_preflight clean -- --claude-token-cmd '')"
assert_contains "macOS: no token command is a no-go naming the Keychain" \
  "PREFLIGHT VERDICT: no-go — macOS needs --claude-token-cmd: the nono jail denies the Keychain" "$out5c"
assert_contains "macOS: no token command skips the smoke (it would refuse too)" \
  "PREFLIGHT SMOKE: skip (macOS needs --claude-token-cmd" "$out5c"
out5c="$(mac_preflight clean FAKE_RENDER_FAIL=1)"
assert_contains "macOS: a consent profile that will not render fails the probe" \
  "PREFLIGHT CONSENT_GATE: FAIL (render-nono-profile failed)" "$out5c"
assert_contains "macOS: the render failure is a blocker carrying its reason" "render failed (fixture)" "$out5c"

# --- Case 6: a run outside every protected location needs no grant ---------
mkdir -p "$BASE/elsewhere"
out6="$(mac_preflight clean PREFLIGHT_TCC_LOCATIONS="$BASE/elsewhere")"
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
if ! command -v plutil >/dev/null 2>&1; then
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
  PF_SCRIPT="$amp_root/scripts/preflight.sh" PF_RUN_ROOT="$amp_root" \
    mac_preflight clean PREFLIGHT_TCC_LOCATIONS="$amp_protected" >/dev/null 2>&1
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
spaced="$BASE/with space"
spaced_root="$spaced/fixture"
mkdir -p "$spaced_root"
cp -R "$ROOT/scripts" "$ROOT/dev_docs" "$spaced_root/"
GIT_CONFIG_GLOBAL=/dev/null git -C "$spaced_root" init -q
out9="$(PF_SCRIPT="$spaced_root/scripts/preflight.sh" PF_RUN_ROOT="$spaced_root" \
  mac_preflight clean PREFLIGHT_TCC_LOCATIONS="$spaced")"
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
out9b="$(PF_SCRIPT="$globbed_root/scripts/preflight.sh" PF_RUN_ROOT="$globbed_root" \
  mac_preflight clean PREFLIGHT_TCC_LOCATIONS="$globbed")"
assert_contains "glob metacharacter in a protected location is not expanded" \
  "PREFLIGHT CONSENT_PROTECTED: $globbed" "$out9b"

# --- Case 10: Linux — 3b checks the pinned nono, 4 runs the nono smoke --------
# The systemd user manager, stubbed (#1350): its state and Linger come from env.
printf '#!/bin/sh\necho "${STUB_SD_STATE-degraded}"\n' >"$FIXBIN/fake-systemctl"
printf '#!/bin/sh\necho "${STUB_LINGER-yes}"\n' >"$FIXBIN/fake-loginctl"
printf '#!/bin/sh\nexit 0\n' >"$FIXBIN/fake-systemd-run"
chmod +x "$FIXBIN/fake-systemctl" "$FIXBIN/fake-loginctl" "$FIXBIN/fake-systemd-run"
linux_preflight() { # [VAR=value ...] [-- preflight args...]
  local -a e=() a=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do
    e+=("$1")
    shift
  done
  [ "${1:-}" = -- ] && shift
  a=("$@")
  env CONSENT_FAKE_MODE=clean CAPTURE="$CAPTURE" PATH="$FIXTURE_PATH" PREFLIGHT_OS=Linux \
    PREFLIGHT_LAUNCHCTL="$FIXBIN/fake-launchctl" PREFLIGHT_TCC_LOCATIONS="$PROTECTED" \
    PREFLIGHT_SYSTEMCTL="$FIXBIN/fake-systemctl" PREFLIGHT_LOGINCTL="$FIXBIN/fake-loginctl" \
    PREFLIGHT_SYSTEMD_RUN="$FIXBIN/fake-systemd-run" \
    AUTO_PILOT_NONO_HOME="$NH" AUTO_PILOT_NONO_TARGET=x86_64-unknown-linux-gnu \
    AUTO_PILOT_LSM_FILE="$BASE/lsm-ok" PREFLIGHT_NONO_SMOKE="$FIXBIN/smoke-nono" \
    SMOKE_ARGS="$BASE/smoke-args" ${e[@]+"${e[@]}"} \
    bash "$SCRIPT" --source plan --base main --run-root "$ROOT" ${a[@]+"${a[@]}"} 2>&1
}
out10="$(linux_preflight AUTO_PILOT_NONO_HOME="$BASE/no-nono-here")"
assert_contains "linux: a missing nono reports no launcher" "PREFLIGHT LAUNCHER: none (Linux: nono not ready)" "$out10"
assert_contains "linux: a missing nono is a no-go" \
  "PREFLIGHT VERDICT: no-go — nono jail not ready: nono 0.81.0 is not installed" "$out10"
assert_contains "linux: the no-go names the install command" "fix: bash $ROOT/scripts/nono-install.sh install" "$out10"
assert_contains "linux: no smoke without a ready nono" "PREFLIGHT SMOKE: skip (nono is not ready" "$out10"
out10="$(linux_preflight)"
assert_contains "linux: a wrong checksum is a no-go" "does not match the pinned SHA-256" "$out10"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" STUB_VERSION=0.79.0)"
assert_contains "linux: a wrong version is a no-go" "--version printed 'nono 0.79.0'" "$out10"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" AUTO_PILOT_LSM_FILE="$BASE/lsm-none")"
assert_contains "linux: no Landlock is a no-go" "nono jail not ready: Landlock is not an active LSM" "$out10"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" STUB_ABI=3)"
assert_contains "linux: Landlock ABI 3 is a no-go" "Landlock ABI 3 is below 4" "$out10"

out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" -- --claude-token-cmd 'op read op://v/claude/token')"
assert_contains "linux: a ready nono is reported" "PREFLIGHT NONO: ok $NH/0.81.0/nono (nono 0.81.0, Landlock ABI 8)" "$out10"
assert_contains "linux: the launcher is nono + systemd-run" \
  "PREFLIGHT LAUNCHER: nono + systemd-run --user (user manager: degraded, Linger=yes)" "$out10"
assert_not_contains "linux: the supervisor is no longer a blocker" "no Linux supervisor yet" "$out10"
assert_contains "linux: every smoke assertion is listed" "PREFLIGHT SMOKE_WRITE_HOME: pass" "$out10"
assert_contains "linux: the smoke runs the pinned nono with the token command" \
  "--nono-bin $NH/0.81.0/nono --claude-token-cmd op read op://v/claude/token" "$(cat "$BASE/smoke-args" 2>/dev/null)"
assert_contains "linux: a run root that is not from a run clone is reported" \
  "PREFLIGHT RUN_CLONE: none (" "$out10"
assert_contains "linux: a run root that is not from a run clone is a no-go naming run-clone" \
  "PREFLIGHT VERDICT: no-go — the run worktree is not from a run clone" "$out10"
assert_not_contains "linux: worker worktrees are no longer a blocker" "no worker worktrees in the Linux jail yet" "$out10"
# A run built by run-clone passes that check.
RC="$BASE/rc"
mkdir -p "$RC"
(
  export GIT_CONFIG_GLOBAL=/dev/null
  git init -q --bare "$RC/origin.git"
  git init -q -b main "$RC/main"
  git -C "$RC/main" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
  git -C "$RC/main" remote add origin "$RC/origin.git"
  git -C "$RC/main" push -q origin main 2>/dev/null
  bash "$REAL_SPAWN" run-clone --repo "$RC/main" --run-root "$RC/root" --run-id r >"$RC/clone.log" 2>&1
) || bad "linux: the run-clone fixture builds ($(tail -1 "$RC/clone.log" 2>/dev/null))"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" -- --run-root "$RC/root/r")"
assert_contains "linux: a run worktree from run-clone is reported with its clone" \
  "PREFLIGHT RUN_CLONE: $(cd "$RC/root" && pwd -P)/main.git" "$out10"
assert_not_contains "linux: a run worktree from run-clone is not a blocker" "not from a run clone" "$out10"
assert_not_contains "linux: the verify broker is no longer a blocker" "no Linux verify broker yet" "$out10"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" STUB_LINGER=no)"
assert_contains "linux: Linger=no is a blocker naming the fix" "fix: loginctl enable-linger" "$out10"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" STUB_SD_STATE=)"
assert_contains "linux: an unreachable user manager is a blocker" "the systemd user manager is unreachable" "$out10"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" PREFLIGHT_SYSTEMD_RUN="$BASE/no-such-systemd-run")"
assert_contains "linux: no systemd-run is a blocker" "no systemd-run on this host" "$out10"
out10="$(linux_preflight AUTO_PILOT_NONO_SHA256="$NSUM" SMOKE_CRED=FAIL)"
assert_contains "linux: a failed smoke assertion is printed" "PREFLIGHT SMOKE_CRED_READ: FAIL" "$out10"
assert_contains "linux: a failed smoke assertion is a blocker" "confinement smoke: CRED_READ failed" "$out10"

echo
echo "test-preflight-consent: $pass_count passed, $fail_count failed, $skip_count skipped"
[ "$fail" -eq 0 ] || exit 1
echo "test-preflight-consent: OK"
