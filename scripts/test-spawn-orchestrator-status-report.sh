#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034,SC2154 # Fixtures intentionally contain shell source, retain outputs for diagnostics, and read names the prelude defines.
# status-report: the periodic heartbeat report (task 20, finding #28).
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: the prelude builds it a private fixture
# tree and the isolation guards, the epilogue asserts they held. See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share, and
# dev_docs/gate-performance.md for why this suite is several files.
#
# Run directly: bash scripts/test-spawn-orchestrator-status-report.sh
set -uo pipefail
SO_PART=status-report
# The lib dir goes in a dedicated name, never $ROOT: fixtures below reassign
# ROOT for their own trees (the verify-branch block does ROOT="$VB/root"), so
# sourcing the epilogue off $ROOT would resolve inside a fixture instead.
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

# --- status-report: the periodic heartbeat report (task 20, finding #28) -----
# Reuses this suite's existing conventions: real fixture dirs, a fake `gh`
# (flat-file DB, same shape as restack's), and everything driven through the
# REAL generated wrapper wherever the acceptance criteria demand it (NO model
# call; emitted on a gate-closed wake) — never a re-implementation of the
# wrapper's own call sequence.
SR="$BASE/status-report"
mkdir -p "$SR"

if command -v git >/dev/null 2>&1; then
  SR_REPO="$SR/repo"
  git init -q "$SR_REPO"
  git -C "$SR_REPO" config user.email test@example.com
  git -C "$SR_REPO" config user.name "Test"
  git -C "$SR_REPO" checkout -q -b main
  echo root >"$SR_REPO/root.txt"
  git -C "$SR_REPO" add root.txt
  git -C "$SR_REPO" commit -q -m root

  # branch_impl: a commit made "now" — comfortably inside a generous ceiling.
  git -C "$SR_REPO" checkout -q -b branch_impl
  echo impl >"$SR_REPO/impl.txt"
  git -C "$SR_REPO" add impl.txt
  git -C "$SR_REPO" commit -q -m "impl work"

  # branch_over: a commit stamped 1h ago — over a 2-minute ceiling, the
  # working-vs-wedged signal the report exists to surface.
  git -C "$SR_REPO" checkout -q main
  git -C "$SR_REPO" checkout -q -b branch_over
  echo over >"$SR_REPO/over.txt"
  git -C "$SR_REPO" add over.txt
  # An unambiguous "@<epoch> +0000" form — a bare "YYYY-MM-DDTHH:MM:SS" with no
  # zone is read by git as LOCAL time, which silently produced a commit git
  # thought was hours in the FUTURE on a non-UTC box (a real bug this exact
  # fixture caught once already).
  SR_OVER_EPOCH=$(($(date +%s) - 3600))
  GIT_AUTHOR_DATE="@$SR_OVER_EPOCH +0000" GIT_COMMITTER_DATE="@$SR_OVER_EPOCH +0000" \
    git -C "$SR_REPO" commit -q -m "over-ceiling work"

  git -C "$SR_REPO" checkout -q main
  for b in branch_handed branch_parked branch_child branch_claimed; do
    git -C "$SR_REPO" checkout -q -b "$b" main >/dev/null
    echo "$b" >"$SR_REPO/$b.txt"
    git -C "$SR_REPO" add "$b.txt"
    git -C "$SR_REPO" commit -q -m "$b"
  done
  # branch_fresh: claimed but NO commits beyond its base yet — every base..branch
  # range is empty, the shape whose old whole-history fallback selected the
  # repository's oldest commit ("running since repo genesis").
  git -C "$SR_REPO" branch branch_fresh main
  git -C "$SR_REPO" checkout -q main

  # Fake gh: same flat-file-DB shape as restack's fixture, extended with
  # `pr list --head <branch>` (the #23 divergence lookup status-report adds).
  SR_GHDB="$SR/ghdb"
  mkdir -p "$SR_GHDB"
  printf 'MERGED\n' >"$SR_GHDB/201.state"
  printf 'main\n' >"$SR_GHDB/201.base"
  printf 'UNKNOWN\n' >"$SR_GHDB/201.mergeable"
  printf '\n' >"$SR_GHDB/202.state"
  printf '' >"$SR_GHDB/202.base" # base ref deleted (LOUD orphan)
  printf 'OPEN\n' >"$SR_GHDB/203.state"
  printf '203\n' >"$SR_GHDB/list-branch_claimed.number"
  SR_GH="$SR/gh"
  cat >"$SR_GH" <<'GHEOF'
#!/usr/bin/env bash
set -uo pipefail
db="${SR_GHDB:?SR_GHDB not set}"
: >>"${SR_GH_CALLS:-/dev/null}"
echo "$*" >>"${SR_GH_CALLS:-/dev/null}"
[ "$1" = pr ] || exit 1
sub="$2"; shift 2
case "$sub" in
  view)
    num="$1"; shift
    jqexpr=""
    while [ $# -gt 0 ]; do case "$1" in --jq) jqexpr="$2"; shift 2 ;; *) shift ;; esac; done
    case "$jqexpr" in
      .baseRefName) f="$db/$num.base" ;;
      .state)       f="$db/$num.state" ;;
      .mergeable)   f="$db/$num.mergeable" ;;
      *) exit 1 ;;
    esac
    # Real `gh` contract (measured): a missing PR exits 1 with a GraphQL
    # "Could not resolve" line on STDERR — never exit 0 with empty output.
    if [ -f "$f" ]; then cat "$f"; else
      echo "GraphQL: Could not resolve to a PullRequest with the number of $num. (repository.pullRequest)" >&2
      exit 1
    fi
    ;;
  list)
    head=""
    while [ $# -gt 0 ]; do case "$1" in --head) head="$2"; shift 2 ;; *) shift ;; esac; done
    cat "$db/list-$head.number" 2>/dev/null || printf 'null\n'
    ;;
  *) exit 1 ;;
esac
GHEOF
  chmod +x "$SR_GH"
  export SR_GHDB

  # Fake claude-usage.sh: canned session-window JSON, no network.
  SR_USAGE="$SR/usage.sh"
  cat >"$SR_USAGE" <<'USEOF'
#!/usr/bin/env bash
printf '{"session":{"percent":42,"resets_at":"2099-01-01T00:00:00Z"},"weekly_all":{"percent":18,"resets_at":"2099-01-08T00:00:00Z"},"spend_used_minor":0}\n'
USEOF
  chmod +x "$SR_USAGE"

  # ISO-8601 UTC N seconds from now, portable BSD/GNU.
  _sr_iso() { date -u -v+"${1}"S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "+${1} seconds" +%Y-%m-%dT%H:%M:%SZ; }

  SR_RUN="$SR/run"
  mkdir -p "$SR_RUN/.auto-pilot"
  _sr_write_run_md() {
    # $1: task_claimed's phase (claimed|implementing) so the delta test can
    # advance it; $2: task_over's branch elapsed baseline stays fixed.
    {
      printf -- '---\n'
      printf 'base_branch: main\n'
      printf 'until: %s\n' "$(_sr_iso 3600)"
      printf 'min_task_budget: 20m\n'
      printf -- '---\n\n'
      printf '| task | phase | branch | base | base_sha | pr | notes |\n'
      printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
      printf '| task_pending  | pending      | -             | main         | -   | -    | |\n'
      printf '| task_impl     | implementing | branch_impl   | main         | -   | -    | |\n'
      printf '| task_over     | implementing | branch_over   | main         | -   | -    | |\n'
      printf '| task_handed   | handed-off   | branch_handed | main         | -   | #201 | |\n'
      printf '| task_parked   | parked       | branch_parked | main         | -   | -    | |\n'
      printf '| task_child    | pr-open      | branch_child  | branch_parent| -   | #202 | |\n'
      printf '| task_claimed  | %s | branch_claimed | main       | -   | -    | |\n' "$1"
      printf '| task_fresh    | implementing | branch_fresh  | main         | -   | -    | |\n'
    } >"$SR_RUN/.auto-pilot/RUN.md"
  }
  _sr_write_run_md claimed
  # A git repo of its own (status-report reads/writes .auto-pilot/ and computes
  # `_run_head` from THIS dir, distinct from SR_REPO which supplies the task
  # branches for elapsed-time lookups).
  git init -q "$SR_RUN"
  git -C "$SR_RUN" config user.email test@example.com
  git -C "$SR_RUN" config user.name Test
  git -C "$SR_RUN" checkout -q -b auto-pilot/test-run
  git -C "$SR_RUN" add .auto-pilot/RUN.md
  git -C "$SR_RUN" commit -q -m "run state v1"

  # --- A: core rendering, direct call, --force (bypass the interval gate) ---
  srAout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 2>&1)"
  srAmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: renders the phase table" '| task_impl | implementing' "$srAmd"
  have "status-report: in-flight elapsed is reported" 'task_impl (implementing): elapsed' "$srAmd"
  have "status-report: OVER-ceiling task is flagged" 'task_over (implementing): elapsed' "$srAmd"
  have "status-report: OVER-ceiling task says OVER" 'OVER the per-task ceiling' "$srAmd"
  ! printf '%s\n' "$srAmd" | grep 'task_impl (implementing)' | grep -q 'OVER' \
    && ok "status-report: within-ceiling task is not marked OVER" \
    || bad "status-report: within-ceiling task is not marked OVER"
  # A freshly-claimed branch with NO commits beyond its base must degrade to
  # "elapsed unknown" — the old whole-history fallback selected the repo's
  # OLDEST commit and reported the task as running since repository genesis.
  have "status-report: a branch with no commits beyond base reads elapsed unknown, never repo genesis" \
    'task_fresh (implementing): elapsed unknown' "$srAmd"
  ! printf '%s\n' "$srAmd" | grep 'task_fresh (implementing)' | grep -q 'OVER' \
    && ok "status-report: the fresh branch is never marked OVER off the repo's first commit" \
    || bad "status-report: the fresh branch is never marked OVER off the repo's first commit"
  have "status-report: embeds status --label's own output" 'Live state (from `status' "$srAmd"
  have "status-report: heartbeat line present (from status)" 'heartbeat:' "$srAmd"
  have "status-report: PR state + mergeable for task_handed" '| task_handed | #201 | MERGED | UNKNOWN |' "$srAmd"
  have "status-report: rate window rendered from --usage-bin" 'session 42% consumed' "$srAmd"
  have "status-report: until remaining vs min_task_budget rendered" 'min_task_budget 20m' "$srAmd"
  have "status-report: until remaining is OK (plenty of runway)" 'OK, at least one more task likely fits' "$srAmd"
  have "status-report: first report says so (no prior state)" 'first report for this run' "$srAmd"

  # --- interval gate: a call within --report-every is a silent no-op ---------
  # (no --force). A long interval + an immediate re-call must NOT rewrite
  # STATUS.md — the whole point of "on by default, every 15m" is that most
  # wakes are cheap no-ops, not a fresh render each time.
  #
  # Assert on EXISTENCE, not on mtime/content: every timestamp available here
  # (stat mtime, last_emitted_at) is second-resolution, and these two calls are
  # sub-second apart — so an mtime comparison passes even with the gate deleted
  # outright (`if false; then`), which is exactly what the earlier version of
  # this test did. Deleting the file first makes the assertion capable of
  # failing: a gate that does not hold recreates it.
  rm -f "$SR_RUN/.auto-pilot/STATUS.md"
  "$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 --report-every 3600 >/dev/null 2>&1
  [ ! -e "$SR_RUN/.auto-pilot/STATUS.md" ] \
    && ok "status-report: a call inside --report-every (no --force) does not rewrite STATUS.md" \
    || bad "status-report: the interval gate did not hold — STATUS.md was rewritten early" "STATUS.md was recreated inside the interval"

  # --- B: the delta — no forward progress vs a phase advance -----------------
  srBout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 2>&1)"
  srBmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: no-forward-progress fires when the run-state HEAD hasn't moved" \
    'no forward progress in' "$srBmd"
  have "status-report: the no-forward-progress line names the in-flight task + ceiling" \
    'task_impl (implementing): elapsed' "$(printf '%s\n' "$srBmd" | grep 'no forward progress')"

  # Now advance task_claimed's phase and commit — the run-state HEAD moves.
  _sr_write_run_md implementing
  git -C "$SR_RUN" add .auto-pilot/RUN.md
  git -C "$SR_RUN" commit -q -m "task_claimed -> implementing"
  srCout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 2>&1)"
  srCmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: a phase advance is named in the delta" \
    'changed: task_claimed claimed -> implementing' "$srCmd"
  lack "status-report: a phase advance does NOT also claim no-forward-progress" \
    'no forward progress' "$srCmd"

  # --- B2: task-branch commits ARE forward progress (no false stall) ---------
  # RUN.md is committed only when /deliver-task returns; `implementing` means
  # local task-branch commits with the run-state HEAD parked. A task actively
  # producing commits must NOT be reported as stalled — compare the persisted
  # branch tips, not just the run-state HEAD.
  git -C "$SR_REPO" checkout -q branch_impl
  echo more >>"$SR_REPO/impl.txt"
  git -C "$SR_REPO" commit -q -am "impl keeps working"
  git -C "$SR_REPO" checkout -q main
  "$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 >/dev/null 2>&1
  srDmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: run-state HEAD parked + task-branch commits is NOT a stall" \
    'no forward progress' "$srDmd"
  have "status-report: the branch progress is named (which task advanced)" \
    'task branch(es) advanced — new commits on: task_impl' "$srDmd"
  # ...and a genuinely idle interval right after IS still a stall (the tip
  # comparison must not have destroyed the true-positive).
  "$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.a --force \
    --repo "$SR_REPO" --gh "$SR_GH" --usage-bin "$SR_USAGE" --task-ceiling 120 >/dev/null 2>&1
  srEmd="$(cat "$SR_RUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: a genuinely idle interval still reads no-forward-progress" \
    'no forward progress' "$srEmd"

  # --- D: stale RUN.md cannot read as healthy (finding #23's shape) ----------
  have "status-report: flags RUN.md lagging reality (claimed phase, live PR already open)" \
    'DIVERGENCE task_claimed' "$srAmd"
  have "status-report: divergence names the live PR" 'PR #203' "$srAmd"

  # --- E: reuses restack's OWN orphan detector, not a second reconciler ------
  have "status-report: flags the orphaned chained PR (base ref deleted, finding #25 LOUD case)" \
    'DEFECT task_child' "$srAmd"

  # --- E2: a FAILING gh must read DEGRADED, never clean ----------------------
  # Expired auth / rate limit / network: `gh` exits non-zero with empty
  # stdout. With no chained PRs the orphan scan finds nothing and the #23
  # lookup comes back empty — the old code then rendered "clean", blessing a
  # stale RUN.md it never actually cross-checked.
  SR_FAILGH="$SR/gh-failing"
  printf '#!/bin/sh\necho "HTTP 401: Bad credentials (https://api.github.com/graphql)" >&2\nexit 1\n' >"$SR_FAILGH"
  chmod +x "$SR_FAILGH"
  SR_BRUN="$SR/degraded-run"
  mkdir -p "$SR_BRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_claimed | claimed | branch_claimed | main | - | - | |\n'
  } >"$SR_BRUN/.auto-pilot/RUN.md"
  git init -q "$SR_BRUN"
  git -C "$SR_BRUN" config user.email t@e
  git -C "$SR_BRUN" config user.name T
  git -C "$SR_BRUN" checkout -q -b auto-pilot/degraded-run
  git -C "$SR_BRUN" add .auto-pilot/RUN.md
  git -C "$SR_BRUN" commit -q -m "run state"
  srXout="$("$SCRIPT" status-report --dir "$SR_BRUN" --label com.autopilot.sr.deg --force \
    --repo "$SR_REPO" --gh "$SR_FAILGH" --task-ceiling 120 2>&1)"
  srXmd="$(cat "$SR_BRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a failing gh never renders the reality check as clean" \
    'clean: no divergence' "$srXmd"
  have "status-report: a failing gh renders the reality check as DEGRADED" \
    'DEGRADED:' "$srXmd"
  have "status-report: the DEGRADED result reaches the one-line digest too" \
    'reality=DEGRADED' "$srXout"
  # ...and a transient failure against a CHAINED PR is also degraded — never a
  # false DEFECT (the restack scan's UNDETERMINED path, seen from the report).
  SR_CRUN="$SR/degraded-chained"
  mkdir -p "$SR_CRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_chained | pr-open | branch_child | branch_parent | - | #299 | |\n'
  } >"$SR_CRUN/.auto-pilot/RUN.md"
  git init -q "$SR_CRUN"
  git -C "$SR_CRUN" config user.email t@e
  git -C "$SR_CRUN" config user.name T
  git -C "$SR_CRUN" checkout -q -b auto-pilot/degraded-chained
  git -C "$SR_CRUN" add .auto-pilot/RUN.md
  git -C "$SR_CRUN" commit -q -m "run state"
  "$SCRIPT" status-report --dir "$SR_CRUN" --label com.autopilot.sr.degc --force \
    --repo "$SR_REPO" --gh "$SR_FAILGH" --task-ceiling 120 >/dev/null 2>&1
  srYmd="$(cat "$SR_CRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a transient gh failure on a chained PR is never a DEFECT" \
    'DEFECT' "$srYmd"
  have "status-report: the chained transient failure reads DEGRADED instead" \
    'DEGRADED:' "$srYmd"

  # --- E2c: the ORDINARY shape must degrade too — the gap E2 above missed ----
  # Both fixtures above happen to make a gh call the report already counts: a
  # `claimed` task hits the #23 divergence lookup, a CHAINED task hits the
  # orphan scan. Neither covers the everyday run: tasks in `pr-open` based
  # DIRECTLY on base_branch. There the orphan scan skips every task (unchained)
  # and the divergence loop skips every task (wrong phase), so the ONLY gh calls
  # are the PR table's state/mergeable queries — and while those discarded their
  # exit status, a totally dead gh made zero TRACKED calls: the table rendered
  # `unknown/unknown` and the reality check still asserted "clean". Fail-open,
  # in the single section this whole feature exists to make trustworthy.
  SR_ORUN="$SR/degraded-ordinary"
  mkdir -p "$SR_ORUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_open | pr-open | branch_handed | main | - | #201 | |\n'
  } >"$SR_ORUN/.auto-pilot/RUN.md"
  git init -q "$SR_ORUN"
  git -C "$SR_ORUN" config user.email t@e
  git -C "$SR_ORUN" config user.name T
  git -C "$SR_ORUN" checkout -q -b auto-pilot/degraded-ordinary
  git -C "$SR_ORUN" add .auto-pilot/RUN.md
  git -C "$SR_ORUN" commit -q -m "run state"
  srZout="$("$SCRIPT" status-report --dir "$SR_ORUN" --label com.autopilot.sr.deg0 --force \
    --repo "$SR_REPO" --gh "$SR_FAILGH" --task-ceiling 120 2>&1)"
  srZmd="$(cat "$SR_ORUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a dead gh on an ordinary pr-open run never reads clean" \
    'clean: no divergence' "$srZmd"
  have "status-report: a dead gh on an ordinary pr-open run reads DEGRADED" \
    'DEGRADED:' "$srZmd"
  have "status-report: the ordinary-shape degrade reaches the digest" \
    'reality=DEGRADED' "$srZout"

  # --- E2d: the Open-PRs table prints its header ONCE, not once per row ------
  # printf recycles its whole format string per argument, so folding the header
  # into the row format re-printed it before EVERY row. The fixture has two PRs
  # (#201, #202), so the table was mangled in the artifact a human reads while
  # the suite's single-row substring assert sailed past it.
  srA_hdrs="$(printf '%s\n' "$srAmd" | grep -c '^| task | pr | state | mergeable |$' || true)"
  [ "$srA_hdrs" = 1 ] \
    && ok "status-report: the Open-PRs table prints its header exactly once (2 PRs in the fixture)" \
    || bad "status-report: the Open-PRs table header is repeated per row (printf format recycling)" \
      "header printed $srA_hdrs times, expected 1"

  # --- E2e: the stall duration ACCUMULATES; it is not the report interval ----
  # "no forward progress in X" measured X from last_emitted_at — rewritten on
  # every emit — so a run wedged for six hours read "no forward progress in
  # 15m" at every single tick. The number that separates slow from wedged could
  # never say so. It now measures from last_progress_at, carried forward across
  # reports that see nothing move.
  SR_SRUN="$SR/stall-run"
  mkdir -p "$SR_SRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_stuck | implementing | branch_over | main | - | - | |\n'
  } >"$SR_SRUN/.auto-pilot/RUN.md"
  git init -q "$SR_SRUN"
  git -C "$SR_SRUN" config user.email t@e
  git -C "$SR_SRUN" config user.name T
  git -C "$SR_SRUN" checkout -q -b auto-pilot/stall-run
  git -C "$SR_SRUN" add .auto-pilot/RUN.md
  git -C "$SR_SRUN" commit -q -m "run state"
  SR_SSTATE="$SR_SRUN/.auto-pilot/status-report-state"
  # First report seeds the state; nothing has moved since, so the next one is a
  # genuine stall.
  "$SCRIPT" status-report --dir "$SR_SRUN" --label com.autopilot.sr.stall --force \
    --repo "$SR_REPO" --task-ceiling 120 >/dev/null 2>&1
  # Backdate the moment the run last MOVED to 2h ago, leaving last_emitted_at
  # recent — exactly the state an overnight wedge produces after many reports.
  SR_STALL_SINCE=$(($(date +%s) - 7200))
  sed -e "s/^last_progress_at: .*/last_progress_at: $SR_STALL_SINCE/" "$SR_SSTATE" >"$SR_SSTATE.tmp"
  mv "$SR_SSTATE.tmp" "$SR_SSTATE"
  "$SCRIPT" status-report --dir "$SR_SRUN" --label com.autopilot.sr.stall --force \
    --repo "$SR_REPO" --task-ceiling 120 >/dev/null 2>&1
  srSmd="$(cat "$SR_SRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  have "status-report: a 2h stall reports 2h, not the report interval" \
    'no forward progress in 2h' "$srSmd"
  # ...and the clock is NOT reset by the act of reporting: the carried-forward
  # last_progress_at must survive, or the stall restarts from zero every emit.
  grep -q "^last_progress_at: $SR_STALL_SINCE$" "$SR_SSTATE" \
    && ok "status-report: emitting a report does not reset the stall clock" \
    || bad "status-report: the stall clock was reset by the emission" \
      "expected last_progress_at: $SR_STALL_SINCE, got $(sed -n 's/^last_progress_at: //p' "$SR_SSTATE")"

  # --- E3: duration parsing accepts digits only, never arithmetic ------------
  # `--report-every '1+1m'` used to reach bash arithmetic and be ACCEPTED —
  # the contract is off | integer seconds | <n>s|m|h, nothing else.
  #
  # `0` (and `0s`/`0m`) is in this list for a different reason than the
  # arithmetic cases: it PARSES fine, it just isn't a duration. It bakes
  # `sleep 0` into the generated wrapper's in-wake reporter — a busy-loop that
  # spins a CPU and re-queries GitHub continuously for the whole model call,
  # since the interval gate can never close against 0 either. `off` is how you
  # say "never"; there is no way to say "always".
  for badv in '1+1m' '+5s' '2 2h' '0' '0s' '0m'; do
    bdout="$("$SCRIPT" status-report --dir "$SR_RUN" --label com.autopilot.sr.bad \
      --report-every "$badv" 2>&1)"
    bdc=$?
    [ "$bdc" = 2 ] && printf '%s' "$bdout" | grep -qF 'must be off' \
      && ok "status-report: --report-every '$badv' is rejected (digits only, no arithmetic)" \
      || bad "status-report: --report-every '$badv' is rejected (digits only, no arithmetic)" "exit=$bdc $bdout"
  done

  # --- E4: a QUOTED front-matter `until:` still parses ------------------------
  # The other front-matter readers strip wrapping quotes; the report's reader
  # must too, or `until: "2026-…"` renders as "unparseable until".
  SR_QRUN="$SR/quoted-run"
  mkdir -p "$SR_QRUN/.auto-pilot"
  {
    printf -- '---\n'
    printf 'base_branch: main\n'
    printf 'until: "%s"\n' "$(_sr_iso 7200)"
    printf "min_task_budget: '20m'\n"
    printf -- '---\n\n'
    printf '| task | phase | branch | base | base_sha | pr | notes |\n'
    printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
    printf '| task_pending | pending | - | main | - | - | |\n'
  } >"$SR_QRUN/.auto-pilot/RUN.md"
  git init -q "$SR_QRUN"
  git -C "$SR_QRUN" config user.email t@e
  git -C "$SR_QRUN" config user.name T
  git -C "$SR_QRUN" checkout -q -b auto-pilot/quoted-run
  git -C "$SR_QRUN" add .auto-pilot/RUN.md
  git -C "$SR_QRUN" commit -q -m "run state"
  "$SCRIPT" status-report --dir "$SR_QRUN" --label com.autopilot.sr.q --force \
    --repo "$SR_REPO" --task-ceiling 120 >/dev/null 2>&1
  srQmd="$(cat "$SR_QRUN/.auto-pilot/STATUS.md" 2>/dev/null)"
  lack "status-report: a quoted until: does not read as unparseable" \
    'unparseable until' "$srQmd"
  # The budget note only renders when until_epoch actually PARSED — so this is
  # the effect assertion that the quotes were stripped (2h runway vs 20m).
  have "status-report: a quoted until:+min_task_budget still yields the runway verdict" \
    'OK, at least one more task likely fits' "$srQmd"

  # --- E5: writes are atomic IN the destination dir ---------------------------
  # Temp files must be created in .auto-pilot/ and renamed (the
  # _write_done_sentinel pattern) — a TMPDIR temp + cross-filesystem `mv` is a
  # copy+delete a watcher can observe half-written. Effect asserted: with an
  # UNUSABLE TMPDIR the report must still succeed, because nothing on its
  # write path may depend on TMPDIR at all.
  SR_ARUN="$SR/atomic-run"
  rm -rf "$SR_ARUN"
  cp -R "$SR_BRUN" "$SR_ARUN"
  rm -f "$SR_ARUN/.auto-pilot/status-report-state" "$SR_ARUN/.auto-pilot/STATUS.md"
  atout="$(TMPDIR="$SR/definitely-nonexistent-tmp" "$SCRIPT" status-report --dir "$SR_ARUN" \
    --label com.autopilot.sr.atomic --force --repo "$SR_REPO" --task-ceiling 120 2>&1)"
  atc=$?
  [ "$atc" = 0 ] && [ -f "$SR_ARUN/.auto-pilot/STATUS.md" ] \
    && ok "status-report: succeeds with an unusable TMPDIR (temp files live in .auto-pilot/, renamed in place)" \
    || bad "status-report: succeeds with an unusable TMPDIR (temp files live in .auto-pilot/, renamed in place)" "exit=$atc $atout"
  leftover="$(find "$SR_ARUN/.auto-pilot" -name '.status-report*' 2>/dev/null)"
  [ -z "$leftover" ] && ok "status-report: no temp-file droppings left beside the report" \
    || bad "status-report: no temp-file droppings left beside the report" "$leftover"

  # --- F: no live gh/usage-bin call when the caller doesn't opt in -----------
  SR_LEAK="$SR/leak-bin"
  mkdir -p "$SR_LEAK"
  SR_GH_CALLED="$SR/gh-leak-called"
  printf '#!/bin/sh\n: >"%s"\nexit 1\n' "$SR_GH_CALLED" >"$SR_LEAK/gh"
  chmod +x "$SR_LEAK/gh"
  SR_USAGE_CALLED="$SR/usage-leak-called"
  printf '#!/bin/sh\n: >"%s"\nexit 1\n' "$SR_USAGE_CALLED" >"$SR_LEAK/claude-usage.sh"
  chmod +x "$SR_LEAK/claude-usage.sh"
  rm -f "$SR_GH_CALLED" "$SR_USAGE_CALLED"
  PATH="$SR_LEAK:$PATH" "$SCRIPT" supervisor-scan --dir "$SR_RUN" --label com.autopilot.sr.f \
    --report-every off >/dev/null 2>&1
  [ ! -f "$SR_GH_CALLED" ] && ok "status-report: no --gh means NO gh call is made, even if one resolves on PATH" \
    || bad "status-report: a real/PATH-resolved gh was invoked despite no --gh being passed"
  # (--report-every off above also proves the disabled path never touches gh/usage
  # at all; a second case with reporting ON but --gh/--usage-bin both omitted:)
  PATH="$SR_LEAK:$PATH" "$SCRIPT" supervisor-scan --dir "$SR_RUN" --label com.autopilot.sr.f2 \
    --report-every 1 >/dev/null 2>&1
  [ ! -f "$SR_GH_CALLED" ] && ok "status-report: reporting ON but --gh omitted still makes no gh call" \
    || bad "status-report: reporting ON but --gh omitted still called a PATH-resolved gh"
  [ ! -f "$SR_USAGE_CALLED" ] && ok "status-report: --usage-bin omitted never calls a PATH-resolved claude-usage.sh" \
    || bad "status-report: --usage-bin omitted called a PATH-resolved claude-usage.sh"

  # --- G: a status-report failure can't take supervisor-scan down with it ----
  # (die/exit containment — see the "task 26" convention comment). A garbage
  # --report-every makes status_report `die`; supervisor-scan must still exit 0.
  "$SCRIPT" supervisor-scan --dir "$SR_RUN" --label com.autopilot.sr.g --report-every 'garbage' >/dev/null 2>&1
  [ $? = 0 ] && ok "status-report: a die inside status_report does not propagate out of supervisor-scan" \
    || bad "status-report: supervisor-scan's exit code leaked status_report's die"

  # --- G2: a HUNG gh must not wedge the wake ---------------------------------
  # The subshell above contains a `die` (an EXIT). It does NOT contain a HANG.
  # status_report is the ONLY thing on the supervisor's per-wake path that makes
  # network calls, and `launch` auto-resolves a real `gh`, so production wakes do
  # reach the network. launchd will not start the next StartInterval wake while
  # this one is still running — so ONE hung `gh` (blackholed TCP, captive portal,
  # an auth prompt) wedges the supervisor PERMANENTLY: no agent, and no further
  # alarm scans or pause-exempt-ledger checks either. Finding #22's silent
  # zero-work loop, reached through the OBSERVABILITY feature. Bounded by a
  # hand-rolled watchdog (macOS ships no coreutils `timeout`).
  SR_HANG="$SR/hangbin"
  mkdir -p "$SR_HANG"
  printf '#!/bin/sh\nsleep 120\n' >"$SR_HANG/gh"
  chmod +x "$SR_HANG/gh"
  # A FRESH run dir, with no prior status-report-state: reusing $SR_RUN let the
  # interval gate SKIP the report entirely, so the hang never happened and the
  # timing assertion passed in 0s while proving nothing. (Caught only because the
  # "announced" assertion below went red — an elapsed-time bound is satisfied just
  # as well by never running the thing.)
  SR_HRUN="$SR/hangrun"
  rm -rf "$SR_HRUN"
  cp -R "$SR_RUN" "$SR_HRUN"
  rm -f "$SR_HRUN/.auto-pilot/status-report-state" "$SR_HRUN/.auto-pilot/STATUS.md" 2>/dev/null
  srh_start="$(date +%s)"
  PATH="$GUARD:$PATH" SPAWN_REPORT_TIMEOUT=2 "$SCRIPT" supervisor-scan --dir "$SR_HRUN" \
    --label com.autopilot.sr.hang --report-every 1 --gh "$SR_HANG/gh" >/dev/null 2>"$SR/hang.err"
  srh_rc=$?
  srh_el=$(($(date +%s) - srh_start))
  [ "$srh_rc" = 0 ] && ok "status-report [hung gh]: supervisor-scan still exits 0 (the wake completes)" \
    || bad "status-report [hung gh]: supervisor-scan did not complete" "rc=$srh_rc"
  [ "$srh_el" -lt 30 ] \
    && ok "status-report [hung gh]: the wake is BOUNDED (${srh_el}s), not wedged until the hang ends" \
    || bad "status-report [hung gh]: the wake WEDGED — a hung gh stops every future alarm scan and ledger check" "elapsed=${srh_el}s"
  have "status-report [hung gh]: the kill is announced, not silent" \
    'status-report exceeded' "$(cat "$SR/hang.err" 2>/dev/null)"
  # and the hung gh must not survive as an orphan, accumulating one per interval
  sleep 1
  if pgrep -f "$SR_HANG/gh" >/dev/null 2>&1; then
    bad "status-report [hung gh]: the killed report left an ORPHANED gh running" "pgrep matched"
  else
    ok "status-report [hung gh]: the whole process group is reaped — no orphaned gh"
  fi

  # --- C: NO MODEL CALL, and still emitted on a GATE-CLOSED wake --------------
  # Driven through the REAL generated wrapper (write-launch), never a
  # reimplementation of its call sequence — same discipline as the gate tests
  # above. paused_until is an hour in the future, so the gate closes and
  # `claude` must never run; the report must still be written.
  SRW="$SR/wrapper"
  mkdir -p "$SRW/.auto-pilot" "$SRW/bin"
  cp "$SR_RUN/.auto-pilot/RUN.md" "$SRW/.auto-pilot/RUN.md"
  # RUN.md needs its own paused_until for the gate; append it to the front matter.
  SRW_FUTURE="$(_sr_iso 3600)"
  awk -v p="$SRW_FUTURE" '
    /^---$/ { c++; if (c==2 && !done) { print "paused_until: " p; done=1 } }
    { print }
  ' "$SRW/.auto-pilot/RUN.md" >"$SRW/.auto-pilot/RUN.md.tmp" && mv "$SRW/.auto-pilot/RUN.md.tmp" "$SRW/.auto-pilot/RUN.md"
  SRW_CLAUDE="$SRW/bin/claude-stub"
  printf '#!/bin/sh\n: >"%s/claude-called"\nexit 0\n' "$SRW" >"$SRW_CLAUDE"
  chmod +x "$SRW_CLAUDE"
  SRW_SANDBOX="$SRW/bin/sandbox-exec"
  printf '#!/bin/sh\n: >"%s/sandbox-exec-called"\nshift 2\nexec "$@"\n' "$SRW" >"$SRW_SANDBOX"
  chmod +x "$SRW_SANDBOX"
  SRW_PATH="$SRW/bin:$GUARD:/usr/bin:/bin:/usr/sbin:/sbin"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$SRW" \
    --log "$SRW/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.sr.wrap \
    --claude-bin "$SRW_CLAUDE" --path "$SRW_PATH" --report-every 1 --report-gh "$SR_GH" \
    --out-script "$SRW/launch.sh" --out-plist "$SRW/job.plist" >/dev/null 2>&1
  "$SRW/launch.sh" >/dev/null 2>&1
  [ ! -f "$SRW/claude-called" ] && ok "status-report: gate-closed wake never invokes claude (no model call)" \
    || bad "status-report: gate-closed wake invoked claude"
  [ -f "$SRW/.auto-pilot/STATUS.md" ] && ok "status-report: STILL emitted on a gate-closed wake" \
    || bad "status-report: STATUS.md missing after a gate-closed wake"
  have "status-report: the digest reaches the wake's log too" 'status-report:' "$(cat "$SRW/o.log" 2>/dev/null)"

  # --- H: reports keep firing WHILE claude runs (review [A]) ------------------
  # launchd does NOT fire StartInterval while an instance is still running
  # (verified empirically: StartInterval=2s + a 12s job → starts only every
  # ~14s, no concurrency, no queued firings). So the wake-start emission alone
  # goes silent for the whole duration of a model call — a wedged claude means
  # ZERO reports, on exactly the runs the report exists to monitor. The
  # generated wrapper must therefore run an in-wake background reporter while
  # claude runs, and reap it when claude exits. Driven through the REAL
  # generated wrapper, gate OPEN, with a claude that takes 3s and a 1s
  # interval: more than one report digest must land in the log.
  SRL="$SR/loop-wrapper"
  mkdir -p "$SRL/.auto-pilot" "$SRL/bin"
  cp "$SR_RUN/.auto-pilot/RUN.md" "$SRL/.auto-pilot/RUN.md" # no paused_until: gate OPEN
  SRL_CLAUDE="$SRL/bin/claude-slow"
  printf '#!/bin/sh\n: >"%s/claude-called"\nsleep 3\nexit 0\n' "$SRL" >"$SRL_CLAUDE"
  chmod +x "$SRL_CLAUDE"
  printf '#!/bin/sh\nshift 2\nexec "$@"\n' >"$SRL/bin/sandbox-exec"
  chmod +x "$SRL/bin/sandbox-exec"
  SRL_PATH="$SRL/bin:$GUARD:/usr/bin:/bin:/usr/sbin:/sbin"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$SRL" \
    --log "$SRL/o.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.sr.loop \
    --claude-bin "$SRL_CLAUDE" --path "$SRL_PATH" --report-every 1 \
    --out-script "$SRL/launch.sh" --out-plist "$SRL/job.plist" >/dev/null 2>&1
  # The seam, pinned by position: the reporter starts BELOW the gate (a
  # gate-closed wake exits in seconds; launchd's own cadence covers it) and
  # BEFORE the claude invocation; the reap follows the claude invocation.
  srl_gate_ln="$(grep -n 'supervisor-gate' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  srl_loop_ln="$(grep -n 'report-tick' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  srl_claude_ln="$(grep -n 'sandbox-exec -f' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  srl_kill_ln="$(grep -n 'kill -TERM -"$rpt"' "$SRL/launch.sh" | head -1 | cut -d: -f1)"
  if [ -n "$srl_gate_ln" ] && [ -n "$srl_loop_ln" ] && [ -n "$srl_claude_ln" ] && [ -n "$srl_kill_ln" ] \
    && [ "$srl_gate_ln" -lt "$srl_loop_ln" ] && [ "$srl_loop_ln" -lt "$srl_claude_ln" ] \
    && [ "$srl_claude_ln" -lt "$srl_kill_ln" ]; then
    ok "status-report [in-wake]: reporter sits below the gate, brackets the claude invocation, reap follows it"
  else
    bad "status-report [in-wake]: reporter sits below the gate, brackets the claude invocation, reap follows it" \
      "gate@$srl_gate_ln loop@$srl_loop_ln claude@$srl_claude_ln kill@$srl_kill_ln"
  fi
  "$SRL/launch.sh" >/dev/null 2>&1
  [ -f "$SRL/claude-called" ] && ok "status-report [in-wake]: gate was OPEN — claude really ran (3s)" \
    || bad "status-report [in-wake]: gate was OPEN — claude really ran (3s)"
  srl_digests="$(grep -c 'status-report: tasks=' "$SRL/o.log" 2>/dev/null | tr -d ' ')"
  case "$srl_digests" in '' | *[!0-9]*) srl_digests=0 ;; esac
  if [ "$srl_digests" -ge 2 ]; then
    ok "status-report [in-wake]: reports kept firing DURING the model call ($srl_digests digests, wake-start alone would be 1)"
  else
    bad "status-report [in-wake]: reports kept firing DURING the model call" "digests=$srl_digests (only the wake-start emission fired — the [A] cadence hole)"
  fi
  # Poll to a deadline rather than sleeping a fixed second. The reap is a
  # process-GROUP TERM followed by a wait (`kill -TERM -"$rpt"; wait "$rpt"` in
  # spawn-orchestrator.sh), so a report-tick caught mid gh/status-report call is
  # still draining when write-launch returns. A flat `sleep 1` races that drain
  # and fails on a loaded machine for a reason that has nothing to do with the
  # property under test — observed flapping pass/fail across runs with no code
  # change. The claim is "eventually reaped", so bound the wait instead: still
  # matching after 10s is a real leaked reaper, which is what this should catch.
  # Note the match is argv-based, and every fork of the report-tick process
  # inherits that argv — so this also covers _run_bounded's job and watchdog and
  # anything its TERM handler does. The 10s is a budget for that teardown as
  # well as for the reap; spend against it knowingly.
  srl_reaped=0
  srl_tries=0
  while [ "$srl_tries" -lt 50 ]; do
    if ! pgrep -f "report-tick --dir $SRL" >/dev/null 2>&1; then
      srl_reaped=1
      break
    fi
    sleep 0.2
    srl_tries=$((srl_tries + 1))
  done
  if [ "$srl_reaped" = 1 ]; then
    ok "status-report [in-wake]: the reporter loop is reaped when claude exits"
  else
    bad "status-report [in-wake]: the reporter loop is reaped when claude exits" "pgrep still matched after 10s"
  fi
  # --report-every off must emit NO reporter loop at all.
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$SRL" \
    --log "$SRL/off.log" --prompt-file "$BASE/prompt.txt" --label com.autopilot.sr.loopoff \
    --claude-bin "$SRL_CLAUDE" --path "$SRL_PATH" --report-every off \
    --out-script "$SRL/launch-off.sh" --out-plist "$SRL/job-off.plist" >/dev/null 2>&1
  lack "status-report [in-wake]: --report-every off emits no reporter loop" \
    'report-tick' "$(cat "$SRL/launch-off.sh" 2>/dev/null)"
else
  echo "skip - status-report suite (no git available)"
fi

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
