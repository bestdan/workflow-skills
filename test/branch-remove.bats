#!/usr/bin/env bats
# Every gate in scripts/branch-remove.sh, ported case-for-case from the recipe
# suite it replaces. Its job is that a branch is deleted only on PR evidence that
# the commit it points at NOW actually merged, and that every refusal says which
# gate ruled rather than exiting 0 the way bare `git branch -d` does.
#
# `gh` is stubbed throughout. The gate asks it whether the branch merged, so a
# real one would reach the network and answer about whichever repo it guessed
# from a fixture that has no remote. The default stub fails, which is the
# keep-the-branch path; each case sets the verdict it needs.

setup() {
  setup_test
  SCRIPT="$REPO_ROOT/scripts/branch-remove.sh"
  # Captured before a git stub can exist in BIN_DIR, so a stub can pass through
  # to the real binary rather than recursing into itself.
  REAL_GIT="$(command -v git)"
  HOME="$TEST_TMPDIR/home"
  mkdir -p "$HOME"
  export HOME REAL_GIT

  REPO="$TEST_TMPDIR/repo"
  git init -q -b main "$REPO"
  echo committed >"$REPO/file.txt"
  git -C "$REPO" add -A
  git -C "$REPO" commit -qm fixture
  # The script resolves the default branch from origin/HEAD, so the fixture needs
  # one even though it has no remote — symbolic-ref writes the ref without
  # checking that either half exists, which is what lets a remote-less fixture
  # answer the question at all. The `default` cases below delete it again to
  # reach the fallback.
  git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  # git reports worktree paths resolved, and on macOS $TMPDIR sits under a
  # /tmp -> /private/tmp symlink — so a message naming a checkout is compared
  # against the resolved spelling, not the one mktemp handed back. Without this
  # the checked-out cases fail on a Mac and pass in CI.
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

# `gh repo view` gets its own body; everything else gets the merged-PR lines.
# Only the two default-branch-fallback cases need this.
gh_dispatch() { # repo-view-body merged-lines
  cat >"$BIN_DIR/gh" <<STUB
#!/bin/sh
case "\$1 \$2" in
  "repo view") $1 ;;
  *) printf %s "$2" ;;
esac
STUB
  chmod +x "$BIN_DIR/gh"
}

has_branch() { git -C "$REPO" show-ref --quiet --verify "refs/heads/$1"; }
tip_of() { git -C "$REPO" rev-parse "refs/heads/$1"; }
# A branch that exists only as a ref — no worktree, no checkout. That is the
# whole point: this is the shape a worktree teardown has no way to be handed.
mkbranch() { git -C "$REPO" branch "$1" main; }

# run_br <cwd> <args...> — bats' own `run` captures status and output.
run_br() {
  local cwd="$1"
  shift
  run env HOME="$HOME" bash -c 'cd "$1" && shift && exec "$@"' _ "$cwd" "$SCRIPT" "$@"
}

# ------------------------------------------------------------ the ordinary path

@test "a branch whose PR merged into the default is deleted" {
  mkbranch merged
  git -C "$REPO" config branch.merged.remote origin
  gh_says 0 "$(tip_of merged) main"
  run_br "$REPO" merged
  assert_success
  refute has_branch merged
  assert_output --partial "deleted branch merged"
  run git -C "$REPO" config --get-regexp '^branch\.merged\.'
  assert_failure
}

# ------------------------------------------------------------------- the gates

@test "a branch that moved past its merged PR is kept" {
  mkbranch tipmoved
  gh_says 0 "0000000000000000000000000000000000000000 main"
  run_br "$REPO" tipmoved
  assert_failure 3
  assert has_branch tipmoved
  assert_output --partial "moved past it"
  refute_output --partial "deleted branch"
}

@test "a branch with no merged PR is kept, and the message names that gate" {
  mkbranch unmerged
  gh_says 0 ""
  run_br "$REPO" unmerged
  assert_failure 3
  assert has_branch unmerged
  assert_output --partial "no merged PR"
  refute_output --partial "could not reach gh"
}

@test "an unreachable gh is kept without claiming a verdict" {
  mkbranch nogh
  gh_says 1
  run_br "$REPO" nogh
  assert_failure 3
  assert has_branch nogh
  assert_output --partial "could not reach gh"
  refute_output --partial "no merged PR"
}

# --------------------------------------------------------------- the base gate

@test "a PR merged into a parent feature branch keeps the branch and names the parent" {
  mkbranch stacked
  gh_says 0 "$(tip_of stacked) feature/parent"
  run_br "$REPO" stacked
  assert_failure 3
  assert has_branch stacked
  assert_output --partial "merged into feature/parent"
  assert_output --partial "main (per origin/HEAD)"
  refute_output --partial "moved past it"
}

@test "a parent-base PR at the tip plus an older default-base PR still keeps the branch" {
  # The case that passes under a naive two-test implementation: gating on the
  # base separately from the OID membership lets one test find the default-base
  # PR and the other find the tip, and they are not the same PR. The OID set has
  # to be built from default-base PRs alone, which is what this pins.
  mkbranch stackmixed
  gh_says 0 "$(tip_of stackmixed) feature/parent
0000000000000000000000000000000000000000 main"
  run_br "$REPO" stackmixed
  assert_failure 3
  assert has_branch stackmixed
  assert_output --partial "merged into feature/parent"
}

@test "with no origin/HEAD the default comes from gh and the branch is deleted" {
  git -C "$REPO" symbolic-ref --delete refs/remotes/origin/HEAD
  mkbranch nodefault
  gh_dispatch 'printf %s main' "$(tip_of nodefault) main"
  run_br "$REPO" nodefault
  assert_success
  refute has_branch nodefault
}

@test "with neither source for the default the branch is kept and the repair named" {
  git -C "$REPO" symbolic-ref --delete refs/remotes/origin/HEAD
  mkbranch nodefaultgone
  gh_dispatch 'exit 1' "$(tip_of nodefaultgone) main"
  run_br "$REPO" nodefaultgone
  assert_failure 3
  assert has_branch nodefaultgone
  assert_output --partial "which branch is the"
  assert_output --partial "remote set-head origin -a"
}

# ------------------------------------------------------------------ checked out

@test "a branch checked out in a linked worktree is kept, naming the checkout" {
  mkbranch heldhere
  git -C "$REPO" worktree add -q "$TEST_TMPDIR/held" heldhere
  heldwt_real="$(cd "$TEST_TMPDIR/held" && pwd -P)"
  gh_says 0 "$(tip_of heldhere) main"
  run_br "$REPO" heldhere
  assert_failure 3
  assert has_branch heldhere
  assert_output --partial "checked out at $heldwt_real"
  refute_output --partial "did not take"
}

@test "the branch you are standing on is kept, naming the main checkout" {
  gh_says 0 "$(tip_of main) main"
  run_br "$REPO" main
  assert_failure 3
  assert has_branch main
  assert_output --partial "checked out at $REPO_REAL"
}

# ------------------------------------------------------------ argument handling

@test "a name that is not a branch is rejected, not reported as a gate refusal" {
  run_br "$REPO" no-such-branch
  assert_failure 64
  assert_output --partial "no branch named no-such-branch"
  refute_output --partial "kept branch"
}

@test "a missing ref whose stanza survives has the stanza cleared" {
  git -C "$REPO" config branch.orphanstanza.remote origin
  run_br "$REPO" orphanstanza
  assert_success
  assert_output --partial "already gone"
  run git -C "$REPO" config --get-regexp '^branch\.orphanstanza\.'
  assert_failure
}

@test "a dotted sibling stanza is not mistaken for the branch's own" {
  # Dots are legal in ref names, and `config --list --name-only` flattens a
  # subsection into the key — so the stanza of `sib.dotted` prints as
  # `branch.sib.dotted.remote`, carrying the prefix `branch.sib.` that a text
  # guard for the branch `sib` would match.
  git -C "$REPO" config branch.sib.dotted.remote origin
  run_br "$REPO" sib
  assert_failure 64
  refute_output --partial "denies that write"
  refute_output --partial "cleared"
  run git -C "$REPO" config --get branch.sib.dotted.remote
  assert_success
}

@test "a missing branch is rejected as missing even when the config cannot be written" {
  # git takes the config lock BEFORE checking whether a section exists, so under
  # a denial a missing section and a denied removal are the same exit code (255).
  # Anything reading that code reports "the sandbox denies that write" for a
  # branch that simply does not exist. This pins the existence check ahead of the
  # removal attempt.
  cat >"$BIN_DIR/git" <<STUB
#!/bin/sh
case " \$* " in
  *" --remove-section "*)
    echo "error: could not lock config file .git/config" >&2
    exit 255 ;;
esac
exec "$REAL_GIT" "\$@"
STUB
  chmod +x "$BIN_DIR/git"
  run_br "$REPO" ghost-branch
  rm -f "$BIN_DIR/git"
  assert_failure 64
  refute_output --partial "denies that write"
  assert_output --partial "no branch named ghost-branch"
}

@test "an empty name is rejected rather than falling through into the gate" {
  run_br "$REPO" ""
  assert_failure 64
  assert_output --partial "name the branch"
}

@test "outside a repo with no root argument there is nothing to resolve" {
  run_br "$TEST_TMPDIR" some-branch
  assert_failure 64
  assert_output --partial "not inside a git repository"
}

# --------------------------------------------------- the root it resolves itself

@test "run from a linked worktree, the root comes from --git-common-dir" {
  # --git-dir there is the admin entry under .git/worktrees/<name>/, whose parent
  # is .git/worktrees — not a repo, so the branch lookup would miss and this
  # would exit 64. That is what makes this case detect the difference rather than
  # merely exercise it.
  mkbranch fromwt
  git -C "$REPO" worktree add -q "$TEST_TMPDIR/vantage" -b vantage-branch
  gh_says 0 "$(tip_of fromwt) main"
  run_br "$TEST_TMPDIR/vantage" fromwt
  assert_success
  refute has_branch fromwt
  refute_output --partial "no branch named"
}

@test "an explicit root wins over the cwd" {
  # What a worktree teardown depends on: by the time it calls, the directory it
  # was invoked from may not exist any more.
  mkbranch byroot
  gh_says 0 "$(tip_of byroot) main"
  run_br "$TEST_TMPDIR" byroot "$REPO"
  assert_success
  refute has_branch byroot
}

# ------------------------------- the delete is judged by the ref, not the status

@test "a delete that did not take is reported as kept, not as deleted" {
  # A sandboxed delete removes the ref and STILL returns 0 after failing to lock
  # .git/config, so git's status cannot be trusted in the direction that matters.
  # An unwritable refs/heads is the inverse — a delete that did not take — and
  # only asking whether the ref survived catches both.
  mkbranch stuckref
  gh_says 0 "$(tip_of stuckref) main"
  chmod 500 "$REPO/.git/refs/heads"
  run_br "$REPO" stuckref
  chmod 700 "$REPO/.git/refs/heads"
  assert_failure 3
  assert has_branch stuckref
  refute_output --partial "deleted branch"
  assert_output --partial "did not take"
}

# --------------------------------------------------- the residue it was filed over

@test "a denied stanza write still reports the delete as done, naming the residue" {
  # Reaching this path takes a stub, for two compounding reasons. First, `git
  # branch -D` clears the stanza ITSELF as part of the delete — so against a
  # writable fixture the cleanup finds nothing left to do and never runs. It is
  # repair code for one condition only: a delete whose ref half succeeded and
  # whose config half was denied. Second, no chmod produces that condition. Mode
  # 400 on .git/config does not block the write at all, because git builds
  # config.lock and renames it over the target, so only the directory's write bit
  # counts; mode 500 on .git does block it, but also blocks `branch -D`
  # (packed-refs.lock lives in the same directory), landing the case on the "did
  # not take" path instead. So the stub reconstructs the post-condition directly.
  mkbranch stanza
  git -C "$REPO" config branch.stanza.remote origin
  gh_says 0 "$(tip_of stanza) main"
  cat >"$BIN_DIR/git" <<STUB
#!/bin/sh
case " \$* " in
  *" branch -D "*)
    "$REAL_GIT" "\$@"; rc=\$?
    "$REAL_GIT" -C "$REPO" config branch.stanza.remote origin
    exit \$rc ;;
  *" --remove-section "*)
    echo "error: could not lock config file .git/config" >&2
    exit 255 ;;
esac
exec "$REAL_GIT" "\$@"
STUB
  chmod +x "$BIN_DIR/git"
  run_br "$REPO" stanza
  rm -f "$BIN_DIR/git"
  assert_success
  refute has_branch stanza
  assert_output --partial "deleted branch stanza"
  assert_output --partial "left [branch \"stanza\"]"
  run git -C "$REPO" config --get branch.stanza.remote
  assert_success
}
