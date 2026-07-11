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
#       --cred-ro <file> [--cred-ro <file> ...] \
#       --exec <path> [--exec <path> ...] \
#       --exec-dir <dir> [--exec-dir <dir> ...] [--toolchain] \
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
#     --cred-ro  A credential FILE kept read-only even when it lives inside an
#                --rw state dir (a tool's own token under ~/.codex/~/.claude). The
#                state dir stays writable; only the token is denied writes, via a
#                specific (deny file-write* (literal …)) emitted after the RW block.
#                Must be an existing file. Repeatable.
#     --exec  A binary permitted to exec (resolved coder + base toolchain).
#             Repeatable.
#     --exec-dir  A directory permitted to exec (subpath, coarser than --exec).
#                 Must exist and must not be "/". Repeatable.
#     --toolchain  Convenience: add each of a standard set of bin dirs (that
#                  exist on this host) to the exec-dir set.
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

# Emit a (deny file-write* (literal …)) form over credential files, or a
# placeholder comment when the list is empty. This is emitted AFTER the RW block
# so it overrides the state-dir write allow (Seatbelt = last matching rule wins),
# keeping a token read-only while its enclosing state dir stays writable. Args:
# <cred-path>...
emit_deny_write() {
  if [ "$#" -eq 0 ]; then
    printf ';; (no isolated credential files)\n'
    return
  fi
  printf '(deny file-write*\n'
  local p
  for p in "$@"; do
    printf '  (literal "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

# Emit the process-exec allow form: literal binaries (--exec) and subpath bin
# dirs (--exec-dir/--toolchain). Args: <literal>... -- <subpath>...
emit_exec() {
  local -a lits=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do lits+=("$1"); shift; done
  shift || true   # drop the -- separator
  if [ "${#lits[@]}" -eq 0 ] && [ "$#" -eq 0 ]; then
    printf ';; (no coder/toolchain binaries resolved)\n'
    return
  fi
  printf '(allow process-exec\n'
  local p
  for p in ${lits[@]+"${lits[@]}"}; do
    printf '  (literal "%s")\n' "$(sbpl_escape "$p")"
  done
  for p in "$@"; do
    printf '  (subpath "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

render_profile() {
  local out="" template="$TEMPLATE_DEFAULT" toolchain=0
  local -a rw=() ro=() cred=() ex=() exd=() confine=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --rw) [ $# -ge 2 ] || die "missing value for --rw"; rw+=("$2"); shift 2 ;;
      --ro) [ $# -ge 2 ] || die "missing value for --ro"; ro+=("$2"); shift 2 ;;
      --cred-ro) [ $# -ge 2 ] || die "missing value for --cred-ro"; cred+=("$2"); shift 2 ;;
      --exec) [ $# -ge 2 ] || die "missing value for --exec"; ex+=("$2"); shift 2 ;;
      --exec-dir) [ $# -ge 2 ] || die "missing value for --exec-dir"; exd+=("$2"); shift 2 ;;
      --toolchain) toolchain=1; shift ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; confine+=("$2"); shift 2 ;;
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      --template) [ $# -ge 2 ] || die "missing value for --template"; template="$2"; shift 2 ;;
      *) die "unknown render-profile argument: $1" ;;
    esac
  done

  [ -n "$out" ] || die "render-profile requires --out <file>"
  [ -f "$template" ] || die "profile template not found: $template"
  case "$out" in /*) ;; *) die "--out must be absolute (fail-closed): $out" ;; esac

  # --toolchain: expand to the standard bin dirs, skipping any that don't exist
  # on this host (a missing standard dir is not fail-closed — it's just omitted).
  if [ "$toolchain" = 1 ]; then
    local d
    for d in /bin /usr/bin /usr/sbin /usr/libexec /opt/homebrew/bin \
      "$HOME/.local/bin" "$HOME/.local/share/claude" "$HOME/.codex" "$HOME/.nvm"; do
      [ -d "$d" ] && exd+=("$d")
    done
  fi

  # Canonicalize ALL inputs first — any bad path aborts before we write, so a
  # partial profile is never emitted.
  local -a rw_c=() ro_c=() cred_c=() ex_c=() exd_c=() confine_c=()
  local p c
  for p in ${confine[@]+"${confine[@]}"}; do c="$(canonicalize "$p")" || exit 2; confine_c+=("$c"); done
  for p in ${rw[@]+"${rw[@]}"}; do c="$(canonicalize "$p")" || exit 2; rw_c+=("$c"); done
  for p in ${ro[@]+"${ro[@]}"}; do c="$(canonicalize "$p")" || exit 2; ro_c+=("$c"); done
  # Credential files: must be an existing FILE (a token, not a dir). The literal
  # deny is emitted after the RW block so it overrides a co-located state-dir
  # write allow; a cred file outside every RW scope is harmless (already unwritable).
  # KNOWN LIMITATION (documented, not fail-closed): Seatbelt matches on the path,
  # not the inode, so a second HARD LINK to the same credential inode inside an RW
  # scope stays writable and would mutate the token through the alias. We do NOT
  # reject nlink>1 here — a benign multiply-linked file must not fail a launch
  # closed at 3am — but tool credential files are not hard-linked in practice.
  for p in ${cred[@]+"${cred[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    [ -f "$c" ] || die "cred-ro path is not a file (fail-closed): $c"
    cred_c+=("$c")
  done
  for p in ${ex[@]+"${ex[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    { [ -f "$c" ] && [ -x "$c" ]; } || die "exec path is not an executable file (fail-closed): $c"
    ex_c+=("$c")
  done
  for p in ${exd[@]+"${exd[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    [ -d "$c" ] || die "exec-dir is not a directory (fail-closed): $c"
    # Dedupe against what's already resolved (explicit --exec-dir + --toolchain
    # can overlap, e.g. /opt/homebrew/bin listed twice).
    case " ${exd_c[@]+"${exd_c[@]}"} " in *" $c "*) continue ;; esac
    exd_c+=("$c")
  done

  # Floor (ALWAYS, even without --confine-under): a write scope of "/" emits a
  # whole-filesystem write rule that defeats the jail. Refuse it unconditionally —
  # the containment guard below is opt-in and can't protect itself if a caller
  # forgets --confine-under, so this floor closes the worst case regardless.
  local w r under
  for w in ${rw_c[@]+"${rw_c[@]}"}; do
    [ "$w" = "/" ] && die "refusing --rw / (whole-filesystem write, fail-closed)"
  done
  # Same floor for exec breadth: an exec-dir of "/" would allow-list exec of
  # every binary on the filesystem, defeating the exec wall entirely.
  for w in ${exd_c[@]+"${exd_c[@]}"}; do
    [ "$w" = "/" ] && die "refusing --exec-dir / (whole-filesystem exec, fail-closed)"
  done
  # Containment: when --confine-under roots are given, every WRITE scope must
  # canonicalize inside one of them (so a worktree symlinked to /$HOME can't slip
  # a broad write through). Both sides are already pwd -P'd; the prefix test is
  # LITERAL (`${w#"$r"/}`, not a glob) so a root containing * / [ can't over-match.
  # RO/exec are intentionally exempt (creds and system binaries live outside the
  # run root). Fail-closed on any escape.
  if [ "${#confine_c[@]}" -gt 0 ]; then
    for w in ${rw_c[@]+"${rw_c[@]}"}; do
      under=0
      for r in "${confine_c[@]}"; do
        { [ "$w" = "$r" ] || [ "${w#"$r"/}" != "$w" ]; } && under=1
      done
      [ "$under" = 1 ] || die "write scope escapes --confine-under (fail-closed): $w"
    done
  fi

  # Build the token blocks.
  local ro_block rw_block cred_block ex_block
  ro_block="$(emit_allow "file-read*" ${ro_c[@]+"${ro_c[@]}"})"
  # RW scopes need both read and write.
  rw_block="$(emit_allow "file-read*" ${rw_c[@]+"${rw_c[@]}"})
$(emit_allow "file-write*" ${rw_c[@]+"${rw_c[@]}"})"
  cred_block="$(emit_deny_write ${cred_c[@]+"${cred_c[@]}"})"
  ex_block="$(emit_exec ${ex_c[@]+"${ex_c[@]}"} -- ${exd_c[@]+"${exd_c[@]}"})"

  # Substitute tokens line-by-line into a temp file, then move into place so a
  # failure mid-render leaves no partial --out.
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator.sb.XXXXXX")" || die "mktemp failed"
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      *@@RO_PATHS@@*) printf '%s\n' "$ro_block" ;;
      *@@RW_PATHS@@*) printf '%s\n' "$rw_block" ;;
      *@@CRED_DENY@@*) printf '%s\n' "$cred_block" ;;
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

  # Validate EVERY host, fail-closed. Host values from --mcp-host / --agy-host are
  # resolved per-run from the (possibly adversary-influenced) work source, so an
  # unvalidated value could smuggle a bare `*` (nullifying the allowlist) or inject
  # JSON in hosts_to_json_array. Require a real hostname — labels of [A-Za-z0-9-]
  # separated by dots, with at most a single leading `*.` subdomain wildcard (which
  # `*.githubusercontent.com` legitimately uses; a bare `*` has no labels and is
  # rejected). The agy-specific guard above additionally bans ANY wildcard for agy.
  local h
  for h in "${hosts[@]}"; do
    printf '%s' "$h" | grep -Eq '^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$' \
      || die "invalid egress host (fail-closed): $h"
  done

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
  local out="" localbind=false
  local -a passthru=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      # Default OFF: a listen socket lets the inside process reach host-local
      # services (the Solo control port, local proxies, …) — a pivot the jail
      # shouldn't grant. Enable only if a run genuinely needs to bind loopback.
      --allow-local-binding) localbind=true; shift ;;
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
  printf '{"sandbox":{"enabled":true,"network":{"allowedDomains":%s,"allowLocalBinding":%s}}}\n' "$array" "$localbind" >"$tmp" \
    || { rm -f "$tmp"; die "failed to write settings"; }
  mv "$tmp" "$out" || { rm -f "$tmp"; die "failed to write settings: $out"; }
  echo "spawn-orchestrator: settings OK $out"
}

# ---------------------------------------------------------------------------
# Task 3 — spawn mechanics: launch script + relaunchable launchd supervisor,
# an auth smoke-test THROUGH the wrapper (before detaching), and the run handle.
# ---------------------------------------------------------------------------

PLIST_TEMPLATE_DEFAULT="$ROOT/scripts/orchestrator.plist.tmpl"

# Escape a value for inclusion inside a plist <string>…</string>. `&` MUST be
# replaced first. Without this, a value containing plist/XML metacharacters (legal
# in macOS paths, and a label is caller-set) could inject launchd keys — e.g. a
# second <key>ProgramArguments</key>, which launchd execs DIRECTLY, not under
# sandbox-exec, bypassing the whole jail. This is the plist analogue of sbpl_escape.
xml_escape() {
  local s="$1"
  s="${s//&/&amp;}"
  s="${s//</&lt;}"
  s="${s//>/&gt;}"
  s="${s//\"/&quot;}"
  printf '%s' "$s"
}

# Inline token replacement (bash, not sed) so arbitrary paths with regex-special
# chars can't corrupt the render; every substituted string value is XML-escaped.
render_plist() {
  local label launch_script workdir log
  label="$(xml_escape "$1")"; launch_script="$(xml_escape "$2")"
  workdir="$(xml_escape "$3")"; log="$(xml_escape "$4")"
  local interval="$5" throttle="$6" template="$7"   # integers, validated numeric upstream
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line//@@LABEL@@/$label}"
    line="${line//@@LAUNCH_SCRIPT@@/$launch_script}"
    line="${line//@@WORKDIR@@/$workdir}"
    line="${line//@@LOG@@/$log}"
    line="${line//@@INTERVAL@@/$interval}"
    line="${line//@@THROTTLE@@/$throttle}"
    printf '%s\n' "$line"
  done <"$template"
}

# Emit the self-contained launch script + the launchd plist from resolved inputs.
# The launch script is what the plist runs: it composes the jail
# (sandbox-exec -f <profile>) around `claude -p --permission-mode bypassPermissions`
# with the layer-2 --settings, reads the run prompt from a file, and redirects to
# the log. %q-quoting keeps every interpolated path/string shell-safe.
write_launch() {
  local profile="" settings="" workdir="" log="" prompt="" until="" \
        label="" interval="300" throttle="30" out_script="" out_plist="" \
        plist_template="$PLIST_TEMPLATE_DEFAULT" claude_bin=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) profile="$2"; shift 2 ;;
      --settings) settings="$2"; shift 2 ;;
      --workdir) workdir="$2"; shift 2 ;;
      --log) log="$2"; shift 2 ;;
      --prompt-file) prompt="$2"; shift 2 ;;
      --until) until="$2"; shift 2 ;;
      --label) label="$2"; shift 2 ;;
      --interval) interval="$2"; shift 2 ;;
      --throttle) throttle="$2"; shift 2 ;;
      --claude-bin) claude_bin="$2"; shift 2 ;;
      --out-script) out_script="$2"; shift 2 ;;
      --out-plist) out_plist="$2"; shift 2 ;;
      --plist-template) plist_template="$2"; shift 2 ;;
      *) die "unknown write-launch argument: $1" ;;
    esac
  done
  [ -n "$out_script" ] && [ -n "$out_plist" ] || die "write-launch requires --out-script and --out-plist"
  [ -n "$label" ] || die "write-launch requires --label"
  # Pin the label to a launchd reverse-DNS charset — belt-and-suspenders against
  # plist injection on top of xml_escape, and it's what launchd expects anyway.
  case "$label" in *[!A-Za-z0-9._-]*) die "--label must be [A-Za-z0-9._-] (fail-closed): $label" ;; esac
  local f
  for f in "$profile" "$settings" "$prompt"; do
    [ -n "$f" ] || die "write-launch requires --profile, --settings, and --prompt-file"
    [ -f "$f" ] || die "not found (fail-closed): $f"
  done
  [ -n "$workdir" ] && [ -d "$workdir" ] || die "write-launch requires an existing --workdir"
  [ -n "$log" ] || die "write-launch requires --log"
  case "$interval$throttle" in *[!0-9]*) die "--interval/--throttle must be integers" ;; esac

  local settings_json; settings_json="$(cat "$settings")"
  # Resolve claude to an ABSOLUTE path: a detached launchd job runs with a minimal
  # PATH (/usr/bin:/bin:/usr/sbin:/sbin) and would not find a Homebrew `claude`.
  # The caller should pass --claude-bin (the same path it gave render-profile's
  # --exec, so the launch invokes exactly the binary the profile permits); default
  # to `command -v claude` for convenience.
  [ -n "$claude_bin" ] || claude_bin="$(command -v claude 2>/dev/null)" || claude_bin=""
  [ -n "$claude_bin" ] || die "claude not found (fail-closed): pass --claude-bin or put claude on PATH"
  case "$claude_bin" in /*) ;; *) die "--claude-bin must be absolute (fail-closed): $claude_bin" ;; esac

  local tmp; tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-launch.XXXXXX")" || die "mktemp failed"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Auto-pilot orchestrator launch script (generated — do not edit).\n'
    printf 'set -uo pipefail\n'
    printf 'export AUTO_PILOT_UNTIL=%q\n' "$until"
    printf 'cd %q\n' "$workdir"
    # exec so the launchd-tracked PID is claude itself, not a wrapper shell.
    printf 'exec sandbox-exec -f %q \\\n' "$profile"
    printf '  %q -p "$(cat %q)" \\\n' "$claude_bin" "$prompt"
    printf '  --permission-mode bypassPermissions \\\n'
    printf '  --settings %q \\\n' "$settings_json"
    printf '  --output-format stream-json \\\n'
    printf '  >>%q 2>&1\n' "$log"
  } >"$tmp" || { rm -f "$tmp"; die "failed to write launch script"; }
  mv "$tmp" "$out_script" || { rm -f "$tmp"; die "failed to write launch script: $out_script"; }
  chmod +x "$out_script"

  [ -f "$plist_template" ] || die "plist template not found: $plist_template"
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-plist.XXXXXX")" || die "mktemp failed"
  render_plist "$label" "$out_script" "$workdir" "$log" "$interval" "$throttle" "$plist_template" >"$tmp" \
    || { rm -f "$tmp"; die "failed to render plist"; }
  mv "$tmp" "$out_plist" || { rm -f "$tmp"; die "failed to write plist: $out_plist"; }
  echo "spawn-orchestrator: launch written $out_script $out_plist"
}

# Record the orchestrator's identity for stale/recycled-PID detection: the PID,
# its process start-time (guards against a recycled PID being mistaken for a live
# run), and the run's --until deadline.
record_handle() {
  local pid="" until="" out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pid) pid="$2"; shift 2 ;;
      --until) until="$2"; shift 2 ;;
      --out) out="$2"; shift 2 ;;
      *) die "unknown record-handle argument: $1" ;;
    esac
  done
  [ -n "$pid" ] && [ -n "$out" ] || die "record-handle requires --pid and --out"
  case "$pid" in *[!0-9]*|"") die "--pid must be numeric: $pid" ;; esac
  local started; started="$(ps -p "$pid" -o lstart= 2>/dev/null)" || true
  [ -n "$started" ] || die "no live process at pid $pid (cannot record a dead handle)"
  {
    printf 'orchestrator_pid: %s\n' "$pid"
    printf 'orchestrator_started_at: %s\n' "$started"
    printf 'until: %s\n' "$until"
  } >"$out" || die "failed to write handle: $out"
  echo "spawn-orchestrator: handle recorded $out"
}

# Auth smoke-test THROUGH the exact wrapper + settings, before detaching, so a
# dead credential fails loudly now rather than silently at 3am. Executes claude.
smoke_test() {
  local profile="" settings=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) profile="$2"; shift 2 ;;
      --settings) settings="$2"; shift 2 ;;
      *) die "unknown smoke-test argument: $1" ;;
    esac
  done
  [ -f "$profile" ] && [ -f "$settings" ] || die "smoke-test requires --profile and --settings files"
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not available (macOS only)"
  local json; json="$(cat "$settings")"
  local claude_bin; claude_bin="$(command -v claude 2>/dev/null)" || die "claude not found on PATH"
  # Match the REAL launch's posture (bypassPermissions + the wrapper + settings) so
  # the smoke validates the same invocation the detached job will run. Assert on
  # observable output, not just $?, so a degraded claude that exits 0 without a live
  # credential can't false-pass the auth gate.
  local out
  out="$(sandbox-exec -f "$profile" "$claude_bin" -p 'ok' --max-turns 1 \
    --permission-mode bypassPermissions --settings "$json" 2>/dev/null)" \
    || die "auth smoke-test failed THROUGH the wrapper — a credential or the jail is wrong; not detaching"
  [ -n "$out" ] || die "auth smoke-test produced no output THROUGH the wrapper — credential/jail suspect; not detaching"
  echo "spawn-orchestrator: smoke-test OK"
}

detach() {
  local plist=""
  while [ $# -gt 0 ]; do
    case "$1" in --plist) plist="$2"; shift 2 ;; *) die "unknown detach argument: $1" ;; esac
  done
  [ -f "$plist" ] || die "detach requires an existing --plist"
  command -v launchctl >/dev/null 2>&1 || die "launchctl not available (macOS only)"
  launchctl bootstrap "gui/$(id -u)" "$plist" || die "launchctl bootstrap failed for $plist"
  echo "spawn-orchestrator: detached (launchd) $plist"
}

teardown() {
  local label=""
  while [ $# -gt 0 ]; do
    case "$1" in --label) label="$2"; shift 2 ;; *) die "unknown teardown argument: $1" ;; esac
  done
  [ -n "$label" ] || die "teardown requires --label"
  command -v launchctl >/dev/null 2>&1 || die "launchctl not available (macOS only)"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  echo "spawn-orchestrator: torn down $label"
}

# Orchestrate the spawn in the ONE safe order: build the launch artifacts, then
# smoke-test auth THROUGH the wrapper, and only if that passes, detach + record.
# The ordering is load-bearing — detaching before auth is verified is exactly the
# "fails silently at 3am" mode the pre-flight exists to prevent. `--dry-run` prints
# the plan without executing (so the order is testable offline).
launch() {
  local dry=0
  local -a wl=() sm=() dt=() rh=()
  local plist="" out_script="" out_plist="" handle="" until="" label=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) dry=1; shift ;;
      --profile) wl+=(--profile "$2"); sm+=(--profile "$2"); shift 2 ;;
      --settings) wl+=(--settings "$2"); sm+=(--settings "$2"); shift 2 ;;
      --workdir|--log|--prompt-file|--interval|--throttle|--plist-template|--claude-bin) wl+=("$1" "$2"); shift 2 ;;
      --until) wl+=(--until "$2"); until="$2"; shift 2 ;;
      --label) wl+=(--label "$2"); label="$2"; shift 2 ;;
      --out-script) wl+=(--out-script "$2"); out_script="$2"; shift 2 ;;
      --out-plist) wl+=(--out-plist "$2"); out_plist="$2"; plist="$2"; shift 2 ;;
      --handle) handle="$2"; shift 2 ;;
      *) die "unknown launch argument: $1" ;;
    esac
  done
  [ -n "$out_plist" ] && [ -n "$label" ] && [ -n "$handle" ] || die "launch requires --out-plist, --label, and --handle"

  if [ "$dry" = 1 ]; then
    printf 'launch plan (order is load-bearing — auth is verified BEFORE we spawn):\n'
    printf '  1. write-launch  -> %s %s\n' "$out_script" "$out_plist"
    printf '  2. smoke-test    (claude -p through the sandbox wrapper + settings)\n'
    printf '  3. detach        (launchctl bootstrap %s)\n' "$plist"
    printf '  4. record-handle -> %s\n' "$handle"
    return 0
  fi

  write_launch "${wl[@]}"
  smoke_test "${sm[@]}"            # BEFORE detach — a dead credential stops here.
  teardown --label "$label" >/dev/null 2>&1   # clear any stale registration; bootstrap fails on a dup label.
  detach --plist "$plist"
  # `launchctl bootstrap` is asynchronous — the pid may not be reported for a beat,
  # so poll briefly rather than reading once (a single read fails closed spuriously).
  local pid="" i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    pid="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | awk -F'= ' '/[^a-z]pid = /{print $2; exit}')"
    case "$pid" in ''|*[!0-9]*) pid="" ;; *) break ;; esac
    sleep 0.2
  done
  if [ -z "$pid" ]; then
    teardown --label "$label" >/dev/null 2>&1   # don't leave an orphaned job loaded
    die "detached but could not read the orchestrator PID from launchctl (job booted out)"
  fi
  record_handle --pid "$pid" --until "$until" --out "$handle"
  echo "spawn-orchestrator: launched pid=$pid label=$label"
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
  write-launch) write_launch "$@" ;;
  record-handle) record_handle "$@" ;;
  smoke-test) smoke_test "$@" ;;
  detach) detach "$@" ;;
  teardown) teardown "$@" ;;
  launch) launch "$@" ;;
  check-profile) check_profile "$@" ;;
  -h|--help) sed -n '2,/^[^#]/{/^#/p;}' "$0"; exit 0 ;;
  *) die "unknown subcommand: $sub" ;;
esac
