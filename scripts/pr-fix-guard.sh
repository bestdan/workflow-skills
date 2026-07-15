#!/usr/bin/env bash
# pr-fix-guard.sh — stop review fixes from being silently orphaned when a PR is
# merged mid-flow.
#
# The failure this prevents (observed on PR #214): `/co-review` reads a PR,
# applies fixes, commits, and pushes to the PR's branch. If the PR is merged in
# the window between the review starting and the push landing, the push still
# SUCCEEDS (the branch ref still exists) and exits 0 — but the fixes now sit on a
# branch nobody will merge again. They never reach the base branch, and nothing
# errors to say so.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY THIS USES A CONTENT DIFF AND NOT AN ANCESTRY CHECK — do not "simplify" it.
# This repo SQUASH-merges. After a squash merge, the branch's own commits are
# NEVER ancestors of the base branch, even on a perfectly healthy merge — the
# squash creates a brand-new commit with different SHAs. So
# `git merge-base --is-ancestor <branch-sha> origin/main` returns false in BOTH
# the healthy case and the orphaned case: it cannot tell them apart and is
# useless here. The ONLY reliable test of "did my fixes reach the base branch"
# is to compare the CONTENT of the files the commit touched against the base
# tip. That is what `verify` does. A regression test pins this; if you find
# yourself reaching for `--is-ancestor`, stop.
# ─────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   scripts/pr-fix-guard.sh check  --pr <n> [--repo <owner/name>]
#   scripts/pr-fix-guard.sh verify --pr <n> --commit <sha> \
#                                  [--base <branch>] [--remote <name>] [--repo <owner/name>]
#
#   check   Query PR state BEFORE committing/pushing review fixes. If the PR is
#           already merged or closed, the branch is dead — don't push into it.
#   verify  AFTER pushing, confirm the fixes actually reached <base>. Catches a
#           merge that raced the push. Content diff, not ancestry (see above).
#
#   --pr      PR number (required).
#   --commit  The fix commit to verify reached base (required for `verify`).
#   --base    Base branch the PR merges into. Default: main.
#   --remote  Remote to fetch/compare against. Default: origin.
#   --repo    owner/name for `gh` when not inferable from the checkout.
#
# `check` structured final line + exit status:
#   0  PRGUARD: state=open        PR open — safe to push fixes
#   4  PRGUARD: state=merged      PR merged — branch is dead, recover instead
#   4  PRGUARD: state=closed      PR closed unmerged — same, don't push
#   3  PRGUARD: state=unknown     gh failed / offline — caller warns, decides
#
# `verify` structured final line + exit status:
#   0  PRGUARD: verdict=open      PR still open — nothing to verify yet
#   0  PRGUARD: verdict=landed    the touched files' content is in <base>
#   5  PRGUARD: verdict=orphaned  fixes are NOT in <base> (one orphaned-file=
#      line per differing path precedes the verdict) — recover them
#   3  PRGUARD: verdict=unknown   gh/fetch failed — caller warns, decides
#
# Other exit status:
#   2  usage or dependency error
#
# Network: both subcommands call `gh` (PR state); `verify` also runs
# `git fetch <remote> <base>`. Run unsandboxed.
set -uo pipefail

die() {
  echo "pr-fix-guard: $*" >&2
  exit 2
}

usage() {
  # Print the leading comment block (past the shebang), stopping at the first
  # non-comment line — so this can't drift into printing code as the file grows.
  awk 'NR > 1 { if (/^#/) print; else exit }' "$0"
}

# Print the PR's state (OPEN/MERGED/CLOSED) lowercased, or empty on failure.
pr_state() {
  local pr="$1" repo="$2" out
  local -a args=(pr view "$pr" --json state --jq .state)
  [ -n "$repo" ] && args+=(--repo "$repo")
  out="$(gh "${args[@]}" 2>/dev/null)" || return 1
  printf '%s' "$out" | tr '[:upper:]' '[:lower:]'
}

cmd_check() {
  local pr="" repo=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pr) [ $# -ge 2 ] || die "missing value for --pr"; pr="$2"; shift 2 ;;
      --repo) [ $# -ge 2 ] || die "missing value for --repo"; repo="$2"; shift 2 ;;
      -h | --help) usage; exit 0 ;;
      *) die "unknown argument: $1" ;;
    esac
  done
  [ -n "$pr" ] || die "check: --pr is required"
  command -v gh >/dev/null 2>&1 || die "gh is required but not found in PATH"

  local state
  if ! state="$(pr_state "$pr" "$repo")" || [ -z "$state" ]; then
    echo "PRGUARD: state=unknown"
    exit 3
  fi
  case "$state" in
    open) echo "PRGUARD: state=open"; exit 0 ;;
    merged) echo "PRGUARD: state=merged"; exit 4 ;;
    closed) echo "PRGUARD: state=closed"; exit 4 ;;
    *) echo "PRGUARD: state=unknown"; exit 3 ;;
  esac
}

cmd_verify() {
  local pr="" commit="" base="main" remote="origin" repo=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pr) [ $# -ge 2 ] || die "missing value for --pr"; pr="$2"; shift 2 ;;
      --commit) [ $# -ge 2 ] || die "missing value for --commit"; commit="$2"; shift 2 ;;
      --base) [ $# -ge 2 ] || die "missing value for --base"; base="$2"; shift 2 ;;
      --remote) [ $# -ge 2 ] || die "missing value for --remote"; remote="$2"; shift 2 ;;
      --repo) [ $# -ge 2 ] || die "missing value for --repo"; repo="$2"; shift 2 ;;
      -h | --help) usage; exit 0 ;;
      *) die "unknown argument: $1" ;;
    esac
  done
  [ -n "$pr" ] || die "verify: --pr is required"
  [ -n "$commit" ] || die "verify: --commit is required"
  command -v gh >/dev/null 2>&1 || die "gh is required but not found in PATH"
  command -v git >/dev/null 2>&1 || die "git is required but not found in PATH"
  git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository"
  git cat-file -e "${commit}^{commit}" 2>/dev/null || die "commit '$commit' not found"

  local state
  if ! state="$(pr_state "$pr" "$repo")" || [ -z "$state" ]; then
    echo "PRGUARD: verdict=unknown"
    exit 3
  fi
  # Still open → the push has a live branch to land on; nothing to verify.
  if [ "$state" = "open" ]; then
    echo "PRGUARD: verdict=open"
    exit 0
  fi

  # Merged/closed: did the fix content actually reach <base>? Refresh the base
  # tip, then compare ONLY the files this commit touched (content, not ancestry
  # — see the header). Empty commit (no files) counts as trivially landed.
  if ! git fetch "$remote" "$base" >/dev/null 2>&1; then
    echo "PRGUARD: verdict=unknown"
    exit 3
  fi

  # Portable to bash 3.2 (macOS /bin/bash) — no `mapfile`.
  local -a files=()
  local line
  while IFS= read -r line; do
    [ -n "$line" ] && files+=("$line")
  done < <(git diff-tree --no-commit-id --name-only -r "$commit")
  if [ "${#files[@]}" -eq 0 ]; then
    echo "PRGUARD: verdict=landed"
    exit 0
  fi

  if git diff --quiet "$remote/$base" "$commit" -- "${files[@]}"; then
    echo "PRGUARD: verdict=landed"
    exit 0
  fi

  # Something the commit changed differs from base → orphaned. Report which.
  local f
  for f in "${files[@]}"; do
    git diff --quiet "$remote/$base" "$commit" -- "$f" || echo "PRGUARD: orphaned-file=$f"
  done
  echo "PRGUARD: verdict=orphaned"
  exit 5
}

[ $# -ge 1 ] || { usage >&2; exit 2; }
subcmd="$1"
shift
case "$subcmd" in
  check) cmd_check "$@" ;;
  verify) cmd_verify "$@" ;;
  -h | --help) usage; exit 0 ;;
  *) echo "pr-fix-guard: unknown subcommand: $subcmd" >&2; usage >&2; exit 2 ;;
esac
