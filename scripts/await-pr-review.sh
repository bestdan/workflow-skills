#!/usr/bin/env bash
# await-pr-review.sh — block until a bot reviewer lands its review on a PR.
#
# Replaces the ad-hoc `for i in $(seq …); do gh pr view … ; sleep 30; done`
# poll loops that get re-derived (inconsistently, and untested) every time a
# flow needs to wait for a bot reviewer — e.g. `/co-review` on a freshly
# opened PR waiting for GitHub Copilot before reconciling.
#
# Usage:
#   scripts/await-pr-review.sh --pr <N> --repo <owner/name> \
#     [--reviewer <login> ...] [--mode all|any] \
#     [--interval <seconds>] [--timeout <seconds>]
#
#   --pr        PR number (required).
#   --repo      owner/name (required).
#   --reviewer  Expected reviewer login. Repeatable. Default: Copilot.
#   --mode      all  → wait for every expected reviewer (default).
#               any  → return as soon as one expected reviewer lands.
#   --interval  Seconds between polls. Default: 30.
#   --timeout   Total seconds before giving up. Default: 900 (15m).
#
# Exit status: 0 when the wait is satisfied (landed), non-zero on timeout.
# Final line is structured and parseable:
#   AWAIT_REVIEW: landed reviewer=<csv> after=<S>s
#   AWAIT_REVIEW: timeout reviewer=<csv-of-missing> after=<S>s
#
# "Landed" detection (precedence, per reviewer):
#   1. PRIMARY — a `reviews[]` entry whose author.login matches the reviewer.
#      This is authoritative: the review actually exists.
#   2. FALLBACK — the reviewer was present in `reviewRequests[]` on the FIRST
#      poll but is absent now. A requested bot that drops out of
#      reviewRequests[] has reviewed and been cleared, even in the rare window
#      where its reviews[] entry isn't surfaced yet. (A reviewer that was never
#      requested and never reviewed never trips this fallback.)
#
# Login gotcha (why matching is a case-insensitive SUBSTRING, not equality):
#   GitHub reports Copilot under TWO different identifiers. In `reviews[]` the
#   author.login is `copilot-pull-request-reviewer`; in `reviewRequests[]` the
#   requested reviewer shows the app display name `Copilot`. Keying on a single
#   exact string (`select(.author.login=="Copilot")`) matches the request but
#   NEVER the landed review — so the watcher loops the full timeout and then
#   exits "success" having noticed nothing. Substring matching lets the default
#   token `Copilot` match BOTH `Copilot` and `copilot-pull-request-reviewer`.
set -uo pipefail

pr=""
repo=""
reviewers=()
mode="all"
interval=30
timeout=900

die() {
  echo "await-pr-review: $*" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --pr)
      pr="$2"
      shift 2
      ;;
    --repo)
      repo="$2"
      shift 2
      ;;
    --reviewer)
      reviewers+=("$2")
      shift 2
      ;;
    --mode)
      mode="$2"
      shift 2
      ;;
    --interval)
      interval="$2"
      shift 2
      ;;
    --timeout)
      timeout="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,40p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$pr" ] || die "--pr is required"
[ -n "$repo" ] || die "--repo is required"
[ "${#reviewers[@]}" -gt 0 ] || reviewers=("Copilot")
case "$mode" in all | any) ;; *) die "--mode must be 'all' or 'any'" ;; esac
# Non-integer interval/timeout would make `sleep` fail and the `-ge` comparison
# error every tick — with no `set -e` that busy-spins, hammering the gh API.
case "$interval" in *[!0-9]* | "") die "--interval must be a non-negative integer (seconds)" ;; esac
case "$timeout" in *[!0-9]* | "") die "--timeout must be a non-negative integer (seconds)" ;; esac
# A missing dependency would otherwise look like a transient empty response and
# loop until timeout — fail fast with an actionable message instead.
command -v gh >/dev/null 2>&1 || die "gh CLI is required but not found in PATH"
command -v jq >/dev/null 2>&1 || die "jq is required but not found in PATH"

# Lowercase helper for case-insensitive matching.
lc() { tr '[:upper:]' '[:lower:]'; }

# Reviewers we still need to see land (lowercased). Drained as they land.
pending=()
for r in "${reviewers[@]}"; do pending+=("$(printf '%s' "$r" | lc)"); done
landed=()

# Requested-reviewer set observed on the first poll (for the fallback signal).
initial_requested=""
first_poll=1

SECONDS=0
while :; do
  json="$(gh pr view "$pr" --repo "$repo" --json reviews,reviewRequests)"
  if [ -z "$json" ]; then
    # Transient gh/network failure: surface it (don't silently swallow) and
    # retry on the next tick rather than treating it as "landed".
    echo "await-pr-review: gh returned no data (transient?), retrying" >&2
  else
    review_logins="$(printf '%s' "$json" | jq -r '.reviews[]?.author.login // empty' | lc)"
    requested_logins="$(printf '%s' "$json" | jq -r '.reviewRequests[]? | (.login // .name // empty)' | lc)"
    [ "$first_poll" = 1 ] && initial_requested="$requested_logins" && first_poll=0

    still_pending=()
    for token in "${pending[@]}"; do
      if printf '%s\n' "$review_logins" | grep -qiF "$token"; then
        landed+=("$token") # signal 1: reviews[]
      elif printf '%s\n' "$initial_requested" | grep -qiF "$token" \
        && ! printf '%s\n' "$requested_logins" | grep -qiF "$token"; then
        landed+=("$token") # signal 2: dropped out
      else
        still_pending+=("$token")
      fi
    done
    pending=("${still_pending[@]+"${still_pending[@]}"}") # empty-safe under bash 3.2 set -u

    if [ "$mode" = "any" ] && [ "${#landed[@]}" -gt 0 ]; then
      IFS=,
      echo "AWAIT_REVIEW: landed reviewer=${landed[*]} after=${SECONDS}s"
      exit 0
    fi
    if [ "${#pending[@]}" -eq 0 ]; then
      IFS=,
      echo "AWAIT_REVIEW: landed reviewer=${landed[*]} after=${SECONDS}s"
      exit 0
    fi
  fi

  [ "$SECONDS" -ge "$timeout" ] && break
  sleep "$interval"
  [ "$SECONDS" -ge "$timeout" ] && break
done

IFS=,
echo "AWAIT_REVIEW: timeout reviewer=${pending[*]} after=${SECONDS}s"
exit 1
