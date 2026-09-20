#!/usr/bin/env bats
# Every gate in scripts/worktree-remove.sh, ported case-for-case from the recipe
# suite it replaces. Its job is that a removal whose protected paths are
# unwritable is refused before anything is deleted — the probe is a pre-flight
# check, not a transaction — and that when one does still die part-way, it says
# whose fault the leftover state is. Each case builds a throwaway repo plus a
# linked worktree, runs the script, and asserts on exit status, what survives on
# disk, and whether the self-inflicted guidance fired.
#
# `gh` is stubbed throughout. The last step asks it whether the branch merged, so
# a real one would reach the network, read whatever auth the developer has, and
# answer about whichever repo it guessed from a fixture that has no remote. The
# default stub fails, which is the keep-the-branch path; the branch cases set the
# verdict they need.

setup() {
  setup_test
  SCRIPT="$REPO_ROOT/scripts/worktree-remove.sh"
  HOME="$TEST_TMPDIR/home"
  mkdir -p "$HOME"
  export HOME

  # A fixture repo that versions its own agent config, which is what makes the
  # sandbox refuse part of a removal on the real thing.
  REPO="$TEST_TMPDIR/repo"
  git init -q -b main "$REPO"
  mkdir -p "$REPO/.claude" "$REPO/sub"
  echo '{}' >"$REPO/.claude/settings.json"
  echo committed >"$REPO/file.txt"
  echo committed >"$REPO/sub/a.txt"
  git -C "$REPO" add -A
  git -C "$REPO" commit -qm fixture
  # The branch gate resolves the default branch from origin/HEAD, so the fixture
  # needs one even though it has no remote — symbolic-ref writes the ref without
  # checking that either half exists. test/branch-remove.bats owns the cases
  # where it is missing.
  git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  # git reports worktree paths resolved, and on macOS $TMPDIR sits under a
  # /tmp -> /private/tmp symlink — so a message naming a checkout is compared
  # against the resolved spelling, not the one mktemp handed back.
  REPO_REAL="$(cd "$REPO" && pwd -P)"
  export REPO REPO_REAL

  gh_says 1
}
teardown() { teardown_test; }
load test_helper

gh_says() { # exit-code [stdout]
  printf '#!/bin/sh\nprintf %%s "%s"\nexit %s\n' "${2:-}" "$1" >"$BIN_DIR/gh"
  chmod +x "$BIN_DIR/gh"
}

has_branch() { git -C "${2:-$REPO}" show-ref --quiet --verify "refs/heads/$1"; }
tip_of() { git -C "$REPO" rev-parse "refs/heads/$1"; }

# wt <name> — add a linked worktree on the fixture repo, echo its path
wt() {
  git -C "$REPO" worktree add -q "$TEST_TMPDIR/$1" -b "branch-$1" >/dev/null
  echo "$TEST_TMPDIR/$1"
}

# run_wr <cwd> <args...> — bats' own `run` captures status and output. The cwd
# is an argument because two gates (the inside-the-target refusal, and the main
# checkout's admin probe) only fire from the right vantage point.
run_wr() {
  local cwd="$1"
  shift
  run env HOME="$HOME" bash -c 'cd "$1" && shift && exec "$@"' _ "$cwd" "$SCRIPT" "$@"
}

# --------------------------------------------------------- the pre-flight probes

@test "a worktree whose protected paths cannot be written is refused untouched" {
  # The whole point of the pre-flight probe. `touch` is stubbed to fail because
  # the sandbox denial it stands in for is not reproducible from inside a test.
  target=$(wt probe)
  printf '#!/bin/sh\nexit 1\n' >"$BIN_DIR/touch"
  chmod +x "$BIN_DIR/touch"
  run_wr "$REPO" "$target"
  rm -f "$BIN_DIR/touch"
  assert_failure
  assert_output --partial "cannot write"
  assert_output --partial "Re-run unsandboxed"
  assert [ -f "$target/file.txt" ]
  assert [ -f "$target/.claude/settings.json" ]
}

@test "an unwritable admin dir is refused before the checkout is deleted" {
  # The removal's other half lives in the main checkout. An unwritable admin dir
  # would let the checkout be deleted and leave its .git/worktrees entry
  # stranded, so it is refused up front too — here the denial is real, not
  # stubbed.
  target=$(wt admin)
  chmod 500 "$REPO/.git/worktrees/admin"
  run_wr "$REPO" "$target"
  chmod 700 "$REPO/.git/worktrees/admin"
  assert_failure
  assert_output --partial "cannot write"
  assert [ -f "$target/file.txt" ]
  # The subshell around the probe's redirection is load-bearing: without it the
  # shell prints its own "Permission denied" ahead of the script's message.
  refute_output --partial "Permission denied"
  run git -C "$REPO" worktree list --porcelain
  assert_output --partial "branch-admin"
}

@test "an unwritable admin PARENT is refused too" {
  # Deleting the admin dir needs write permission on its PARENT, so that probe
  # carries its own weight — and the case above can't show it, since the child
  # failing short-circuits first. A mode-500 parent still admits writes to an
  # already-writable child, so only the parent trips.
  target=$(wt adminparent)
  chmod 500 "$REPO/.git/worktrees"
  run_wr "$REPO" "$target"
  chmod 700 "$REPO/.git/worktrees"
  assert_failure
  assert_output --partial "cannot write"
  assert [ -f "$target/file.txt" ]
  refute_output --partial "Permission denied"
}

@test "an unwritable directory nested in the admin tree is refused too" {
  # The admin entry is a tree, not a file: it holds logs/ and refs/. An
  # unwritable directory down there passes a probe that only checks the top, and
  # git then deletes the entire checkout before failing on it — the stranded
  # entry, reached through the probe meant to prevent it. Verified against real
  # git before this case was written.
  target=$(wt adminnested)
  chmod 500 "$REPO/.git/worktrees/adminnested/logs"
  run_wr "$REPO" "$target"
  chmod 700 "$REPO/.git/worktrees/adminnested/logs"
  assert_failure
  assert_output --partial "cannot write"
  assert [ -f "$target/file.txt" ]
  refute_output --partial "Permission denied"
}

@test "the main checkout is refused by git, never by the admin probe" {
  # Pointed at the main checkout there is no admin dir to strand, so the probe
  # must not run there at all — probing would write a scratch file into a tracked
  # working tree that other agents may be staging. An unwritable repo root is
  # what makes the difference observable: unguarded, the probe fails and blames
  # the repo root; guarded, git refuses the main worktree on its own terms.
  # Asserting on leftover files instead would prove nothing — the unguarded probe
  # cleans up after itself. Run from OUTSIDE the repo: with cwd inside it, the
  # cwd guard fires first and this case would pass without ever reaching the
  # admin probe.
  chmod 500 "$REPO"
  run_wr "$TEST_TMPDIR" "$REPO"
  chmod 700 "$REPO"
  assert_failure
  refute_output --partial "cannot write"
}

# ------------------------------------------------------------- the ordinary path

@test "a clean worktree is removed and its admin entry goes with it" {
  target=$(wt clean)
  run_wr "$REPO" "$target"
  assert_success
  assert [ ! -e "$target" ]
  # The entry, not just the directory: a removal that took the checkout and left
  # the .git/worktrees entry behind is the failure this whole script is built
  # around, and it is invisible to a test that only looks at the filesystem.
  run git -C "$REPO" worktree list --porcelain
  assert_success
  refute_output --partial "branch-clean"
}

# ---------------------------------------------------------- whose fault it is

@test "a tree the caller dirtied is refused without blaming the script" {
  # That misdirection is the bug this suite exists for, pointing the other way.
  target=$(wt dirty)
  echo scratch >"$target/untracked.txt"
  run_wr "$REPO" "$target"
  assert_failure
  assert_output --partial "could not remove"
  refute_output --partial "not your work"
}

@test "a removal that died part-way says the leftover is its own doing" {
  # git deletes what it can, takes the admin dir with it, and the leftover
  # reports "not a git repository" rather than listing its deletions.
  target=$(wt partial)
  chmod 500 "$target/sub"
  run_wr "$REPO" "$target"
  chmod 700 "$target/sub"
  assert_failure
  assert_output --partial "could not remove"
  assert_output --partial "not your work"
}

@test "a locked worktree fails intact, so the guidance must not fire" {
  # It fails with everything still intact, so the same guidance would be a false
  # accusation in the opposite direction.
  target=$(wt locked)
  git -C "$REPO" worktree lock "$target"
  run_wr "$REPO" "$target"
  git -C "$REPO" worktree unlock "$target"
  assert_failure
  assert_output --partial "could not remove"
  refute_output --partial "not your work"
}

@test "standing inside the target is refused before anything else happens" {
  # A subshell cannot cd the caller out of a directory it just deleted.
  target=$(wt inside)
  run_wr "$target" "$target"
  assert_failure 1
  assert_output --partial "cd out first"
  assert [ -f "$target/file.txt" ]
  # The refusal has to land ahead of every probe, so nothing may have run yet.
  refute_output --partial "cannot write"
}

# ------------------------------------------------------------------ the prune

@test "an already-rm -rf'd worktree is absorbed rather than reported as a failure" {
  target=$(wt stale)
  rm -rf "$target"
  run_wr "$REPO" "$target"
  assert_success
  run git -C "$REPO" worktree list --porcelain
  refute_output --partial "branch-stale"
}

@test "an unrelated stranded entry is pruned by the paired prune" {
  # The case above says nothing about the paired prune, because modern git's
  # `worktree remove` already drops the entry for a missing path all by itself —
  # assert on the entry the removal never touches. A worktree stranded by
  # something ELSE is the only thing the prune can be credited with, so strand
  # one and remove an unrelated worktree.
  git -C "$REPO" worktree add -q "$TEST_TMPDIR/ghost" -b branch-ghost
  rm -rf "$TEST_TMPDIR/ghost"
  target=$(wt bystander)
  run_wr "$REPO" "$target"
  assert_success
  run git -C "$REPO" worktree list --porcelain
  refute_output --partial "branch-ghost"
}

# ------------------------------------------------ the repo comes from the target
# `git worktree remove` resolves the registry from the cwd, so without the root
# pin a run from outside the repo, or from another repo, fails with "not a
# working tree" and leaves the checkout in place. The hook that calls this at
# session exit and a sibling worktree tearing this one down are both that shape.

@test "a worktree is removed from a cwd outside any repository" {
  target=$(wt outside)
  run_wr "$TEST_TMPDIR" "$target"
  assert_success
  assert [ ! -e "$target" ]
  run git -C "$REPO" worktree list --porcelain
  refute_output --partial "branch-outside"
}

@test "a worktree is removed from the checkout of a different repository" {
  git init -q -b main "$TEST_TMPDIR/other"
  git -C "$TEST_TMPDIR/other" commit -q --allow-empty -m other
  target=$(wt crossrepo)
  run_wr "$TEST_TMPDIR/other" "$target"
  assert_success
  assert [ ! -e "$target" ]
  run git -C "$REPO" worktree list --porcelain
  refute_output --partial "branch-crossrepo"
  # The other repo was never touched: its only entry is its own checkout.
  run git -C "$TEST_TMPDIR/other" worktree list --porcelain
  refute_output --partial "crossrepo"
}

@test "an rm -rf'd worktree is absorbed from the checkout of a different repository" {
  # A stale path cannot name its repo, so this run can only find the OTHER
  # repo, and it must fail rather than claim a teardown that never happened.
  git init -q -b main "$TEST_TMPDIR/other"
  git -C "$TEST_TMPDIR/other" commit -q --allow-empty -m other
  target=$(wt stalecross)
  rm -rf "$target"
  run_wr "$TEST_TMPDIR/other" "$target"
  assert_failure
  refute_output --partial "deleted branch"
  # The entry is still there for a run from the right repo to prune.
  run git -C "$REPO" worktree list --porcelain
  assert_output --partial "branch-stalecross"
}

@test "with no repository on either side the run is refused, not reported done" {
  run_wr "$TEST_TMPDIR" "$TEST_TMPDIR/nowhere"
  assert_failure 64
  assert_output --partial "not in a git repository"
}

# --------------------------------------------------------- populated submodules
# `git worktree remove` refuses any worktree with a POPULATED submodule, however
# clean it is: "working trees containing submodules cannot be moved or removed".
# Only --force gets through, and forcing destroys the submodule's objects — a
# linked worktree keeps its submodule gitdir under .git/worktrees/<name>/modules/,
# not the shared .git/modules/, so nothing survives to recover from. The script
# forces only behind two gates; these cases pin both, and the un-populated case
# pins that the refusal is what's detected, not the mere presence of a submodule.
# Every claim here was verified against real git before the cases were written.

mk_sub_fixture() { # sets SUBROOT
  git init -q -b main "$TEST_TMPDIR/upstream"
  echo up >"$TEST_TMPDIR/upstream/s.txt"
  git -C "$TEST_TMPDIR/upstream" add -A
  git -C "$TEST_TMPDIR/upstream" commit -qm upstream
  SUBROOT="$TEST_TMPDIR/sup"
  git init -q -b main "$SUBROOT"
  echo top >"$SUBROOT/file.txt"
  git -C "$SUBROOT" add -A
  git -C "$SUBROOT" commit -qm fixture
  git -C "$SUBROOT" -c protocol.file.allow=always submodule add -q "$TEST_TMPDIR/upstream" vendor/s
  git -C "$SUBROOT" commit -qm addsub
}

# swt <name> <populate|nopopulate> — linked worktree on the submodule fixture
swt() {
  git -C "$SUBROOT" worktree add -q "$TEST_TMPDIR/$1" -b "sub-$1" >/dev/null
  if [ "$2" = populate ]; then
    git -C "$TEST_TMPDIR/$1" -c protocol.file.allow=always submodule update --init -q
  fi
  echo "$TEST_TMPDIR/$1"
}

@test "an unpopulated submodule is removed normally" {
  # Not the failing case — git removes it, so the script must not treat "repo has
  # submodules" as the trigger.
  mk_sub_fixture
  target=$(swt bare nopopulate)
  run_wr "$SUBROOT" "$target"
  assert_success
  assert [ ! -e "$target" ]
}

@test "a clean populated submodule with no stash is forced, and says why" {
  mk_sub_fixture
  target=$(swt clean populate)
  run_wr "$SUBROOT" "$target"
  assert_success
  assert [ ! -e "$target" ]
  assert_output --partial "forcing is safe"
  run git -C "$SUBROOT" worktree list --porcelain
  refute_output --partial "sub-clean"
}

@test "the force also runs in the target's repository, from a cwd outside it" {
  # The forced retry is a second removal, so it needs the same root pin as the
  # first — once the plain attempt is pinned, this is the call that would
  # otherwise fail after both gates had already passed.
  mk_sub_fixture
  target=$(swt outsideforce populate)
  run_wr "$TEST_TMPDIR" "$target"
  assert_success
  assert [ ! -e "$target" ]
  assert_output --partial "forcing is safe"
  run git -C "$SUBROOT" worktree list --porcelain
  refute_output --partial "sub-outsideforce"
}

@test "uncommitted work inside a submodule blocks the force" {
  # It surfaces in the SUPERPROJECT as ` M vendor/s`, so the cleanliness gate
  # already covers it — and forcing would delete it.
  mk_sub_fixture
  target=$(swt dirtysub populate)
  echo scratch >"$target/vendor/s/s.txt"
  run_wr "$SUBROOT" "$target"
  assert_failure
  assert [ -f "$target/file.txt" ]
  assert [ -f "$target/vendor/s/s.txt" ]
  assert_output --partial "tree is dirty"
  refute_output --partial "not your work"
}

@test "a stash inside a submodule blocks the force and survives it" {
  # A stash is invisible to `status --porcelain`, so cleanliness alone would wave
  # the force through and lose it. The submodule's gitdir is per-worktree, so
  # this stash is genuinely local to the worktree being removed.
  mk_sub_fixture
  target=$(swt stashsub populate)
  (cd "$target/vendor/s" && echo wip >s.txt && git stash -q)
  run git -C "$target" status --porcelain
  assert_output ""
  run_wr "$SUBROOT" "$target"
  assert_failure
  assert [ -f "$target/file.txt" ]
  assert_output --partial "stash that --force would destroy"
  run git -C "$target/vendor/s" stash list
  refute_output ""
}

@test "a stash inside a NESTED submodule blocks the force too" {
  # Its gitdir lands under the admin dir at
  # .git/worktrees/<name>/modules/<outer>/modules/<inner>, so it dies with the
  # force exactly like a first-level one — and a first-level-only `submodule
  # foreach` never sees it. Reproduced against real git before this case.
  git init -q -b main "$TEST_TMPDIR/leaf"
  echo leaf >"$TEST_TMPDIR/leaf/l.txt"
  git -C "$TEST_TMPDIR/leaf" add -A
  git -C "$TEST_TMPDIR/leaf" commit -qm leaf
  git init -q -b main "$TEST_TMPDIR/mid"
  echo mid >"$TEST_TMPDIR/mid/m.txt"
  git -C "$TEST_TMPDIR/mid" add -A
  git -C "$TEST_TMPDIR/mid" commit -qm mid
  git -C "$TEST_TMPDIR/mid" -c protocol.file.allow=always submodule add -q "$TEST_TMPDIR/leaf" deep
  git -C "$TEST_TMPDIR/mid" commit -qm adddeep

  nestroot="$TEST_TMPDIR/nest"
  git init -q -b main "$nestroot"
  echo top >"$nestroot/file.txt"
  git -C "$nestroot" add -A
  git -C "$nestroot" commit -qm fixture
  git -C "$nestroot" -c protocol.file.allow=always submodule add -q "$TEST_TMPDIR/mid" vendor/m
  git -C "$nestroot" commit -qm addmid

  target="$TEST_TMPDIR/nestwt"
  git -C "$nestroot" worktree add -q "$target" -b sub-nestwt
  git -C "$target" -c protocol.file.allow=always submodule update --init --recursive -q
  (cd "$target/vendor/m/deep" && echo wip >l.txt && git stash -q)
  assert [ -f "$target/vendor/m/deep/l.txt" ]
  run git -C "$target" status --porcelain
  assert_output ""

  run_wr "$nestroot" "$target"
  assert_failure
  assert [ -f "$target/file.txt" ]
  assert_output --partial "stash that --force would destroy"
  run git -C "$target/vendor/m/deep" stash list
  refute_output ""
}

# --------------------------------------------------- the branch half of teardown
# A merged branch is what the removal leaves behind, so the script deletes it —
# but only on PR-state evidence, and never at the cost of the removal itself.
# Every case here asserts exit 0: the checkout is already gone by the time that
# code runs, and a nonzero status would report a finished teardown as one to
# retry.

@test "a branch whose PR merged this tip is deleted along with its stanza" {
  target=$(wt merged)
  git -C "$REPO" config branch.branch-merged.remote origin
  gh_says 0 "$(tip_of branch-merged) main"
  run_wr "$REPO" "$target"
  assert_success
  assert [ ! -e "$target" ]
  refute has_branch branch-merged
  assert_output --partial "deleted branch branch-merged"
  run git -C "$REPO" config --get branch.branch-merged.remote
  assert_failure
}

@test "a branch that moved past its merged PR is kept, and the removal still succeeds" {
  # A merged PR under this name is not proof about the commit the name points at
  # NOW. Reuse the name, or push past the merge, and a count-based gate would
  # still say "merged" while -D threw the new commits away.
  target=$(wt tipmoved)
  gh_says 0 "0000000000000000000000000000000000000000 main"
  run_wr "$REPO" "$target"
  assert_success
  assert [ ! -e "$target" ]
  assert has_branch branch-tipmoved
  assert_output --partial "moved past it"
  refute_output --partial "deleted branch"
}

@test "no merged PR keeps the branch and names that gate" {
  # Empty output is the legitimate "none" answer, NOT a failure to reach gh —
  # conflating the two would report a definite verdict as an unreachable one.
  target=$(wt unmerged)
  gh_says 0 ""
  run_wr "$REPO" "$target"
  assert_success
  assert has_branch branch-unmerged
  assert_output --partial "no merged PR"
  refute_output --partial "could not reach gh"
}

@test "an unreachable gh keeps the branch and still exits 0 with the worktree gone" {
  # The gate failing is not the same as the gate saying no, but the safe answer
  # is identical. This is the sandboxed and the unauthenticated case both, and it
  # is the one that decides whether this is usable unattended: it must not fail.
  target=$(wt nogh)
  gh_says 1
  run_wr "$REPO" "$target"
  assert_success
  assert [ ! -e "$target" ]
  assert has_branch branch-nogh
  assert_output --partial "could not reach gh"
  refute_output --partial "no merged PR"
}

@test "a detached HEAD never reaches the branch half at all" {
  # gh is primed to say "merged" here, so a script that asked anyway would delete
  # whatever it guessed.
  target=$(wt detached)
  git -C "$target" checkout -q --detach
  gh_says 0 "1 main"
  run_wr "$REPO" "$target"
  assert_success
  assert has_branch branch-detached
  refute_output --partial "branch"
}

@test "an rm -rf'd worktree still gets its branch tidied" {
  # The case this script exists to absorb. No tree is left to read the branch
  # from, but the admin entry still names it until the prune erases it — and this
  # is precisely the case where nobody is coming back to tidy the branch by hand.
  target=$(wt stalewt)
  stale_tip=$(tip_of branch-stalewt)
  rm -rf "$target"
  gh_says 0 "$stale_tip main"
  run_wr "$REPO" "$target"
  assert_success
  refute has_branch branch-stalewt
  assert_output --partial "deleted branch branch-stalewt"
  run git -C "$REPO" worktree list --porcelain
  refute_output --partial "branch-stalewt"
}

@test "a branch delete that did not take is reported as kept, and still exits 0" {
  # The delete is trusted by whether the ref went away, not by git's exit status:
  # a sandboxed delete returns 0 having failed to lock .git/config, so only the
  # ref says what really happened. An unwritable refs/heads is the inverse.
  target=$(wt stuckref)
  gh_says 0 "$(tip_of branch-stuckref) main"
  chmod 500 "$REPO/.git/refs/heads"
  run_wr "$REPO" "$target"
  chmod 700 "$REPO/.git/refs/heads"
  assert_success
  assert has_branch branch-stuckref
  assert_output --partial "did not take"
  refute_output --partial "deleted branch"
}

# ------------------------------------------------------------ argument handling

@test "an empty path is rejected rather than reported as a clean removal" {
  # Without the guard the removal fails, every later test finds nothing, and the
  # branch half exits 0 — a bogus invocation reported as a finished teardown.
  run_wr "$REPO" ""
  assert_failure 64
  assert_output --partial "name the worktree"
}
