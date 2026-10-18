#!/usr/bin/env bash
# Remove a linked worktree, prune the admin entries a stale worktree leaves
# behind, and delete the branch it held once a merged PR proves that branch is
# spent.
#
#   scripts/worktree-remove.sh <path>
#
#   exit 0   removed (or the checkout was already gone)
#   exit 1   refused by the cwd guard or a pre-flight write probe
#   exit 64  the caller named nothing to remove, or no repository could be
#            found for it — neither the path nor the cwd is in one
#   otherwise git's own exit status from the removal
#
# The path may be relative, and the removal runs in the target's own repo (see
# the root pin below), so an absolute spelling is computed once and used for
# every git call after that pin. The `-e "$path"` checks stay on the original
# spelling: the cwd never changes, so they remain correct as written.
#
# `git worktree remove` cleans up after itself, but an `rm -rf`'d worktree
# strands .git/worktrees/<name>/ (config.worktree included) until something
# prunes it — so prune runs whether or not the remove succeeded. A failed remove
# is only an error when the path is still there — if it's already gone, that
# failure IS the case this script exists to clean up, and swallowing it would
# hide a dirty or locked worktree behind an exit 0.
#
# The removal itself is not atomic, so it is gated on a write probe first — see
# below. The branch delete is the opposite kind of step: it never fails the run.
# It is gated on PR state rather than on ancestry (a squash-merging repo makes
# ancestry useless) — and on the branch tip matching a head a merged PR actually
# merged, since a name alone says nothing about where it points now. A gate that
# cannot be evaluated — no network, no gh auth — keeps the branch and says so.
# That half lives in scripts/branch-remove.sh, which this calls.
#
# Covered by test/worktree-remove.bats.

set -uo pipefail

warn() { printf ' .. %s\n' "$1" >&2; }
cont() { printf '    %s\n' "$1" >&2; }
err() { printf ' !! %s\n' "$1" >&2; }

path="${1-}"

if [ -z "$path" ]; then
  err "name the worktree to remove"
  exit 64
fi

# Refuse to remove the worktree the caller is standing in. This script's own
# shell survives that (see the root pin below), but the *calling* shell can't:
# its cwd stops existing, every later command dies in getcwd, and no cd here can
# reach out and fix a parent. An empty $target means the path doesn't resolve —
# leave that to git's error.
target=$(cd -- "$path" 2>/dev/null && pwd -P)
if [ -n "$target" ]; then
  case "$(pwd -P)/" in
    "$target"/*)
      err "you are inside $path — cd out first, then re-run"
      exit 1
      ;;
  esac
fi

# Pre-flight write probe. `git worktree remove` unlinks files one at a time with
# no transaction, so a write denied partway through leaves a half-deleted
# worktree — and git then refuses the retry as "contains modified or untracked
# files", pointing at your work when the damage is this run's own. The denial
# that actually happens is the sandbox protecting a session's agent-config
# paths, which a repo that versions its own agent config checks out into every
# worktree. Those paths are anchored per session, so only a probe from inside
# this shell can tell — check them before deleting anything.
if [ -n "$target" ]; then
  # Mirrors the harness's sandbox deny set (settings.json →
  # sandbox.filesystem.deny, the worktree-anchored entries). That list is not
  # queryable from a shell, so it is copied here and will drift; only *tracked*
  # paths matter, though, since an untracked one makes git refuse before
  # deleting anything, and a missed path degrades into the postmortem message
  # below rather than silent misdirection.
  for rel in .claude .claude/settings.json .claude/settings.local.json .claude/hooks \
    .claude/skills .claude/commands .claude/agents .claude/workflows \
    .claude/routines .claude/output-styles .claude/launch.json \
    .claude/scheduled_tasks.json .claude/loop.md \
    .mcp.json agents/CLAUDE.md .vscode .vscode/tasks.json; do
    [ -e "$target/$rel" ] || continue
    touch -- "$target/$rel" 2>/dev/null && continue
    err "cannot write $target/$rel — removing would strand a"
    cont "half-deleted worktree, so nothing was touched. Re-run unsandboxed."
    exit 1
  done
fi

# The other half of the removal happens outside the worktree: git deletes the
# admin dir .git/worktrees/<name>/ from the main checkout, and the paired prune
# below finishes that job. Denied there, the checkout goes but the entry stays —
# stranded and prunable, the exact state the prune pairing exists to prevent,
# and a later `git branch -D` then claims the branch is still checked out at a
# path that no longer exists. Deleting an entry needs write permission on the
# directory holding it, so probe by creating a file in each rather than touching
# one.
#
# Only a *linked* worktree has an admin dir to strand. Pointed at the main
# checkout, --git-dir is the repo's own .git and its parent is the repo root, so
# probing would drop a scratch file in a tracked working tree that other agents
# may be `git add -A`-ing — before `git worktree remove` refuses the main
# worktree anyway. --git-common-dir is the same path only in the main checkout,
# which is exactly the case to skip.
#
# Only asked of a path that resolved. `git -C ""` is a no-op, so an empty target
# would answer for the cwd instead — and from a linked worktree that is the
# caller's own admin dir, which the removal never touches but the probe below
# would then walk and could refuse on.
admin=""
common=""
if [ -n "$target" ]; then
  admin=$(git -C "$target" rev-parse --path-format=absolute --git-dir 2>/dev/null)
  common=$(git -C "$target" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
fi
[ "$admin" = "$common" ] && admin=""
if [ -n "$admin" ] && [ -d "$admin" ]; then
  # Every directory in the admin tree counts, not just its top: the entry holds
  # logs/ and refs/, and unlinking a file needs write permission on the
  # directory holding it. A denial inside logs/ sails past a top-only probe and
  # then deletes the whole checkout before failing — the stranded entry this
  # script exists to prevent, reached through the probe meant to prevent it.
  # Split on newlines so a path with spaces survives.
  nl=$'\n'
  oldifs=$IFS
  IFS=$nl
  dirs="$(find "$admin" -type d)${nl}$(dirname "$admin")"
  for dir in $dirs; do
    IFS=$oldifs
    # The redirection is what fails here, and the shell reports that itself — so
    # the 2>/dev/null has to wrap a subshell, not the command, or its
    # "Permission denied" prints ahead of the message below. Same reason `rm`'s
    # stderr is muted: a cleanup that failed would leak the diagnostic this
    # whole dance exists to suppress.
    probe="$dir/.wt-remove-probe.$$"
    (: >"$probe") 2>/dev/null && rm -f "$probe" 2>/dev/null && continue
    err "cannot write $dir — removing would delete the checkout"
    cont "but strand its .git/worktrees entry, so nothing was touched. Re-run unsandboxed."
    exit 1
  done
  IFS=$oldifs
fi

# Record whether the tree was clean going in. It has to be read now, not after a
# failure: `git worktree remove` never deletes out of a dirty tree, so
# cleanliness here is what proves any leftover deletions came from this run
# rather than from you.
before_clean=""
if [ -n "$target" ] && before=$(git -C "$target" status --porcelain 2>/dev/null) \
  && [ -z "$before" ]; then
  before_clean=1
fi

# The branch the worktree holds, for the cleanup at the bottom. It has to be
# read now for the same reason as cleanliness: once the checkout is gone there
# is nothing left to ask. --quiet leaves this empty on a detached HEAD, which is
# the right answer — there is no branch to delete. The main checkout does report
# a branch here, but never reaches the delete: `git worktree remove` refuses it,
# and that failure exits before the branch half.
branch=""
abs="$path"
if [ -n "$target" ]; then
  abs="$target"
  branch=$(git -C "$target" symbolic-ref --quiet --short HEAD 2>/dev/null)
else
  # An already-`rm -rf`'d worktree has no checkout left to ask — and that is the
  # case this script exists to absorb, so skipping the branch here would do half
  # the job in the half of the job that matters most: whoever deleted the
  # directory by hand is not coming back to run `git branch -D` either. The
  # admin entry still knows the branch, and survives until the prune below
  # erases it, so this window is the only chance to read it.
  parent=$(cd -- "$(dirname -- "$path")" 2>/dev/null && pwd -P)
  if [ -n "$parent" ]; then
    # Resolve the path the same way `pwd -P` did for a live worktree, so the
    # porcelain match survives /tmp -> /private/tmp and friends. A stale
    # *detached* worktree prints `detached` where a branch line would be, so the
    # capture stays empty and the step skips — which is the right answer,
    # exactly as for a live detached HEAD. The same spelling is what the
    # removal below is handed, so the two agree by construction.
    abs="$parent/$(basename -- "$path")"
    listing=$(git worktree list --porcelain 2>/dev/null)
    branch=$(awk -v w="worktree $abs" '
      $0 == w { found = 1; next }
      /^worktree / { found = 0 }
      found && sub(/^branch refs\/heads\//, "") { print; exit }' <<<"$listing")
  fi
fi

# Pin the repo root up front: removing the worktree you're standing in deletes
# this shell's cwd, and a cwd-less `git worktree prune` dies with a bare
# `fatal:`. The target's own common dir is the authoritative answer and is
# already in hand; the cwd is the fallback for a path that no longer resolves.
root=""
[ -n "$common" ] && root=$(dirname "$common")
if [ -z "$root" ]; then
  cwd_common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  [ -n "$cwd_common" ] && root=$(dirname "$cwd_common")
fi
# No root means no repository to remove from. Without this the removal fails,
# the prune is skipped, and the empty branch reaches the final exit 0 — a bogus
# invocation reported as a finished teardown. A stale path can only find its
# repo through the cwd, which is why the message names both.
if [ -z "$root" ]; then
  err "$path is not in a git repository, and neither is the cwd — run this"
  cont "from inside the repository that owns the worktree"
  exit 64
fi

# Capture the failure text so the submodule refusal below can be recognised,
# then replay it. `git worktree remove` is silent on success, so this only ever
# re-emits a real error, and folding stdout in is safe for the same reason.
#
# LC_ALL=C is load-bearing, not tidiness: the branch below matches git's refusal
# text, and git ships translations for ~20 locales, so on a non-English shell
# the match never fires and the retry silently never happens. There is no git
# config knob for message language, so an env prefix is the only form available.
# The replayed text is English as a consequence, which is the intended trade.
#
# -C "$root" because `git worktree remove` resolves the registry from the cwd:
# run from outside the repo, or from another repo, it fails with "not a working
# tree" while the checkout stays put. The root is the target's own, so the call
# works from anywhere — which is what lets a hook or a sibling worktree tear
# this one down. Hence the absolute path: under -C a relative one would resolve
# against the root instead of the cwd it was typed in.
errtext=$(LC_ALL=C git -C "$root" worktree remove -- "$abs" 2>&1)
rc=$?

# A populated submodule makes git refuse outright — "working trees containing
# submodules cannot be moved or removed" — however clean the tree is, and
# --force is the only way through. The refusal is a pre-check: nothing is
# deleted, so the tree is still whole here. An *un*populated submodule removes
# normally, which is why this is a refusal to detect rather than a repo shape to
# special-case.
#
# Forcing is not free. A linked worktree keeps its submodule gitdirs under
# .git/worktrees/<name>/modules/, not the shared .git/modules/, so --force takes
# the submodule's objects and refs along with the checkout and the shared store
# cannot bring them back. Two gates make it safe to do unasked:
#   * the tree was clean going in — the superproject reports dirty content,
#     untracked files, and a commit that moved the submodule's checked-out HEAD
#     all as ` M <path>`, so before_clean excludes those three. It does NOT see
#     other branches, tags, or reflog-only commits inside the submodule: a
#     gitlink only tracks HEAD. Those are left uncovered on purpose — a reflog
#     is never empty, so gating on one would refuse every teardown and push the
#     caller back to a bare --force that checks nothing.
#   * no submodule holds a stash — a stash is invisible to `status --porcelain`,
#     and a submodule's gitdir is per-worktree, so its stash dies with the
#     force. The superproject's own stash is deliberately not checked:
#     refs/stash is shared by every worktree, so checking it would refuse any
#     repo that has ever stashed, while the stash itself is never at risk.
#
# Merge status is deliberately not a gate. The branch and its commits survive
# removal untouched — only the checkout goes — so a PR lookup would add a
# network call and decide nothing.
forced=""
refuse=""
if [ "$rc" -ne 0 ] && [ -n "$target" ]; then
  case "$errtext" in
    *"containing submodules cannot be moved or removed"*)
      # --recursive because a nested submodule's gitdir lives under the admin
      # dir too (.git/worktrees/<name>/modules/<outer>/modules/<inner>), so its
      # stash dies with the force just the same, and a first-level-only walk
      # never sees it.
      stashed=$(git -C "$target" submodule foreach --recursive --quiet 'git stash list' 2>&1)
      probe_rc=$?
      if [ -z "$before_clean" ]; then
        refuse="the tree is dirty — commit, stash or discard it first"
      elif [ "$probe_rc" -ne 0 ]; then
        # Fail closed, like the write probes above: an empty capture means "no
        # stashes" and a failed walk produces one too, and guessing wrong here
        # costs data.
        refuse="the submodules could not be checked for stashes: $stashed"
      elif [ -n "$stashed" ]; then
        refuse="a submodule holds a stash that --force would destroy: $stashed"
      else
        warn "populated submodules block a plain remove; the tree is"
        cont "clean and no submodule holds a stash, so forcing is safe here"
        git -C "$root" worktree remove --force -- "$abs"
        rc=$?
        forced=1
      fi
      ;;
  esac
fi

# Replay the original error unless the retry cleared it — a refusal followed by
# a successful force is not something the caller has to read.
if [ -n "$errtext" ] && { [ -z "$forced" ] || [ "$rc" -ne 0 ]; }; then
  printf '%s\n' "$errtext" >&2
fi
[ -n "$root" ] && git -C "$root" worktree prune -v

# Report a gated refusal on its own terms and stop. The generic postmortem below
# would add nothing, and its self-inflicted-damage guidance would be actively
# wrong — git refused before touching anything.
if [ -n "$refuse" ]; then
  err "could not remove $path — it has populated submodules,"
  cont "which only --force can remove, and $refuse"
  cont "  git -C $root worktree remove --force -- $abs"
  exit "$rc"
fi

if [ "$rc" -ne 0 ] && [ -e "$path" ]; then
  err "could not remove $path"
  # Only claim self-inflicted damage when the tree was clean going in and
  # something actually got deleted — a locked or busy worktree fails with
  # everything still intact, and telling you to --force through that would be
  # the same misdirection in reverse. A status that now *fails* counts as damage
  # too: a part-way remove takes the admin dir with it, so the leftover tree
  # reports "not a git repository" rather than listing the deletions. Only a
  # tree that still answers, and answers clean, is really intact.
  damaged=""
  if [ -n "$before_clean" ]; then
    if after=$(git -C "$path" status --porcelain 2>/dev/null); then
      [ -n "$after" ] && damaged=1
    else
      damaged=1
    fi
  fi
  if [ -n "$damaged" ]; then
    cont "The tree was clean before this attempt, so a \"modified or untracked\""
    cont "refusal on retry is this run's own partial deletion, not your work:"
    cont "  git -C $root worktree remove --force -- $abs   # unsandboxed"
  fi
  exit "$rc"
fi

# The checkout can be gone and the removal still have failed — a denial on the
# admin side takes out the working tree first, then dies. That falls outside the
# branch above (the path no longer exists), and exiting 0 there would report a
# stranded entry as a clean removal. Distinguish it from the already-`rm -rf`'d
# case, which is the one this script exists to absorb, by asking whether the
# entry actually went away.
#
# Here-string rather than `git … | grep -Fqx`: this script sets pipefail, which
# the `sh` recipe it was ported from did not. `grep -q` exits the moment it
# matches, the writer dies of SIGPIPE, and pipefail then turns a SUCCESSFUL
# match into status 141 — which reads as "not found", exactly inverting the
# test.
#
# The other way to get here is a root that never owned the path: a stale
# worktree names no repo of its own, so root came from the cwd, and if that is
# a different repository git refuses with "is not a working tree" and lists no
# entry. The absorbed case never lands here — git drops a missing path's entry
# itself and exits 0 — so a nonzero exit with nothing listed is a wrong-repo
# run, and exiting 0 would report a teardown that never happened.
if [ "$rc" -ne 0 ] && [ ! -e "$path" ]; then
  entries=$(git -C "$root" worktree list --porcelain 2>/dev/null)
  if grep -Fqx "worktree $abs" <<<"$entries"; then
    err "$path is gone but git exited $rc — its .git/worktrees"
    cont "entry survived and is now stranded. Finish the job unsandboxed:"
    cont "  git -C $root worktree prune"
    exit "$rc"
  fi
  err "could not remove $path — $root does not list it as a worktree, and the"
  cont "path itself is gone, so its repository cannot be found from here. Run this"
  cont "from inside the repository that owns it."
  exit "$rc"
fi

# The branch half of teardown lives in scripts/branch-remove.sh — the same work
# is wanted with no worktree in sight, so it is a script of its own and this
# calls it rather than repeating it. Two things about the call are deliberate:
#   * the empty-branch guard stays on THIS side. A detached HEAD has no branch
#     to delete, and handing an empty name over to be told so would put the word
#     "branch" in the output of a teardown that has nothing to say about one.
#   * its exit status is discarded. Every path through the removal is already
#     committed to exiting 0 by the time we reach here: the checkout is gone, so
#     the job the caller asked for succeeded, and turning "the branch could not
#     be tidied" into a failure would report a done job as undone — worse, it
#     would invite a retry of a removal with nothing left to remove.
#     branch-remove.sh says why it kept a branch on its own.
[ -n "$branch" ] || exit 0
script_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
"$script_dir/branch-remove.sh" "$branch" "$root" || true
