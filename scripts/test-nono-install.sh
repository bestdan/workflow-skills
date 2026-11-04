#!/usr/bin/env bash
# test-nono-install.sh — fixture tests for scripts/nono-install.sh: `check`'s
# no-go cases each name their fix, and `install` refuses a tarball or binary
# whose checksum is not the pin. Offline: the "release" is a file:// tarball
# holding a stub nono, and the pins are overridden to that stub's checksums.
#
# Run directly: bash scripts/test-nono-install.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO/scripts/nono-install.sh"
BASE="$(mktemp -d "${TMPDIR:-/tmp}/test-nono-install.XXXXXX")" || exit 2
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
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# A stub nono whose version and Landlock ABI come from its environment.
mkstub() { # <path>
  mkdir -p "$(dirname "$1")"
  cat >"$1" <<'STUB'
#!/bin/sh
case "$1" in
  --version) echo "nono ${STUB_VERSION:-0.81.0}" ;;
  setup) echo "  * Landlock V${STUB_ABI:-8}" ;;
esac
exit 0
STUB
  chmod 755 "$1"
}
printf 'lockdown,capability,landlock,yama\n' >"$BASE/lsm-ok"
printf 'lockdown,capability,yama\n' >"$BASE/lsm-none"

T=x86_64-unknown-linux-gnu
H="$BASE/home"
BIN="$H/0.81.0/nono"
check() { # [env assignments...] — runs `check` against $H
  env AUTO_PILOT_NONO_HOME="$H" AUTO_PILOT_NONO_TARGET="$T" AUTO_PILOT_LSM_FILE="$BASE/lsm-ok" "$@" \
    bash "$SCRIPT" check 2>&1
}

# --- check ---------------------------------------------------------------------
o="$(check)"
rc=$?
if [ "$rc" = 1 ]; then ok "check: a missing nono is a no-go"; else bad "check: a missing nono is a no-go" "rc=$rc"; fi
have "check: a missing nono says where it looked" "is not installed at $BIN" "$o"
have "check: a missing nono names the install command" "NONO FIX: bash $SCRIPT install" "$o"

mkstub "$BIN"
o="$(check)"
have "check: a binary that is not the pinned build is a no-go" "does not match the pinned SHA-256" "$o"
have "check: a checksum mismatch names the reinstall" "NONO FIX: reinstall the pinned build" "$o"

S="$(sha "$BIN")"
o="$(check AUTO_PILOT_NONO_SHA256="$S" STUB_VERSION=0.80.0)"
have "check: a wrong version is a no-go" "--version printed 'nono 0.80.0', want 'nono 0.81.0'" "$o"

o="$(check AUTO_PILOT_NONO_SHA256="$S" AUTO_PILOT_LSM_FILE="$BASE/lsm-none")"
have "check: no Landlock LSM is a no-go" "Landlock is not an active LSM" "$o"
have "check: no Landlock names the kernel fix" "NONO FIX: enable Landlock" "$o"

o="$(check AUTO_PILOT_NONO_SHA256="$S" STUB_ABI=3)"
have "check: Landlock ABI below 4 is a no-go" "Landlock ABI 3 is below 4" "$o"

o="$(check AUTO_PILOT_NONO_SHA256="$S")"
rc=$?
if [ "$rc" = 0 ]; then ok "check: the pinned build on a Landlock 4+ kernel passes"; else bad "check: the pinned build on a Landlock 4+ kernel passes" "rc=$rc $o"; fi
have "check: a pass names the binary and the ABI" "NONO: ok $BIN (nono 0.81.0, Landlock ABI 8)" "$o"

o="$(check AUTO_PILOT_NONO_TARGET=Darwin/arm64)"
have "check: an unpinned target is a no-go" "no pinned nono build for this host (Darwin/arm64)" "$o"

# The checksum is verified BEFORE the binary runs: a replaced file never executes.
cat >"$BIN" <<STUB
#!/bin/sh
: >"$BASE/ran"
echo "nono 0.81.0"
STUB
check >/dev/null
if [ -e "$BASE/ran" ]; then bad "check: a binary failing its checksum is never executed"; else ok "check: a binary failing its checksum is never executed"; fi

# --- install -------------------------------------------------------------------
if ! command -v curl >/dev/null 2>&1; then
  echo "skip - install: curl not available"
else
  R="$BASE/release/v0.81.0"
  mkdir -p "$R" "$BASE/stage"
  mkstub "$BASE/stage/nono"
  tar -czf "$R/nono-v0.81.0-$T.tar.gz" -C "$BASE/stage" nono
  TS="$(sha "$R/nono-v0.81.0-$T.tar.gz")"
  BS="$(sha "$BASE/stage/nono")"
  inst() { # <home> [env...]
    local h="$1"
    shift
    env AUTO_PILOT_NONO_HOME="$h" AUTO_PILOT_NONO_TARGET="$T" AUTO_PILOT_NONO_URL_BASE="file://$BASE/release" "$@" \
      bash "$SCRIPT" install 2>&1
  }
  o="$(inst "$BASE/h1" AUTO_PILOT_NONO_TARBALL_SHA256="$TS" AUTO_PILOT_NONO_SHA256="$BS")"
  if [ -x "$BASE/h1/0.81.0/nono" ] && [ "$(sha "$BASE/h1/0.81.0/nono")" = "$BS" ]; then
    ok "install: unpacks the verified binary at the pinned path"
  else
    bad "install: unpacks the verified binary at the pinned path" "$o"
  fi
  if [ -z "$(find "$BASE/h1" -name '.install.*' 2>/dev/null)" ]; then ok "install: leaves no staging dir"; else bad "install: leaves no staging dir"; fi
  o="$(inst "$BASE/h1" AUTO_PILOT_NONO_TARBALL_SHA256="$TS" AUTO_PILOT_NONO_SHA256="$BS")"
  have "install: a second run is a no-op" "already installed" "$o"
  chmod 644 "$BASE/h1/0.81.0/nono"
  inst "$BASE/h1" AUTO_PILOT_NONO_TARBALL_SHA256="$TS" AUTO_PILOT_NONO_SHA256="$BS" >/dev/null
  if [ -x "$BASE/h1/0.81.0/nono" ]; then ok "install: a verified binary that lost its mode is reinstalled"; else bad "install: a verified binary that lost its mode is reinstalled"; fi

  o="$(inst "$BASE/h2" AUTO_PILOT_NONO_SHA256="$BS")"
  have "install: a tarball that is not the pinned one is refused" "tarball SHA-256 mismatch" "$o"
  if [ -e "$BASE/h2/0.81.0/nono" ]; then bad "install: a refused tarball installs nothing"; else ok "install: a refused tarball installs nothing"; fi

  o="$(inst "$BASE/h3" AUTO_PILOT_NONO_TARBALL_SHA256="$TS")"
  have "install: a binary that is not the pinned one is refused" "binary SHA-256 mismatch" "$o"
  if [ -e "$BASE/h3/0.81.0/nono" ]; then bad "install: a refused binary installs nothing"; else ok "install: a refused binary installs nothing"; fi
fi

echo
echo "test-nono-install: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
