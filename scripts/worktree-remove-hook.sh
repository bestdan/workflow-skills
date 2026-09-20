#!/usr/bin/env bash
# Claude Code WorktreeRemove hook: tear a hook-created worktree down through
# scripts/worktree-remove.sh, so the lifecycle worktree-create-hook.sh opens
# closes without a prompt.
#
# What this hook can and cannot reach. The harness dispatches WorktreeRemove
# from exactly three places: the ExitWorktree tool, the interactive exit
# dialog, and a forced background-job delete. A `claude -p` session that exits
# while still inside a worktree fires SessionEnd and nothing else, and both of
# the harness's own sweeps skip a hook-created worktree by construction — one
# only scans .claude/worktrees/, the other returns early when the worktree is
# hook-based. So this hook closes the interactive and explicit-exit paths; it
# does NOT close unattended accumulation, which needs a sweeper. Verified
# against Claude Code 2.1.260 by probing the hook and reading the shipped
# binary; see bestdan/dotfiles#677.
#
# The payload carries `worktree_path` plus the common fields; there is no
# `reason` field, whatever the public docs say. A non-zero exit does not block:
# the harness re-stats the path afterwards and logs "WorktreeRemove hook did
# not remove worktree, kept at: <path>" to the debug log. That log is the only
# place it appears — verified end-to-end, ExitWorktree still answered "Exited
# and removed worktree at <path>" for a worktree this hook had just kept. So a
# refusal here is safe but invisible, which is why every refusal below prints
# its reason.
#
# The harness checks the worktree is clean BEFORE dispatching, so a dirty tree
# never reaches this script. worktree-remove.sh's own dirty refusal stays as a
# backstop rather than as the primary gate.
#
# Hooks run unsandboxed — `gh` is authenticated and api.github.com is
# reachable from here — so worktree-remove.sh's merged-PR branch gate and its
# write probes all work. That gate keeps the branch unless a merged PR's head
# is the branch's current tip, which at session exit it usually is not: the
# worktree goes, the work does not.
#
# Covered by test/worktree-remove-hook.bats.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)

say() { printf 'worktree-remove: %s\n' "$1" >&2; }

# One pass over the payload, same shape as the create hook's reader.
payload=$(python3 -c '
import json, sys
d = json.load(sys.stdin)
for k in ("worktree_path", "session_id"):
    v = d.get(k) or ""
    print(str(v).replace("\n", " "))
')
path=$(sed -n 1p <<<"$payload")
session=$(sed -n 2p <<<"$payload")

if [[ -z "$path" ]]; then
  say 'payload carried no worktree_path'
  exit 1
fi

# Already gone — the job is done, and saying so as a failure would report a
# finished teardown as one to retry.
real=$(cd -- "$path" 2>/dev/null && pwd -P) || true
if [[ -z "$real" ]]; then
  exit 0
fi

# --- ownership gates -------------------------------------------------------
# This hook is configured plugin-wide, so it is handed every worktree the
# harness removes, including ones created by hand or by some other tool. Each
# gate below answers "did worktree-create-hook.sh make this, for this session?"
# and a no keeps the worktree. A kept worktree costs one
# `scripts/worktree-remove.sh` later; a wrongly removed one costs the checkout.

# The same resolver the create hook used, run from the worktree so a repo-local
# config is read from the repo this worktree belongs to.
configured=$(cd -- "$real" && "$script_dir/worktree-config.sh" root) || {
  say "keeping $path — the worktree root could not be resolved"
  exit 1
}
parent=$(cd -- "$configured" 2>/dev/null && pwd -P) || true
if [[ -z "$parent" || "$real" != "$parent"/*/* ]]; then
  say "keeping $path — not under $configured/<repo>/, so this hook did not create it"
  exit 0
fi

gitdir=$(git -C "$real" rev-parse --path-format=absolute --git-dir 2>/dev/null) || true
common=$(git -C "$real" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || true
if [[ -z "$gitdir" || -z "$common" || "$gitdir" == "$common" ]]; then
  say "keeping $path — not a linked worktree"
  exit 0
fi

# worktree-create-hook.sh names the directory and the branch together. A branch
# that no longer matches means someone re-pointed the checkout, and this hook
# has no business guessing what they meant.
prefix=$(cd -- "$real" && "$script_dir/worktree-config.sh" prefix) || {
  say "keeping $path — the branch prefix could not be resolved"
  exit 1
}
expected="$prefix$(basename -- "$real")"
branch=$(git -C "$real" symbolic-ref --quiet --short HEAD 2>/dev/null) || true
if [[ "$branch" != "$expected" ]]; then
  say "keeping $path — on ${branch:-a detached HEAD}, not $expected"
  exit 0
fi

# The collision this closes: worktree-create-hook.sh deliberately hands an
# existing worktree back rather than refusing a re-used name, so two sessions
# can share one checkout, and one session's exit would delete the other's live
# working tree. The create hook stamps a session id only on the worktree it
# actually created; a hand-back to a different session rewrites the marker to
# the sticky `shared`, which matches nothing here, and a hand-back of an
# unmarked worktree leaves it unmarked. So the three keeps below cover every
# shared tree, and a worktree predating this scheme is kept as well.
owner_file="$gitdir/claude-session-owner"
owner=$(cat -- "$owner_file" 2>/dev/null) || true
if [[ -z "$owner" ]]; then
  say "keeping $path — no session owner recorded, so it predates this hook"
  exit 0
fi
# Ownership is the gate on a destructive operation, so an unidentifiable
# session is a refusal, not a pass. A payload with no session_id would
# otherwise skip the comparison and tear down a worktree a live session owns.
if [[ -z "$session" ]]; then
  say "keeping $path — payload carried no session_id, so ownership cannot be checked"
  exit 0
fi
if [[ "$owner" != "$session" ]]; then
  say "keeping $path — owned by session $owner, not $session"
  exit 0
fi

# --- teardown --------------------------------------------------------------
# worktree-remove.sh refuses to remove the worktree its caller is standing in,
# and this hook's own cwd IS the worktree (verified: the probe logged PWD as
# the target). So run it from the main checkout.
root=$(dirname -- "$common")
cd -- "$root"
exec "$script_dir/worktree-remove.sh" "$real"
