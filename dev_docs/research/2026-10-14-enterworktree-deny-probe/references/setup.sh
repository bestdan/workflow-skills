#!/usr/bin/env bash
# Build the throwaway repo for the #1001 probe.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$here/probe1001"
rm -rf "$repo"
mkdir -p "$repo/.claude"
cd "$repo"
GIT_CONFIG_GLOBAL=/dev/null git init -q -b main
echo "probe" >README.md
GIT_CONFIG_GLOBAL=/dev/null git add README.md
GIT_CONFIG_GLOBAL=/dev/null git -c user.name=probe -c user.email=probe@example.invalid commit -qm init
# Project settings stay untracked, so the worktree checkout carries no .claude/.
cat >.claude/settings.json <<EOF
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "EnterWorktree",
        "hooks": [
          { "type": "command", "command": "python3 $here/stub-deny-enter-by-path.py" }
        ]
      }
    ]
  }
}
EOF
git log --oneline
git status --short
