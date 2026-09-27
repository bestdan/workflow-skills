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
#     [--interval <seconds>] [--timeout <seconds>] \
#     [--grace <seconds>] [--commit <sha>]
#
#   --pr        PR number (required).
#   --repo      owner/name (required).
#   --reviewer  Expected reviewer login. Repeatable. Default: Copilot.
#   --mode      all  → wait for every expected reviewer (default).
#               any  → return as soon as one expected reviewer lands.
#   --interval  Seconds between polls. Default: 30.
#   --timeout   Total seconds before giving up. Default: 900 (15m).
#   --grace     Seconds a reviewer gets to be requested. Once this has
#               elapsed, a reviewer that has not landed and was never requested
#               (see "Requested" below) is dropped as not-requested rather than
#               waited on for the full --timeout. Default: unset (wait on every
#               reviewer). GitHub requests an auto-review Copilot about a second
#               after the PR opens, so 30 is ample.
#   --commit    Count only reviews of this commit (a review's `commit.oid`,
#               matched as a prefix), and only review requests made after it
#               was committed. Pass the PR's headRefOid, so a review of an
#               earlier push does not satisfy the wait after a re-push.
#               Default: unset (any review or request counts).
#
# Exit status: 0 landed, 1 timeout, 2 usage error, 3 when --grace dropped
# every expected reviewer as not-requested and none landed.
# Final line is structured and parseable:
#   AWAIT_REVIEW: landed reviewer=<csv> after=<S>s [not-requested=<csv>]
#   AWAIT_REVIEW: timeout reviewer=<csv-of-missing> after=<S>s [not-requested=<csv>]
#   AWAIT_REVIEW: not-requested reviewer=<csv> after=<S>s
#
# "Landed" means a `reviews[]` entry whose author.login matches the reviewer
# (and, with --commit, reviewed that commit). Nothing else counts. In
# particular, a reviewer leaving `reviewRequests[]` is NOT a landing signal:
# Copilot leaves it when it STARTS work (the `copilot_work_started` timeline
# event, ~30s after the request), and its review arrives 1-3 minutes later.
#
# "Requested" (the --grace test) means the reviewer appeared in
# `reviewRequests[]` on some poll, or the PR's timeline holds a
# `review_requested` event for it or an event whose name contains it
# (`copilot_work_started`). The timeline is what makes a late start work:
# polled more than ~30s after the request, `reviewRequests[]` no longer shows
# Copilot, and only the timeline says it is reviewing.
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
grace=""
commit=""

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
    --grace)
      grace="$2"
      shift 2
      ;;
    --commit)
      commit="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,57p' "$0"
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
case "$grace" in *[!0-9]*) die "--grace must be a non-negative integer (seconds)" ;; esac
# An empty or non-hex --commit would prefix-match every review, or none.
case "$commit" in
  "") ;;
  *[!0-9a-fA-F]*) die "--commit must be a hex commit sha" ;;
esac
# A missing dependency would otherwise look like a transient empty response and
# loop until timeout — fail fast with an actionable message instead.
command -v gh >/dev/null 2>&1 || die "gh CLI is required but not found in PATH"
command -v jq >/dev/null 2>&1 || die "jq is required but not found in PATH"

# Lowercase helper for case-insensitive matching.
lc() { tr '[:upper:]' '[:lower:]'; }

csv() {
  local IFS=,
  echo "$*"
}

# Reviewers we still need to see land (lowercased). Drained as they land.
pending=()
for r in "${reviewers[@]}"; do pending+=("$(printf '%s' "$r" | lc)"); done
landed=()
not_requested=()

# Emit the final structured line: $1 is the outcome, $2 the reviewer csv.
report() {
  local extra=""
  if [ "$1" != not-requested ] && [ "${#not_requested[@]}" -gt 0 ]; then
    extra=" not-requested=$(csv "${not_requested[@]}")"
  fi
  echo "AWAIT_REVIEW: $1 reviewer=$2 after=${SECONDS}s$extra"
}

# Everything that shows a reviewer was requested: reviewRequests[] logins from
# every poll so far, plus timeline matches once the --grace test needs them.
seen_requested=""

# With --commit, a timeline request only counts if it came after that commit
# was made — an earlier push's request says nothing about this one. If the
# date can't be read, every request counts, which errs toward waiting.
requested_since=""
if [ -n "$grace" ] && [ -n "$commit" ]; then
  requested_since="$(gh api "repos/$repo/commits/$commit" | jq -r '.commit.committer.date // empty')"
fi

# Print the timeline's request evidence, one lowercased entry per line: the
# requested reviewer of each review_requested event, and every other event's
# name (so `copilot_work_started` matches the token `copilot`). Prints nothing
# and fails when the timeline can't be read.
timeline_requests() {
  local tl
  tl="$(gh api --paginate "repos/$repo/issues/$pr/timeline")"
  [ -n "$tl" ] || return 1
  printf '%s' "$tl" | jq -r --arg since "$requested_since" \
    '.[]? | select($since == "" or (.created_at // "") >= $since)
     | if .event == "review_requested" then (.requested_reviewer.login // .requested_team.name // empty) else (.event // empty) end' | lc
}

SECONDS=0
while :; do
  polled_at=$SECONDS
  json="$(gh pr view "$pr" --repo "$repo" --json reviews,reviewRequests)"
  if [ -z "$json" ]; then
    # Transient gh/network failure: surface it (don't silently swallow) and
    # retry on the next tick rather than treating it as "landed".
    echo "await-pr-review: gh returned no data (transient?), retrying" >&2
  else
    review_logins="$(printf '%s' "$json" | jq -r --arg commit "$commit" \
      '.reviews[]? | select($commit == "" or ((.commit.oid // "") | ascii_downcase | startswith($commit | ascii_downcase))) | .author.login // empty' | lc)"
    requested_logins="$(printf '%s' "$json" | jq -r '.reviewRequests[]? | (.login // .name // empty)' | lc)"
    seen_requested="$seen_requested
$requested_logins"
    timeline_read=0

    still_pending=()
    for token in "${pending[@]}"; do
      if grep -qiF "$token" <<<"$review_logins"; then
        landed+=("$token")
        continue
      fi
      if [ -n "$grace" ] && [ "$polled_at" -ge "$grace" ] \
        && ! grep -qiF "$token" <<<"$seen_requested"; then
        # Read the timeline at most once per poll, and only when a reviewer is
        # about to be dropped. An unreadable timeline drops nobody this poll.
        if [ "$timeline_read" = 0 ]; then
          if tl_requests="$(timeline_requests)"; then
            timeline_read=1
            seen_requested="$seen_requested
$tl_requests"
          else
            timeline_read=2
            echo "await-pr-review: could not read the PR timeline, retrying" >&2
          fi
        fi
        if [ "$timeline_read" = 1 ] && ! grep -qiF "$token" <<<"$seen_requested"; then
          not_requested+=("$token")
          continue
        fi
      fi
      still_pending+=("$token")
    done
    pending=("${still_pending[@]+"${still_pending[@]}"}") # empty-safe under bash 3.2 set -u

    if [ "$mode" = "any" ] && [ "${#landed[@]}" -gt 0 ]; then
      report landed "$(csv "${landed[@]}")"
      exit 0
    fi
    if [ "${#pending[@]}" -eq 0 ]; then
      if [ "${#landed[@]}" -gt 0 ]; then
        report landed "$(csv "${landed[@]}")"
        exit 0
      fi
      report not-requested "$(csv "${not_requested[@]}")"
      exit 3
    fi
  fi

  [ "$SECONDS" -ge "$timeout" ] && break
  # Poll again at the grace deadline rather than up to an interval past it, so
  # a reviewer that was never requested costs --grace and no more.
  nap=$interval
  if [ -n "$grace" ] && [ "$SECONDS" -lt "$grace" ] && [ $((grace - SECONDS)) -lt "$nap" ]; then
    nap=$((grace - SECONDS))
  fi
  sleep "$nap"
  [ "$SECONDS" -ge "$timeout" ] && break
done

report timeout "$(csv "${pending[@]}")"
exit 1
