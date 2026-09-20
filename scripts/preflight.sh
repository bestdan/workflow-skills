#!/usr/bin/env bash
# preflight.sh — READ-ONLY go/no-go pre-flight for an auto-pilot launch.
#
# Extracts the ad-hoc auth/env probing that skills/auto-pilot/SKILL.md "Step 2
# — Non-interactive auth probes" used to describe inline (it names these as
# "good candidates to extract into a small pre-flight helper script"), and
# composes the existing probes rather than re-implementing them:
#   - scripts/probe-coders.sh        coder CLI availability/auth
#   - scripts/preflight-freshness.sh base-branch freshness vs its remote
#   - scripts/spawn-orchestrator.sh  render-profile / render-settings, reused
#                                    for the confinement smoke
#
# Never mutates git or filesystem state outside a private scratch dir it
# creates and removes for the confinement smoke.
#
# Usage:
#   scripts/preflight.sh --source <plan|linear> [--base <branch>]
#                        [--run-root <path>]
#   scripts/preflight.sh --scout-run-md <path to RUN.md>
#
#   --source        Task-graph source the run reads from. Required unless
#                    --scout-run-md is given.
#   --base          Base branch to check for staleness. Default: main.
#   --run-root      The checkout the detached run will work in — the run
#                    worktree launch step 1 created. Default: $PWD. The
#                    consent-gate probe tests THIS path (and its
#                    --git-common-dir), never the installed plugin's own
#                    directory, which is never where the run's files are. A
#                    value that is not a git checkout is fatal, not a skip.
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
# /dev/null, no controlling TTY, through the rendered Seatbelt profile) and
# blocks launch on a denial.
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
while [ $# -gt 0 ]; do
  case "$1" in
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
      sed -n '2,76p' "$0"
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
  plan | linear) ;;
  "") die "requires --source <plan|linear>" ;;
  *) die "unknown --source (fail-closed): $source_arg" ;;
esac
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

fresh_out="$(bash "$FRESHNESS" --ref "$base" 2>&1)"
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

task_cfg="$ROOT/dev_docs/tasks/.task-config.yml"
task_cfg_local="$ROOT/dev_docs/tasks/.task-config.local.yml"
handler="repo-pr"
for f in "$task_cfg" "$task_cfg_local"; do
  [ -f "$f" ] || continue
  h="$(sed -n 's/^handler:[[:space:]]*//p' "$f" | head -1 | tr -d '[:space:]')"
  [ -n "$h" ] && handler="$h"
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

# --- 4. Confinement smoke ---------------------------------------------------
# Layer 1 (filesystem/exec) is the rendered Seatbelt profile; layer 2 (network
# egress) is the settings.json render-settings emits — see
# skills/auto-pilot/references/launch-runtime.md "Sandbox profile". Nested
# sandbox-exec is itself denied in some sandboxed dev environments, so probe
# that capability first and degrade to a logged SKIP rather than a blocker.

if ! command -v sandbox-exec >/dev/null 2>&1; then
  echo "PREFLIGHT SMOKE: skip (sandbox-exec not available — non-macOS host)"
  skip_notes+=("confinement smoke skipped: sandbox-exec absent (non-macOS host)")
elif ! sandbox-exec -p '(version 1)(allow default)' /usr/bin/true >/dev/null 2>&1; then
  echo "PREFLIGHT SMOKE: skip (nested sandbox-exec denied in this environment)"
  skip_notes+=("confinement smoke skipped: nested sandbox-exec apply failed in this environment")
else
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/preflight-smoke.XXXXXX" 2>/dev/null || true)"
  if [ -z "$scratch" ] || [ ! -d "$scratch" ]; then
    echo "PREFLIGHT SMOKE: skip (could not create a scratch dir)"
    skip_notes+=("confinement smoke skipped: scratch dir creation failed")
  else
    scratch_done=false
    home_probe=""
    smoke_cleanup() {
      $scratch_done || {
        rm -rf "$scratch"
        rm -f "${home_probe:-}"
      }
      scratch_done=true
    }
    # On a signal, clean up and DIE — don't let bash resume past the trap and run
    # the rest of the smoke against a just-deleted scratch dir (a spurious blocker).
    trap smoke_cleanup EXIT
    trap 'smoke_cleanup; trap - EXIT INT TERM; exit 130' INT TERM

    ex_args=()
    for bin in $FINGERPRINT_BINS; do
      p="$(command -v "$bin" 2>/dev/null || true)"
      [ -n "$p" ] && ex_args+=(--exec "$p")
    done
    # The smoke itself execs through the rendered profile (env, bash) to run
    # its checks — those binaries must be on the exec whitelist too, or the
    # smoke's own checks fail against the wall rather than testing it.
    for smoke_bin in /usr/bin/env "$(command -v bash 2>/dev/null || true)"; do
      [ -n "$smoke_bin" ] && [ -x "$smoke_bin" ] && ex_args+=(--exec "$smoke_bin")
    done
    prof="$scratch/preflight.sb"
    if bash "$SPAWN" render-profile --rw "$scratch" ${ex_args[@]+"${ex_args[@]}"} --out "$prof" >/dev/null 2>&1; then
      smoke_exec_ok=false
      if sandbox-exec -f "$prof" /usr/bin/env bash -c 'exit 0' >/dev/null 2>&1; then
        smoke_exec_ok=true
        echo "PREFLIGHT SMOKE_EXEC: pass (sed/git/env bash exec through the jail succeeds)"
      else
        echo "PREFLIGHT SMOKE_EXEC: FAIL"
        blockers+=("confinement smoke: exec through the rendered toolchain profile failed — the exec wall is broken, not just narrow")
      fi

      if ! $smoke_exec_ok; then
        echo "PREFLIGHT SMOKE_HOME_WRITE: skip (SMOKE_EXEC failed — exec wall is already a blocker)"
      else
        home_probe="$(mktemp -u "$HOME/.preflight-smoke-XXXXXXXX" 2>/dev/null || true)"
        if [ -z "$home_probe" ] || [ -e "$home_probe" ]; then
          echo "PREFLIGHT SMOKE_HOME_WRITE: skip (could not pick a collision-free probe path)"
          skip_notes+=("confinement smoke: HOME-write check skipped — no collision-free probe path")
        else
          sandbox-exec -f "$prof" /usr/bin/env bash -c ': > "$1"' _ "$home_probe" >/dev/null 2>&1
          if [ -e "$home_probe" ]; then
            rm -f "$home_probe"
            echo "PREFLIGHT SMOKE_HOME_WRITE: FAIL (write to \$HOME succeeded through the jail)"
            blockers+=("confinement smoke: a write to \$HOME succeeded through the rendered profile — the filesystem wall is broken")
          else
            echo "PREFLIGHT SMOKE_HOME_WRITE: pass (write to \$HOME denied, as expected)"
          fi
        fi
      fi

      settings="$scratch/preflight-settings.json"
      if bash "$SPAWN" render-settings --source "$source_arg" --out "$settings" >/dev/null 2>&1 \
        && grep -q '"allowedDomains"' "$settings" && grep -q '"enabled":true' "$settings"; then
        echo "PREFLIGHT SMOKE_EGRESS: pass (layer-2 allowlist renders enabled + deny-by-default)"
      else
        echo "PREFLIGHT SMOKE_EGRESS: FAIL"
        blockers+=("confinement smoke: layer-2 egress allowlist did not render as an enabled, deny-by-default allowlist")
      fi
    else
      echo "PREFLIGHT SMOKE: FAIL (render-profile failed for this environment's fingerprint)"
      blockers+=("confinement smoke: render-profile failed to produce a launch profile for this environment's fingerprint — the renderer is broken, not just narrow")
    fi
    smoke_cleanup
    trap - EXIT INT TERM
  fi
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
# launchd job, stdin on /dev/null, no controlling terminal, exec'd through the
# same rendered Seatbelt profile the run uses.
#
# A COMPLETED probe reporting a denial is a hard blocker. An environment that
# cannot run the probe at all (no sandbox-exec, launchd refuses the bootstrap)
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
# sandbox-exec. Here the plainer failure matters more: `&` and `<` are legal in
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

# Which protected locations does the run actually NEED? The jail allows
# `(allow file-read*)` globally — deliberately; a narrow read list broke
# traversal — so nothing stops an incidental read of ~/Documents from raising a
# gate. Recording the needed set is what makes "none" an answer: a run whose
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

# **`$ROOT` is not the run's checkout.** It is `dirname($0)/..`, and production
# invokes this as `"${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh"` — so on an
# installed plugin `$ROOT` is `~/.claude/plugins/…`, which is never in a
# protected location. Inventorying from it reports `none` and a `go` for a
# checkout sitting in ~/Documents: the exact run this probe exists to stop.
# The run root is the caller's, so it is an argument, not a derivation.
#
# Two paths come out of it, not one. A linked worktree under ~/src/worktrees
# still reads and writes its main checkout's `.git` on every git operation, so a
# probe that tests only the worktree passes tonight and dies on the first fetch
# at 3am. `--git-common-dir` is that second path.
consent_run_root="${run_root_arg:-$PWD}"
run_toplevel="$(git -C "$consent_run_root" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$run_toplevel" ] || die "run root is not a git checkout (fail-closed): $consent_run_root — pass --run-root <the run's worktree>"
run_gitdir="$(git -C "$consent_run_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [ -z "$run_gitdir" ]; then # git < 2.31 has no --path-format
  run_gitdir="$(cd "$consent_run_root" && cd "$(git rev-parse --git-common-dir)" && pwd)" || run_gitdir=""
fi
echo "PREFLIGHT RUN_ROOT: $run_toplevel"

protected_needed=""
consent_old_ifs="$IFS"
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

consent_verdict=""
if ! command -v sandbox-exec >/dev/null 2>&1; then
  consent_verdict="skip (sandbox-exec not available — non-macOS host)"
  skip_notes+=("consent-gate probe skipped: sandbox-exec absent (non-macOS host; TCC is macOS-only)")
elif ! command -v "$LAUNCHCTL_BIN" >/dev/null 2>&1; then
  consent_verdict="skip (launchctl not available — non-macOS host)"
  skip_notes+=("consent-gate probe skipped: launchctl absent (non-macOS host)")
else
  cscratch="$(mktemp -d "${TMPDIR:-/tmp}/preflight-consent.XXXXXX" 2>/dev/null || true)"
  if [ -z "$cscratch" ] || [ ! -d "$cscratch" ]; then
    consent_verdict="skip (could not create a scratch dir)"
    skip_notes+=("consent-gate probe skipped: scratch dir creation failed")
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

    cprof="$cscratch/consent.sb"
    cprobe="$cscratch/consent-probe.sh"
    cwrapper="$cscratch/consent-launch.sh"
    cresult="$cscratch/consent-result"
    clog="$cscratch/consent.log"
    cplist="$cscratch/consent.plist"

    # The launch wrapper exists so ProgramArguments[0] is `/bin/bash`, matching
    # the production job (scripts/orchestrator.plist.tmpl), whose program is also
    # `/bin/bash <launch script>` and which composes `sandbox-exec … claude -p …`
    # as a CHILD. launchd makes a job's own initial process the one TCC holds
    # responsible, and children inherit it — so ProgramArguments[0] is the single
    # variable that decides which identity this probe measures. Dispatching
    # sandbox-exec directly would guarantee a different one from production's.
    # Runs sandbox-exec as a child rather than exec'ing it, mirroring what
    # spawn-orchestrator.sh's write-launch composes.
    cat >"$cwrapper" <<'WRAPPER'
#!/bin/bash
# Written by scripts/preflight.sh — launchd's program, so that the job's
# responsible process is /bin/bash exactly as it is for the real run.
/usr/bin/sandbox-exec -f "$1" /bin/bash "$2" "$3" "${@:4}"
WRAPPER

    # The probe runs on launchd's minimal PATH under the 3.2 /bin/bash, so every
    # binary it reaches is named absolutely and granted by --exec below.
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
  if err="$(/bin/ls -- "$path" 2>&1 >/dev/null)"; then
    echo "CONSENT resource $key: ok $path" >>"$out"
  else
    echo "CONSENT resource $key: denied $path — ${err##*: }" >>"$out"
  fi
done
echo "CONSENT done: 1" >>"$out"
PROBE

    cex_args=()
    for bin in $FINGERPRINT_BINS; do
      p="$(command -v "$bin" 2>/dev/null || true)"
      [ -n "$p" ] && cex_args+=(--exec "$p")
    done
    for cbin in /bin/bash /bin/ls; do
      [ -x "$cbin" ] && cex_args+=(--exec "$cbin")
    done

    if ! bash "$SPAWN" render-profile --rw "$cscratch" ${cex_args[@]+"${cex_args[@]}"} --out "$cprof" >/dev/null 2>&1; then
      consent_verdict="skip (render-profile failed — the confinement smoke above owns that blocker)"
      skip_notes+=("consent-gate probe skipped: render-profile produced no profile for this environment")
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
        consent_verdict="skip (launchd refused the probe bootstrap in this environment)"
        skip_notes+=("consent-gate probe skipped: launchctl bootstrap failed — re-run this pre-flight from a normal login session before trusting a go")
      else
        consent_waited=0
        while [ "$consent_waited" -lt "$CONSENT_TIMEOUT_TICKS" ]; do
          grep -q '^CONSENT done: 1$' "$cresult" 2>/dev/null && break
          sleep 0.25
          consent_waited=$((consent_waited + 1))
        done
        "$LAUNCHCTL_BIN" bootout "gui/$(id -u)/$consent_label" >/dev/null 2>&1 || true

        if ! grep -q '^CONSENT done: 1$' "$cresult" 2>/dev/null; then
          consent_verdict="FAIL (probe did not complete)"
          blockers+=("consent-gate probe did not complete under launchd attribution — the detached run's own entry path could not be exercised, so a prompt-free run is unproven; probe log: $clog")
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
