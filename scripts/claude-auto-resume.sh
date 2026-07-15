#!/usr/bin/env bash
# claude-auto-resume.sh — usually aliased to `car` by the user (see README).
#
# External launcher that keeps an interactive Claude Code session going across
# the 5-hour usage-limit wall. When you hit the cap, Anthropic kills the claude
# PROCESS — the agent cannot relaunch itself. This wrapper owns that process
# from the outside: on exit it decides whether we were rate-limited, and if so
# it sleeps until the reset time, then resumes the SAME conversation.
#
# You must start the session with this script (or an alias to it) instead of
# `claude` directly. Claude Code cannot invoke this for itself — nothing
# inside the agent survives the process being killed.
#
# Deciding whether to resume: the live usage query is authoritative. This
# script resumes only when scripts/claude-usage.sh --session-status reports
# the 5h window at the true cap (>= CAR_CAP_PCT, default 100) with a future
# reset — that is what tells "killed by the wall" apart from "quit while
# merely near cap". Works out of the box with zero setup beyond
# claude-usage.sh sitting alongside it in the same directory.
#
# Optional offline fallback: if you have your own Claude Code statusline hook,
# you can arm ~/.claude/.rl_warn from it (e.g. once your session crosses 90%)
# by writing two lines to that file:
#   5h_pct=<int>
#   5h_reset=<unix-epoch>
# That flag is consulted only when the live claude-usage.sh query is
# unreachable (offline), as a best-effort reset-epoch source — this repo does
# not ship a statusline hook, so the flag is never required.
#
# By default this script re-execs itself inside a tmux session so the sleep
# survives a closed terminal / dropped SSH — just run it and walk away. Opt
# out with `--no-tmux …` or CAR_TMUX=0 (e.g. when scripting it or already in
# tmux).
#
# Env knobs:
#   CAR_CAP_PCT       resume only when live 5h usage >= this (default 100);
#                     distinct from whatever threshold your own statusline
#                     hook uses to arm ~/.claude/.rl_warn
#   CAR_BUFFER        seconds to wait past the reset before resuming (default 60)
#   CAR_MAX_LOOPS     safety cap on resumes (default 12)
#   CAR_TMUX          1 = self-wrap in tmux (default), 0 = never
#   CAR_TMUX_SESSION  tmux session name to use / reattach (default "car")

set -uo pipefail

# --- tmux self-wrap -------------------------------------------------------
# Consume a leading --no-tmux, then (unless disabled / already in tmux / no
# tty / tmux absent) re-exec into a named, attach-or-create tmux session with
# the same args. `-A` reattaches to a running car session instead of erroring,
# so re-invoking car mid-run drops you back into the overnight session. Args
# are passed as separate argv so a quoted prompt survives intact.
USE_TMUX="${CAR_TMUX:-1}"
if [ "${1:-}" = "--no-tmux" ]; then
  USE_TMUX=0
  shift
fi
if [ "$USE_TMUX" = 1 ] && [ -z "${TMUX:-}" ] && [ -t 1 ] && command -v tmux >/dev/null 2>&1; then
  exec tmux new-session -A -s "${CAR_TMUX_SESSION:-car}" "$0" --no-tmux "$@"
fi

FLAG="$HOME/.claude/.rl_warn"
USAGE="$(dirname "$0")/claude-usage.sh"
CAP_PCT="${CAR_CAP_PCT:-100}"
BUFFER="${CAR_BUFFER:-60}"
MAX_LOOPS="${CAR_MAX_LOOPS:-12}"

# Resolve the real binary so the `claude` alias can't recurse into this wrapper.
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude)}"
if [ -z "$CLAUDE_BIN" ]; then
  echo "car: cannot find the claude binary on PATH" >&2
  exit 127
fi

now() { date +%s; }

# Echo the reset epoch if claude was actually killed by the usage wall, else
# echo nothing. The live query is authoritative: only a 5h window at the true
# cap (>= CAP_PCT) means the wall was hit — merely being near cap is not, or a
# voluntary quit near cap would spuriously resume. The flag is only a
# reset-epoch source and an offline fallback.
capped_reset_epoch() {
  local line pct reset
  if line=$("$USAGE" --session-status 2>/dev/null); then
    pct=${line%% *}
    reset=${line##* }
    if [ -n "$reset" ] && [ "$pct" -ge "$CAP_PCT" ] && [ "$reset" -gt "$(now)" ]; then
      printf '%s' "$reset"
      return 0
    fi
    return 1 # reached the endpoint and we're below the cap → not rate-limited
  fi
  # Endpoint unreachable (offline). Best-effort: trust the statusline flag's
  # reset epoch if it's still in the future — better to resume than to stop.
  reset=$(sed -n 's/^5h_reset=//p' "$FLAG" 2>/dev/null)
  if [ -n "$reset" ] && [ "$reset" -gt "$(now)" ]; then
    printf '%s' "$reset"
    return 0
  fi
  return 1
}

# Sleep until `epoch + BUFFER`, printing a one-line countdown. Ctrl-C aborts the
# wait (and the whole wrapper) rather than dumping you into a resumed session.
wait_until() {
  local target=$(($1 + BUFFER))
  trap 'echo; echo "car: wait aborted — not resuming."; exit 130' INT
  while :; do
    local remaining=$((target - $(now)))
    [ "$remaining" -le 0 ] && break
    printf '\rcar: rate-limited. Resuming in %02dh%02dm%02ds …' \
      $((remaining / 3600)) $(((remaining % 3600) / 60)) $((remaining % 60))
    sleep 15
  done
  trap - INT
  printf '\rcar: reset window passed — resuming.%40s\n' ''
}

loops=0
first=1
while :; do
  # Absorb Ctrl-C so it reaches claude (interrupting the turn) without killing
  # this wrapper; the wrapper's own Ctrl-C handling lives in wait_until.
  trap 'true' INT
  if [ "$first" -eq 1 ]; then
    "$CLAUDE_BIN" "$@"
    rc=$?
    first=0
  else
    # --continue resumes the most recent conversation in this directory, so we
    # avoid parsing session UUIDs out of the JSONL. In an overnight tmux run
    # this session is the only one, so "most recent" is unambiguous.
    "$CLAUDE_BIN" --continue
    rc=$?
  fi
  trap - INT

  reset=$(capped_reset_epoch) || {
    # Not rate-limited — claude exited on its own. Propagate its status so a
    # crash/error isn't masked as success for scripted (CAR_TMUX=0) callers.
    echo "car: claude exited (status $rc) and no active rate limit detected — done."
    exit "$rc"
  }

  loops=$((loops + 1))
  if [ "$loops" -gt "$MAX_LOOPS" ]; then
    echo "car: hit CAR_MAX_LOOPS=$MAX_LOOPS resumes — stopping to avoid a runaway loop." >&2
    exit 1
  fi

  wait_until "$reset"
  rm -f "$FLAG"
done
