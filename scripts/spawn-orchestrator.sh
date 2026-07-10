#!/usr/bin/env bash
# spawn-orchestrator.sh — materialize the auto-pilot detached orchestrator's
# launch wrapper and its two-layer jail from a run's resolved inputs, so an
# agent never hand-authors a sandbox-exec profile at launch (PRE-484).
#
# The jail is two layers (skills/auto-pilot/references/launch-runtime.md
# "Sandbox profile"):
#   1. filesystem + process/exec  → this script's Seatbelt profile (below)
#   2. host-level network egress  → the detached `claude -p`'s own
#      sandbox.network allowlist (added by a later task)
#
# Ships the layer-1 profile renderer + compile check (render-profile /
# check-profile) and the layer-2 egress-allowlist emitter (render-settings).
# Subcommands added by sibling tasks: write-launch, detach, teardown.
#
# Usage:
#   spawn-orchestrator.sh render-profile \
#       --rw <path> [--rw <path> ...] \
#       --ro <path> [--ro <path> ...] \
#       --exec <path> [--exec <path> ...] \
#       --out <file> [--template <file>]
#   spawn-orchestrator.sh render-settings \
#       --source <linear|plan> \
#       [--coder <codex|devin|agy> ...] [--agy-host <host>] \
#       [--mcp-host <host> ...] [--npm] \
#       --out <file>
#   spawn-orchestrator.sh check-profile <file>
#
#   render-settings  Emit the ephemeral `claude -p --settings` JSON: layer-2
#                    network egress (sandbox.network.allowedDomains) narrowed to
#                    the resolved coders + source, deny-by-default. Fail-closed on
#                    an unresolvable required host (e.g. agy without --agy-host).
#
#   render-profile  Render the Seatbelt (.sb) profile from resolved paths into
#                   --out. Every path must be ABSOLUTE and EXIST; a relative or
#                   missing path fails closed and no file is written.
#     --rw    A read-write scope (run/worker worktrees, $TMPDIR). Repeatable.
#     --ro    A read-only scope (rest of repo, credential stores). Repeatable.
#     --exec  A binary permitted to exec (resolved coder + base toolchain).
#             Repeatable.
#     --out   Destination path for the rendered profile. Required.
#     --template  Override the profile template (default: scripts/orchestrator.sb.tmpl).
#   check-profile  Confirm a rendered profile COMPILES via `sandbox-exec -f`,
#                  surfacing the Seatbelt parser error verbatim on failure.
#
# Exit status and structured final line:
#   0  spawn-orchestrator: profile OK <path>       render/check succeeded
#   2  usage, dependency, or fail-closed path error (nothing partial written)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DEFAULT="$ROOT/scripts/orchestrator.sb.tmpl"

die() { echo "spawn-orchestrator: $*" >&2; exit 2; }

# Canonicalize an absolute, existing path. Prints the canonical path on success;
# on a fail-closed condition it writes the reason to stderr and RETURNS non-zero
# (never `exit` — this runs inside a $(command substitution), where `exit` would
# only kill the subshell and let the caller sail on). Callers must `|| exit 2`.
# Resolves via cd/pwd -P so no external `realpath` is required (not on all macOS).
canonicalize() {
  local p="$1"
  case "$p" in
    /*) ;;
    *) echo "spawn-orchestrator: path must be absolute (fail-closed): $p" >&2; return 1 ;;
  esac
  [ -e "$p" ] || { echo "spawn-orchestrator: path does not exist (fail-closed): $p" >&2; return 1; }
  if [ -d "$p" ]; then
    (cd "$p" && pwd -P)
  else
    # Resolve the file's symlink chain: Seatbelt matches process-exec (and file
    # rules) against the vnode's canonical target, not the symlink literal. Many
    # macOS binaries are symlinks (e.g. /opt/homebrew/bin/gh → Cellar/…), and
    # `command -v` hands back the symlink — so authorizing only the symlink path
    # would silently deny the exec. Follow links, re-canonicalizing the dir each
    # hop, with a loop guard.
    local d b target guard=0
    while [ -L "$p" ]; do
      guard=$((guard + 1))
      [ "$guard" -le 40 ] || { echo "spawn-orchestrator: symlink chain too deep (fail-closed): $1" >&2; return 1; }
      target="$(readlink "$p")" || { echo "spawn-orchestrator: cannot read link (fail-closed): $p" >&2; return 1; }
      case "$target" in
        /*) p="$target" ;;
        *)  p="$(cd "$(dirname "$p")" && pwd -P)/$target" ;;
      esac
    done
    d="$(cd "$(dirname "$p")" && pwd -P)" || { echo "spawn-orchestrator: cannot resolve directory of: $p" >&2; return 1; }
    b="$(basename "$p")"
    printf '%s/%s' "$d" "$b"
  fi
}

# Escape a path for use inside a Seatbelt "(subpath \"…\")" / "(literal \"…\")".
sbpl_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# Emit an s-expression allowing `op` over the given canonical paths, or a
# placeholder comment when the list is empty. Args: <op> <path>...
emit_allow() {
  local op="$1"; shift
  if [ "$#" -eq 0 ]; then
    printf ';; (none)\n'
    return
  fi
  printf '(allow %s\n' "$op"
  local p
  for p in "$@"; do
    printf '  (subpath "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

emit_exec() {
  if [ "$#" -eq 0 ]; then
    printf ';; (no coder/toolchain binaries resolved)\n'
    return
  fi
  printf '(allow process-exec\n'
  local p
  for p in "$@"; do
    printf '  (literal "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

render_profile() {
  local out="" template="$TEMPLATE_DEFAULT"
  local -a rw=() ro=() ex=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --rw) [ $# -ge 2 ] || die "missing value for --rw"; rw+=("$2"); shift 2 ;;
      --ro) [ $# -ge 2 ] || die "missing value for --ro"; ro+=("$2"); shift 2 ;;
      --exec) [ $# -ge 2 ] || die "missing value for --exec"; ex+=("$2"); shift 2 ;;
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      --template) [ $# -ge 2 ] || die "missing value for --template"; template="$2"; shift 2 ;;
      *) die "unknown render-profile argument: $1" ;;
    esac
  done

  [ -n "$out" ] || die "render-profile requires --out <file>"
  [ -f "$template" ] || die "profile template not found: $template"
  case "$out" in /*) ;; *) die "--out must be absolute (fail-closed): $out" ;; esac

  # Canonicalize ALL inputs first — any bad path aborts before we write, so a
  # partial profile is never emitted.
  local -a rw_c=() ro_c=() ex_c=()
  local p c
  for p in ${rw[@]+"${rw[@]}"}; do c="$(canonicalize "$p")" || exit 2; rw_c+=("$c"); done
  for p in ${ro[@]+"${ro[@]}"}; do c="$(canonicalize "$p")" || exit 2; ro_c+=("$c"); done
  for p in ${ex[@]+"${ex[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    { [ -f "$c" ] && [ -x "$c" ]; } || die "exec path is not an executable file (fail-closed): $c"
    ex_c+=("$c")
  done

  # Build the three token blocks.
  local ro_block rw_block ex_block
  ro_block="$(emit_allow "file-read*" ${ro_c[@]+"${ro_c[@]}"})"
  # RW scopes need both read and write.
  rw_block="$(emit_allow "file-read*" ${rw_c[@]+"${rw_c[@]}"})
$(emit_allow "file-write*" ${rw_c[@]+"${rw_c[@]}"})"
  ex_block="$(emit_exec ${ex_c[@]+"${ex_c[@]}"})"

  # Substitute tokens line-by-line into a temp file, then move into place so a
  # failure mid-render leaves no partial --out.
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator.sb.XXXXXX")" || die "mktemp failed"
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      *@@RO_PATHS@@*) printf '%s\n' "$ro_block" ;;
      *@@RW_PATHS@@*) printf '%s\n' "$rw_block" ;;
      *@@EXEC_PATHS@@*) printf '%s\n' "$ex_block" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done <"$template" >"$tmp"

  mv "$tmp" "$out" || { rm -f "$tmp"; die "failed to write profile: $out"; }
  echo "spawn-orchestrator: profile OK $out"
}

# Build the narrowed egress host set (sorted, unique), fail-closed. This is
# layer 2 of the jail: Seatbelt can't filter by hostname, so egress is enforced
# by the detached `claude -p`'s own sandbox.network allowlist. The set is
# narrowed to the run's resolved coders + source so a linear+codex run never
# opens devin's or agy's endpoints. Args are parsed by render_settings below.
render_network_allowlist() {
  local source="" agy_host="" npm=0
  local -a coders=() mcp=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --coder) [ $# -ge 2 ] || die "missing value for --coder"; coders+=("$2"); shift 2 ;;
      --source) [ $# -ge 2 ] || die "missing value for --source"; source="$2"; shift 2 ;;
      --agy-host) [ $# -ge 2 ] || die "missing value for --agy-host"; agy_host="$2"; shift 2 ;;
      --mcp-host) [ $# -ge 2 ] || die "missing value for --mcp-host"; mcp+=("$2"); shift 2 ;;
      --npm) npm=1; shift ;;
      *) die "unknown allowlist argument: $1" ;;
    esac
  done
  case "$source" in
    linear|plan) ;;
    "") die "network allowlist requires --source <linear|plan>" ;;
    *) die "unknown --source (fail-closed): $source" ;;
  esac

  # Always needed: the orchestrator model + GitHub (PRs, git over HTTPS).
  local -a hosts=(api.anthropic.com api.github.com github.com codeload.github.com '*.githubusercontent.com')
  [ "$source" = linear ] && hosts+=(api.linear.app)
  [ "$npm" = 1 ] && hosts+=(registry.npmjs.org)

  local c
  for c in ${coders[@]+"${coders[@]}"}; do
    case "$c" in
      codex) hosts+=(api.openai.com) ;;
      devin) hosts+=(api.devin.ai server.codeium.com) ;;
      agy)
        [ -n "$agy_host" ] || die "coder 'agy' requires --agy-host <resolved Antigravity host> (fail-closed): install/re-route or resolve the host at pre-flight"
        case "$agy_host" in
          \**) die "agy host must be a concrete host, never a wildcard (fail-closed): $agy_host" ;;
        esac
        hosts+=("$agy_host") ;;
      *) die "unknown --coder (fail-closed): $c" ;;
    esac
  done
  hosts+=(${mcp[@]+"${mcp[@]}"})

  printf '%s\n' "${hosts[@]}" | sort -u
}

# Emit a JSON array literal from the host lines on stdin.
hosts_to_json_array() {
  local first=1 out="["
  local h
  while IFS= read -r h; do
    [ -n "$h" ] || continue
    [ "$first" = 1 ] && first=0 || out+=","
    out+="\"$h\""
  done
  out+="]"
  printf '%s' "$out"
}

render_settings() {
  local out=""
  local -a passthru=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      *) passthru+=("$1"); shift ;;
    esac
  done
  [ -n "$out" ] || die "render-settings requires --out <file>"
  case "$out" in /*) ;; *) die "--out must be absolute (fail-closed): $out" ;; esac

  # render_network_allowlist runs in a $() — its die() exits only the subshell,
  # so capture the status and abort here (fail-closed: no settings on any error).
  local hosts array
  hosts="$(render_network_allowlist ${passthru[@]+"${passthru[@]}"})" || exit 2
  array="$(printf '%s\n' "$hosts" | hosts_to_json_array)"

  # Ephemeral run settings for `claude -p --settings '<json>'`: deny-by-default
  # egress narrowed to $array, plus loopback for the enforcement proxy + local
  # tooling. Written to --out (audit trail); the caller passes it via --settings,
  # never mutating ~/.claude.
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-settings.XXXXXX")" || die "mktemp failed"
  printf '{"sandbox":{"enabled":true,"network":{"allowedDomains":%s,"allowLocalBinding":true}}}\n' "$array" >"$tmp" \
    || { rm -f "$tmp"; die "failed to write settings"; }
  mv "$tmp" "$out" || { rm -f "$tmp"; die "failed to write settings: $out"; }
  echo "spawn-orchestrator: settings OK $out"
}

check_profile() {
  [ $# -eq 1 ] || die "check-profile takes exactly one <file>"
  local f="$1"
  [ -f "$f" ] || die "profile not found: $f"
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not available (macOS only)"
  # Compile the profile by loading it. We test COMPILATION, not permission: a
  # locked-down profile legitimately denies exec of an unlisted probe binary, so
  # reaching execvp() means the profile parsed and loaded fine. Only a genuine
  # parse/compile error (which never reaches execvp) is a failure.
  local errout status
  errout="$(sandbox-exec -f "$f" /usr/bin/true 2>&1)"; status=$?
  if [ "$status" -eq 0 ] || printf '%s' "$errout" | grep -q 'execvp('; then
    echo "spawn-orchestrator: profile OK $f"
    return 0
  fi
  [ -n "$errout" ] && echo "$errout" >&2
  die "profile failed to compile: $f"
}

[ $# -ge 1 ] || die "usage: spawn-orchestrator.sh <render-profile|check-profile> …"
sub="$1"; shift
case "$sub" in
  render-profile) render_profile "$@" ;;
  render-settings) render_settings "$@" ;;
  check-profile) check_profile "$@" ;;
  -h|--help) sed -n '2,/^[^#]/{/^#/p;}' "$0"; exit 0 ;;
  *) die "unknown subcommand: $sub" ;;
esac
