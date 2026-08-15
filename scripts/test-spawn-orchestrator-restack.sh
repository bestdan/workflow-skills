#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034,SC2154 # Fixtures intentionally contain shell source, retain outputs for diagnostics, and read names the prelude defines.
# Post-merge restack of stacked PRs (task 18, finding #25).
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: the prelude builds it a private fixture
# tree and the isolation guards, the epilogue asserts they held. See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share, and
# dev_docs/gate-performance.md for why this suite is several files.
#
# Run directly: bash scripts/test-spawn-orchestrator-restack.sh
set -uo pipefail
SO_PART=restack
# The lib dir goes in a dedicated name, never $ROOT: fixtures below reassign
# ROOT for their own trees (the verify-branch block does ROOT="$VB/root"), so
# sourcing the epilogue off $ROOT would resolve inside a fixture instead.
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

# --- restack: post-merge restack of stacked PRs (task 18, finding #25) -------
# Builds a REAL git repo (a bare "origin" + a working clone) with a
# squash-merged parent + a stacked child, and a FAKE `gh` (offline — no
# GitHub calls) so the git mechanics run for real while every GitHub
# read/write is mocked and inspectable.
if command -v git >/dev/null 2>&1; then
  RS="$BASE/restack"
  mkdir -p "$RS"
  ORIGIN="$RS/origin.git"
  WORK="$RS/work"
  git init --bare -q "$ORIGIN"
  git init -q "$WORK"
  git -C "$WORK" remote add origin "$ORIGIN"
  git -C "$WORK" config user.email test@example.com
  git -C "$WORK" config user.name "Test"
  git -C "$WORK" checkout -q -b main
  echo root >"$WORK/root.txt"
  git -C "$WORK" add root.txt
  git -C "$WORK" commit -q -m root
  git -C "$WORK" push -q origin main

  # parent branch (task_parent, PR #100): adds parent.txt
  git -C "$WORK" checkout -q -b parent-branch
  printf 'line1\n' >"$WORK/parent.txt"
  git -C "$WORK" add parent.txt
  git -C "$WORK" commit -q -m "parent change"
  git -C "$WORK" push -q origin parent-branch
  PARENT_SHA="$(git -C "$WORK" rev-parse parent-branch)" # the child's frozen base_sha

  # child branch (task_child, PR #101): stacked on parent-branch, touches ONLY
  # child.txt — must restack cleanly regardless of what else happens to main.
  git -C "$WORK" checkout -q -b child-branch
  echo child >"$WORK/child.txt"
  git -C "$WORK" add child.txt
  git -C "$WORK" commit -q -m "child change"
  git -C "$WORK" push -q origin child-branch

  # Simulate the human squash-merging the parent PR into main: a NEW squash
  # commit on main, not an ancestor of parent-branch's own commits — the exact
  # shape that orphans a child under squash-merge.
  git -C "$WORK" checkout -q main
  git -C "$WORK" merge -q --squash parent-branch >/dev/null
  git -C "$WORK" commit -q -m "parent change (squashed)"
  # Then simulate a POST-HAND-OFF human review fix on the SAME line (run-state.md
  # "restacked child is stale" note): the child was co-reviewed against the
  # pre-review parent, so a clean rebase later must not be mistaken for proof
  # nothing was missed.
  printf 'line1-SECURITY-FIXED\n' >"$WORK/parent.txt"
  git -C "$WORK" add parent.txt
  git -C "$WORK" commit -q -m "parent: post-hand-off review fix"
  git -C "$WORK" push -q origin main

  # Fake gh: PR state lives in flat files under $FAKE_GH_DB; `pr edit --base`
  # rewrites the base file (so a second restack observes the retarget) and
  # appends to edits.log (so the test can assert exactly what was retargeted).
  FAKE_GH_DB="$RS/ghdb"
  mkdir -p "$FAKE_GH_DB"
  printf 'MERGED\n' >"$FAKE_GH_DB/100.state"
  printf 'main\n' >"$FAKE_GH_DB/100.base"
  printf 'OPEN\n' >"$FAKE_GH_DB/101.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/101.base"
  FAKE_GH="$RS/gh"
  cat >"$FAKE_GH" <<'GHEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${FAKE_GH_DB:?FAKE_GH_DB not set}"
[ "$1" = pr ] || exit 1
sub="$2"; num="$3"; shift 3
case "$sub" in
  view)
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .baseRefName) f="$db/$num.base" ;;
      .state)       f="$db/$num.state" ;;
      *) exit 1 ;;
    esac
    # Real `gh` contract (measured): a PR that does not exist exits 1 with a
    # GraphQL "Could not resolve" line on STDERR — never exit 0 with empty
    # output (the invariant-3 stub bug from the review-feedback doc).
    if [ -f "$f" ]; then cat "$f"; else
      echo "GraphQL: Could not resolve to a PullRequest with the number of $num. (repository.pullRequest)" >&2
      exit 1
    fi
    ;;
  edit)
    [ -f "$db/$num.editfail" ] && exit 1   # simulate a rejected `gh pr edit`
    while [ $# -gt 0 ]; do
      case "$1" in --base) printf '%s\n' "$2" >"$db/$num.base"; echo "$num $2" >>"$db/edits.log"; shift 2 ;; *) shift ;; esac
    done
    ;;
  *) exit 1 ;;
esac
GHEOF
  chmod +x "$FAKE_GH"
  export FAKE_GH_DB

  RUNDIR_RS="$RS/run"
  mkdir -p "$RUNDIR_RS/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n'
    printf '\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_parent | handed-off | parent-branch | main | - | #100 | |\n'
    printf '| task_child  | handed-off | child-branch | parent-branch | %s | #101 | |\n' "$PARENT_SHA"
  } >"$RUNDIR_RS/.auto-pilot/RUN.md"

  # HEAD invariant (finding #23 / task 13's assert-run-head): restack must never
  # move the caller's HEAD. `git rebase --onto X Y <branch>` CHECKS OUT <branch>,
  # so a naive restack would park the RUN worktree's HEAD on a task branch — the
  # exact bug task 13 guards against. Record HEAD before every restack below.
  head_ref_0="$(git -C "$WORK" rev-parse --abbrev-ref HEAD)"
  head_sha_0="$(git -C "$WORK" rev-parse HEAD)"

  rsout="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rsc=$?
  [ "$rsc" = 0 ] && ok "restack: exits 0 on a clean stacked restack" || bad "restack: exits 0 on a clean stacked restack" "$rsout"
  have "restack: reports task_child done" 'restack task_child done' "$rsout"
  have "restack: prints the copy-pasteable rebase command" 'git rebase --onto origin/main' "$rsout"
  have "restack: retargets the PR base" '101 main' "$(cat "$FAKE_GH_DB/edits.log" 2>/dev/null)"

  if [ "$(git -C "$WORK" rev-parse --abbrev-ref HEAD)" = "$head_ref_0" ] \
    && [ "$(git -C "$WORK" rev-parse HEAD)" = "$head_sha_0" ]; then
    ok "restack: a successful restack does NOT move the caller's HEAD"
  else
    bad "restack: a successful restack does NOT move the caller's HEAD" \
      "was $head_ref_0@$head_sha_0 now $(git -C "$WORK" rev-parse --abbrev-ref HEAD)@$(git -C "$WORK" rev-parse HEAD)"
  fi
  # …and leaves no scratch worktree registered behind it
  if git -C "$WORK" worktree list 2>/dev/null | grep -q 'restack-wt'; then
    bad "restack: removes its scratch worktree"
  else
    ok "restack: removes its scratch worktree"
  fi

  # REPORT.md is where a human actually looks — the re-verify + stale-co-review
  # requirement is worthless on stdout alone (it only reaches orchestrator.log).
  rsreport="$(cat "$RUNDIR_RS/.auto-pilot/REPORT.md" 2>/dev/null)"
  have "restack: appends a Restack section to REPORT.md" '## Restack' "$rsreport"
  have "restack: REPORT.md demands re-verify for the child" 'Re-verify required' "$rsreport"
  have "restack: REPORT.md flags the co-review as STALE" 'STALE' "$rsreport"

  git -C "$WORK" fetch -q origin
  diffnames="$(git -C "$WORK" diff --name-only origin/main origin/child-branch)"
  if [ "$diffnames" = "child.txt" ]; then
    ok "restack: child's post-restack diff contains ONLY its own file"
  else
    bad "restack: child's post-restack diff contains ONLY its own file" "got: $diffnames"
  fi
  lack "restack: child diff does not re-propose parent.txt" 'parent.txt' "$diffnames"

  # idempotency: a second restack makes no additional rebase/push/gh-edit, and
  # does not churn REPORT.md either
  editcount_before="$(wc -l <"$FAKE_GH_DB/edits.log" 2>/dev/null | tr -d ' ')"
  reportsize_before="$(wc -c <"$RUNDIR_RS/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  rsout2="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rsc2=$?
  [ "$rsc2" = 0 ] && ok "restack: idempotent second run exits 0" || bad "restack: idempotent second run exits 0" "$rsout2"
  have "restack: idempotent second run reports no-op" 'already based on main (no-op)' "$rsout2"
  editcount_after="$(wc -l <"$FAKE_GH_DB/edits.log" 2>/dev/null | tr -d ' ')"
  [ "$editcount_before" = "$editcount_after" ] && ok "restack: idempotent run makes no additional gh edit" \
    || bad "restack: idempotent run makes no additional gh edit" "before=$editcount_before after=$editcount_after"
  reportsize_after="$(wc -c <"$RUNDIR_RS/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  [ "$reportsize_before" = "$reportsize_after" ] && ok "restack: idempotent run does not churn REPORT.md" \
    || bad "restack: idempotent run does not churn REPORT.md" "before=$reportsize_before after=$reportsize_after"

  # --- fail-closed on conflict + the HEAD invariant under a MIXED run ---------
  # Build two more stacked children so ONE restack run both succeeds (clean2)
  # and conflicts (conflict-child):
  #   conflict-child edits the SAME line the parent's post-hand-off review fix
  #     touched — the "clean rebase proves nothing" case, except here it isn't
  #     clean: it must conflict, abort, and never force-push or retarget.
  #   clean2 touches only its own file and must restack normally.
  # Asserting HEAD across THIS run is the real test: a naive `git rebase --onto
  # … <branch>` would have checked out (and left HEAD on) a task branch.
  git -C "$WORK" checkout -q parent-branch
  PARENT_SHA2="$(git -C "$WORK" rev-parse parent-branch)"
  git -C "$WORK" checkout -q -b conflict-child
  printf 'line1-CONFLICTING-EDIT\n' >"$WORK/parent.txt"
  git -C "$WORK" add parent.txt
  git -C "$WORK" commit -q -m "child edits the same line"
  git -C "$WORK" push -q origin conflict-child
  printf 'OPEN\n' >"$FAKE_GH_DB/102.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/102.base"

  git -C "$WORK" checkout -q parent-branch
  git -C "$WORK" checkout -q -b clean2
  echo clean2 >"$WORK/clean2.txt"
  git -C "$WORK" add clean2.txt
  git -C "$WORK" commit -q -m "clean2 change"
  git -C "$WORK" push -q origin clean2
  printf 'OPEN\n' >"$FAKE_GH_DB/103.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/103.base"

  {
    printf '| task_conflict | handed-off | conflict-child | parent-branch | %s | #102 | |\n' "$PARENT_SHA2"
    printf '| task_clean2   | handed-off | clean2         | parent-branch | %s | #103 | |\n' "$PARENT_SHA2"
  } >>"$RUNDIR_RS/.auto-pilot/RUN.md"

  # Park HEAD somewhere deliberate (main) so a stray checkout is unmistakable.
  git -C "$WORK" checkout -q main
  head_ref_1="$(git -C "$WORK" rev-parse --abbrev-ref HEAD)"
  head_sha_1="$(git -C "$WORK" rev-parse HEAD)"

  precommit_tip="$(git -C "$ORIGIN" rev-parse conflict-child)"
  rsout3="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rsc3=$?
  [ "$rsc3" = 2 ] && ok "restack: fail-closed conflict exits non-zero" || bad "restack: fail-closed conflict exits non-zero" "exit=$rsc3"
  have "restack: fail-closed conflict reports a conflict" 'FAILED — rebase conflict' "$rsout3"
  have "restack: a conflict does not stop the other children" 'restack task_clean2 done' "$rsout3"
  postcommit_tip="$(git -C "$ORIGIN" rev-parse conflict-child)"
  [ "$precommit_tip" = "$postcommit_tip" ] && ok "restack: conflict never force-pushes the broken branch" \
    || bad "restack: conflict never force-pushes the broken branch"
  lack "restack: conflict never retargets the PR" '102 main' "$(cat "$FAKE_GH_DB/edits.log" 2>/dev/null)"

  # THE invariant: a run that both succeeded and conflicted left HEAD untouched.
  if [ "$(git -C "$WORK" rev-parse --abbrev-ref HEAD)" = "$head_ref_1" ] \
    && [ "$(git -C "$WORK" rev-parse HEAD)" = "$head_sha_1" ]; then
    ok "restack: HEAD unchanged across a mixed success+conflict run (task 13 invariant)"
  else
    bad "restack: HEAD unchanged across a mixed success+conflict run (task 13 invariant)" \
      "was $head_ref_1@$head_sha_1 now $(git -C "$WORK" rev-parse --abbrev-ref HEAD)@$(git -C "$WORK" rev-parse HEAD)"
  fi
  [ -z "$(git -C "$WORK" status --porcelain 2>/dev/null)" ] \
    && ok "restack: caller's worktree left clean (no half-applied rebase)" \
    || bad "restack: caller's worktree left clean (no half-applied rebase)"
  if git -C "$WORK" worktree list 2>/dev/null | grep -q 'restack-wt'; then
    bad "restack: removes its scratch worktree even on the conflict path"
  else
    ok "restack: removes its scratch worktree even on the conflict path"
  fi

  # fail-closed: a dirty caller worktree is never touched (an automated rebase
  # over a human's uncommitted work is how "helpful" recovery destroys state)
  echo dirty >"$WORK/uncommitted.txt"
  dirty_out="$("$SCRIPT" restack --run-dir "$RUNDIR_RS" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  dirty_c=$?
  if [ "$dirty_c" = 2 ] && printf '%s' "$dirty_out" | grep -qF 'worktree is dirty'; then
    ok "restack: dirty caller worktree fails closed"
  else
    bad "restack: dirty caller worktree fails closed" "exit=$dirty_c msg=$dirty_out"
  fi
  rm -f "$WORK/uncommitted.txt"

  # --- orphaned-child detector: a merged/deleted base is a flagged defect ----
  RSD="$RS/detect-run"
  mkdir -p "$RSD/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_parent  | handed-off | parent-branch | main | - | #100 | |\n'
    # deleted/unreadable base ref (LOUD case: gh has nothing for its baseRefName)
    printf '| task_deleted | handed-off | deleted-child | gone-branch | deadbeef | #200 | |\n'
    # base branch's own tracked PR is MERGED, but this child was never
    # retargeted (QUIET case — restack itself can't fix it: no base_sha) —
    # the exact "looks healthy, does nothing" shape finding #25 warns about
    printf '| task_quiet   | handed-off | quiet-child   | parent-branch | - | #201 | |\n'
  } >"$RSD/.auto-pilot/RUN.md"
  printf 'OPEN\n' >"$FAKE_GH_DB/200.state" # no 200.base file at all -> baseRefName lookup fails
  printf 'OPEN\n' >"$FAKE_GH_DB/201.state"
  printf 'parent-branch\n' >"$FAKE_GH_DB/201.base"

  dsout="$("$SCRIPT" restack --run-dir "$RSD" --repo "$WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  dsc=$?
  [ "$dsc" = 2 ] && ok "restack: orphan-detector run reports the missing-base_sha failure" \
    || bad "restack: orphan-detector run reports the missing-base_sha failure" "exit=$dsc"
  have "restack: flags a deleted/unreadable base as a defect" 'DEFECT task_deleted' "$dsout"
  have "restack: flags a PR still targeting a merged branch as a defect" 'DEFECT task_quiet' "$dsout"
  have "restack: defect summary count is non-zero" 'defects=2' "$dsout"

  # --- transient gh failure is UNDETERMINED, never a DEFECT ------------------
  # Real gh contract: a missing PR exits 1 with "Could not resolve to a
  # PullRequest" (a positive "it's gone", flagged above); an expired auth /
  # rate limit / network error also exits 1 but says something ELSE — that
  # proves nothing about the PR, so it must neither fail the restack (exit 2)
  # nor mint a false DEFECT during a GitHub blip.
  RSU="$RS/undetermined-run"
  mkdir -p "$RSU/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_t | handed-off | t-branch | untracked-parent | - | #301 | |\n'
  } >"$RSU/.auto-pilot/RUN.md"
  TRANSIENT_GH="$RS/gh-transient"
  printf '#!/bin/sh\necho "HTTP 401: Bad credentials (https://api.github.com/graphql)" >&2\nexit 1\n' >"$TRANSIENT_GH"
  chmod +x "$TRANSIENT_GH"
  udout="$("$SCRIPT" restack --run-dir "$RSU" --repo "$WORK" --remote origin --gh "$TRANSIENT_GH" 2>&1)"
  udc=$?
  [ "$udc" = 0 ] && ok "restack: a transient gh failure does NOT fail the restack (exit 0, fail-safe)" \
    || bad "restack: a transient gh failure does NOT fail the restack (exit 0, fail-safe)" "exit=$udc $udout"
  lack "restack: a transient gh failure is never flagged as a DEFECT" 'DEFECT' "$udout"
  have "restack: a transient gh failure is announced as UNDETERMINED (not silent)" 'UNDETERMINED task_t' "$udout"
  have "restack: the undetermined summary still reads defects=0" 'defects=0' "$udout"

  # === co-review scenarios: cascade (3-deep), retarget-failure, closed child ===
  # A fresh bare origin + clone so prior mutations don't bleed in.
  C_ORIGIN="$RS/c-origin.git"
  C_WORK="$RS/c-work"
  git init --bare -q "$C_ORIGIN"
  git init -q "$C_WORK"
  git -C "$C_WORK" remote add origin "$C_ORIGIN"
  git -C "$C_WORK" config user.email t@e
  git -C "$C_WORK" config user.name T
  git -C "$C_WORK" checkout -q -b main
  echo r >"$C_WORK/r.txt"
  git -C "$C_WORK" add r.txt
  git -C "$C_WORK" commit -q -m r
  git -C "$C_WORK" push -q origin main
  # chain A <- B <- C (each touches only its own file).
  git -C "$C_WORK" checkout -q -b br-a
  echo a >"$C_WORK/a.txt"
  git -C "$C_WORK" add a.txt
  git -C "$C_WORK" commit -q -m a
  git -C "$C_WORK" push -q origin br-a
  A_SHA="$(git -C "$C_WORK" rev-parse br-a)"
  git -C "$C_WORK" checkout -q -b br-b
  echo b >"$C_WORK/b.txt"
  git -C "$C_WORK" add b.txt
  git -C "$C_WORK" commit -q -m b
  git -C "$C_WORK" push -q origin br-b
  B_SHA="$(git -C "$C_WORK" rev-parse br-b)"
  git -C "$C_WORK" checkout -q -b br-c
  echo c >"$C_WORK/c.txt"
  git -C "$C_WORK" add c.txt
  git -C "$C_WORK" commit -q -m c
  git -C "$C_WORK" push -q origin br-c
  # A squash-merges to main.
  git -C "$C_WORK" checkout -q main
  git -C "$C_WORK" merge -q --squash br-a >/dev/null
  git -C "$C_WORK" commit -q -m "a squashed"
  git -C "$C_WORK" push -q origin main
  git -C "$C_WORK" checkout -q main
  C_DB="$RS/c-ghdb"
  mkdir -p "$C_DB"
  printf 'MERGED\n' >"$C_DB/1.state"
  printf 'main\n' >"$C_DB/1.base" # A merged
  printf 'OPEN\n' >"$C_DB/2.state"
  printf 'br-a\n' >"$C_DB/2.base" # B on parent branch
  printf 'OPEN\n' >"$C_DB/3.state"
  printf 'br-b\n' >"$C_DB/3.base" # C on B's branch
  C_RUN="$RS/c-run"
  mkdir -p "$C_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_a | handed-off | br-a | main | - | #1 | |\n'
    printf '| t_b | handed-off | br-b | br-a | %s | #2 | |\n' "$A_SHA"
    printf '| t_c | handed-off | br-c | br-b | %s | #3 | |\n' "$B_SHA"
  } >"$C_RUN/.auto-pilot/RUN.md"
  export FAKE_GH_DB="$C_DB"
  cout="$("$SCRIPT" restack --run-dir "$C_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  ccode=$?
  # B restacks onto-base (main); C CASCADEs onto B's new tip in a later pass —
  # its PR base stays br-b, never re-proposing B's changeset.
  have "restack cascade: B retargeted to main" '2 main' "$(cat "$C_DB/edits.log" 2>/dev/null)"
  have "restack cascade: C restacked in cascade mode" 'restack t_c done (cascade)' "$cout"
  lack "restack cascade: C is NOT retargeted to main" '3 main' "$(cat "$C_DB/edits.log" 2>/dev/null)"
  [ "$(cat "$C_DB/3.base")" = "br-b" ] && ok "restack cascade: C's PR base stays br-b" || bad "restack cascade: C's PR base stays br-b" "$(cat "$C_DB/3.base")"
  git -C "$C_WORK" fetch -q origin
  # C's PR targets br-b (cascade keeps the parent base), so its PR diff is br-b..br-c
  # — must be ONLY c.txt, i.e. C sits cleanly on B's NEW tip with no orphaned
  # duplicate of B's pre-rewrite commits.
  cdiff="$(git -C "$C_WORK" diff --name-only origin/br-b origin/br-c)"
  [ "$cdiff" = "c.txt" ] && ok "restack cascade: C's PR diff (br-b..br-c) is ONLY c.txt (cascaded onto B's new tip)" \
    || bad "restack cascade: C's PR diff is ONLY c.txt" "got: $cdiff"
  [ "$ccode" = 0 ] && ok "restack cascade: clean 3-deep run exits 0" || bad "restack cascade: clean 3-deep run exits 0" "exit=$ccode"

  # RESUMABLE cascade (fix 1): a PARTIAL earlier run rewrote the parent (B) but
  # never cascaded the grandchild (C) — a fresh process has an empty in-memory
  # _RS_NEWTIP, so C must be detected from the REMOTE (parent OPEN + already on
  # base_branch + its remote tip moved off C's base_sha) and cascaded, not
  # silently stranded. Fresh chain X<-Y<-Z; X merged.
  git -C "$C_WORK" checkout -q main
  git -C "$C_WORK" checkout -q -b br-x
  echo x >"$C_WORK/x.txt"
  git -C "$C_WORK" add x.txt
  git -C "$C_WORK" commit -q -m x
  git -C "$C_WORK" push -q origin br-x
  X_SHA="$(git -C "$C_WORK" rev-parse br-x)"
  git -C "$C_WORK" checkout -q -b br-y
  echo y >"$C_WORK/y.txt"
  git -C "$C_WORK" add y.txt
  git -C "$C_WORK" commit -q -m y
  git -C "$C_WORK" push -q origin br-y
  Y_SHA="$(git -C "$C_WORK" rev-parse br-y)"
  git -C "$C_WORK" checkout -q -b br-z
  echo z >"$C_WORK/z.txt"
  git -C "$C_WORK" add z.txt
  git -C "$C_WORK" commit -q -m z
  git -C "$C_WORK" push -q origin br-z
  git -C "$C_WORK" checkout -q main
  git -C "$C_WORK" merge -q --squash br-x >/dev/null
  git -C "$C_WORK" commit -q -m "x squashed"
  git -C "$C_WORK" push -q origin main
  git -C "$C_WORK" checkout -q main
  printf 'MERGED\n' >"$C_DB/30.state"
  printf 'main\n' >"$C_DB/30.base"
  printf 'OPEN\n' >"$C_DB/31.state"
  printf 'br-x\n' >"$C_DB/31.base"
  printf 'OPEN\n' >"$C_DB/32.state"
  printf 'br-y\n' >"$C_DB/32.base"
  # Phase 1: RUN.md WITHOUT Z, so only Y restacks (Z is never seen this run).
  P1_RUN="$RS/p1-run"
  mkdir -p "$P1_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_x | handed-off | br-x | main | - | #30 | |\n'
    printf '| t_y | handed-off | br-y | br-x | %s | #31 | |\n' "$X_SHA"
  } >"$P1_RUN/.auto-pilot/RUN.md"
  "$SCRIPT" restack --run-dir "$P1_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" >/dev/null 2>&1
  [ "$(cat "$C_DB/31.base")" = "main" ] && ok "restack resumable: phase-1 restacks Y to main" || bad "restack resumable: phase-1 restacks Y to main" "$(cat "$C_DB/31.base")"
  # Phase 2: a FRESH process (empty _RS_NEWTIP) with Z now in the table. Z's
  # recorded base_sha is Y's OLD tip; Y's remote tip has moved — Z must cascade.
  P2_RUN="$RS/p2-run"
  mkdir -p "$P2_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_x | handed-off | br-x | main | - | #30 | |\n'
    printf '| t_y | handed-off | br-y | br-x | %s | #31 | |\n' "$X_SHA"
    printf '| t_z | handed-off | br-z | br-y | %s | #32 | |\n' "$Y_SHA"
  } >"$P2_RUN/.auto-pilot/RUN.md"
  p2out="$("$SCRIPT" restack --run-dir "$P2_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  have "restack resumable: Z cascaded from the REMOTE parent tip (not stranded)" 'restack t_z done (cascade)' "$p2out"
  lack "restack resumable: Z not retargeted to main (base stays br-y)" '32 main' "$(cat "$C_DB/edits.log" 2>/dev/null)"
  git -C "$C_WORK" fetch -q origin
  zdiff="$(git -C "$C_WORK" diff --name-only origin/br-y origin/br-z)"
  [ "$zdiff" = "z.txt" ] && ok "restack resumable: Z's PR diff (br-y..br-z) is ONLY z.txt" || bad "restack resumable: Z's PR diff is ONLY z.txt" "got: $zdiff"
  # idempotent: a cascaded child re-run is a no-op — no re-cascade, no REPORT churn.
  p2report_before="$(wc -c <"$P2_RUN/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  p3out="$("$SCRIPT" restack --run-dir "$P2_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  lack "restack resumable: re-run does NOT re-cascade Z" 'restack t_z done (cascade)' "$p3out"
  have "restack resumable: re-run reports the cascaded child a no-op" 'already cascaded' "$p3out"
  p2report_after="$(wc -c <"$P2_RUN/.auto-pilot/REPORT.md" 2>/dev/null | tr -d ' ')"
  [ "$p2report_before" = "$p2report_after" ] && ok "restack resumable: re-run does not churn REPORT.md" \
    || bad "restack resumable: re-run does not churn REPORT.md" "before=$p2report_before after=$p2report_after"

  # retarget-failure (fix 2): push succeeds, `gh pr edit` rejected -> DEFECT, the
  # child is NOT marked done, and the run exits non-zero. Fresh single stack.
  printf 'MERGED\n' >"$C_DB/10.state"
  printf 'main\n' >"$C_DB/10.base"
  printf 'OPEN\n' >"$C_DB/11.state"
  printf 'br-a\n' >"$C_DB/11.base"
  : >"$C_DB/11.editfail"
  git -C "$C_WORK" checkout -q br-a
  git -C "$C_WORK" checkout -q -b br-rt
  echo rt >"$C_WORK/rt.txt"
  git -C "$C_WORK" add rt.txt
  git -C "$C_WORK" commit -q -m rt
  git -C "$C_WORK" push -q origin br-rt
  git -C "$C_WORK" checkout -q main
  RT_RUN="$RS/rt-run"
  mkdir -p "$RT_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_p | handed-off | br-a | main | - | #10 | |\n'
    printf '| t_rt | handed-off | br-rt | br-a | %s | #11 | |\n' "$A_SHA"
  } >"$RT_RUN/.auto-pilot/RUN.md"
  rtout="$("$SCRIPT" restack --run-dir "$RT_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  rtcode=$?
  have "restack retarget-fail: reports a DEFECT" 'DEFECT — rebased and force-pushed' "$rtout"
  lack "restack retarget-fail: does NOT report the child done" 'restack t_rt done' "$rtout"
  [ "$rtcode" = 2 ] && ok "restack retarget-fail: exits non-zero" || bad "restack retarget-fail: exits non-zero" "exit=$rtcode"

  # closed child (fix 3): a CLOSED child PR is a LOUD orphan — flag it, and NEVER
  # force-push its branch.
  printf 'MERGED\n' >"$C_DB/20.state"
  printf 'main\n' >"$C_DB/20.base"
  printf 'CLOSED\n' >"$C_DB/21.state"
  printf 'br-a\n' >"$C_DB/21.base"
  git -C "$C_WORK" checkout -q br-a
  git -C "$C_WORK" checkout -q -b br-closed
  echo cl >"$C_WORK/cl.txt"
  git -C "$C_WORK" add cl.txt
  git -C "$C_WORK" commit -q -m cl
  git -C "$C_WORK" push -q origin br-closed
  git -C "$C_WORK" checkout -q main
  CLOSED_TIP_BEFORE="$(git -C "$C_WORK" rev-parse origin/br-closed)"
  CL_RUN="$RS/cl-run"
  mkdir -p "$CL_RUN/.auto-pilot"
  {
    printf -- '---\nbase_branch: main\n---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| t_p2 | handed-off | br-a | main | - | #20 | |\n'
    printf '| t_cl | handed-off | br-closed | br-a | %s | #21 | |\n' "$A_SHA"
  } >"$CL_RUN/.auto-pilot/RUN.md"
  clout="$("$SCRIPT" restack --run-dir "$CL_RUN" --repo "$C_WORK" --remote origin --gh "$FAKE_GH" 2>&1)"
  clcode=$?
  have "restack closed-child: flagged as a DEFECT" 'DEFECT — PR #21 is CLOSED' "$clout"
  git -C "$C_WORK" fetch -q origin
  [ "$(git -C "$C_WORK" rev-parse origin/br-closed)" = "$CLOSED_TIP_BEFORE" ] \
    && ok "restack closed-child: branch was NOT force-pushed" \
    || bad "restack closed-child: branch was NOT force-pushed"
  [ "$clcode" = 2 ] && ok "restack closed-child: exits non-zero" || bad "restack closed-child: exits non-zero" "exit=$clcode"

  unset FAKE_GH_DB
else
  echo "skip - restack: git not available"
fi

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
