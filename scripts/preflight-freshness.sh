#!/usr/bin/env bash
# preflight-freshness.sh — check that local branches aren't stale before a
# flow that depends on them (review, diff, branch-off) starts.
#
# Replaces the failure mode where a flow like `/co-review` reviews or edits a
# checkout that is behind its remote — e.g. `--local` diffing against a stale
# `main`, or auto-fixes applied to a branch whose remote tip has moved.
#
# Read-only by design: it compares refs via `git ls-remote` and never fetches,
# so local state is not mutated. It needs network for the ls-remote call.
#
# Usage:
#   scripts/preflight-freshness.sh [--remote <name>] [--ref <branch> ...]
#
#   --remote  Remote to compare against. Default: origin.
#   --ref     Branch to check. Repeatable. Default: the current branch.
#
# Per-ref verdicts (local branch vs remote tip):
#   fresh     local == remote tip, or local is strictly ahead (remote tip is
#             an ancestor of local).
#   stale     local is behind or has diverged from the remote tip, or the
#             remote tip object isn't present locally (an unfetched remote
#             commit is by definition ahead of us).
#   skipped   the branch has no counterpart on the remote (never pushed) or
#             no local ref exists — nothing to be stale against.
#
# Exit status and structured final line (parseable, mirrors await-pr-review):
#   0  FRESHNESS: fresh refs=<csv>                        all checked refs fresh
#   1  FRESHNESS: stale refs=<csv> hint="git fetch <remote>"   any ref stale
#   2  usage or dependency error
#   3  FRESHNESS: unknown reason=ls-remote-failed         offline/sandboxed —
#      callers should warn and let the user decide, not hard-fail
set -uo pipefail

remote="origin"
refs=()

die() { echo "preflight-freshness: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --remote) [ $# -ge 2 ] || die "missing value for --remote"; remote="$2"; shift 2 ;;
    --ref) [ $# -ge 2 ] || die "missing value for --ref"; refs+=("$2"); shift 2 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

command -v git >/dev/null 2>&1 || die "git is required but not found in PATH"
git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository"
git remote get-url "$remote" >/dev/null 2>&1 || die "remote '$remote' is not configured"

if [ "${#refs[@]}" -eq 0 ]; then
  current="$(git branch --show-current)"
  [ -n "$current" ] || die "detached HEAD and no --ref given"
  refs=("$current")
fi

# One network round-trip for all refs. A failure here is the sandbox or a dead
# network, not staleness — report unknown so callers can degrade gracefully.
if ! remote_heads="$(git ls-remote --heads "$remote" "${refs[@]}" 2>&1)"; then
  echo "preflight-freshness: git ls-remote failed: $remote_heads" >&2
  echo "FRESHNESS: unknown reason=ls-remote-failed"
  exit 3
fi

stale=()
fresh=()
for ref in "${refs[@]}"; do
  remote_sha="$(printf '%s\n' "$remote_heads" | awk -v r="refs/heads/$ref" '$2 == r {print $1}')"
  if [ -z "$remote_sha" ]; then
    echo "preflight-freshness: $ref — skipped (no refs/heads/$ref on $remote)"
    continue
  fi
  local_sha="$(git rev-parse --verify --quiet "refs/heads/$ref")" || {
    echo "preflight-freshness: $ref — skipped (no local branch)"
    continue
  }
  if [ "$local_sha" = "$remote_sha" ]; then
    fresh+=("$ref")
    echo "preflight-freshness: $ref — fresh (local == $remote)"
  elif git cat-file -e "$remote_sha" 2>/dev/null \
    && git merge-base --is-ancestor "$remote_sha" "$local_sha"; then
    fresh+=("$ref")
    echo "preflight-freshness: $ref — fresh (local ahead of $remote)"
  else
    stale+=("$ref")
    echo "preflight-freshness: $ref — STALE (local $local_sha vs $remote $remote_sha)"
  fi
done

if [ "${#stale[@]}" -gt 0 ]; then
  IFS=,; echo "FRESHNESS: stale refs=${stale[*]} hint=\"git fetch $remote\""
  exit 1
fi
IFS=,; echo "FRESHNESS: fresh refs=${fresh[*]-}"
exit 0
