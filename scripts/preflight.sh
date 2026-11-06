#!/usr/bin/env bash
# preflight.sh — READ-ONLY go/no-go pre-flight for an auto-pilot launch.
#
# Extracts the ad-hoc auth/env probing that skills/auto-pilot/SKILL.md "Step 2
# — Non-interactive auth probes" used to describe inline (it names these as
# "good candidates to extract into a small pre-flight helper script"), and
# composes the existing probes rather than re-implementing them:
#   - scripts/probe-coders.sh        coder CLI availability/auth
#   - scripts/preflight-freshness.sh base-branch freshness vs its remote
#   - scripts/nono-install.sh        the pinned nono the jail runs under
#   - scripts/smoke-nono.sh          the confinement smoke, through that nono
#   - scripts/spawn-orchestrator.sh  render-nono-profile, reused for the
#                                    consent-gate probe
#
# Never mutates git or filesystem state outside private scratch dirs it
# creates and removes for the smoke and the consent probe.
#
# Usage:
#   scripts/preflight.sh --source <plan|linear|gh-issue> --run-root <path>
#                        [--base <branch>] [--claude-token-cmd '<cmd>']
#   scripts/preflight.sh --scout-run-md <path to RUN.md>
#
#   --source        Task-graph source the run reads from. Required unless
#                    --scout-run-md is given.
#   --base          Base branch to check for staleness. Default: main.
#   --claude-token-cmd
#                    The command the launch will hand to write-launch to
#                    supply CLAUDE_CODE_OAUTH_TOKEN. The nono smoke runs it the
#                    same way and proves the credential file stays outside the
#                    jail. Required on macOS, where nono denies the Keychain
#                    that holds the /login credential (a blocker without it).
#                    Omitted on Linux, the smoke runs the documented fallback
#                    (~/.claude writable in the jail).
#   --run-root      The checkout the detached run will work in — the run
#                    worktree launch step 1 created. REQUIRED: the consent-gate
#                    probe tests THIS path and its --git-common-dir, and a
#                    default would fail OPEN, certifying whatever tree the
#                    caller stood in. The installed plugin's own directory is
#                    never where the run's files are. A value that is not a git
#                    checkout is fatal, not a skip.
#   --scout-run-md   Run ONLY the per-task capability-join scout (auto-pilot
#                    launch step 6 / resume's capability join) against the
#                    given RUN.md: read each task's `coder` column, probe
#                    `command -v` per distinct backend, and — only when any
#                    task's backend is `cao` — run the CAO gate (`cao`,
#                    `cao-run`, `cao-server` on PATH; `nc -z localhost 9889`;
#                    every `cao_coder_mapping` key in the fixed CAO fleet
#                    `codex agy`). `opus` is a native subagent and is never
#                    probed. Mutually exclusive with --source.
#
# Output (default mode): parseable `PREFLIGHT <KEY>: <val>` lines on stdout,
# one key per line, ending in a single `PREFLIGHT VERDICT: go` / `PREFLIGHT
# VERDICT: no-go — <reason>` line.
#
# Output (--scout-run-md): one `BLOCKS LAUNCH: <task> -> <backend> (missing)`
# line per gap, ending in a single `SCOUT VERDICT: go` / `SCOUT VERDICT:
# no-go — <reason>` line — the same shape as PREFLIGHT VERDICT above.
#
# Exit status:
#   0  go       — no hard blocker found
#   1  no-go    — at least one hard blocker (see PREFLIGHT/BLOCKS LAUNCH lines)
#   2  usage or dependency error
#
# Consent-gate probe (default mode, macOS): the detached run is spawned by
# launchd, which makes the job's own program — not the terminal — the process
# macOS attributes TCC grants to, so the grants the user gave Terminal do not
# carry. The probe re-runs the entry path under that real attribution (a
# transient launchd job whose program is /bin/bash, as production's is, stdin on
# /dev/null, no controlling TTY, through a rendered nono profile and the pinned
# nono) and blocks launch on a denial.
#
# What it covers, exactly: FILESYSTEM consent — TCC folder access and Full Disk
# Access — against the run's own paths. The other interactive gate classes
# launch-runtime.md §3 names (Keychain, biometric, browser OAuth) are NOT
# exercised here; they are covered only by the credential probes in section 1
# above, which run under this terminal's attribution rather than launchd's.
# That is a known gap, not a silent one.
#
# Env overrides (for tests only — never needed in normal use):
#   PREFLIGHT_PROBE_CODERS  path to a probe-coders.sh-compatible executable.
#   PREFLIGHT_NC            path to an `nc`-compatible executable (scout's
#                            CAO port probe).
#   PREFLIGHT_LAUNCHCTL     path to a `launchctl`-compatible executable
#                            (consent-gate probe's bootstrap/bootout).
#   PREFLIGHT_TCC_LOCATIONS colon-separated protected-location list the
#                            consent-gate probe checks the run's paths against.
#   PREFLIGHT_CONSENT_TICKS how many 0.25s ticks to wait for the probe job.
#   PREFLIGHT_OS            the host OS (uname -s) the launcher, smoke and
#                            consent checks branch on.
#   PREFLIGHT_NONO_SMOKE    path to a smoke-nono.sh-compatible executable.
set -uo pipefail
# xml_escape's replacements emit a literal `&`, which patsub_replacement (on by
# default in bash 5.2+) would expand to the matched text — rendering `&amp;` as
# `&<the match>amp;` and breaking the plist. Quoting the replacement is not the
# fix; bash 3.2 keeps the quotes literally.
shopt -u patsub_replacement 2>/dev/null || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBE_CODERS="${PREFLIGHT_PROBE_CODERS:-$ROOT/scripts/probe-coders.sh}"
FRESHNESS="$ROOT/scripts/preflight-freshness.sh"
SPAWN="$ROOT/scripts/spawn-orchestrator.sh"
FINGERPRINT_BINS="claude git gh codex uv node op"
NC_BIN="${PREFLIGHT_NC:-nc}"
CAO_FLEET="codex agy"
LAUNCHCTL_BIN="${PREFLIGHT_LAUNCHCTL:-launchctl}"
SYSTEMD_RUN_BIN="${PREFLIGHT_SYSTEMD_RUN:-systemd-run}"
SYSTEMCTL_BIN="${PREFLIGHT_SYSTEMCTL:-systemctl}"
LOGINCTL_BIN="${PREFLIGHT_LOGINCTL:-loginctl}"
CONSENT_TIMEOUT_TICKS="${PREFLIGHT_CONSENT_TICKS:-120}" # 120 × 0.25s = 30s
# The macOS TCC-protected home locations. Removable and network volumes are the
# other gated class, but they are named by the run's own paths, not by a list.
# Measured on macOS 26 (2026-09-20) under launchd attribution, through the
# rendered profile: Documents, Desktop, Downloads and iCloud Drive are denied
# while the same reads succeed from a terminal. ~/Pictures, ~/Movies and ~/Music
# are NOT folder-gated (the Photos *library* is a separate app-scoped
# permission, not a path a checkout lives under), so they are deliberately out —
# adding them would be three inventory entries that can never fire.
TCC_LOCATIONS="${PREFLIGHT_TCC_LOCATIONS:-$HOME/Documents:$HOME/Desktop:$HOME/Downloads:$HOME/Library/Mobile Documents}"

die() {
  echo "preflight: $*" >&2
  exit 2
}

# --- Scout: per-task capability join (--scout-run-md) -----------------------
# Reads RUN.md's task table's `coder` column (by header name, not position —
# one home for the probe list regardless of column order) and the front
# matter's `cao_coder_mapping`, then joins each task's declared backend
# against this environment. Mirrors
# skills/auto-pilot/references/run-state.md "RUN.md" table shape and the
# `_restack_read_run_md` parsing conventions in spawn-orchestrator.sh
# (blank/`-`/`—` cells are empty; the separator row is pipes/colons/dashes/
# spaces only).
run_scout() {
  local run_md="$1"
  [ -f "$run_md" ] || die "RUN.md not found: $run_md"

  local front cao_map
  front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
  cao_map="$(printf '%s\n' "$front" | sed -n 's/^cao_coder_mapping:[[:space:]]*//p' | head -1)"

  local header
  header="$(awk '/^\|/{print; exit}' "$run_md")"

  local coder_idx=0 i=0 cell
  local -a hcols=()
  if [ -n "$header" ]; then
    IFS='|' read -ra hcols <<<"$header"
    for cell in "${hcols[@]}"; do
      cell="$(printf '%s' "$cell" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')"
      [ "$cell" = "coder" ] && coder_idx=$i
      i=$((i + 1))
    done
  fi

  # Fail closed on a missing `coder` column: coder_idx stays 0 whether the
  # header carries no such column or there's no header at all, and 0 is
  # never a legitimate index (position 0 is the empty cell before the first
  # `|`) — so this can't be confused with a task's per-cell "not yet
  # resolved" empty marker, which is handled separately below.
  if [ "$coder_idx" -eq 0 ]; then
    echo "BLOCKS LAUNCH: RUN.md task table has no coder column"
    echo "SCOUT VERDICT: no-go — RUN.md task table has no coder column"
    return 1
  fi

  local -a distinct_backends=() backend_tasks=() cao_tasks=()
  local cao_needed=false

  if [ "$coder_idx" -gt 0 ]; then
    local line
    while IFS= read -r line; do
      case "$line" in *[!'|'' ':-]*) ;; *) continue ;; esac # separator row
      local -a cols=()
      IFS='|' read -ra cols <<<"$line"
      [ "${#cols[@]}" -gt "$coder_idx" ] || continue
      local task backend
      task="$(printf '%s' "${cols[1]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      [ "$task" != "task" ] || continue # header row
      [ -n "$task" ] || continue
      backend="$(printf '%s' "${cols[$coder_idx]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      case "$backend" in
        '' | - | '—') continue ;; # not yet resolved — nothing to check
        opus) continue ;;         # native subagent, never probed
        cao)
          cao_needed=true
          cao_tasks+=("$task")
          continue
          ;;
      esac
      local found=false j=0 existing
      for existing in ${distinct_backends[@]+"${distinct_backends[@]}"}; do
        if [ "$existing" = "$backend" ]; then
          backend_tasks[$j]="${backend_tasks[$j]},${task}"
          found=true
          break
        fi
        j=$((j + 1))
      done
      if ! $found; then
        distinct_backends+=("$backend")
        backend_tasks+=("$task")
      fi
    done < <(awk '/^\|/{print}' "$run_md")
  fi

  local -a gaps=()
  local k=0 backend
  for backend in ${distinct_backends[@]+"${distinct_backends[@]}"}; do
    if ! command -v "$backend" >/dev/null 2>&1; then
      local t
      local -a tarr=()
      IFS=',' read -ra tarr <<<"${backend_tasks[$k]}"
      for t in "${tarr[@]}"; do
        gaps+=("$t -> $backend (missing)")
      done
    fi
    k=$((k + 1))
  done

  if $cao_needed; then
    local cao_ok=true b
    for b in cao cao-run cao-server; do
      command -v "$b" >/dev/null 2>&1 || cao_ok=false
    done
    if $cao_ok; then
      "$NC_BIN" -z localhost 9889 >/dev/null 2>&1 || cao_ok=false
    fi
    if $cao_ok; then
      local map_body key pair
      map_body="$(printf '%s' "$cao_map" | sed -e 's/^{//' -e 's/}$//')"
      if [ -n "$map_body" ]; then
        local -a pairs=()
        IFS=',' read -ra pairs <<<"$map_body"
        for pair in "${pairs[@]}"; do
          key="$(printf '%s' "${pair%%:*}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
          [ -n "$key" ] || continue
          case " $CAO_FLEET " in
            *" $key "*) ;;
            *) cao_ok=false ;;
          esac
        done
      fi
    fi
    if ! $cao_ok; then
      local t
      for t in "${cao_tasks[@]}"; do
        gaps+=("$t -> cao (missing)")
      done
    fi
  fi

  if [ "${#gaps[@]}" -gt 0 ]; then
    local g
    for g in "${gaps[@]}"; do
      echo "BLOCKS LAUNCH: $g"
    done
    echo "SCOUT VERDICT: no-go — ${gaps[0]}"
    return 1
  fi
  echo "SCOUT VERDICT: go"
  return 0
}

source_arg=""
base="main"
scout_run_md=""
run_root_arg=""
token_cmd=""
while [ $# -gt 0 ]; do
  case "$1" in
    --claude-token-cmd)
      [ $# -ge 2 ] || die "missing value for --claude-token-cmd"
      token_cmd="$2"
      shift 2
      ;;
    --run-root)
      [ $# -ge 2 ] || die "missing value for --run-root"
      run_root_arg="$2"
      shift 2
      ;;
    --source)
      [ $# -ge 2 ] || die "missing value for --source"
      source_arg="$2"
      shift 2
      ;;
    --base)
      [ $# -ge 2 ] || die "missing value for --base"
      base="$2"
      shift 2
      ;;
    --scout-run-md)
      [ $# -ge 2 ] || die "missing value for --scout-run-md"
      scout_run_md="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,85p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [ -n "$scout_run_md" ]; then
  [ -z "$source_arg" ] || die "--source and --scout-run-md are mutually exclusive"
  run_scout "$scout_run_md"
  exit $?
fi

case "$source_arg" in
  plan | linear | gh-issue) ;;
  "") die "requires --source <plan|linear|gh-issue>" ;;
  *) die "unknown --source (fail-closed): $source_arg" ;;
esac

# The run root decides which paths the consent-gate probe tests (section 5), and
# it is REQUIRED rather than defaulted. Omitting `--source` fails closed;
# a `--run-root` that quietly fell back to `$PWD` would fail **open** —
# certifying whatever tree the caller happened to be standing in, which is the
# defect class the flag exists to remove. `$ROOT` cannot stand in for it either:
# in production this script runs from the installed plugin directory.
#
# Two paths come out of it. A linked worktree under ~/src/worktrees still reads
# and writes its main checkout's `.git` on every git operation, so a probe that
# tests only the worktree passes tonight and dies on the first fetch at 3am.
#
# Validated here, beside `--source`, rather than in section 5: an argument error
# should cost a usage message, not a full run of the auth probes, the network
# freshness check and the confinement smoke before exiting from mid-stream.
[ -n "$run_root_arg" ] || die "requires --run-root <the run's worktree> — the checkout the detached run will work in"
run_toplevel="$(git -C "$run_root_arg" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$run_toplevel" ] || die "--run-root is not a git checkout (fail-closed): $run_root_arg"
run_gitdir="$(git -C "$run_root_arg" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"

[ -f "$PROBE_CODERS" ] || die "coder probe not found: $PROBE_CODERS"
[ -f "$FRESHNESS" ] || die "not found: $FRESHNESS"
[ -f "$SPAWN" ] || die "not found: $SPAWN"

blockers=()
skip_notes=()

# --- 1. Auth/env probes --------------------------------------------------

gh_ok=false
if command -v gh >/dev/null 2>&1; then
  gh auth status >/dev/null 2>&1 && gh_ok=true
else
  blockers+=("gh is not on PATH — install the GitHub CLI or confirm the GitHub MCP is connected instead")
fi
echo "PREFLIGHT GH_AUTH: $gh_ok"
if command -v gh >/dev/null 2>&1 && ! $gh_ok; then
  blockers+=("gh auth status failed — run: gh auth login")
fi

viewer_perm=""
if $gh_ok; then
  viewer_perm="$(gh repo view --json viewerPermission --jq .viewerPermission 2>/dev/null)"
fi
echo "PREFLIGHT VIEWER_PERMISSION: ${viewer_perm:-unknown}"

# Binary fingerprint: absolute paths of the CLIs the run depends on, and the
# unique dirnames those paths resolve to (feeds PATH_DIR / EXEC_DIR below).
resolved_dirs=""
codex_path=""
for bin in $FINGERPRINT_BINS; do
  p="$(command -v "$bin" 2>/dev/null || true)"
  echo "PREFLIGHT BIN $bin: ${p:-absent}"
  if [ -n "$p" ]; then
    [ "$bin" = "codex" ] && codex_path="$p"
    d="$(cd "$(dirname "$p")" && pwd -P)" || continue
    resolved_dirs="$resolved_dirs
$d"
  fi
done
uniq_dirs="$(printf '%s\n' "$resolved_dirs" | sed '/^$/d' | sort -u)"

env_class="claude-web"
[ -n "$codex_path" ] && env_class="local-full"
echo "PREFLIGHT ENV_CLASS: $env_class"

coder_out="$(bash "$PROBE_CODERS" 2>&1)"
coder_status=$?
if [ "$coder_status" != 0 ]; then
  blockers+=("coder probe failed (exit $coder_status) — cannot confirm coder availability/auth; rerun: $PROBE_CODERS")
fi
yaml_field() { # <section> <key>
  printf '%s\n' "$coder_out" | awk -v s="  $1:" -v k="$2:" '
    $0==s {insec=1; next}
    insec && /^  [a-zA-Z0-9_]+:/ {insec=0}
    insec {
      line=$0
      gsub(/^[ \t]+/, "", line)
      if (index(line, k)==1) {
        sub(k, "", line); sub(/ *#.*/, "", line); gsub(/^ +| +$/, "", line); print line; exit
      }
    }
  '
}
codex_installed="$(yaml_field codex installed)"
codex_model="$(yaml_field codex default_model)"
agy_installed="$(yaml_field agy installed)"
agy_logged_in="$(yaml_field agy logged_in)"
devin_installed="$(yaml_field devin installed)"
devin_logged_in="$(yaml_field devin logged_in)"
echo "PREFLIGHT CODER codex: installed=${codex_installed:-unknown} default_model=${codex_model:-unknown}"
echo "PREFLIGHT CODER agy: installed=${agy_installed:-unknown} logged_in=${agy_logged_in:-unknown}"
echo "PREFLIGHT CODER devin: installed=${devin_installed:-unknown} logged_in=${devin_logged_in:-unknown}"

# Installed-but-logged-out is a hard blocker: the run resolved that coder as
# available and would silently fail auth mid-flight. codex has no logged_in
# field in probe-coders.sh (its config-file check doesn't imply auth), so it's
# excluded from this check.
# Fail closed on anything that isn't an explicit logged_in=true: an installed
# coder whose logged_in we couldn't parse as true (probe format drift, empty
# field) is treated as NOT logged in, so a parse-miss can't silently reintroduce
# the mid-flight auth failure this check exists to catch.
if [ "$agy_installed" = "true" ] && [ "$agy_logged_in" != "true" ]; then
  blockers+=("coder 'agy' is installed but not confirmed logged in (logged_in=${agy_logged_in:-unknown}) — run: agy login (or refresh the SSH file-store token)")
fi
if [ "$devin_installed" = "true" ] && [ "$devin_logged_in" != "true" ]; then
  blockers+=("coder 'devin' is installed but not confirmed logged in (logged_in=${devin_logged_in:-unknown}) — run: devin auth login")
fi

# --- 2. Base freshness ----------------------------------------------------

# Run in the run worktree: its base is the one the run builds from. For a run
# clone that is the clone's copy of <base>, not the main checkout's. On resume
# the jail has written that clone's config, so the remote is queried isolated
# from it (launch-runtime.md §5).
fresh_out="$(cd "$run_toplevel" && bash "$FRESHNESS" --ref "$base" --isolated-remote 2>&1)"
fresh_status=$?
printf '%s\n' "$fresh_out"
case $fresh_status in
  0)
    freshness_verdict=fresh
    fresh_csv="$(printf '%s\n' "$fresh_out" | sed -n 's/^FRESHNESS: fresh refs=//p' | tail -1)"
    base_checked=false
    IFS=',' read -r -a fresh_refs <<<"$fresh_csv"
    for r in "${fresh_refs[@]-}"; do
      [ "$r" = "$base" ] && base_checked=true
    done
    if ! $base_checked; then
      blockers+=("base '$base' was not actually checked for freshness — no local branch or no counterpart on origin; check the branch name")
    fi
    ;;
  1)
    freshness_verdict=stale
    blockers+=("base '$base' is stale — run: git fetch origin $base:$base")
    ;;
  3)
    freshness_verdict=unknown
    blockers+=("base freshness could not be determined — ls-remote failed, offline or sandboxed; verify '$base' manually or re-run with network")
    ;;
  *)
    freshness_verdict=error
    blockers+=("preflight-freshness.sh failed unexpectedly (exit $fresh_status) — see the FRESHNESS lines above")
    ;;
esac
echo "PREFLIGHT FRESHNESS: $freshness_verdict"

# --- 3. Resolved PATH/exec dirs + destination host -------------------------

while IFS= read -r d; do
  [ -n "$d" ] || continue
  echo "PREFLIGHT PATH_DIR: $d"
done <<EOF
$uniq_dirs
EOF
while IFS= read -r d; do
  [ -n "$d" ] || continue
  echo "PREFLIGHT EXEC_DIR: $d"
done <<EOF
$uniq_dirs
EOF

# The task config is the RUN's, so it is read from the run root — never from
# $ROOT. In production $ROOT is the installed plugin directory, and this repo
# ships its own tracked dev_docs/tasks/.task-config.yml in every release, so a
# $ROOT lookup reported this repository's handler and destination host to every
# installed user regardless of their own config.
#
# Each file is looked up in the run root first and then in the run root's MAIN
# checkout. For every handler except repo-pr the config is local, excluded
# from git (`.git/info/exclude`), and the local override is ignored for all of
# them — and a linked worktree checks out tracked files only, so the run
# worktree the launch creates never carries either file. The main checkout is
# where they live. The run root's own copy wins when present, because a
# committed config on the branch the run builds from is the one that applies
# there. On a main checkout the two roots are the same directory, so the
# fallback changes nothing. A run with no config in either place falls to the
# documented `repo-pr` default and says so in HANDLER_SOURCE rather than
# inheriting anything from the plugin dir. A run clone's common dir is the
# bare `<run root>/<repo>.git`, which does not lead back to the main checkout,
# so `run-clone` records that path in the clone's config and it is read here.
# On resume the jail may have rewritten it. That grants nothing new: the run
# worktree's own task config wins over the main checkout's, and the jail can
# already write that.
main_root="$run_toplevel"
if [ -n "$run_gitdir" ]; then
  main_root="$(git --git-dir="$run_gitdir" config --get auto-pilot.mainCheckout 2>/dev/null)" \
    || main_root="$(dirname "$run_gitdir")"
fi
task_cfg="$run_toplevel/dev_docs/tasks/.task-config.yml"
[ -f "$task_cfg" ] || task_cfg="$main_root/dev_docs/tasks/.task-config.yml"
task_cfg_local="$run_toplevel/dev_docs/tasks/.task-config.local.yml"
[ -f "$task_cfg_local" ] || task_cfg_local="$main_root/dev_docs/tasks/.task-config.local.yml"
handler="repo-pr"
handler_source="default (no task config under $run_toplevel)"
[ "$main_root" = "$run_toplevel" ] || handler_source="default (no task config under $run_toplevel or $main_root)"
for f in "$task_cfg" "$task_cfg_local"; do
  [ -f "$f" ] || continue
  h="$(sed -n 's/^handler:[[:space:]]*//p' "$f" | head -1 | tr -d '[:space:]')"
  if [ -n "$h" ]; then
    handler="$h"
    handler_source="$f"
  fi
done
case "$handler" in
  linear) dest_host="api.linear.app" ;;
  jira)
    site="$(sed -n 's/^[[:space:]]*site:[[:space:]]*//p' "$task_cfg" "$task_cfg_local" 2>/dev/null | tail -1 | tr -d '[:space:]')"
    dest_host="${site:-github.com}"
    ;;
  gh-issue | repo-pr) dest_host="github.com" ;;
  *) dest_host="github.com" ;;
esac
echo "PREFLIGHT DEST_HOST: $dest_host"
echo "PREFLIGHT HANDLER: $handler"
echo "PREFLIGHT HANDLER_SOURCE: $handler_source"

# --- 3b. Launcher -------------------------------------------------------------
# Both OSes jail the run under a pinned nono (Linux #1349, macOS #1361),
# checked here by scripts/nono-install.sh: the binary's checksum first, then its
# version, then (Linux) Landlock with ABI 4 or later. The supervisor is launchd
# on macOS, a systemd-run --user pair on Linux. A host with neither cannot carry
# out launch step 7. Sections 4 and 5 log a skip there — truthfully, as "this
# probe could not run" — but a skip is not a launch that can happen, so the
# missing launcher is a blocker of its own: the pre-flight fails closed instead
# of printing `go` for a launch with no way to spawn.
host_os="${PREFLIGHT_OS:-$(uname -s 2>/dev/null || echo unknown)}"
nono_bin=""
if [ "$host_os" = Darwin ]; then
  # nono's default profile denies the Keychain, where /login keeps Claude's
  # credential, so on macOS the token command is the jailed claude's only way
  # to authenticate (render-nono-profile requires it).
  [ -n "$token_cmd" ] || blockers+=("macOS needs --claude-token-cmd: the nono jail denies the Keychain, where /login keeps Claude's credential, so the detached claude authenticates only with a token command (a \`claude setup-token\` token, e.g. read with op) — pass the command the launch will use")
  nono_out="$(bash "$ROOT/scripts/nono-install.sh" check 2>&1)"
  nono_ok=$?
  nono_problem="$(sed -n 's/^NONO: //p' <<<"$nono_out" | head -1)"
  nono_fix="$(sed -n 's/^NONO FIX: //p' <<<"$nono_out" | head -1)"
  if [ "$nono_ok" = 0 ]; then
    nono_bin="$(sed -n 's/^ok \(.*\) (nono .*$/\1/p' <<<"$nono_problem")"
    echo "PREFLIGHT NONO: $nono_problem"
    if command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1; then
      echo "PREFLIGHT LAUNCHER: launchd + nono"
    else
      echo "PREFLIGHT LAUNCHER: none (macOS: no launchctl)"
      blockers+=("no launchctl on this host: the macOS supervisor is a launchd job")
    fi
  else
    echo "PREFLIGHT NONO: ${nono_problem:-the check failed: $nono_out}"
    echo "PREFLIGHT LAUNCHER: none (macOS: nono not ready)"
    blockers+=("nono jail not ready: ${nono_problem:-nono-install.sh check failed} — fix: ${nono_fix:-see PREFLIGHT NONO}")
  fi
elif [ "$host_os" = Linux ]; then
  nono_out="$(bash "$ROOT/scripts/nono-install.sh" check 2>&1)"
  nono_ok=$?
  nono_problem="$(sed -n 's/^NONO: //p' <<<"$nono_out" | head -1)"
  nono_fix="$(sed -n 's/^NONO FIX: //p' <<<"$nono_out" | head -1)"
  if [ "$nono_ok" = 0 ]; then
    nono_bin="$(sed -n 's/^ok \(.*\) (nono .*$/\1/p' <<<"$nono_problem")"
    echo "PREFLIGHT NONO: $nono_problem"
    # The supervisor (#1350) is a transient `systemd-run --user` timer +
    # service pair, so the user manager must be reachable and must outlive the
    # launching session: Linger=yes, or the units die at logout.
    sd_state="$("$SYSTEMCTL_BIN" --user is-system-running 2>/dev/null)"
    linger="$("$LOGINCTL_BIN" show-user "$(id -un)" -p Linger --value 2>/dev/null)"
    if ! command -v "$SYSTEMD_RUN_BIN" >/dev/null 2>&1; then
      echo "PREFLIGHT LAUNCHER: none (Linux: no systemd-run)"
      blockers+=("no systemd-run on this host: the Linux supervisor is a systemd-run --user timer")
    elif ! grep -Eqx 'running|degraded|starting|initializing' <<<"$sd_state"; then
      echo "PREFLIGHT LAUNCHER: none (Linux: the systemd user manager is unreachable: ${sd_state:-no answer})"
      blockers+=("the systemd user manager is unreachable (systemctl --user is-system-running: ${sd_state:-no answer}) — if this pre-flight ran inside a sandbox, re-run it unsandboxed; otherwise check that XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS are set, as a login session sets them")
    elif [ "$linger" != yes ]; then
      echo "PREFLIGHT LAUNCHER: nono + systemd-run --user (Linger=${linger:-unknown})"
      blockers+=("lingering is off for this user (Linger=${linger:-unknown}): the run's systemd units would die when the launching session logs out — fix: loginctl enable-linger $(id -un)")
    else
      echo "PREFLIGHT LAUNCHER: nono + systemd-run --user (user manager: $sd_state, Linger=yes)"
    fi
  else
    echo "PREFLIGHT NONO: ${nono_problem:-the check failed: $nono_out}"
    echo "PREFLIGHT LAUNCHER: none (Linux: nono not ready)"
    blockers+=("nono jail not ready: ${nono_problem:-nono-install.sh check failed} — fix: ${nono_fix:-see PREFLIGHT NONO}")
  fi
else
  echo "PREFLIGHT LAUNCHER: none ($host_os)"
  blockers+=("no supported launcher on this host ($host_os): the orchestrator spawns under nono through launchd on macOS, or through systemd-run on Linux, and this host is neither")
fi

# --- 3c. Run clone -----------------------------------------------------------
# Both jails write only the run's own git repo, so the run worktree must come
# from `spawn-orchestrator.sh run-clone`. A linked worktree of the main checkout
# keeps its index and HEAD in the main checkout's .git, which neither jail
# grants: on macOS its first commit fails (measured on the Mac test host), and
# on Linux render-nono-profile --git-worktree refuses it at launch.
if rc_out="$(bash "$SPAWN" check-run-clone --worktree "$run_toplevel" 2>&1)"; then
  echo "PREFLIGHT RUN_CLONE: $rc_out"
else
  echo "PREFLIGHT RUN_CLONE: none (${rc_out#spawn-orchestrator: })"
  blockers+=("the run worktree is not from a run clone: $run_toplevel — create the run with spawn-orchestrator.sh run-clone (launch-preflight.md step 1)")
fi

# --- 4. Confinement smoke ---------------------------------------------------
# scripts/smoke-nono.sh, on both OSes, run through the pinned nono 3b just
# verified: one `SMOKE <CHECK>:` line per assertion, each reprinted here as
# `PREFLIGHT SMOKE_<CHECK>:`, and every FAIL is a blocker. Its header lists the
# checks; on macOS they include the launchd escapes the profile's raw Seatbelt
# rules must refuse.

if [ -z "$nono_bin" ]; then
  case "$host_os" in
    Linux | Darwin) echo "PREFLIGHT SMOKE: skip (nono is not ready — see PREFLIGHT NONO)" ;;
    *) echo "PREFLIGHT SMOKE: skip (no supported launcher on this host)" ;;
  esac
elif [ "$host_os" = Darwin ] && [ -z "$token_cmd" ]; then
  # The smoke would refuse too: there is no macOS profile without a token.
  echo "PREFLIGHT SMOKE: skip (macOS needs --claude-token-cmd — see the blocker)"
else
  smoke_args=(--nono-bin "$nono_bin")
  [ -n "$token_cmd" ] && smoke_args+=(--claude-token-cmd "$token_cmd")
  smoke_out="$(bash "${PREFLIGHT_NONO_SMOKE:-$ROOT/scripts/smoke-nono.sh}" "${smoke_args[@]}" 2>&1)"
  smoke_rc=$?
  smoke_fails=0
  while IFS= read -r line; do
    case "$line" in
      "SMOKE "*)
        echo "PREFLIGHT SMOKE_${line#SMOKE }"
        case "$line" in
          *": FAIL "*)
            smoke_fails=$((smoke_fails + 1))
            check="${line#SMOKE }"
            blockers+=("confinement smoke: ${check%%:*} failed — ${check#*: FAIL }")
            ;;
        esac
        ;;
    esac
  done <<<"$smoke_out"
  if [ "$smoke_rc" != 0 ] && [ "$smoke_fails" = 0 ]; then
    echo "PREFLIGHT SMOKE: FAIL (the nono smoke could not run: $(tail -1 <<<"$smoke_out"))"
    blockers+=("confinement smoke: the nono smoke could not run — $(tail -1 <<<"$smoke_out")")
  fi
  [ -n "$token_cmd" ] || skip_notes+=("no --claude-token-cmd: the jail runs the documented fallback, ~/.claude writable")
fi

# --- 5. Consent-gate probe --------------------------------------------------
# The launch blocker skills/auto-pilot/references/launch-runtime.md §3 names —
# a credential reachable only through an interactive prompt — generalizes to
# ANY interactive consent gate, and the live failure was not Keychain at all:
# it was TCC folder consent.
#
# The act of detaching is what creates the gate. macOS attributes a TCC grant
# to a RESPONSIBLE PROCESS. Run from a terminal, folder access belongs to
# Terminal/iTerm, which the user authorized long ago, so nothing prompts. The
# orchestrator is spawned by launchd, which makes the orchestrator's own
# binary the responsible process — a different TCC identity holding none of
# those grants. So a probe that passes from this terminal proves nothing about
# the detached run. This one runs under the real attribution: a transient
# launchd job, stdin on /dev/null, no controlling terminal, run through the
# pinned nono under a rendered nono profile, as the run is.
#
# A COMPLETED probe reporting a denial is a hard blocker. An environment that
# cannot run the probe at all (not macOS, nono not ready, no launchctl)
# degrades to a logged SKIP, the same way the confinement smoke above does — a
# missing capability is not evidence of a consent gate.

# Resolve a symlink chain without readlink -f, which BSD readlink lacked until
# macOS 12.3 and which this must not depend on.
resolve_link() {
  local p="$1" t n=0
  while [ -L "$p" ] && [ "$n" -lt 40 ]; do
    t="$(readlink "$p")"
    case "$t" in
      /*) p="$t" ;;
      *) p="$(dirname "$p")/$t" ;;
    esac
    n=$((n + 1))
  done
  (cd "$(dirname "$p")" 2>/dev/null && printf '%s/%s\n' "$(pwd -P)" "$(basename "$p")")
}

# Escape a value for a plist <string>…</string>; `&` MUST go first. The same
# helper and the same reasoning as spawn-orchestrator.sh's `xml_escape` — an
# unescaped value can close the string and inject a second
# <key>ProgramArguments</key>, which launchd execs DIRECTLY rather than under
# the jail. Here the plainer failure matters more: `&` and `<` are legal in
# macOS paths, and a malformed plist makes `launchctl bootstrap` fail, which this
# section degrades to a SKIP — so an unescaped path turns a gate check into a
# `go`. Relies on patsub_replacement being off (see the shopt below `set`).
xml_escape() {
  local s="$1"
  s="${s//&/&amp;}"
  s="${s//</&lt;}"
  s="${s//>/&gt;}"
  s="${s//\"/&quot;}"
  printf '%s' "$s"
}

# The attribution identity: the RESOLVED claude binary, never the `claude`
# symlink. This is the name the human has to find in System Settings, and the
# reason they cannot: the target is a bare Mach-O, not an .app bundle, so macOS
# has no display name for it and falls back to the filename — which is the
# version number. A dialog from "2.1.207" is indistinguishable from malware.
claude_link="$(command -v claude 2>/dev/null || true)"
attribution_bin=""
[ -n "$claude_link" ] && attribution_bin="$(resolve_link "$claude_link")"
echo "PREFLIGHT ATTRIBUTION_BIN: ${attribution_bin:-absent}"

# Which protected locations does the run actually NEED? TCC gates a read
# before any jail rule is consulted, so a run path under ~/Documents raises the
# gate whatever the profile grants. Recording the needed set is what makes "none" an answer: a run whose
# paths all sit outside these locations should never trip one.
under_path() { # <candidate> <ancestor> — true when candidate is at or under ancestor
  case "$1" in "$2" | "$2"/*) return 0 ;; *) return 1 ;; esac
}
add_protected() { # <location> — append to protected_needed, once
  case ":$protected_needed:" in
    *":$1:"*) ;;
    *) protected_needed="${protected_needed:+$protected_needed:}$1" ;;
  esac
}

# `run_toplevel` and `run_gitdir` are resolved at argument-validation time, from
# the required `--run-root` — see the block beside the `--source` check.
echo "PREFLIGHT RUN_ROOT: $run_toplevel"

# `set -f` for the two colon-split loops below. Setting IFS makes the unquoted
# expansion split on ':' only, but it does NOT stop the resulting words from
# being pathname-expanded — so a location holding a glob character would silently
# expand to something else, which is a location the probe then does not check.
protected_needed=""
consent_old_ifs="$IFS"
set -f
IFS=':'
for loc in $TCC_LOCATIONS; do
  IFS="$consent_old_ifs"
  if [ -n "$loc" ] && [ -d "$loc" ]; then
    for run_path in "$run_toplevel" "$run_gitdir" "$ROOT" "$HOME/.claude" "${TMPDIR:-/tmp}"; do
      [ -n "$run_path" ] || continue
      under_path "$run_path" "$loc" && add_protected "$loc"
    done
  fi
  IFS=':'
done
IFS="$consent_old_ifs"
set +f
# A removable or network volume is the other gated class, and it is named by
# the run's own paths rather than by any fixed list.
for run_path in "$run_toplevel" "$run_gitdir" "$ROOT" "${TMPDIR:-/tmp}"; do
  [ -n "$run_path" ] || continue
  case "$run_path" in
    /Volumes/*) add_protected "/Volumes/$(printf '%s' "${run_path#/Volumes/}" | cut -d/ -f1)" ;;
  esac
done
echo "PREFLIGHT CONSENT_PROTECTED: ${protected_needed:-none}"

# Always probe the run's own paths: a checkout unreadable under launchd
# attribution is the same failure whether or not it sits in a named TCC location
# — Full Disk Access covers cases no location list enumerates. `plugin_root` is
# read at runtime too, so it is probed; it is just never the run root.
consent_specs="run_root=$run_toplevel
plugin_root=$ROOT"
[ -n "$run_gitdir" ] && consent_specs="$consent_specs
git_common_dir=$run_gitdir"
consent_i=0
consent_old_ifs="$IFS"
set -f
IFS=':'
for loc in $protected_needed; do
  IFS="$consent_old_ifs"
  if [ -n "$loc" ]; then
    consent_i=$((consent_i + 1))
    consent_specs="$consent_specs
protected_$consent_i=$loc"
  fi
  IFS=':'
done
IFS="$consent_old_ifs"
set +f

# **Only a non-macOS host skips.** Past the two checks below, this machine can
# run the probe — so anything that then stops it (no scratch dir, no rendered
# profile, a refused bootstrap, a probe that never reports) leaves
# launchd-attributed consent UNPROVEN, and a `go` on an unproven property is the
# fail-open this whole section exists to close. The earlier reasoning — "a
# missing capability is not evidence of a consent gate" — holds only for a
# genuinely absent capability, which is what the two skips below test for.
# Blocking costs nothing real: the run itself bootstraps into the same
# `gui/$(id -u)` domain, so a domain that refuses the probe refuses the run too.
# nono not ready is the third skip: 3b already blocks the launch on it, so the
# skip cannot turn into a `go`.
consent_verdict=""
if [ "$host_os" != Darwin ]; then
  consent_verdict="skip (non-macOS host — TCC is macOS-only)"
  skip_notes+=("consent-gate probe skipped: non-macOS host (TCC is macOS-only)")
elif [ -z "$nono_bin" ]; then
  consent_verdict="skip (nono not ready — see PREFLIGHT NONO)"
  skip_notes+=("consent-gate probe skipped: nono not ready (see PREFLIGHT NONO; already a blocker)")
elif ! command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1; then
  consent_verdict="skip (launchctl not available — non-macOS host)"
  skip_notes+=("consent-gate probe skipped: launchctl absent (non-macOS host)")
else
  cscratch="$(mktemp -d "${TMPDIR:-/tmp}/preflight-consent.XXXXXX" 2>/dev/null || true)"
  if [ -z "$cscratch" ] || [ ! -d "$cscratch" ]; then
    consent_verdict="FAIL (no scratch dir)"
    blockers+=("consent-gate probe could not create its scratch dir under ${TMPDIR:-/tmp} — launchd-attributed consent is unproven on a host that can run the probe")
  else
    consent_label="com.bestdan.workflow-skills.preflight-consent.$$"
    consent_done=false
    consent_cleanup() {
      $consent_done || {
        "$LAUNCHCTL_BIN" bootout "gui/$(id -u)/$consent_label" >/dev/null 2>&1 || true
        rm -rf "$cscratch"
      }
      consent_done=true
    }
    trap consent_cleanup EXIT
    trap 'consent_cleanup; trap - EXIT INT TERM; exit 130' INT TERM

    cprof="$cscratch/consent.json"
    cprobe="$cscratch/consent-probe.sh"
    cwrapper="$cscratch/consent-launch.sh"
    cresult="$cscratch/consent-result"
    clog="$cscratch/consent.log"
    cplist="$cscratch/consent.plist"

    # The launch wrapper exists so ProgramArguments[0] is `/bin/bash`, matching
    # the production job (scripts/orchestrator.plist.tmpl), whose program is also
    # `/bin/bash <launch script>` and which composes `nono run … claude -p …`
    # as a CHILD. launchd makes a job's own initial process the one TCC holds
    # responsible, and children inherit it — so ProgramArguments[0] is the single
    # variable that decides which identity this probe measures. Dispatching
    # nono directly would guarantee a different one from production's. Runs
    # nono as a child rather than exec'ing it, with the environment and flags
    # spawn-orchestrator.sh's write-launch --jail nono composes.
    {
      printf '#!/bin/bash\n'
      printf '# Written by scripts/preflight.sh — launchd'"'"'s program, so that the job'"'"'s\n'
      printf '# responsible process is /bin/bash exactly as it is for the real run.\n'
      printf 'NONO_NO_UPDATE_CHECK=1 NONO_NO_MIGRATE=1 %q run --silent --profile "$1" -- /bin/bash "$2" "$3" "${@:4}"\n' "$nono_bin"
    } >"$cwrapper"

    # The probe runs on launchd's minimal PATH under the 3.2 /bin/bash, so every
    # binary it reaches is named absolutely.
    cat >"$cprobe" <<'PROBE'
#!/bin/bash
# Written by scripts/preflight.sh — runs jailed, under launchd attribution.
out="$1"
shift
: >"$out"
# Self-checks first: a probe that ran with a terminal attached measured the
# terminal's grants, which is exactly the thing that proves nothing.
if [ -t 0 ]; then
  echo "CONSENT stdin_closed: FAIL" >>"$out"
else
  echo "CONSENT stdin_closed: ok" >>"$out"
fi
# stderr is redirected FIRST: bash applies redirections left to right and
# reports a failed one on the old stderr, so the other order leaks
# "/dev/tty: Device not configured" whenever the check correctly passes.
if : 2>/dev/null >/dev/tty; then
  echo "CONSENT no_controlling_tty: FAIL" >>"$out"
else
  echo "CONSENT no_controlling_tty: ok" >>"$out"
fi
for spec in "$@"; do
  key="${spec%%=*}"
  path="${spec#*=}"
  # Announced BEFORE the read, so a resource that hangs or kills the job leaves a
  # record of which one it was. The result's last `attempting` line with no
  # matching `resource` line names the path that never came back.
  echo "CONSENT attempting $key: $path" >>"$out"
  if err="$(/bin/ls -- "$path" 2>&1 >/dev/null)"; then
    echo "CONSENT resource $key: ok $path" >>"$out"
  else
    echo "CONSENT resource $key: denied $path — ${err##*: }" >>"$out"
  fi
done
echo "CONSENT done: 1" >>"$out"
PROBE

    # Every path the probe reads is granted read-only. nono's reads are NOT
    # global, as the Seatbelt template's `(allow file-read*)` was: without these
    # grants a denial would be nono's, not TCC's, and the probe would block on a
    # gate that does not exist. --claude-token because the macOS profile
    # requires it; the probe runs no claude, so no token is fetched.
    cro_args=()
    while IFS= read -r spec; do
      [ -n "$spec" ] && cro_args+=(--ro "${spec#*=}")
    done <<EOF
$consent_specs
EOF
    mkdir -p "$cscratch/tmp"
    if ! bash "$SPAWN" render-nono-profile --rw "$cscratch" --tmpdir "$cscratch/tmp" --toolchain --claude-token \
      ${cro_args[@]+"${cro_args[@]}"} --out "$cprof" >"$cscratch/render.log" 2>&1; then
      consent_verdict="FAIL (render-nono-profile failed)"
      blockers+=("consent-gate probe could not render a nono profile for this environment — launchd-attributed consent is unproven: $(tail -1 "$cscratch/render.log" 2>/dev/null)")
    else
      {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0">'
        echo '<dict>'
        echo '  <key>Label</key>'
        printf '  <string>%s</string>\n' "$(xml_escape "$consent_label")"
        echo '  <key>ProgramArguments</key>'
        echo '  <array>'
        for a in /bin/bash "$cwrapper" "$cprof" "$cprobe" "$cresult"; do
          printf '    <string>%s</string>\n' "$(xml_escape "$a")"
        done
        while IFS= read -r spec; do
          [ -n "$spec" ] && printf '    <string>%s</string>\n' "$(xml_escape "$spec")"
        done <<EOF
$consent_specs
EOF
        echo '  </array>'
        # stdin on /dev/null is half of "nobody can answer it": the probe must
        # not be able to read an answer from anywhere.
        echo '  <key>StandardInPath</key>'
        echo '  <string>/dev/null</string>'
        echo '  <key>StandardOutPath</key>'
        printf '  <string>%s</string>\n' "$(xml_escape "$clog")"
        echo '  <key>StandardErrorPath</key>'
        printf '  <string>%s</string>\n' "$(xml_escape "$clog")"
        echo '  <key>RunAtLoad</key>'
        echo '  <true/>'
        echo '</dict>'
        echo '</plist>'
      } >"$cplist"

      if ! "$LAUNCHCTL_BIN" bootstrap "gui/$(id -u)" "$cplist" >/dev/null 2>&1; then
        consent_verdict="FAIL (launchd refused the probe bootstrap)"
        blockers+=("consent-gate probe could not be bootstrapped into gui/$(id -u) — launchd-attributed consent is unproven, and the run itself bootstraps into that same domain, so a domain that refuses this will refuse the run. Re-run this pre-flight from a normal login session.")
      else
        consent_waited=0
        while [ "$consent_waited" -lt "$CONSENT_TIMEOUT_TICKS" ]; do
          grep -q '^CONSENT done: 1$' "$cresult" 2>/dev/null && break
          sleep 0.25
          consent_waited=$((consent_waited + 1))
        done
        "$LAUNCHCTL_BIN" bootout "gui/$(id -u)/$consent_label" >/dev/null 2>&1 || true

        if ! grep -q '^CONSENT done: 1$' "$cresult" 2>/dev/null; then
          # Name the resource it died on, and carry the same remedy the denial
          # branch carries — a timeout on a protected path is a consent failure
          # wearing a different hat, and "see the log" is not an answer at 3am.
          #
          # The probe is a single sequential writer (`attempting K`, then
          # `resource K`, per spec), so a read is still pending exactly when the
          # LAST line is an `attempting` one. Hence tail-then-match, not
          # match-then-tail: the latter names the last resource attempted even
          # when the file goes on to show it returned, which would send the
          # operator to grant access for a path the same file records as `ok`.
          stalled="$(tail -n 1 "$cresult" 2>/dev/null | sed -n 's/^CONSENT attempting //p')"
          consent_verdict="FAIL (probe did not complete)"
          blockers+=("consent-gate probe did not complete under launchd attribution${stalled:+, and stalled on $stalled} — launchd-attributed consent is unproven. If it stalled on a protected path, the remedy is the denial one: move the run's paths out of that location, or grant Full Disk Access under System Settings → Privacy & Security → Full Disk Access to the job's responsible process ('/bin/bash', or possibly '${attribution_bin:-<claude is not on PATH>}') and re-run this pre-flight. Probe log: $clog")
        else
          consent_verdict="pass"
          while IFS= read -r line; do
            echo "PREFLIGHT $line"
            case "$line" in
              "CONSENT stdin_closed: FAIL" | "CONSENT no_controlling_tty: FAIL")
                consent_verdict="FAIL (probe ran attached to a terminal)"
                blockers+=("consent-gate probe ran with a terminal attached — it measured this terminal's TCC grants, not the detached run's, and so proves nothing; the probe job's stdin/TTY setup is broken")
                ;;
              "CONSENT resource "*": denied "*)
                gated_key="${line#CONSENT resource }"
                gated_key="${gated_key%%:*}"
                gated_path="${line#*: denied }"
                gated_path="${gated_path% — *}" # strip the trailing error only
                consent_verdict="FAIL (interactive consent gate)"
                # The remedy leads with the fix that needs no grant, because it
                # is the only one this pre-flight can be sure of. The grant
                # target is NOT established: launchd makes a job's own program
                # the responsible process, which for both this probe and the
                # real run is /bin/bash — but macOS may re-attribute on exec of
                # a non-platform binary, and that cannot be measured without a
                # desktop session to answer the dialog. So name both candidates
                # and say the probe is the check, rather than sending the user
                # to grant access to a binary that may not be the one asking.
                blockers+=("interactive consent gate on '$gated_path' ($gated_key) — it is not reachable under launchd attribution, so the detached run raises a macOS consent dialog on a locked screen, addressed to nobody. Fix, in order: (1) move the run's paths out of the protected location — then no grant is needed at all; or (2) grant Full Disk Access under System Settings → Privacy & Security → Full Disk Access to the launchd job's responsible process, then re-run this pre-flight to check whether it cleared. Most likely that is '/bin/bash' (the job's own program, for this probe and the real run alike); it may instead be the resolved binary '${attribution_bin:-<claude is not on PATH>}', which the list and the dialog would identify only by its version number ('$(basename "${attribution_bin:-unknown}")') rather than as \"Claude\", it being a bare executable with no .app bundle. This pre-flight cannot tell which, so grant and re-check rather than trusting either name.")
                ;;
            esac
          done <"$cresult"
        fi
      fi
    fi
    consent_cleanup
    trap - EXIT INT TERM
  fi
fi
echo "PREFLIGHT CONSENT_GATE: $consent_verdict"

# --- 6. Verdict --------------------------------------------------------------

for n in ${skip_notes[@]+"${skip_notes[@]}"}; do
  echo "PREFLIGHT SKIP_NOTE: $n"
done

if [ "${#blockers[@]}" -gt 0 ]; then
  for b in "${blockers[@]}"; do
    echo "PREFLIGHT BLOCKER: $b"
  done
  echo "PREFLIGHT VERDICT: no-go — ${blockers[0]}"
  exit 1
fi
echo "PREFLIGHT VERDICT: go"
exit 0
