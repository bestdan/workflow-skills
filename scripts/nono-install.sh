#!/usr/bin/env bash
# nono-install.sh — the pinned nono the orchestrator jail runs under, on Linux
# (Landlock, #1349) and macOS (Seatbelt, #1361;
# dev_docs/designs/2026-11-03-orchestrator-jail.md).
#
# nono is pre-1.0 and releases weekly, so the jail never runs "whatever nono is
# on PATH". It runs one version, from a release tarball whose SHA-256 and whose
# binary's SHA-256 are pinned below, unpacked into a plugin-managed directory in
# the user's home. No sudo, no package manager, no update channel: moving the pin
# is a reviewed edit to this file.
#
# Usage:
#   nono-install.sh path      print the pinned binary's path
#   nono-install.sh install   download, verify, unpack (idempotent)
#   nono-install.sh check     verify the installed binary (and, on Linux, the
#                             kernel's Landlock support)
#
# `check` prints `NONO: ok <path> (...)` and exits 0, or prints `NONO: <problem>`
# then `NONO FIX: <what to run>` and exits 1. Pre-flight §3b reads those lines.
# Its order is load-bearing: the checksum is verified BEFORE the binary is ever
# executed, so a replaced file never runs.
#
# Env (the first is for users; the rest are for tests only):
#   AUTO_PILOT_NONO_HOME            install root (default
#                                   ${XDG_DATA_HOME:-$HOME/.local/share}/auto-pilot/nono)
#   AUTO_PILOT_NONO_TARGET          the release target, instead of uname
#   AUTO_PILOT_NONO_SHA256          the binary pin, instead of the table
#   AUTO_PILOT_NONO_TARBALL_SHA256  the tarball pin, instead of the table
#   AUTO_PILOT_NONO_URL_BASE        the release download base
#   AUTO_PILOT_LSM_FILE             the kernel's LSM list (Linux targets)
set -uo pipefail

NONO_VERSION="0.81.0"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

# <target> -> "<tarball sha256> <binary sha256>". Tarball sums are the release's
# own SHA256SUMS.txt; binary sums were taken from those tarballs (Linux
# 2026-11-03, macOS 2026-11-06).
_pin() {
  case "$1" in
    x86_64-unknown-linux-gnu)
      echo "7ecc33ff9c349bdfa84b4496e7a7555f67bcc33f1e125c7df9efb653c6c4e569 d8ac3fa92d87bfbe80a52ea68729a8b5b461149437d55225c356b82af5bfc90f"
      ;;
    aarch64-unknown-linux-gnu)
      echo "a4d87ddb0917ef96224bcc41a2587318d7d0e92441e7ff454696e5980c9f7924 6d9c53bed08eb81e5b4c38fdf1d0089087566dc9035da7614ce26d011ed2e8f4"
      ;;
    aarch64-apple-darwin)
      echo "728b1deefbf6390c9b74fc982efbded1e1c4b37b99dcdefa2e07266dfef4d9e9 b1135f21cf81ae3c8ae3b035dbbeea70072595b54f6138afad7a5402c7cedcb1"
      ;;
    x86_64-apple-darwin)
      echo "f795aa88faaf75975ffc600eb870e7c9f2b52ba2035df5087ec972a0252afd40 e871eaa58e4d6b6cc3fffe6aaa19e2b74b98d023097924a52b07bc9378146d77"
      ;;
    *) return 1 ;;
  esac
}

_target() {
  if [ -n "${AUTO_PILOT_NONO_TARGET:-}" ]; then
    echo "$AUTO_PILOT_NONO_TARGET"
    return 0
  fi
  case "$(uname -s 2>/dev/null)/$(uname -m 2>/dev/null)" in
    Linux/x86_64) echo x86_64-unknown-linux-gnu ;;
    Linux/aarch64 | Linux/arm64) echo aarch64-unknown-linux-gnu ;;
    Darwin/arm64) echo aarch64-apple-darwin ;;
    Darwin/x86_64) echo x86_64-apple-darwin ;;
    *) echo "$(uname -s 2>/dev/null)/$(uname -m 2>/dev/null)" ;;
  esac
}

_home() {
  local h="${AUTO_PILOT_NONO_HOME:-${XDG_DATA_HOME:-${HOME:-}/.local/share}/auto-pilot/nono}"
  case "$h" in /*) echo "$h" ;; *) return 1 ;; esac
}

_bin() {
  local h
  h="$(_home)" || return 1
  echo "$h/$NONO_VERSION/nono"
}

_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

fail() { # <problem> <fix>
  echo "NONO: $1"
  echo "NONO FIX: $2"
  exit 1
}

cmd_check() {
  local target pins bin want got ver lsm abi
  target="$(_target)"
  pins="$(_pin "$target")" || fail "no pinned nono build for this host ($target)" \
    "the jail runs on x86_64 or aarch64 Linux, or Apple silicon or Intel macOS, only"
  bin="$(_bin)" || fail "the nono install root is not an absolute path" \
    "set AUTO_PILOT_NONO_HOME, XDG_DATA_HOME or HOME to an absolute path"
  [ -f "$bin" ] || fail "nono $NONO_VERSION is not installed at $bin" "bash $SELF install"
  want="${AUTO_PILOT_NONO_SHA256:-${pins#* }}"
  got="$(_sha256 "$bin")"
  [ "$got" = "$want" ] || fail "$bin does not match the pinned SHA-256 (got $got, want $want)" \
    "reinstall the pinned build: bash $SELF install"
  [ -x "$bin" ] || fail "$bin is not executable" "reinstall the pinned build: bash $SELF install"
  ver="$(NONO_NO_UPDATE_CHECK=1 NONO_NO_MIGRATE=1 "$bin" --version 2>/dev/null | head -1)"
  [ "$ver" = "nono $NONO_VERSION" ] || fail "$bin --version printed '$ver', want 'nono $NONO_VERSION'" \
    "reinstall the pinned build: bash $SELF install"
  # macOS: nono confines with Seatbelt, which every supported macOS has; the
  # Landlock checks below are the Linux kernel's.
  case "$target" in
    *-apple-darwin)
      echo "NONO: ok $bin (nono $NONO_VERSION, Seatbelt)"
      return 0
      ;;
  esac
  lsm="$(cat "${AUTO_PILOT_LSM_FILE:-/sys/kernel/security/lsm}" 2>/dev/null || true)"
  case ",$lsm," in
    *,landlock,*) ;;
    *) fail "Landlock is not an active LSM on this kernel (lsm: ${lsm:-unreadable})" \
      "enable Landlock (kernel 6.7+, with landlock in the lsm= boot list), then re-run pre-flight" ;;
  esac
  abi="$(NONO_NO_UPDATE_CHECK=1 NONO_NO_MIGRATE=1 "$bin" setup --check-only 2>&1 | sed -n 's/.*Landlock V\([0-9][0-9]*\).*/\1/p' | head -1)"
  case "$abi" in
    '' | *[!0-9]*) fail "could not read the Landlock ABI from '$bin setup --check-only'" \
      "run '$bin setup --check-only' and read its Landlock line" ;;
  esac
  [ "$abi" -ge 4 ] || fail "Landlock ABI $abi is below 4: TCP rules fail closed, so the run cannot reach the API" \
    "upgrade to a kernel with Landlock ABI 4 or later (6.7+)"
  echo "NONO: ok $bin (nono $NONO_VERSION, Landlock ABI $abi)"
}

cmd_install() {
  local target pins bin home dir tar_sum bin_sum url got
  target="$(_target)"
  pins="$(_pin "$target")" || {
    echo "nono-install: no pinned nono build for this host ($target)" >&2
    exit 1
  }
  tar_sum="${AUTO_PILOT_NONO_TARBALL_SHA256:-${pins%% *}}"
  bin_sum="${AUTO_PILOT_NONO_SHA256:-${pins#* }}"
  home="$(_home)" || {
    echo "nono-install: the install root is not an absolute path" >&2
    exit 1
  }
  bin="$home/$NONO_VERSION/nono"
  # -x as well: a verified binary that lost its mode would fail `check`, whose fix
  # text points back here, so the no-op must not accept it.
  if [ -f "$bin" ] && [ -x "$bin" ] && [ "$(_sha256 "$bin")" = "$bin_sum" ]; then
    echo "nono-install: nono $NONO_VERSION already installed at $bin"
    return 0
  fi
  command -v curl >/dev/null 2>&1 || {
    echo "nono-install: curl is required" >&2
    exit 1
  }
  dir="$home/$NONO_VERSION"
  mkdir -p "$dir" || exit 1
  # Staged beside the destination, so the final mv is a rename on one filesystem.
  # Global, not local: the EXIT trap runs after this function has returned.
  tmp="$(mktemp -d "$home/.install.XXXXXX")" || exit 1
  trap 'rm -rf "${tmp:-}"' EXIT
  url="${AUTO_PILOT_NONO_URL_BASE:-https://github.com/nolabs-ai/nono/releases/download}/v$NONO_VERSION/nono-v$NONO_VERSION-$target.tar.gz"
  echo "nono-install: downloading $url"
  curl -fsSL -o "$tmp/nono.tar.gz" "$url" || {
    echo "nono-install: download failed: $url" >&2
    exit 1
  }
  got="$(_sha256 "$tmp/nono.tar.gz")"
  [ "$got" = "$tar_sum" ] || {
    echo "nono-install: tarball SHA-256 mismatch (got $got, want $tar_sum) — not installing" >&2
    exit 1
  }
  mkdir "$tmp/x" && tar -xzf "$tmp/nono.tar.gz" -C "$tmp/x" nono || {
    echo "nono-install: could not unpack nono from the tarball" >&2
    exit 1
  }
  got="$(_sha256 "$tmp/x/nono")"
  [ "$got" = "$bin_sum" ] || {
    echo "nono-install: binary SHA-256 mismatch (got $got, want $bin_sum) — not installing" >&2
    exit 1
  }
  chmod 755 "$tmp/x/nono" && mv -f "$tmp/x/nono" "$bin" || exit 1
  echo "nono-install: installed nono $NONO_VERSION at $bin"
}

case "${1:-}" in
  path)
    _bin || exit 1
    ;;
  check) cmd_check ;;
  install) cmd_install ;;
  *)
    echo "usage: nono-install.sh {path|install|check}" >&2
    exit 2
    ;;
esac
