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
# Verify broker (task 4 — run the run's verify_command OUTSIDE the jail): the
# jailed orchestrator can't spawn an un-jailed verifier (children inherit the
# seatbelt profile) and `bash scripts/check.sh` execve-denies in-jail (finding
# #4). So write-verify-broker installs a SEPARATE, un-jailed launchd job that
# polls a sentinel dir; the orchestrator hands off with verify-request and reads
# the outcome with verify-await. The broker runs a COMMAND-PINNED verify only,
# in a worktree confined to the run root — see the "Task 4" block below.
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
#       [--mcp-host <host> ...] [--add-task-host <host> ...] [--npm] \
#       --out <file>
#   spawn-orchestrator.sh check-profile <file>
#   spawn-orchestrator.sh status --label <label> [--dir <run-dir>]
#   spawn-orchestrator.sh teardown --label <label> [--done-sentinel <path>]
#
#   status  Read-only: report the run's live state in one shot — the RUN.md
#           run-level `status:`, the per-task phase table, the last
#           meaningful event from `orchestrator.log`, whether the recorded
#           orchestrator PID is actually live (guarding against a recycled
#           PID), the `--until` deadline, and whether the done-sentinel
#           (written by `teardown --done-sentinel`, see below) is present.
#           Never mutates anything. `--dir` defaults to $PWD; state is read
#           from <dir>/.auto-pilot/{RUN.md,orchestrator.log,orchestrator.done}.
#   teardown  Boot the launchd job out (`launchctl bootout`). With
#             `--done-sentinel <path>`, first atomically writes that file as
#             the durable completion marker, THEN boots the job out — so a
#             watcher polling the sentinel never observes "job gone, no
#             done-marker yet". This is the ONE completion mechanism `status`
#             also reads (see launch-runtime.md "Logs / observability").
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
# The done-sentinel filename `status` looks for under <dir>/.auto-pilot/. Callers
# pass the matching absolute path (<dir>/.auto-pilot/orchestrator.done) to
# `teardown --done-sentinel`; the two agree on this one name so it's a single
# completion mechanism, never a second independent marker (launch-runtime.md
# "Logs / observability").
DONE_SENTINEL_NAME="orchestrator.done"

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
  local -a coders=() mcp=() add_task=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --coder) [ $# -ge 2 ] || die "missing value for --coder"; coders+=("$2"); shift 2 ;;
      --source) [ $# -ge 2 ] || die "missing value for --source"; source="$2"; shift 2 ;;
      --agy-host) [ $# -ge 2 ] || die "missing value for --agy-host"; agy_host="$2"; shift 2 ;;
      --mcp-host) [ $# -ge 2 ] || die "missing value for --mcp-host"; mcp+=("$2"); shift 2 ;;
      --add-task-host) [ $# -ge 2 ] || die "missing value for --add-task-host"; add_task+=("$2"); shift 2 ;;
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
  # The add-task destination host is added regardless of --source: a plan-source
  # run whose /add-task handler routes to Linear/Jira still needs egress there, or
  # the run's own settings would deny its own tracked-follow-up filing.
  hosts+=(${add_task[@]+"${add_task[@]}"})

  # Validate EVERY host, fail-closed. Host values from --mcp-host / --agy-host /
  # --add-task-host are resolved per-run from the (possibly adversary-influenced)
  # work source, so an unvalidated value could smuggle a bare `*` (nullifying the
  # allowlist) or inject JSON in hosts_to_json_array. Require a real hostname —
  # labels of [A-Za-z0-9-] separated by dots, with at most a single leading `*.` subdomain wildcard (which
  # `*.githubusercontent.com` legitimately uses; a bare `*` has no labels and is
  # rejected). The agy-specific guard above additionally bans ANY wildcard for agy.
  local h
  for h in "${hosts[@]}"; do
    # Reject an embedded newline FIRST: `grep -Eq` matches if ANY line matches,
    # so a multiline value could pass the anchored regex on one line while
    # hosts_to_json_array splits it into a second, unvalidated host — smuggling a
    # wildcard past the allowlist. A real hostname never contains a newline.
    case "$h" in *$'\n'*) die "invalid egress host (embedded newline, fail-closed): $h" ;; esac
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
        plist_template="$PLIST_TEMPLATE_DEFAULT" claude_bin="" path=""
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
      --path) path="$2"; shift 2 ;;
      --out-script) out_script="$2"; shift 2 ;;
      --out-plist) out_plist="$2"; shift 2 ;;
      --plist-template) plist_template="$2"; shift 2 ;;
      *) die "unknown write-launch argument: $1" ;;
    esac
  done
  [ -n "$out_script" ] && [ -n "$out_plist" ] || die "write-launch requires --out-script and --out-plist"
  [ -n "$path" ] || die "write-launch requires --path <PATH> (fail-closed): a launchd job has a minimal PATH; pass the fingerprint-resolved toolchain dirs"
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
    printf 'export PATH=%q\n' "$path"
    printf 'cd %q\n' "$workdir"
    # exec so the launchd-tracked PID is claude itself, not a wrapper shell.
    printf 'exec sandbox-exec -f %q \\\n' "$profile"
    printf '  %q -p "$(cat %q)" \\\n' "$claude_bin" "$prompt"
    printf '  --permission-mode bypassPermissions \\\n'
    printf '  --settings %q \\\n' "$settings_json"
    printf '  --verbose \\\n'
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
    --permission-mode bypassPermissions --settings "$json" \
    --verbose --output-format stream-json 2>/dev/null)" \
    || die "auth smoke-test failed THROUGH the wrapper — a credential or the jail is wrong; not detaching"
  [ -n "$out" ] || die "auth smoke-test produced no output THROUGH the wrapper — credential/jail suspect; not detaching"
  # Assert the output is actually parseable stream-json (a line beginning with
  # `{` and containing "type"), not just non-empty: a degraded claude could print
  # a plain-text error to stdout and false-pass the emptiness check above.
  printf '%s\n' "$out" | grep -Eq '^\{.*"type"' \
    || die "auth smoke-test produced non-stream-json output THROUGH the wrapper — flag/jail suspect; not detaching"
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
  local label="" done_sentinel=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --done-sentinel) [ $# -ge 2 ] || die "missing value for --done-sentinel"; done_sentinel="$2"; shift 2 ;;
      *) die "unknown teardown argument: $1" ;;
    esac
  done
  [ -n "$label" ] || die "teardown requires --label"

  # Write the done-sentinel FIRST (atomically: tmp + mv), THEN boot the job
  # out — so a watcher polling the sentinel never sees "gone but no
  # done-marker" (the ordering is load-bearing, same reasoning as launch()'s
  # smoke-test-before-detach). This is the single completion mechanism;
  # `status` reads the same file.
  if [ -n "$done_sentinel" ]; then
    case "$done_sentinel" in /*) ;; *) die "--done-sentinel must be absolute (fail-closed): $done_sentinel" ;; esac
    local sdir; sdir="$(dirname "$done_sentinel")"
    mkdir -p "$sdir" || die "failed to create sentinel directory: $sdir"
    # Create the temp file IN the sentinel's own directory so the `mv` below is a
    # same-filesystem atomic rename — a temp under $TMPDIR could be on another
    # filesystem, making `mv` a non-atomic copy that can fail with EXDEV or leave
    # a watcher observing a partial sentinel.
    local tmp; tmp="$(mktemp "$sdir/.orchestrator-done.XXXXXX")" || die "mktemp failed"
    printf '%s done %s\n' "$label" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$tmp" \
      || { rm -f "$tmp"; die "failed to write done sentinel"; }
    mv "$tmp" "$done_sentinel" || { rm -f "$tmp"; die "failed to write done sentinel: $done_sentinel"; }
  fi

  # launchctl may genuinely be absent (non-macOS, or the in-jail test harness) —
  # that must not skip the sentinel write above; the sentinel is authoritative.
  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  fi
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
      --workdir|--log|--prompt-file|--interval|--throttle|--plist-template|--claude-bin|--path) wl+=("$1" "$2"); shift 2 ;;
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

# ---------------------------------------------------------------------------
# Task 4 — verify broker: run the run's verify_command OUTSIDE the jail.
#
# A sandbox-exec-confined process's children INHERIT the profile, so the jailed
# orchestrator cannot spawn an un-jailed verifier — and the run's verify
# (`bash scripts/check.sh`, which runs `bash scripts/test-*.sh`) execve-denies
# in-jail (finding #4: `bad interpreter: Operation not permitted`, exit 126,
# regardless of the diff). Verify therefore runs in a SEPARATE, UN-JAILED launchd
# job (write-verify-broker) that polls a sentinel dir and runs a COMMAND-PINNED
# verify in the requested worktree:
#
#   jailed orchestrator             un-jailed broker (launchd, NO sandbox-exec)
#   -------------------             ------------------------------------------
#   verify-request  ── writes ──▶   <dir>/<id>.request {worktree, cmd_hash}
#                                   verify-broker: refuse unless the request's
#                                   cmd_hash == the broker's OWN pinned hash AND
#                                   the worktree is under the run root, then run
#                                   its OWN pinned command there and write
#   verify-await   ◀── reads ───    <dir>/<id>.result  {code, output}
#
# Trust boundary (launch-runtime.md "Sandbox profile"): verify runs un-jailed, so
# it executes the diff-under-test with full privilege + network — the SAME trust
# the human extends re-running check.sh before merge, moved earlier. It is bounded
# by (a) command PINNING — the broker runs a FIXED string baked in at install,
# NEVER a command from the request; the request carries only a hash both sides must
# agree on — and (b) worktree-only execution — the broker refuses a worktree
# outside the run root. Neither the request nor an agent ever supplies the command.
# ---------------------------------------------------------------------------

# Pin hash of a verify command string (sha256 hex). Request and broker compute it
# identically, so a stale request or a re-pinned broker is caught before any run.
verify_hash() {
  command -v shasum >/dev/null 2>&1 || die "shasum not available (needed for verify pin)"
  printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
}

# Atomically write a result file: `code:` on line 1, then the raw output after a
# fixed marker so verify-await can split it back out losslessly.
_write_verify_result() {
  local f="$1" code="$2" out="$3" d; d="$(dirname "$f")"
  local tmp; tmp="$(mktemp "$d/.res.XXXXXX")" || return 1
  { printf 'code: %s\n' "$code"; printf -- '--- output ---\n'; printf '%s\n' "$out"; } >"$tmp" \
    || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$f" || { rm -f "$tmp"; return 1; }
}

# (jailed side) Drop a verify request the un-jailed broker will pick up. The
# request names the worktree + a hash of the pinned command — never the command.
verify_request() {
  local dir="" worktree="" cmd_hash="" id=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --worktree) [ $# -ge 2 ] || die "missing value for --worktree"; worktree="$2"; shift 2 ;;
      --cmd-hash) [ $# -ge 2 ] || die "missing value for --cmd-hash"; cmd_hash="$2"; shift 2 ;;
      --id) [ $# -ge 2 ] || die "missing value for --id"; id="$2"; shift 2 ;;
      *) die "unknown verify-request argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$worktree" ] && [ -n "$cmd_hash" ] \
    || die "verify-request requires --sentinel-dir, --worktree, and --cmd-hash"
  case "$cmd_hash" in *[!0-9a-f]*|"") die "--cmd-hash must be lowercase hex (fail-closed): $cmd_hash" ;; esac
  local wt; wt="$(canonicalize "$worktree")" || exit 2
  [ -d "$wt" ] || die "verify-request --worktree is not a directory (fail-closed): $wt"
  mkdir -p "$dir" || die "cannot create sentinel dir: $dir"
  local d; d="$(cd "$dir" && pwd -P)" || die "cannot resolve sentinel dir: $dir"
  if [ -n "$id" ]; then
    case "$id" in *[!A-Za-z0-9._-]*) die "--id must be [A-Za-z0-9._-] (fail-closed): $id" ;; esac
  else
    local t; t="$(mktemp "$d/req.XXXXXX")" || die "mktemp failed"; id="$(basename "$t")"; rm -f "$t"
  fi
  local reqfile="$d/$id.request" tmp
  tmp="$(mktemp "$d/.req.XXXXXX")" || die "mktemp failed"
  { printf 'worktree: %s\n' "$wt"; printf 'cmd_hash: %s\n' "$cmd_hash"; } >"$tmp" \
    || { rm -f "$tmp"; die "cannot write request"; }
  mv "$tmp" "$reqfile" || { rm -f "$tmp"; die "cannot place request: $reqfile"; }
  echo "spawn-orchestrator: verify-request $id"
}

# (UN-JAILED side, run by the broker launchd job) One scan: for each pending
# request, refuse unless its cmd_hash matches the broker's OWN pinned hash and the
# worktree is under the run root, then run the PINNED command there and write the
# result. Scans once and exits — the launchd StartInterval drives the cadence.
verify_broker() {
  local dir="" cmd="" cmd_hash="" root=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --verify-cmd) [ $# -ge 2 ] || die "missing value for --verify-cmd"; cmd="$2"; shift 2 ;;
      --cmd-hash) [ $# -ge 2 ] || die "missing value for --cmd-hash"; cmd_hash="$2"; shift 2 ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; root="$2"; shift 2 ;;
      *) die "unknown verify-broker argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$cmd" ] && [ -n "$root" ] \
    || die "verify-broker requires --sentinel-dir, --verify-cmd, and --confine-under"
  # The broker runs its OWN pinned command; --cmd-hash (if given) must match it —
  # this catches an install/args mismatch, never authorizes a request's command.
  local pin; pin="$(verify_hash "$cmd")"
  if [ -n "$cmd_hash" ] && [ "$cmd_hash" != "$pin" ]; then
    die "verify-broker --cmd-hash does not match --verify-cmd (fail-closed)"
  fi
  local rootc; rootc="$(canonicalize "$root")" || exit 2
  local d; d="$(cd "$dir" 2>/dev/null && pwd -P)" || die "sentinel dir not found: $dir"

  local req handled=0
  for req in "$d"/*.request; do
    [ -e "$req" ] || continue
    handled=$((handled + 1))
    local id; id="$(basename "$req" .request)"
    local resultf="$d/$id.result"
    local wt rh
    wt="$(sed -n 's/^worktree: //p' "$req" | head -1)"
    rh="$(sed -n 's/^cmd_hash: //p' "$req" | head -1)"
    rm -f "$req"    # claim it — a handled request never re-runs
    if [ "$rh" != "$pin" ]; then
      _write_verify_result "$resultf" 2 "verify-broker: request cmd_hash mismatch (pinned=$pin request=$rh) — refused"
      continue
    fi
    local wtc; wtc="$(cd "$wt" 2>/dev/null && pwd -P)"
    if [ -z "$wtc" ] || { [ "$wtc" != "$rootc" ] && [ "${wtc#"$rootc"/}" = "$wtc" ]; }; then
      _write_verify_result "$resultf" 2 "verify-broker: worktree escapes --confine-under (worktree=$wt root=$rootc) — refused"
      continue
    fi
    # Run the PINNED command in the worktree. `bash -c "$cmd"` on a trusted, pinned
    # string is the same execution the human runs re-invoking check.sh pre-merge.
    local out code
    out="$(cd "$wtc" && bash -c "$cmd" 2>&1)"; code=$?
    _write_verify_result "$resultf" "$code" "$out"
  done
  echo "spawn-orchestrator: verify-broker scanned $d (handled=$handled, pin=$pin)"
}

# (jailed side) Block until the broker writes the result for <id>, then print its
# code + output and exit with the verify command's code (0 = verify passed).
verify_await() {
  local dir="" id="" timeout=600 interval=2
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --id) [ $# -ge 2 ] || die "missing value for --id"; id="$2"; shift 2 ;;
      --timeout) [ $# -ge 2 ] || die "missing value for --timeout"; timeout="$2"; shift 2 ;;
      --interval) [ $# -ge 2 ] || die "missing value for --interval"; interval="$2"; shift 2 ;;
      *) die "unknown verify-await argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$id" ] || die "verify-await requires --sentinel-dir and --id"
  case "$timeout$interval" in *[!0-9]*) die "--timeout/--interval must be integers" ;; esac
  [ "$interval" -gt 0 ] || die "--interval must be > 0 (else the wait loop never advances)"
  local d; d="$(cd "$dir" 2>/dev/null && pwd -P)" || die "sentinel dir not found: $dir"
  local resultf="$d/$id.result" waited=0
  while [ ! -e "$resultf" ]; do
    [ "$waited" -lt "$timeout" ] || die "verify-await timed out after ${timeout}s waiting for $id (broker down?)"
    sleep "$interval"; waited=$((waited + interval))
  done
  local code; code="$(sed -n 's/^code: //p' "$resultf" | head -1)"
  echo "spawn-orchestrator: verify-await $id code=$code"
  sed -n '/^--- output ---$/,$p' "$resultf" | sed '1d'
  case "$code" in ''|*[!0-9]*) return 2 ;; *) return "$code" ;; esac
}

# Render an UN-JAILED broker launch script + its launchd plist. The verify command
# is PINNED here (baked into the script from the run's resolved verify_command), so
# the broker never runs anything a request or an agent supplies. The broker job runs
# `/bin/bash <script>` directly — NO sandbox-exec — which is exactly how it escapes
# the orchestrator's jail to reach a working execve.
write_verify_broker() {
  local sentinel="" verify_cmd="" root="" label="" workdir="" log="" path="" \
        self="$ROOT/scripts/spawn-orchestrator.sh" interval="10" throttle="10" \
        out_script="" out_plist="" plist_template="$PLIST_TEMPLATE_DEFAULT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; sentinel="$2"; shift 2 ;;
      --verify-cmd) [ $# -ge 2 ] || die "missing value for --verify-cmd"; verify_cmd="$2"; shift 2 ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; root="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --workdir) [ $# -ge 2 ] || die "missing value for --workdir"; workdir="$2"; shift 2 ;;
      --log) [ $# -ge 2 ] || die "missing value for --log"; log="$2"; shift 2 ;;
      --path) [ $# -ge 2 ] || die "missing value for --path"; path="$2"; shift 2 ;;
      --self) [ $# -ge 2 ] || die "missing value for --self"; self="$2"; shift 2 ;;
      --interval) [ $# -ge 2 ] || die "missing value for --interval"; interval="$2"; shift 2 ;;
      --throttle) [ $# -ge 2 ] || die "missing value for --throttle"; throttle="$2"; shift 2 ;;
      --out-script) [ $# -ge 2 ] || die "missing value for --out-script"; out_script="$2"; shift 2 ;;
      --out-plist) [ $# -ge 2 ] || die "missing value for --out-plist"; out_plist="$2"; shift 2 ;;
      --plist-template) [ $# -ge 2 ] || die "missing value for --plist-template"; plist_template="$2"; shift 2 ;;
      *) die "unknown write-verify-broker argument: $1" ;;
    esac
  done
  [ -n "$out_script" ] && [ -n "$out_plist" ] || die "write-verify-broker requires --out-script and --out-plist"
  [ -n "$sentinel" ] && [ -n "$verify_cmd" ] && [ -n "$root" ] \
    || die "write-verify-broker requires --sentinel-dir, --verify-cmd, and --confine-under"
  [ -n "$label" ] || die "write-verify-broker requires --label"
  case "$label" in *[!A-Za-z0-9._-]*) die "--label must be [A-Za-z0-9._-] (fail-closed): $label" ;; esac
  [ -n "$path" ] || die "write-verify-broker requires --path <PATH> (a launchd job has a minimal PATH)"
  [ -n "$workdir" ] && [ -d "$workdir" ] || die "write-verify-broker requires an existing --workdir"
  [ -n "$log" ] || die "write-verify-broker requires --log"
  [ -f "$self" ] || die "spawn-orchestrator.sh not found (fail-closed): $self"
  [ -f "$plist_template" ] || die "plist template not found: $plist_template"
  case "$interval$throttle" in *[!0-9]*) die "--interval/--throttle must be integers" ;; esac

  local pin; pin="$(verify_hash "$verify_cmd")"
  local tmp; tmp="$(mktemp "${TMPDIR:-/tmp}/verify-broker-launch.XXXXXX")" || die "mktemp failed"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Auto-pilot verify BROKER (generated — do not edit). Runs UN-JAILED so\n'
    printf '# the run verify (bash scripts/check.sh) reaches a working execve. The\n'
    printf '# verify command below is PINNED (pin=%s); the broker never runs a\n' "$pin"
    printf '# command supplied by a request or an agent.\n'
    printf 'set -uo pipefail\n'
    printf 'export PATH=%q\n' "$path"
    printf 'cd %q\n' "$workdir"
    printf 'exec /bin/bash %q verify-broker \\\n' "$self"
    printf '  --sentinel-dir %q \\\n' "$sentinel"
    printf '  --verify-cmd %q \\\n' "$verify_cmd"
    printf '  --cmd-hash %q \\\n' "$pin"
    printf '  --confine-under %q \\\n' "$root"
    printf '  >>%q 2>&1\n' "$log"
  } >"$tmp" || { rm -f "$tmp"; die "failed to write broker launch script"; }
  mv "$tmp" "$out_script" || { rm -f "$tmp"; die "failed to write broker launch script: $out_script"; }
  chmod +x "$out_script"

  tmp="$(mktemp "${TMPDIR:-/tmp}/verify-broker-plist.XXXXXX")" || die "mktemp failed"
  render_plist "$label" "$out_script" "$workdir" "$log" "$interval" "$throttle" "$plist_template" >"$tmp" \
    || { rm -f "$tmp"; die "failed to render broker plist"; }
  mv "$tmp" "$out_plist" || { rm -f "$tmp"; die "failed to write broker plist: $out_plist"; }
  echo "spawn-orchestrator: verify-broker written $out_script $out_plist (pin $pin)"
}

# Normalize whitespace (collapse runs, trim ends) so a recorded lstart string
# and a freshly-read one compare equal regardless of incidental spacing.
_norm_ws() { printf '%s' "$1" | awk '{$1=$1; print}'; }

# Pull a single top-level "key: value" field out of a captured front-matter
# block (global $front, set by status() before calling this). Anchors on
# `^key:` so e.g. "until:" never matches "paused_until:". Strips a trailing
# ` # comment`, trailing whitespace, then a wrapping pair of single or double
# quotes.
_front_field() {
  local key="$1"
  printf '%s\n' "$front" | grep -E "^${key}:" | head -1 \
    | sed -e "s/^${key}: *//" -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//' \
          -e "s/^'\(.*\)'\$/\1/" -e 's/^"\(.*\)"$/\1/'
}

# Read-only: report the run's live state in one shot. Never writes anything.
status() {
  local label="" dir="$PWD"
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      *) die "unknown status argument: $1" ;;
    esac
  done
  [ -n "$label" ] || die "status requires --label"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac

  local run_md="$dir/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || die "no run state found (fail-closed): $run_md"
  local log="$dir/.auto-pilot/orchestrator.log"
  local sentinel="$dir/.auto-pilot/$DONE_SENTINEL_NAME"

  # Front matter is the block between the first two `---` lines. No YAML tool
  # required — plain awk/sed line matching.
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"

  local run_status until_val orch_pid orch_started
  run_status="$(_front_field status)"; [ -n "$run_status" ] || run_status="unknown"
  until_val="$(_front_field until)"
  orch_pid="$(_front_field orchestrator_pid)"
  orch_started="$(_front_field orchestrator_started_at)"

  # Per-task phase table: every `| ... |` line after the front matter.
  local table; table="$(awk '/^\|/{print}' "$run_md")"
  local task_rows task_count
  task_rows="$(printf '%s\n' "$table" | grep -Ev '^\| *:?-+:? *(\| *:?-+:? *)*\|?$' || true)"
  if [ -n "$task_rows" ]; then
    task_count=$(( $(printf '%s\n' "$task_rows" | wc -l | tr -d ' ') - 1 )) # minus header row
    [ "$task_count" -ge 0 ] || task_count=0
  else
    task_count=0
  fi

  # Last meaningful event from the stream-json orchestrator.log: the last line
  # that looks like an assistant or tool event, else just the last line.
  # Degrades gracefully if the log is absent/empty.
  local last_event=""
  if [ -s "$log" ]; then
    last_event="$(grep -E '"type":"(assistant|tool_use|tool_result)"' "$log" 2>/dev/null | tail -1)"
    [ -n "$last_event" ] || last_event="$(tail -1 "$log")"
    last_event="$(printf '%s' "$last_event" | cut -c1-240)"
  fi

  # PID liveness: live only if the PID is alive AND its process start-time
  # matches the recorded one (guards a recycled PID) — dead / mismatch / none.
  local pid_state="none"
  if [ -n "$orch_pid" ]; then
    if kill -0 "$orch_pid" 2>/dev/null; then
      local actual_started; actual_started="$(ps -o lstart= -p "$orch_pid" 2>/dev/null)"
      if [ -n "$actual_started" ] && [ "$(_norm_ws "$actual_started")" = "$(_norm_ws "$orch_started")" ]; then
        pid_state="live"
      else
        pid_state="mismatch"
      fi
    else
      pid_state="dead"
    fi
  fi

  # Done-sentinel (written by `teardown --done-sentinel`) is the single source
  # of "run is done" — it can mark the run done even before RUN.md's own
  # `status:` field is committed as `done`.
  local sentinel_done="no" run_status_display="$run_status"
  [ -f "$sentinel" ] && { sentinel_done="yes"; run_status_display="done"; }

  echo "run: $label"
  echo "dir: $dir"
  echo "status: $run_status_display (front-matter: $run_status; done-sentinel: $sentinel_done)"
  echo
  echo "tasks:"
  if [ -n "$table" ]; then
    printf '%s\n' "$table"
  else
    echo "  (no task table found in RUN.md)"
  fi
  echo
  echo "log: ${last_event:-(orchestrator.log absent, empty, or no assistant/tool events found)}"
  echo "pid: $pid_state (pid=${orch_pid:-none} started=\"${orch_started:-none}\")"
  echo "until: ${until_val:-(none)}"
  echo "STATUS: $run_status_display pid=$pid_state tasks=$task_count until=${until_val:-none}"
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
  write-verify-broker) write_verify_broker "$@" ;;
  verify-request) verify_request "$@" ;;
  verify-broker) verify_broker "$@" ;;
  verify-await) verify_await "$@" ;;
  check-profile) check_profile "$@" ;;
  status) status "$@" ;;
  -h|--help) sed -n '2,/^[^#]/{/^#/p;}' "$0"; exit 0 ;;
  *) die "unknown subcommand: $sub" ;;
esac
