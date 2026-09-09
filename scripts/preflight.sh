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
#   scripts/preflight.sh --scout-run-md <path to RUN.md>
#
#   --source        Task-graph source the run reads from. Required unless
#                    --scout-run-md is given.
#   --base          Base branch to check for staleness. Default: main.
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
# Env overrides (for tests only — never needed in normal use):
#   PREFLIGHT_PROBE_CODERS  path to a probe-coders.sh-compatible executable.
#   PREFLIGHT_NC            path to an `nc`-compatible executable (scout's
#                            CAO port probe).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROBE_CODERS="${PREFLIGHT_PROBE_CODERS:-$ROOT/scripts/probe-coders.sh}"
FRESHNESS="$ROOT/scripts/preflight-freshness.sh"
SPAWN="$ROOT/scripts/spawn-orchestrator.sh"
FINGERPRINT_BINS="claude git gh codex uv node op"
NC_BIN="${PREFLIGHT_NC:-nc}"
CAO_FLEET="codex agy"

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
while [ $# -gt 0 ]; do
  case "$1" in
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
      sed -n '2,49p' "$0"
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

# --- 5. Verdict --------------------------------------------------------------

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
