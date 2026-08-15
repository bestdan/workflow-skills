#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034 # Fixtures intentionally contain shell source and retain outputs for diagnostics.
# Run-time brokers and supervisor primitives: verify, status, teardown,
# assert-run-head, classify-exit, supervisor-check and the pre-invoke pause gate.
#
# One of the scripts/test-spawn-orchestrator/*.sh suites; see _prelude.sh for
# what they share and dev_docs/gate-performance.md for why they are separate
# files. Runnable on its own: bash scripts/test-spawn-orchestrator/runtime.sh
# shellcheck source=scripts/test-spawn-orchestrator/_prelude.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_prelude.sh"
shared_launch_inputs

# --- task 4: verify broker (run verify OUTSIDE the jail) ----------------------
# The broker just runs `bash -c "$cmd"`, so these round-trip in-jail with no
# sandbox nesting — the pin/containment/fail-closed logic is fully exercised here.
if command -v shasum >/dev/null 2>&1; then
  VB="$BASE/vb"
  mkdir -p "$VB/root/wt" "$VB/outside"
  SENT="$VB/sentinel"
  ROOT="$(cd "$VB/root" && pwd -P)"
  WT="$(cd "$VB/root/wt" && pwd -P)"
  CMD='echo VERIFY_RAN; exit 0'
  PIN="$(printf '%s' "$CMD" | shasum -a 256 | awk '{print $1}')"

  # round trip: request -> broker (one scan) -> await
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "$PIN" --id rt >/dev/null 2>&1
  have "verify-request: writes the request sentinel" "" "$([ -e "$SENT/rt.request" ] && echo present)"
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: consumes the request" "" "$([ ! -e "$SENT/rt.request" ] && echo consumed)"
  rtres="$(cat "$SENT/rt.result" 2>/dev/null)"
  have "verify-broker: result carries code 0" 'code: 0' "$rtres"
  have "verify-broker: ran the pinned command" 'VERIFY_RAN' "$rtres"
  # The result sentinel already exists, so await returns on its first poll; the
  # --timeout is a hang bound for the failure path, not an expected duration —
  # keep it generous so a descheduled run can't blow it (see 1ddb13e).
  awo="$("$SCRIPT" verify-await --sentinel-dir "$SENT" --id rt --timeout 30 2>&1)"
  have "verify-await: reports code + output" 'code=0' "$awo"
  have "verify-await: prints the verify output" 'VERIFY_RAN' "$awo"

  # a request whose cmd_hash != the broker's pinned hash is REFUSED, never run
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash deadbeef --id mism >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  mres="$(cat "$SENT/mism.result" 2>/dev/null)"
  have "verify-broker: hash mismatch refused" 'cmd_hash mismatch' "$mres"
  lack "verify-broker: refused req did not run" 'VERIFY_RAN' "$mres"

  # a worktree OUTSIDE the run root is REFUSED
  OUT="$(cd "$VB/outside" && pwd -P)"
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$OUT" --cmd-hash "$PIN" --id esc >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: worktree escape refused" 'escapes --confine-under' "$(cat "$SENT/esc.result" 2>/dev/null)"

  # the broker's own --cmd-hash must match --verify-cmd (install/args mismatch)
  bhm="$("$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD" --cmd-hash deadbeef --confine-under "$ROOT" 2>&1)"
  bhc=$?
  if [ "$bhc" = 2 ] && printf '%s' "$bhm" | grep -qF 'does not match'; then
    ok "verify-broker: pinned hash/cmd mismatch fails closed"
  else
    bad "verify-broker: pinned hash/cmd mismatch fails closed" "exit=$bhc"
  fi

  # the 126-vs-0 contrast the whole task exists for: a #!/usr/bin/env bash script
  # the broker runs via the pinned `bash <script>` (works) — the same script's
  # direct shebang exec is what execve-denies in-jail (finding #4).
  printf '#!/usr/bin/env bash\necho SHEBANG_OK\n' >"$WT/probe.sh"
  chmod +x "$WT/probe.sh"
  CMD2='bash probe.sh'
  PIN2="$(printf '%s' "$CMD2" | shasum -a 256 | awk '{print $1}')"
  "$SCRIPT" verify-request --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "$PIN2" --id sb >/dev/null 2>&1
  "$SCRIPT" verify-broker --sentinel-dir "$SENT" --verify-cmd "$CMD2" --confine-under "$ROOT" >/dev/null 2>&1
  have "verify-broker: runs a shebang script via pinned bash" 'SHEBANG_OK' "$(cat "$SENT/sb.result" 2>/dev/null)"

  # verify-request fail-closed: non-hex cmd-hash, missing worktree
  fcr() {
    local name="$1" want="$2"
    shift 2
    local o c
    o="$("$SCRIPT" verify-request "$@" 2>&1)"
    c=$?
    if [ "$c" = 2 ] && printf '%s' "$o" | grep -qF "$want"; then ok "verify-request fail-closed: $name"; else bad "verify-request fail-closed: $name" "exit=$c msg=$o"; fi
  }
  fcr "non-hex cmd-hash" "must be lowercase hex" --sentinel-dir "$SENT" --worktree "$WT" --cmd-hash "NOTHEX"
  fcr "missing worktree" "does not exist" --sentinel-dir "$SENT" --worktree "$VB/nope" --cmd-hash "$PIN"
  printf 'x\n' >"$VB/notadir"
  fcr "worktree is a file" "is not a directory" --sentinel-dir "$SENT" --worktree "$VB/notadir" --cmd-hash "$PIN"

  # write-verify-broker: renders an UN-JAILED launch script (no sandbox-exec) + a
  # valid plist, with the verify command pinned in.
  "$SCRIPT" write-verify-broker --sentinel-dir "$SENT" --verify-cmd 'bash scripts/check.sh' \
    --confine-under "$VB" --label com.autopilot.test.verify --workdir "$WT" --log "$VB/b.log" \
    --path '/usr/bin:/bin' --out-script "$VB/broker.sh" --out-plist "$VB/broker.plist" >/dev/null 2>&1
  vbody="$(cat "$VB/broker.sh" 2>/dev/null)"
  have "write-verify-broker: invokes verify-broker" 'verify-broker' "$vbody"
  have "write-verify-broker: pins the verify command" 'bash scripts/check.sh' "$vbody"
  have "write-verify-broker: pins a cmd-hash" '--cmd-hash' "$vbody"
  lack "write-verify-broker: broker is UN-JAILED (no sandbox-exec)" 'sandbox-exec' "$vbody"
  if command -v plutil >/dev/null 2>&1; then
    if plutil -lint "$VB/broker.plist" >/dev/null 2>&1; then ok "write-verify-broker: plist lints"; else bad "write-verify-broker: plist lints"; fi
  else
    echo "skip - write-verify-broker: plist lint (plutil absent)"
  fi
  # write-verify-broker fail-closed: bad label
  wvbc="$("$SCRIPT" write-verify-broker --sentinel-dir "$SENT" --verify-cmd 'x' --confine-under "$VB" \
    --label 'bad label' --workdir "$WT" --log "$VB/b.log" --path '/usr/bin:/bin' \
    --out-script "$VB/x.sh" --out-plist "$VB/x.plist" 2>&1)"
  wvc=$?
  if [ "$wvc" = 2 ] && printf '%s' "$wvbc" | grep -qF 'must be [A-Za-z0-9._-]'; then
    ok "write-verify-broker: bad label fails closed"
  else
    bad "write-verify-broker: bad label fails closed" "exit=$wvc"
  fi
else
  echo "skip - verify broker: shasum not available (needed for the command pin)"
fi

# --- status: read-only run inspection (task 8) ---------------------------------
RUNDIR="$BASE/run"
mkdir -p "$RUNDIR/.auto-pilot"
RUNMD="$RUNDIR/.auto-pilot/RUN.md"
{
  printf -- '---\n'
  printf 'run_id: test-run\n'
  printf 'status: active\n'
  printf 'orchestrator_pid: 999999\n'
  printf 'orchestrator_started_at: "Wed Jul  9 20:00:00 2026"\n'
  printf 'until: 2026-07-10T06:00:00\n'
  printf -- '---\n'
  printf '\n'
  printf '| task | phase        | branch | base | base_sha | pr  | notes |\n'
  printf '| ---- | ------------ | ------ | ---- | -------- | --- | ----- |\n'
  printf '| T-1  | handed-off   | b1     | main | -        | #1  | ok    |\n'
  printf '| T-2  | implementing | b2     | main | -        | -   | wip   |\n'
} >"$RUNMD"
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"working on T-2"}]}}\n' >"$RUNDIR/.auto-pilot/orchestrator.log"

sout="$("$SCRIPT" status --label com.autopilot.test --dir "$RUNDIR" 2>&1)"
sc=$?
[ "$sc" = 0 ] && ok "status: exits 0 on a well-formed run dir" || bad "status: exits 0 on a well-formed run dir" "$sout"
have "status: prints the phase table" 'implementing' "$sout"
have "status: prints a STATUS: line" 'STATUS: active' "$sout"
have "status: STATUS line has tasks=2" 'tasks=2' "$sout"
have "status: STATUS line has until" 'until=2026-07-10T06:00:00' "$sout"

# a bogus/dead recorded PID (999999 — never a real live process) reports not-live
have "status: dead pid reports pid=dead" 'pid=dead' "$sout"

# a live pid (this test process's own $$) with a WRONG recorded start-time must
# still report not-live: kill -0 succeeds, but the start-time can't match a
# fabricated value (and ps is unavailable in some jails, which also falls to
# the not-live branch) — either way this must never read "pid=live".
#
# Whether that not-live report is `mismatch` (a real start-time was read and it
# differed) or `unknown` (the start-time couldn't be read at all, e.g. `ps
# -o lstart=` denied under a sandbox) depends on whether ps actually works
# here. Probe it once: without a real start time to compare against, asserting
# `pid=mismatch` specifically would pass for the wrong reason — it never
# established that a comparison happened, only that the pid wasn't reported
# live. That's exactly the false-green this suite exists to catch elsewhere.
PS_OK=0
[ -n "$(ps -o lstart= -p $$ 2>/dev/null)" ] && PS_OK=1
RUNMD2="$BASE/run2/.auto-pilot"
mkdir -p "$RUNMD2"
{
  printf -- '---\n'
  printf 'status: active\n'
  printf 'orchestrator_pid: %s\n' "$$"
  printf 'orchestrator_started_at: "not-a-real-timestamp"\n'
  printf 'until: 2026-07-10T06:00:00\n'
  printf -- '---\n'
  printf '| task | phase | branch | base | base_sha | pr | notes |\n'
  printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
  printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
} >"$RUNMD2/RUN.md"
sout2="$("$SCRIPT" status --label com.autopilot.test2 --dir "$BASE/run2" 2>&1)"
lack "status: mismatched start-time never reports live" 'pid=live' "$sout2"
if [ "$PS_OK" = 1 ]; then
  have "status: mismatched start-time reports mismatch" 'pid=mismatch' "$sout2"
else
  echo "skip - status: mismatched start-time reports mismatch (ps -o lstart= unreadable here, so a start-time MISMATCH can't be distinguished from an unreadable probe)"
fi

# front-matter parser: a double-quoted `until` with a trailing comment, and a
# `paused_until` line that precedes `until`, must yield the BARE until value
# (no quotes, no comment, not the paused_until value — anchored on `^until:`).
RUNMD3="$BASE/run3/.auto-pilot"
mkdir -p "$RUNMD3"
{
  printf -- '---\n'
  printf 'status: paused\n'
  printf 'paused_until: 2020-01-01T00:00:00\n'
  printf 'until: "2026-12-31T00:00:00"   # hard deadline\n'
  printf -- '---\n'
  printf '| task | phase | branch | base | base_sha | pr | notes |\n'
  printf '| ---- | ----- | ------ | ---- | -------- | -- | ----- |\n'
  printf '| T-1  | claimed | b1   | main | -        | -  | -     |\n'
} >"$RUNMD3/RUN.md"
sout4="$("$SCRIPT" status --label com.autopilot.test3 --dir "$BASE/run3" 2>&1)"
have "status: quoted+commented until parsed bare" 'until=2026-12-31T00:00:00' "$sout4"
lack "status: until does not match paused_until" 'until=2020-01-01T00:00:00' "$sout4"
lack "status: until value keeps no quotes" 'until="2026' "$sout4"

# fail-closed: missing --label / missing RUN.md
o="$("$SCRIPT" status --dir "$RUNDIR" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --label' \
  && ok "status fail-closed: missing --label" || bad "status fail-closed: missing --label" "$o"
o="$("$SCRIPT" status --label x --dir "$BASE/no-such-dir" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'no run state found' \
  && ok "status fail-closed: missing RUN.md" || bad "status fail-closed: missing RUN.md" "$o"

# --- teardown --done-sentinel: the single completion mechanism (task 8) --------
sentinel="$RUNDIR/.auto-pilot/orchestrator.done"
rm -f "$sentinel"
# launchctl bootout will fail/no-op off-macOS or in-jail — that must not stop the
# sentinel write, so don't assert on teardown's own exit code here.
"$SCRIPT" teardown --label com.autopilot.test --done-sentinel "$sentinel" >/dev/null 2>&1
[ -f "$sentinel" ] && ok "teardown: writes the done-sentinel file" || bad "teardown: writes the done-sentinel file"

sout3="$("$SCRIPT" status --label com.autopilot.test --dir "$RUNDIR" 2>&1)"
have "status: reports done once the sentinel exists" 'STATUS: done' "$sout3"

# --- assert-run-head: the run-loop / --resume HEAD guard (task 13) ------------
# Build a real git repo with a run-state branch and a task branch, so the
# fixture matches finding #23 exactly: the run worktree's HEAD found parked on
# a task branch instead of `auto-pilot/<run_id>`.
HEAD_REPO="$BASE/head-repo"
mkdir -p "$HEAD_REPO"
git -C "$HEAD_REPO" init -q -b main
git -C "$HEAD_REPO" config user.email test@example.com
git -C "$HEAD_REPO" config user.name test
: >"$HEAD_REPO/seed"
git -C "$HEAD_REPO" add seed
git -C "$HEAD_REPO" commit -q -m seed

RUN_ID="2026-07-11-test-run"
git -C "$HEAD_REPO" checkout -q -b "auto-pilot/$RUN_ID"
mkdir -p "$HEAD_REPO/.auto-pilot"
printf -- '---\nrun_id: %s\n---\ncontent-on-run-state-branch\n' "$RUN_ID" >"$HEAD_REPO/.auto-pilot/RUN.md"
git -C "$HEAD_REPO" add .auto-pilot/RUN.md
git -C "$HEAD_REPO" commit -q -m "seed RUN.md"

git -C "$HEAD_REPO" checkout -q main
git -C "$HEAD_REPO" checkout -q -b "auto-pilot/hardening-task_3"
printf 'task work\n' >"$HEAD_REPO/task-file"
git -C "$HEAD_REPO" add task-file
git -C "$HEAD_REPO" commit -q -m "task work committed on the wrong branch"
# The run worktree is now parked on the TASK branch — exactly finding #23:
# .auto-pilot/RUN.md is absent here (it only exists on the run-state branch).
[ -f "$HEAD_REPO/.auto-pilot/RUN.md" ] && bad "fixture: RUN.md absent on task branch" \
  || ok "fixture: RUN.md absent from working tree while parked on the task branch"

# (b) --resume's belt: `git show <branch>:<path>` recovers RUN.md correctly
# EVEN THOUGH HEAD is parked on the task branch right now.
shown="$(git -C "$HEAD_REPO" show "auto-pilot/$RUN_ID:.auto-pilot/RUN.md" 2>&1)"
have "git show recovers RUN.md while HEAD is parked on a task branch" \
  'content-on-run-state-branch' "$shown"

# (a) the guard fires: detects the deviation, restores HEAD, and records it.
QFILE="$BASE/QUESTIONS.md"
: >"$QFILE"
gout="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE" 2>&1)"
gc=$?
[ "$gc" = 0 ] && ok "assert-run-head: exits 0 after restoring" || bad "assert-run-head: exits 0 after restoring" "$gout"
have "assert-run-head: reports the deviation" 'HEAD DEVIATION restored' "$gout"
restored="$(git -C "$HEAD_REPO" rev-parse --abbrev-ref HEAD)"
[ "$restored" = "auto-pilot/$RUN_ID" ] && ok "assert-run-head: HEAD restored to the run-state branch" \
  || bad "assert-run-head: HEAD restored to the run-state branch" "got $restored"
qbody="$(cat "$QFILE")"
have "assert-run-head: records the deviation in QUESTIONS.md" 'HEAD was parked on `auto-pilot/hardening-task_3`' "$qbody"
have "assert-run-head: QUESTIONS.md entry is reversible" '**Reversible:** yes' "$qbody"

# idempotent: running it again with HEAD already correct is a silent no-op.
gout2="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE" 2>&1)"
gc2=$?
[ "$gc2" = 0 ] && ok "assert-run-head: exits 0 when HEAD already correct" || bad "assert-run-head: exits 0 when HEAD already correct" "$gout2"
have "assert-run-head: reports HEAD OK on a clean run" 'HEAD OK' "$gout2"
qcount="$(grep -cE '^## Q[0-9]+' "$QFILE")"
[ "$qcount" = 1 ] && ok "assert-run-head: no duplicate entry once HEAD is already correct" \
  || bad "assert-run-head: no duplicate entry once HEAD is already correct" "got $qcount entries"

# fail-closed: not a git worktree at all
mkdir -p "$BASE/plain-dir-not-a-repo"
o="$("$SCRIPT" assert-run-head --dir "$BASE/plain-dir-not-a-repo" --run-id "$RUN_ID" 2>&1)"
[ $? = 2 ] \
  && printf '%s' "$o" | grep -q 'not a git worktree' \
  && ok "assert-run-head fail-closed: not a git worktree" || bad "assert-run-head fail-closed: not a git worktree" "$o"

# fail-closed: missing required args
o="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -q 'requires --dir and --run-id' \
  && ok "assert-run-head fail-closed: missing --run-id" || bad "assert-run-head fail-closed: missing --run-id" "$o"

# fail-closed: a DIRTY deviation must NOT restore (a non-conflicting checkout
# would silently carry the uncommitted task-branch edits onto the run-state branch).
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
printf 'uncommitted edit\n' >>"$HEAD_REPO/task-file"
o="$("$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" 2>&1)"
drc=$?
dhead="$(git -C "$HEAD_REPO" rev-parse --abbrev-ref HEAD)"
[ "$drc" = 2 ] && printf '%s' "$o" | grep -q 'uncommitted changes' && [ "$dhead" = "auto-pilot/hardening-task_3" ] \
  && ok "assert-run-head fail-closed: dirty deviation is not restored" \
  || bad "assert-run-head fail-closed: dirty deviation is not restored" "rc=$drc head=$dhead msg=$o"
git -C "$HEAD_REPO" checkout -q -- task-file # drop the dirty edit for later checks

# numbering uses the MAX existing index, not a count — a non-contiguous
# QUESTIONS.md (Q9, Q10) yields Q11, never a colliding low number.
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
QFILE2="$BASE/Q-noncontig.md"
printf '## Q9 — X — a\n\n## Q10 — X — b\n' >"$QFILE2"
"$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions "$QFILE2" >/dev/null 2>&1
grep -q '^## Q11 — RUN —' "$QFILE2" \
  && ok "assert-run-head: numbers from the max index (Q11 after Q9/Q10)" \
  || bad "assert-run-head: numbers from the max index" "$(cat "$QFILE2")"

# a relative --questions resolves against --dir (the run worktree), matching the
# documented `.auto-pilot/QUESTIONS.md` invocation — not the caller's cwd.
git -C "$HEAD_REPO" checkout -q "auto-pilot/hardening-task_3"
rm -f "$HEAD_REPO/QREL.md"
"$SCRIPT" assert-run-head --dir "$HEAD_REPO" --run-id "$RUN_ID" --questions QREL.md >/dev/null 2>&1
[ -f "$HEAD_REPO/QREL.md" ] && grep -q 'HEAD was parked' "$HEAD_REPO/QREL.md" \
  && ok "assert-run-head: relative --questions resolves against --dir" \
  || bad "assert-run-head: relative --questions resolves against --dir" "$(ls "$HEAD_REPO" 2>&1)"
# --- classify-exit: supervisor-side exit classification (task 10, #22) -------
CX="$BASE/cx"
mkdir -p "$CX"
printf 'ok\n' >"$CX/clean.log"
printf 'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}\n' >"$CX/auth.log"
printf 'API Error: 429 rate_limit_error: overloaded\n' >"$CX/rate.log"
printf 'some other unrelated crash\n' >"$CX/weird.log"

ceo="$("$SCRIPT" classify-exit --exit-code 0 --output "$CX/clean.log" 2>&1)"
cec=$?
[ "$cec" = 0 ] && [ "$ceo" = "done" ] && ok "classify-exit: clean exit -> done" || bad "classify-exit: clean exit -> done" "$ceo"

ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/auth.log" 2>&1)"
have "classify-exit: expired-OAuth 401 -> fatal" 'fatal:' "$ceo"
have "classify-exit: fatal reason names the auth failure" 'non-retryable auth failure' "$ceo"

ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/rate.log" 2>&1)"
have "classify-exit: rate-limit signal -> retry" 'retry:' "$ceo"
lack "classify-exit: rate-limit is not fatal" 'fatal:' "$ceo"

ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/weird.log" 2>&1)"
have "classify-exit: unclassified non-zero -> retry" 'retry:' "$ceo"

# A bare `401` in a transcript is NOT an auth failure. The classified bytes are
# a full stream-json transcript — line numbers, byte counts, SHAs, diffs — where
# those three digits appear constantly. Matching them would halt a healthy run
# with a WRONG diagnosis ("re-authenticate a credential that is fine").
printf '{"type":"assistant","text":"see foo.py:401 and the 4013-byte hunk @@ -401,7 +401,9 @@"}\n' >"$CX/incidental401.log"
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/incidental401.log" 2>&1)"
have "classify-exit: incidental 401 (line number/diff hunk) -> retry" 'retry:' "$ceo"
lack "classify-exit: incidental 401 is NOT fatal" 'fatal:' "$ceo"
# ...but a 401 in a genuine auth CONTEXT still is, including the exact shape the
# motivating run-#2 failure took.
printf 'API Error: 401 Invalid authentication credentials\n' >"$CX/ctx1.log"
have "classify-exit: run-#2 401 status line -> fatal" 'fatal:' "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/ctx1.log" 2>&1)"
printf '{"error":{"message":"nope"},"status":401}\n' >"$CX/ctx2.log"
have "classify-exit: HTTP status field 401 -> fatal" 'fatal:' "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/ctx2.log" 2>&1)"

# --since-offset: the log is APPENDED to across wakes, so a stale 401 from an
# earlier wake must not classify a later, unrelated failure as fatal — otherwise
# a human who re-authenticates and resumes gets halted again and told, falsely,
# that their credential is dead.
cat "$CX/auth.log" >"$CX/appended.log"
OFF="$(wc -c <"$CX/appended.log" | tr -d ' ')"
printf 'some later unrelated crash\n' >>"$CX/appended.log"
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/appended.log" --since-offset "$OFF" 2>&1)"
have "classify-exit: --since-offset ignores a previous wake's 401" 'retry:' "$ceo"
lack "classify-exit: stale 401 is not sticky across wakes" 'fatal:' "$ceo"
# without the offset the same file DOES read fatal — proving the offset is what
# does the work here, not an accident of the fixture.
have "classify-exit: whole-file read of the same log is fatal (the bug)" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/appended.log" 2>&1)"
# a live 401 within THIS wake's slice still halts
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/auth.log" --since-offset 0 2>&1)"
have "classify-exit: --since-offset 0 still sees this wake's 401" 'fatal:' "$ceo"
# fail-SAFE (not fail-closed): a garbage offset degrades to the whole file, so a
# broken offset can only over-halt, never silently relaunch forever.
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/auth.log" --since-offset bogus 2>&1)"
have "classify-exit: invalid --since-offset falls back to the whole file" 'fatal:' "$ceo"

# fail-closed: required args
o="$("$SCRIPT" classify-exit --output "$CX/clean.log" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -qF 'requires --exit-code' \
  && ok "classify-exit fail-closed: missing --exit-code" || bad "classify-exit fail-closed: missing --exit-code" "$o"
o="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/nope.log" 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -qF 'not found' \
  && ok "classify-exit fail-closed: missing --output file" || bad "classify-exit fail-closed: missing --output file" "$o"

# co-review: an auth signal that is CONTENT (transcript prose, a diff, or the
# run's own REPORT.md re-read after --resume), not the orchestrator's own error
# line, must NOT classify fatal — else a task ABOUT auth, or the halt's own
# REPORT.md reason on the next wake, revives finding #22's loop via durable files.
printf '{"type":"assistant","text":"wrote tests asserting authentication_failed and 401 Unauthorized are surfaced"}\n' >"$CX/content-auth.log"
ceo="$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/content-auth.log" 2>&1)"
lack "classify-exit: auth string in transcript CONTENT is not fatal" 'fatal:' "$ceo"
# the exact REPORT.md re-poison shape: a tool_result event carrying the halt's
# own reason text back into a later wake's slice.
printf '{"type":"user","message":{"content":[{"type":"tool_result","content":"## ALARM\\n- Reason: non-retryable auth failure (OAuth token has expired)"}]}}\n' >"$CX/report-echo.log"
lack "classify-exit: REPORT.md alarm echoed as a tool_result is not fatal" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/report-echo.log" 2>&1)"
# boundary: 401 as a PREFIX of a larger number in a status field is not a 401.
printf '{"error":{"message":"x"},"status":4013}\n' >"$CX/status4013.log"
lack "classify-exit: '\"status\":4013' does not match 401" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/status4013.log" 2>&1)"
# R1: `API Error:` INSIDE a stream-json event (a tool_result echoing a coder
# subagent's OWN failure) is content on a `{`-prefixed line — not the
# orchestrator's error surface — so it must NOT halt the run.
printf '%s\n' '{"type":"user","message":{"content":[{"type":"tool_result","content":"codex run failed: API Error: 401 Invalid authentication credentials"}]}}' >"$CX/coder-apierr.log"
lack "classify-exit: 'API Error:' inside event content is not fatal" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/coder-apierr.log" 2>&1)"
# R2: a plain-prose OAuth-expiry line on the CLI's OWN stderr (a NON-`{` line, so
# real error surface) must still classify fatal even without an API Error: / 401.
printf 'OAuth token has expired \xc2\xb7 Please obtain a new token or refresh your existing one.\n' >"$CX/prose-oauth.log"
have "classify-exit: plain-prose OAuth-expiry on stderr -> fatal" 'fatal:' \
  "$("$SCRIPT" classify-exit --exit-code 1 --output "$CX/prose-oauth.log" 2>&1)"

# --- supervisor-check: fatal halt writes systemic status + REPORT alarm + teardown
# (task 10) — fixture is a real git checkout so the run-state commit is observable.
if command -v git >/dev/null 2>&1; then
  SC="$BASE/sc-fatal"
  mkdir -p "$SC/.auto-pilot"
  (cd "$SC" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  scout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/auth.log" --dir "$SC" \
    --label com.autopilot.test.fatal --state "$SC/.auto-pilot/supervisor-state" 2>&1)"
  have "supervisor-check: fatal halt reports itself" 'supervisor halt' "$scout"
  have "supervisor-check: fatal writes status: systemic" 'status: systemic' "$(cat "$SC/.auto-pilot/RUN.md")"
  have "supervisor-check: fatal writes a pause_reason" 'pause_reason: non-retryable auth failure' "$(cat "$SC/.auto-pilot/RUN.md")"
  have "supervisor-check: fatal appends a REPORT.md alarm" 'ALARM' "$(cat "$SC/.auto-pilot/REPORT.md")"
  scommits="$(git -C "$SC" log --oneline | wc -l | tr -d ' ')"
  [ "$scommits" = 2 ] && ok "supervisor-check: fatal halt commits the run-state change" \
    || bad "supervisor-check: fatal halt commits the run-state change" "commits=$scommits"

  # --- no-progress guard: N (default 3) consecutive non-zero, no-commit wakes halts
  SC2="$BASE/sc-noprogress"
  mkdir -p "$SC2/.auto-pilot"
  (cd "$SC2" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  STATE2="$SC2/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" >/dev/null 2>&1
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" >/dev/null 2>&1
  npout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC2" --label com.autopilot.test.np --state "$STATE2" 2>&1)"
  have "supervisor-check: no-progress guard halts after N consecutive failures" 'no forward progress' "$npout"
  have "supervisor-check: no-progress halt also writes status: systemic" 'status: systemic' "$(cat "$SC2/.auto-pilot/RUN.md")"

  # co-review (finding #1): the guard must still fire when _run_head returns
  # EMPTY (a non-git run dir, or git missing from the launchd PATH) — an empty
  # head is sentineled so consecutive wakes still count as no progress instead of
  # resetting the counter to 1 forever and never halting.
  SC_EH="$BASE/sc-emptyhead"
  mkdir -p "$SC_EH/.auto-pilot" # deliberately NOT a git repo
  {
    printf -- '---\n'
    printf 'status: active\n'
    printf 'pause_reason: \n'
    printf -- '---\n'
  } >"$SC_EH/.auto-pilot/RUN.md"
  printf '# report\n' >"$SC_EH/.auto-pilot/REPORT.md"
  STATE_EH="$SC_EH/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" >/dev/null 2>&1
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" >/dev/null 2>&1
  ehout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC_EH" --label com.autopilot.test.eh --state "$STATE_EH" 2>&1)"
  have "supervisor-check: no-progress guard halts even with an empty run HEAD" 'no forward progress' "$ehout"
  have "supervisor-check: empty-HEAD halt still writes status: systemic" 'status: systemic' "$(cat "$SC_EH/.auto-pilot/RUN.md")"

  # --- a legitimate paused_until wait never trips the guard, even repeated ------
  SC3="$BASE/sc-paused"
  mkdir -p "$SC3/.auto-pilot"
  (cd "$SC3" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: paused\n'
      printf 'paused_until: 2099-01-01T00:00:00\n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  STATE3="$SC3/.auto-pilot/supervisor-state"
  i=0
  while [ "$i" -lt 5 ]; do
    "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC3" --label com.autopilot.test.paused --state "$STATE3" >/dev/null 2>&1
    i=$((i + 1))
  done
  lack "supervisor-check: a paused wake never halts, however many repeats" 'systemic' "$(cat "$SC3/.auto-pilot/RUN.md")"

  # --- forward progress (a fresh run-state commit) resets the guard's counter ---
  SC4="$BASE/sc-progress"
  mkdir -p "$SC4/.auto-pilot"
  (cd "$SC4" && git init -q \
    && {
      printf -- '---\n'
      printf 'status: active\n'
      printf 'pause_reason: \n'
      printf -- '---\n'
    } >.auto-pilot/RUN.md \
    && printf '# report\n' >.auto-pilot/REPORT.md \
    && git add -A && git -c user.name=t -c user.email=t@t commit -q -m init)
  STATE4="$SC4/.auto-pilot/supervisor-state"
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" >/dev/null 2>&1
  # a task did real work between wakes: a new run-state commit lands
  (cd "$SC4" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "task progressed")
  "$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" >/dev/null 2>&1
  pgout="$("$SCRIPT" supervisor-check --exit-code 1 --wake-start 1 --log "$CX/weird.log" --dir "$SC4" --label com.autopilot.test.progress --state "$STATE4" 2>&1)"
  have "supervisor-check: a run-state commit resets the no-progress counter" '2/3 consecutive' "$pgout"
  lack "supervisor-check: progress in between never halts" 'systemic' "$(cat "$SC4/.auto-pilot/RUN.md")"
else
  echo "skip - supervisor-check: fatal/no-progress halt (git not available)"
fi

# --- write-launch: the generated script classifies its own exit (task 10) -----
lbody10="$(cat "$BASE/launch.sh" 2>/dev/null)"
have "launch: calls supervisor-check after claude exits" 'supervisor-check' "$lbody10"
have "launch: no longer execs claude directly" 'set +e' "$lbody10"
lack "launch: exec sandbox-exec no longer used" 'exec sandbox-exec' "$lbody10"
# the log offset must be captured BEFORE claude runs, and handed to
# supervisor-check — else classification reads every past wake's bytes too.
have "launch: captures the log offset before the run" 'off=$(wc -c' "$lbody10"
have "launch: passes --since-offset to supervisor-check" '--since-offset "$off"' "$lbody10"
off_ln="$(printf '%s\n' "$lbody10" | grep -n 'off=$(wc -c' | head -1 | cut -d: -f1)"
sbx_ln="$(printf '%s\n' "$lbody10" | grep -n '^sandbox-exec -f' | head -1 | cut -d: -f1)"
if [ -n "$off_ln" ] && [ -n "$sbx_ln" ] && [ "$off_ln" -lt "$sbx_ln" ]; then
  ok "launch: log offset is captured before sandbox-exec runs"
else
  bad "launch: log offset is captured before sandbox-exec runs" "off@$off_ln sandbox@$sbx_ln"
fi

# --- supervisor-gate: pre-invoke pause gate in shell (task 11, finding #19) ---
# Behavioral, on the GENERATED wrapper itself, not a string grep: run the real
# launch.sh with a stub claude (--claude-bin) and a stub sandbox-exec on PATH,
# each recording their own invocation via a marker file. "claude was never
# invoked" is then observable as "marker file absent" — no real jail involved.
GT="$BASE/gate"
mkdir -p "$GT/bin"
GT_CLAUDE="$GT/bin/claude-stub"
{
  printf '#!/bin/sh\n'
  printf ': >"%s/claude-called"\n' "$GT"
  printf 'exit 0\n'
} >"$GT_CLAUDE"
chmod +x "$GT_CLAUDE"
# The generated wrapper invokes `sandbox-exec -f <profile> <claude_bin> ...`
# bare (resolved via the script's own exported PATH) — a stub here both lets
# the test run fully offline (no real Seatbelt jail) and, by exec-ing through
# to the rest of argv, still reaches the claude stub above.
GT_SANDBOX="$GT/bin/sandbox-exec"
{
  printf '#!/bin/sh\n'
  printf ': >"%s/sandbox-exec-called"\n' "$GT"
  printf 'shift 2\n' # drop "-f <profile>"
  printf 'exec "$@"\n'
} >"$GT_SANDBOX"
chmod +x "$GT_SANDBOX"
GT_PATH="$GT/bin:$GUARD:/usr/bin:/bin:/usr/sbin:/sbin"

FUTURE_TS="$(_gate_iso $((NOW_EPOCH + 3600)))"
PAST_TS="$(_gate_iso $((NOW_EPOCH - 3600)))"

# Build one run dir + generate its wrapper via write-launch, given a RUN.md
# front-matter body; returns (via echo) whether the claude stub ran.
_gate_case() {
  local name="$1" front="$2"
  local d="$GT/$name"
  mkdir -p "$d/.auto-pilot"
  { printf -- '---\n%s\n---\n' "$front"; } >"$d/.auto-pilot/RUN.md"
  rm -f "$GT/claude-called" "$GT/sandbox-exec-called"
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" --workdir "$d" \
    --log "$d/o.log" --prompt-file "$BASE/prompt.txt" --label "com.autopilot.gate.$name" \
    --claude-bin "$GT_CLAUDE" --path "$GT_PATH" \
    --out-script "$d/launch.sh" --out-plist "$d/job.plist" >/dev/null 2>&1
  "$d/launch.sh" >/dev/null 2>&1
  echo "rc=$? log=$d/o.log"
}

# Test A: paused_until an hour in the future -> exit 0, claude never invoked.
gaA="$(_gate_case gate-future "status: active
paused_until: $FUTURE_TS")"
rcA="${gaA#rc=}"
rcA="${rcA%% *}"
[ "$rcA" = 0 ] && ok "gate: future paused_until exits 0" || bad "gate: future paused_until exits 0" "$gaA"
[ ! -f "$GT/claude-called" ] && ok "gate: future paused_until never invokes claude" \
  || bad "gate: future paused_until never invokes claude"
have "gate: future paused_until logs the skip" "skipping this wake" "$(cat "$GT/gate-future/o.log" 2>/dev/null)"

# Test B: paused_until in the past -> claude IS invoked.
_gate_case gate-past "status: active
paused_until: $PAST_TS" >/dev/null
[ -f "$GT/claude-called" ] && ok "gate: past paused_until invokes claude" \
  || bad "gate: past paused_until invokes claude"

# Test C: paused_until empty -> claude IS invoked.
_gate_case gate-empty "status: active
paused_until:" >/dev/null
[ -f "$GT/claude-called" ] && ok "gate: empty paused_until invokes claude" \
  || bad "gate: empty paused_until invokes claude"

# Test D: status: done -> claude never invoked, the gate tears the job down.
gaD="$(_gate_case gate-done "status: done
paused_until:")"
[ ! -f "$GT/claude-called" ] && ok "gate: status done never invokes claude" \
  || bad "gate: status done never invokes claude"
have "gate: status done reports a teardown" "torn down" "$(cat "$GT/gate-done/o.log" 2>/dev/null)"

# Test E: status: systemic -> claude never invoked, teardown reported too.
gaE="$(_gate_case gate-systemic "status: systemic
paused_until:")"
[ ! -f "$GT/claude-called" ] && ok "gate: status systemic never invokes claude" \
  || bad "gate: status systemic never invokes claude"
have "gate: status systemic reports a teardown" "torn down" "$(cat "$GT/gate-systemic/o.log" 2>/dev/null)"

# Fail-safe: an unparseable paused_until proceeds rather than skipping forever.
_gate_case gate-garbage "status: active
paused_until: not-a-timestamp" >/dev/null
[ -f "$GT/claude-called" ] && ok "gate: garbage paused_until fails safe (proceeds)" \
  || bad "gate: garbage paused_until fails safe (proceeds)"
have "gate: garbage paused_until logs unparseable+proceeding" "unparseable" \
  "$(cat "$GT/gate-garbage/o.log" 2>/dev/null)"

# supervisor-gate itself (unit-level, not through the wrapper): --dir with no
# RUN.md at all is the other fail-safe path — proceed, don't die.
sgo="$("$SCRIPT" supervisor-gate --dir "$GT/no-such-run" --label com.autopilot.gate.norun 2>&1)"
sgc=$?
[ "$sgc" = 0 ] && ok "supervisor-gate: missing RUN.md fails safe (exit 0)" \
  || bad "supervisor-gate: missing RUN.md fails safe (exit 0)" "rc=$sgc out=$sgo"

# fail-closed: required args
o="$("$SCRIPT" supervisor-gate --label x 2>&1)"
[ $? = 2 ] && printf '%s' "$o" | grep -qF 'requires --dir and --label' \
  && ok "supervisor-gate fail-closed: missing --dir" || bad "supervisor-gate fail-closed: missing --dir" "$o"

finish
