#!/usr/bin/env bash
# Print (and create) this work's private temp directory.
#
# Usage: scripts/work-tmp.sh [--key <name>] [--sub <name>] [--root <dir>]
#   stdout  /tmp/claude/<repo>/<branch>[/<sub>]   absolute, no trailing slash
#   exit 0  printed; the directory exists and every directory this call
#           created is mode 0700
#   exit 2  usage error
#   exit 3  no branch, no $CLAUDE_CODE_SESSION_ID and no --key: nothing to
#           scope the directory by
#
# Why: /tmp/claude/ is one directory shared by every Claude session on the
# machine, and the one path that is identical inside and outside the Bash
# sandbox. A fixed file name there is overwritten by a concurrent session or a
# parallel worker (#1011: one worker pushed another's PR body). Write every
# temp file under the directory this prints instead.
#
# Why the branch: one worktree per branch, so the branch names the work, and
# two pieces of work never share a directory. <repo> is the MAIN repo's name
# (from the common dir), so every worktree of one repo shares it. `/` in a
# branch is kept (bestdan/task-1 -> bestdan/task-1); every character outside
# [A-Za-z0-9._/-] becomes `_`.
#
# Fallbacks, when HEAD names no branch:
#   --key <name>                       replaces the branch component
#   $CLAUDE_CODE_SESSION_ID            _session/<id>            (any cwd)
#   detached HEAD inside a repo        _detached/<short sha>
#   neither (outside a repo)           exit 3
# Outside a repo with a session id the repo component is `_norepo`.
#
# --root <dir> (or $WORK_TMP_ROOT) replaces /tmp/claude, for tests. It never
# reads $TMPDIR, which differs sandboxed vs unsandboxed.
set -uo pipefail

root="${WORK_TMP_ROOT:-/tmp/claude}"
key=""
sub=""
usage() {
  echo "usage: scripts/work-tmp.sh [--key <name>] [--sub <name>] [--root <dir>]" >&2
  exit 2
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --key | --sub | --root)
      [ "$#" -ge 2 ] && [ -n "$2" ] || usage
      case "$1" in
        --key) key="$2" ;;
        --sub) sub="$2" ;;
        --root) root="$2" ;;
      esac
      shift 2
      ;;
    *) usage ;;
  esac
done

# sanitize <s> [slash] — map everything outside the allowed set to `_`.
sanitize() {
  if [ "${2:-}" = slash ]; then
    printf '%s' "$1" | LC_ALL=C sed 's|[^A-Za-z0-9._/-]|_|g'
  else
    printf '%s' "$1" | LC_ALL=C sed 's|[^A-Za-z0-9._-]|_|g'
  fi
}

repo="_norepo"
branch=""
sha=""
common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || common=""
if [ -n "$common" ]; then
  case "$common" in
    */.git) name="$(basename "$(dirname "$common")")" ;;
    *) name="$(basename "$common")" ;;
  esac
  repo="$(sanitize "${name%.git}")"
  branch="$(git symbolic-ref --short -q HEAD 2>/dev/null)" || branch=""
  sha="$(git rev-parse --short HEAD 2>/dev/null)" || sha=""
fi

# `.` survives sanitize, so `--key ..` or `--sub ..` would climb out of the
# directory this exists to scope.
for part in "$key" "$sub"; do
  case "$part" in
    . | ..)
      echo "work-tmp: --key/--sub may not be '.' or '..'" >&2
      exit 2
      ;;
  esac
done

session="${CLAUDE_CODE_SESSION_ID:-}"
if [ -n "$key" ]; then
  leaf="$(sanitize "$key")"
elif [ -n "$branch" ]; then
  leaf="$(sanitize "$branch" slash)"
elif [ -n "$session" ]; then
  leaf="_session/$(sanitize "$session")"
elif [ -n "$sha" ]; then
  leaf="_detached/$(sanitize "$sha")"
else
  echo "work-tmp: no branch and no \$CLAUDE_CODE_SESSION_ID; pass --key <name>" >&2
  exit 3
fi
[ -z "$sub" ] || leaf="$leaf/$(sanitize "$sub")"

dir="${root%/}/$repo/$leaf"
umask 077
mkdir -p "$dir" || exit 1
printf '%s\n' "$dir"
