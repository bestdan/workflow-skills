#!/usr/bin/env bash
# Delete a local branch once a merged PR proves it is spent, and clear the
# [branch "..."] stanza that outlives it.
#
#   scripts/branch-remove.sh <branch> [root]
#
#   exit 0   deleted (or the branch was already gone and its stanza was cleared)
#   exit 3   kept, with the reason on stderr
#   exit 64  the caller named nothing deletable
#
# `root` defaults to the repo containing the cwd; a caller that has already torn
# down a worktree passes its own pinned root, because by then the cwd may be a
# directory that no longer exists.
#
# This is the branch half of teardown standing alone, for the case a worktree
# teardown cannot reach: a branch created, merged and deleted in the main
# checkout, where there is no worktree and so no teardown to hang it off.
#
# It exists because bare `git branch -d` is not a safe delete in a squash-merging
# repo, and fails in the direction that costs work. `-d` proves "merged" two ways
# and both are broken here: the ancestry test calls every squash-merged branch
# unmerged, since the squash builds a new commit the original is no ancestor of,
# so the ONLY test that ever passes is the fallback — does the branch match its
# upstream. That one ignores whether the work landed at all. A branch pushed but
# never merged matches its upstream and is deleted; so is one that merged and
# then took new commits. `-d` reports both as merged.
#
# So the gate here is PR state, and the tip has to match a head some merged PR
# actually merged INTO THE DEFAULT BRANCH — a name is not an identity, and a
# merge into the wrong base is not a merge at all for the purpose of deleting.
# See the notes on each step below.

set -uo pipefail

say() { printf ' .. %s\n' "$1"; }
warn() { printf ' .. %s\n' "$1" >&2; }
cont() { printf '    %s\n' "$1" >&2; }
err() { printf ' !! %s\n' "$1" >&2; }

branch="${1-}"
root="${2-}"

if [ -z "$root" ]; then
  # --git-common-dir, not --git-dir: in a linked worktree the latter is the admin
  # entry under .git/worktrees/<name>/, whose parent is not the repo root.
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -z "$common" ]; then
    err "not inside a git repository — name one with the second argument"
    exit 64
  fi
  root=$(dirname "$common")
fi

if [ -z "$branch" ]; then
  err "name the branch to delete"
  exit 64
fi

# Does a [branch "..."] stanza exist for exactly this branch? Two wrong answers
# to avoid.
#
# A plain prefix match on `config --list --name-only` is ambiguous, because that
# listing flattens a subsection into the key: `branch.foo.bar.remote` — the
# stanza of the branch `foo.bar` — carries the prefix `branch.foo.`, so a guard
# for the branch `foo` matches it. Dots are legal in ref names. What
# disambiguates is that a config KEY name cannot contain a dot, so a line belongs
# to `foo` only when what follows `branch.foo.` has no dot in it. That is an
# exact string test, which is why this is awk and not a regex: a branch name may
# contain regex metacharacters, and escaping them is a second bug waiting to
# happen.
#
# Nor can the removal's own exit code answer it. Unsandboxed, git returns 128
# ("no such section") for a missing one — but it takes the config lock BEFORE
# checking existence, so in a session's own project repo, where the harness
# denies that write, a missing section and a denied removal both return 255.
# Branching on the code there would report "the sandbox denies that write" for a
# branch that simply does not exist — the same invented denial this script exists
# to remove, moved to a different case.
has_stanza() { # branch
  git -C "$root" config --list --name-only 2>/dev/null | awk -v p="branch.$1." '
    index($0, p) == 1 && index(substr($0, length(p) + 1), ".") == 0 { found = 1; exit }
    END { exit !found }'
}

if ! git -C "$root" show-ref --quiet --verify "refs/heads/$branch"; then
  # A missing ref whose stanza is still there is not a missing branch — it is the
  # residue this script exists to prevent, left behind by an earlier sandboxed
  # `git branch -d` that took the ref and could not take the stanza. Clearing it
  # is the entire remaining job, so do that rather than reporting nothing to
  # delete and leaving the cruft in place.
  if has_stanza "$branch"; then
    if git -C "$root" config --remove-section "branch.$branch" 2>/dev/null; then
      say "branch $branch was already gone — cleared the [branch] stanza it left"
      exit 0
    fi
    warn "branch $branch is already gone, but its [branch] stanza"
    cont "could not be cleared — the sandbox denies that write. Unsandboxed:"
    cont "  git -C $root config --remove-section branch.$branch"
    exit 3
  fi
  err "no branch named $branch in $root"
  exit 64
fi

# A checked-out branch cannot be deleted, and in the main checkout that is the
# likely mistake by a wide margin: asking to delete the branch you are standing
# on. git refuses on its own, but that refusal would surface below as "the delete
# did not take", which reads as a permission or sandbox problem rather than the
# simple thing it is. Checked ahead of the gh lookup so a doomed delete costs no
# network call.
held=$(git -C "$root" worktree list --porcelain | awk -v b="branch refs/heads/$branch" '
  /^worktree / { path = substr($0, 10) }
  $0 == b { print path; exit }')
if [ -n "$held" ]; then
  warn "kept branch $branch — it is checked out at $held,"
  cont "so nothing can delete it. Switch away, or remove that worktree, first."
  exit 3
fi

# Merge state comes from the PR, never from ancestry, for the reason in the
# header: a squash-merging repo makes `--is-ancestor` call every merged branch
# unmerged. That is also why the delete below is -D and not -d — -d answers the
# question this gate exists to replace, and answers it wrongly.
#
# The lookup is the only network call here, and it is allowed to fail. Sandboxed
# there is no network at all; unattended there may be no gh auth; either way the
# answer is the same as "not merged" — keep the branch. A kept branch costs one
# `git branch -D` later, while a wrongly deleted one costs the work.
#
# Ask for head commits rather than a count, because a name is not an identity. A
# merged PR proves that *some* commit under this branch name landed; it says
# nothing about the commit the name points at now. Reuse the name for new work,
# or push more commits after the merge, and a count still reads "merged" while -D
# throws the new commits away. Comparing the tip against the OIDs merged PRs
# actually merged closes that, and costs nothing in the normal case: a
# squash-merge builds a new commit on the base and never touches the head ref, so
# the local tip still equals what the PR merged.
#
# `baseRefName` rides along on the same call, because `--state merged` does not
# filter by base: a stacked PR merged into its PARENT satisfies the state gate
# exactly like one merged into the default branch, and the tip test cannot
# separate them — a stacked PR's head OID is the branch tip, so it passes. The
# split has to happen here rather than server-side with `--base`, which gh does
# support: that would collapse "merged into a parent" into "no merged PR" and
# hand the stacked case a message about the wrong gate.
merged=$(cd "$root" 2>/dev/null && gh pr list --head "$branch" --state merged --json headRefOid,baseRefName --jq '.[] | "\(.headRefOid) \(.baseRefName)"' 2>/dev/null)
gh_rc=$?
# rc alone decides reachability. With this --jq an empty result is a legitimate
# answer — no merged PR — so folding emptiness into the failure test, as a
# `length` query could, would report a definite "not merged" as a gh that could
# not be reached.
if [ "$gh_rc" -ne 0 ]; then
  warn "kept branch $branch — could not reach gh to check whether it"
  cont "merged (no network sandboxed, or no auth). Delete it yourself once you know:"
  cont "  git -C $root branch -D $branch"
  exit 3
fi
if [ -z "$merged" ]; then
  warn "kept branch $branch — no merged PR has it as head"
  exit 3
fi

# Which branch is the default is only asked once there is something to compare it
# against, so the ordinary unmerged path still costs exactly one network call and
# never reaches the gh fallback below. This takes a `root` and is documented
# against any repo, so it cannot assume `main`.
#
# origin/HEAD first because it is local and free — and the pr-list call above
# already proved the network is up, so the fallback is worth taking only when the
# local answer is missing, never in preference to it. The ref is written at clone
# time and never refreshed, so it can name a branch that is no longer the
# default. The unsafe case is a default SWITCHED to another live branch: the
# stale name still matches PRs merged into it, so the gate can delete a branch
# whose work never reached the current default. Nothing local detects that —
# asking gh on every delete would, at the cost of the second network call this
# lookup is ordered to avoid. Hence the refusals below name the SOURCE of the
# default, not just the name, so a wrong answer is diagnosable from the message.
#
# A RENAMED default is deliberately not characterised here. Which way it falls
# depends on whether GitHub rewrites baseRefName on already-merged PRs at rename
# time — match and the gate deletes, mismatch and it refuses everything,
# silently. GitHub documents retargeting OPEN pull requests and says nothing
# about merged ones, so that is unverified. Do not write an outcome here without
# testing it first.
default_src="origin/HEAD"
default=$(git -C "$root" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null)
default=${default#origin/}
if [ -z "$default" ]; then
  default_src="gh repo view"
  default=$(cd "$root" 2>/dev/null && gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null)
fi
if [ -z "$default" ]; then
  warn "kept branch $branch — could not tell which branch is the"
  cont "default, and without it a PR merged into a parent branch reads the same as one"
  cont "merged into the default. Point the ref at it and retry:"
  cont "  git -C $root remote set-head origin -a"
  exit 3
fi

# Membership, not "the newest PR" — that avoids depending on gh's ordering, and
# answers the reused-name case correctly: delete only if the tip is exactly a
# head some merged PR merged. The set is built from default-base PRs alone rather
# than gating on the base separately, because two separate tests both pass on a
# branch that has a parent-base PR at its current tip AND an older default-base
# PR — each test finds its own PR, and neither is the same PR.
#
# Here-strings rather than `printf | …`: this script sets pipefail, which the
# `sh` recipe it was ported from did not. A reader that exits early — `grep -q`
# on a match, or an awk with `exit` — kills the writer with SIGPIPE, and pipefail
# then turns a SUCCESSFUL match into status 141, which reads as "not found".
tip=$(git -C "$root" rev-parse --verify --quiet "refs/heads/$branch")
oids=$(awk -v d="$default" '$2 == d { print $1 }' <<<"$merged")
if ! grep -Fqx "$tip" <<<"$oids"; then
  # A merged PR whose head is this exact tip, merged somewhere other than the
  # default, is the stacked case and gets its own message: the branch really did
  # merge, so "moved past its PR" would be a lie, and the parent has to be named
  # because it is what the caller has to wait on. Nothing here can tell whether
  # the parent has since landed — a squash rebuilds the child's commit again on
  # the way in, so the child's merged OID is never an ancestor of the default
  # branch no matter how the stack resolves. There is no check to reach for; the
  # wait is the answer.
  parent=$(awk -v d="$default" -v t="$tip" '$2 != d && $1 == t { print $2; exit }' <<<"$merged")
  if [ -n "$parent" ]; then
    warn "kept branch $branch — its PR merged into $parent, not"
    cont "$default (per $default_src), so the work has not reached $default."
    cont "Once $parent lands:"
    cont "  git -C $root branch -D $branch"
    exit 3
  fi
  warn "kept branch $branch — a PR with this name merged, but the"
  cont "branch has moved past it, so -D would discard commits no PR merged:"
  cont "  git -C $root branch -D $branch"
  exit 3
fi

# Trust the ref, not the exit status. The two disagree in the direction that
# matters: a sandboxed delete removes refs/heads/<branch> and still returns 0
# after failing to lock .git/config (see the stanza note below), so a 0 here does
# not prove the branch is gone. A refusal is the honest case — it exits nonzero
# and leaves the ref — so asking whether the ref survived answers both, and is
# the only question that answers the first.
git -C "$root" branch -D -- "$branch" >/dev/null 2>&1
if git -C "$root" show-ref --quiet --verify "refs/heads/$branch"; then
  warn "kept branch $branch — its PR merged, but the delete"
  cont "did not take:"
  cont "  git -C $root branch -D $branch"
  exit 3
fi
say "deleted branch $branch — its PR merged"

# Repair code for one condition, and it looks dead until you know which. `git
# branch -D` removes the [branch "..."] stanza itself, so with a writable config
# there is nothing here to do and the check below finds nothing. The exception is
# the sandbox: deleting the stanza needs a .git/config lock, the harness denies
# exactly that write, and git downgrades the failure to a warning and still exits
# 0 — leaving the ref gone and the stanza behind. That is the state this clears,
# and the whole reason bare `git branch -d` accumulates residue.
#
# The deny is anchored on the session's own project repo, not on every
# .git/config, so against some other repo the write simply goes through. Hence
# attempted rather than assumed to fail. When it does fail, say so and move on: a
# stale stanza is inert cruft whose only effect is that a future branch of the
# same name inherits its upstream, which is not worth failing a completed delete
# over or asking for an unsandboxed re-run.
#
# has_stanza, not a prefix match and not the removal's exit code — see the note
# on it above.
if has_stanza "$branch" \
  && ! git -C "$root" config --remove-section "branch.$branch" 2>/dev/null; then
  warn "left [branch \"$branch\"] in .git/config — the sandbox denies"
  cont "that write. Harmless; clear it unsandboxed if you like:"
  cont "  git -C $root config --remove-section branch.$branch"
fi
