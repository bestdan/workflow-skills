#!/usr/bin/env bats

# The flush against a local bare repo standing in for the shared GitHub repo.
# Kill states are built directly (an unpushed commit, a dirty tree, a
# half-made clone) rather than by killing a process mid-step.

setup() {
  setup_test
  unset CO_REVIEW_HOST CO_REVIEW_LEDGER_REMOTE CO_REVIEW_SYNC_LOCK_TIMEOUT
  REMOTE="$TEST_TMPDIR/remote.git"
  git init -q --bare -b main "$REMOTE"
  export CO_REVIEW_LEDGER_REMOTE="file://$REMOTE"
  A="$TEST_TMPDIR/a"
  B="$TEST_TMPDIR/b"
  mkdir -p "$A" "$B"
}
teardown() { teardown_test; }
load test_helper

SCRIPT_REL=scripts/co-review-ledger-sync.py

# sync <dir> <host> <push|pull|check>
sync() {
  run env CO_REVIEW_HOST="$2" python3 "$REPO_ROOT/$SCRIPT_REL" --dir "$1" "--$3"
}

# add_run <dir> <host> <run_id>: a run with every field set, and one finding
# carrying every field plus one nobody listed.
add_run() {
  printf '{"type": "run", "schema": 1, "run_id": "%s", "host": "%s", "ts": "2026-10-01T00:00:00Z", "repo": "org/private", "pr": 7, "base_sha": "aaa", "head_sha": "bbb", "mode": {"local": false}, "diff_lines": 10, "co_review_version": "2.94.0", "reconciler_model": "m", "reviewers": [{"name": "claude", "disposition": "ran", "model": "m", "duration_s": 5, "command": "secret-cmd --token x", "reason": "r"}, {"name": "devin", "disposition": "ran", "model": null, "duration_s": 9}]}\n' \
    "$3" "$2" >>"$1/findings.jsonl"
  printf '{"type": "finding", "run_id": "%s", "host": "%s", "sources": ["devin"], "human_corroborated": false, "verdict": "wrong", "issue": "private prose", "rationale": "why", "recommended_fix": "fix", "file": "secret/path.py", "line": 3, "extra": "unlisted"}\n' \
    "$3" "$2" >>"$1/findings.jsonl"
}

remote_file() { git --git-dir="$REMOTE" show "main:ledgers/$1.jsonl"; }
commits() { git --git-dir="$REMOTE" rev-list --count main; }

# reference <dir> <host>: the shared file an uninterrupted first push of
# <dir>'s ledger produces, against a fresh remote.
reference() {
  local ref="$TEST_TMPDIR/ref.git" rdir="$TEST_TMPDIR/ref"
  rm -rf "$ref" "$rdir"
  git init -q --bare -b main "$ref"
  mkdir -p "$rdir"
  cp "$1/findings.jsonl" "$rdir/findings.jsonl"
  CO_REVIEW_LEDGER_REMOTE="file://$ref" CO_REVIEW_HOST="$2" \
    python3 "$REPO_ROOT/$SCRIPT_REL" --dir "$rdir" --push >/dev/null
  git --git-dir="$ref" show "main:ledgers/$2.jsonl"
}

@test "an unset remote prints off, exits 0, and makes no clone" {
  unset CO_REVIEW_LEDGER_REMOTE
  add_run "$A" ha r1
  sync "$A" ha push
  assert_success
  assert_output "ledger sync: off"
  assert_file_not_exists "$A/shared"
}

@test "the first push to an empty remote creates main, and the receipt is the only output" {
  add_run "$A" ha r1
  add_run "$A" ha r2
  sync "$A" ha push
  assert_success
  assert_output "ledger sync: pushed 2 runs"
  assert_equal "$(commits)" 1
  assert_equal "$(git --git-dir="$REMOTE" log -1 --format=%s main)" "ledger: ha +2 runs"
  assert_equal "$(remote_file ha | wc -l | tr -d ' ')" 4
}

@test "redaction copies only the allowlisted fields" {
  add_run "$A" ha r1
  sync "$A" ha push
  assert_success
  run python3 -c '
import json, sys
recs = [json.loads(l) for l in sys.stdin]
print(sorted(recs[0]))
print([sorted(r) for r in recs[0]["reviewers"]])
print(sorted(recs[1]))
' < <(remote_file ha)
  assert_line --index 0 "['co_review_version', 'diff_lines', 'host', 'mode', 'reconciler_model', 'repo', 'reviewers', 'run_id', 'schema', 'ts', 'type']"
  assert_line --index 1 "[['disposition', 'duration_s', 'model', 'name'], ['disposition', 'duration_s', 'model', 'name']]"
  assert_line --index 2 "['host', 'human_corroborated', 'run_id', 'sources', 'type', 'verdict']"
  run remote_file ha
  refute_output --partial "private prose"
  refute_output --partial "secret"
  refute_output --partial "unlisted"
}

@test "a rerun with nothing new appends nothing, commits nothing, and needs no remote" {
  add_run "$A" ha r1
  sync "$A" ha push
  before="$(remote_file ha)"
  sync "$A" ha push
  assert_output "ledger sync: up to date"
  assert_equal "$(commits)" 1
  assert_equal "$(remote_file ha)" "$before"
  mv "$REMOTE" "$TEST_TMPDIR/gone.git"
  sync "$A" ha push
  assert_success
  assert_output "ledger sync: up to date"
}

@test "a run whose last line is torn is skipped whole, not trimmed" {
  add_run "$A" ha r1
  printf '{"type": "run", "schema": 1, "run_id": "r2", "host": "ha", "reviewers": []}\n{"type": "finding", "run_id": "r2", "ho' >>"$A/findings.jsonl"
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  run remote_file ha
  refute_output --partial '"r2"'
}

@test "rows committed but never pushed are pushed by the next flush" {
  add_run "$A" ha r1
  sync "$A" ha push
  add_run "$A" ha r2
  # Kill after the commit: a local commit the remote never saw.
  echo '{"junk": true}' >>"$A/shared/ledgers/ha.jsonl"
  git -C "$A/shared" commit -q -am "unpushed"
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  assert_equal "$(remote_file ha)" "$(reference "$A" ha)"
}

@test "a dirty clone is reset, and the shared file comes out the same" {
  add_run "$A" ha r1
  sync "$A" ha push
  add_run "$A" ha r2
  # Kill after the append: an uncommitted edit and a stray file.
  echo '{"junk": true}' >>"$A/shared/ledgers/ha.jsonl"
  echo stray >"$A/shared/ledgers/stray.jsonl"
  touch "$A/shared/.git/marker"
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  assert_equal "$(remote_file ha)" "$(reference "$A" ha)"
  assert_file_exists "$A/shared/.git/marker"
  run git --git-dir="$REMOTE" show main:ledgers/stray.jsonl
  assert_failure
}

@test "a half-made clone and a leftover shared.tmp are discarded and recloned" {
  add_run "$A" ha r1
  mkdir -p "$A/shared/ledgers" "$A/shared.tmp/.git"
  echo junk >"$A/shared/ledgers/ha.jsonl"
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  assert_dir_exists "$A/shared/.git"
  assert_file_not_exists "$A/shared.tmp"
  assert_equal "$(remote_file ha)" "$(reference "$A" ha)"
}

@test "a clone mid-rebase is discarded and recloned" {
  add_run "$A" ha r1
  sync "$A" ha push
  add_run "$A" ha r2
  mkdir "$A/shared/.git/rebase-merge"
  touch "$A/shared/.git/marker"
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  assert_file_not_exists "$A/shared/.git/marker"
  assert_equal "$(remote_file ha)" "$(reference "$A" ha)"
}

@test "a clone of another remote is discarded and recloned" {
  add_run "$A" ha r1
  sync "$A" ha push
  git -C "$A/shared" remote set-url origin "file://$TEST_TMPDIR/elsewhere.git"
  add_run "$A" ha r2
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  assert_equal "$(git -C "$A/shared" config --get remote.origin.url)" "$CO_REVIEW_LEDGER_REMOTE"
}

# rewrite_remote: replace main's history with a new root commit, same tree.
rewrite_remote() {
  local w="$TEST_TMPDIR/rewriter"
  git clone -q "$CO_REVIEW_LEDGER_REMOTE" "$w"
  git -C "$w" checkout -q --orphan fresh
  git -C "$w" commit -q -m rewritten
  git -C "$w" push -q -f origin fresh:main
}

@test "a rewritten remote is detected on the rejected push and the clone is rebuilt" {
  add_run "$A" ha r1
  sync "$A" ha push
  touch "$A/shared/.git/marker"
  rewrite_remote
  add_run "$A" ha r2
  sync "$A" ha push
  assert_success
  assert_output "ledger sync: pushed 1 runs"
  assert_file_not_exists "$A/shared/.git/marker"
  assert_equal "$(git --git-dir="$REMOTE" log --format=%s main | tail -1)" rewritten
  assert_equal "$(remote_file ha)" "$(reference "$A" ha)"
}

@test "a push rejected because another host pushed is retried, and the clone survives" {
  add_run "$A" ha r1
  sync "$A" ha push
  touch "$A/shared/.git/marker"
  add_run "$B" hb x1
  sync "$B" hb push
  assert_output "ledger sync: pushed 1 runs"
  other="$(remote_file hb)"
  add_run "$A" ha r2
  sync "$A" ha push
  assert_success
  assert_output "ledger sync: pushed 1 runs"
  assert_file_exists "$A/shared/.git/marker"
  assert_equal "$(remote_file hb)" "$other"
  assert_equal "$(remote_file ha)" "$(reference "$A" ha)"
}

@test "two machines sharing a hostname both land their runs in the one file" {
  add_run "$A" same r1
  add_run "$B" same x1
  sync "$A" same push
  sync "$B" same push
  assert_output "ledger sync: pushed 1 runs"
  add_run "$A" same r2
  sync "$A" same push
  assert_output "ledger sync: pushed 1 runs"
  run remote_file same
  assert_equal "$(printf '%s\n' "$output" | grep -c '"type": "run"')" 3
}

# hold_lock <dir> <seconds>: hold shared.lock from another process.
hold_lock() {
  python3 -c 'import fcntl, pathlib, sys, time
f = open(sys.argv[1], "a")
fcntl.flock(f, fcntl.LOCK_EX)
pathlib.Path(sys.argv[2]).touch()
time.sleep(float(sys.argv[3]))' "$1/shared.lock" "$TEST_TMPDIR/held" "$2" >/dev/null 2>&1 3>&- &
  HOLDER=$!
  local i=0
  while [ ! -e "$TEST_TMPDIR/held" ] && [ "$i" -lt 100 ]; do
    sleep 0.1
    i=$((i + 1))
  done
}

@test "a flush that cannot get the lock in time says another flush is running" {
  add_run "$A" ha r1
  hold_lock "$A" 10
  CO_REVIEW_SYNC_LOCK_TIMEOUT=0.3 sync "$A" ha push
  kill "$HOLDER" 2>/dev/null || true
  assert_success
  assert_output "ledger sync: ❌ another flush is running"
  assert_file_not_exists "$A/shared"
}

@test "a second flush waits on the lock, and the run lands once" {
  add_run "$A" ha r1
  hold_lock "$A" 1
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  sync "$A" ha push
  assert_output "ledger sync: up to date"
  run remote_file ha
  assert_equal "$(printf '%s\n' "$output" | grep -c '"type": "run"')" 1
}

@test "an unreachable remote is a fixed reason, exit 0, and leaves no clone" {
  add_run "$A" ha r1
  export CO_REVIEW_LEDGER_REMOTE="file://$TEST_TMPDIR/missing.git"
  sync "$A" ha push
  assert_success
  assert_output "ledger sync: ❌ unreachable"
  assert_file_not_exists "$A/shared"
  assert_file_not_exists "$A/shared.tmp"
}

@test "a remote with branches but no main is a clone failure, not a new branch" {
  w="$TEST_TMPDIR/w"
  git init -q -b other "$w"
  git -C "$w" commit -q --allow-empty -m x
  git -C "$w" push -q "$CO_REVIEW_LEDGER_REMOTE" other:other
  add_run "$A" ha r1
  sync "$A" ha push
  assert_success
  assert_output "ledger sync: ❌ clone failed"
  run git --git-dir="$REMOTE" rev-parse --verify -q main
  assert_failure
}

@test "a remote with main but no file for this host gives an empty watermark" {
  add_run "$B" hb x1
  sync "$B" hb push
  add_run "$A" ha r1
  sync "$A" ha push
  assert_output "ledger sync: pushed 1 runs"
  assert_equal "$(remote_file ha)" "$(reference "$A" ha)"
}

@test "--pull prints a second receipt naming the host files" {
  add_run "$A" ha r1
  add_run "$B" hb x1
  sync "$B" hb push
  sync "$A" ha pull
  assert_success
  assert_line --index 0 "ledger sync: pushed 1 runs"
  assert_line --index 1 "ledger sync: pulled 2 host files"
  assert_equal "${#lines[@]}" 2
}

@test "--pull resets a dirty clone and rebuilds one whose remote was rewritten" {
  add_run "$A" ha r1
  sync "$A" ha push
  echo '{"junk": true}' >>"$A/shared/ledgers/ha.jsonl"
  sync "$A" ha pull
  assert_line --index 1 "ledger sync: pulled 1 host files"
  assert_equal "$(cat "$A/shared/ledgers/ha.jsonl")" "$(remote_file ha)"
  touch "$A/shared/.git/marker"
  rewrite_remote
  sync "$A" ha pull
  assert_output "$(printf 'ledger sync: up to date\nledger sync: pulled 1 host files')"
  assert_file_not_exists "$A/shared/.git/marker"
  assert_equal "$(git -C "$A/shared" log -1 --format=%s)" rewritten
}

@test "stats sum two hosts' files and the local ledger, counting each run once" {
  export HOME="$TEST_TMPDIR/home"
  H="$HOME/.claude/co-review"
  mkdir -p "$H"
  add_run "$H" ha r1
  add_run "$H" ha r2
  add_run "$B" hb x1
  sync "$B" hb push
  sync "$H" ha pull
  assert_line --index 0 "ledger sync: pushed 2 runs"
  assert_line --index 1 "ledger sync: pulled 2 host files"
  run python3 "$REPO_ROOT/scripts/co-review-stats.py"
  assert_success
  assert_output --partial "3 reconciled runs"
  assert_line --regexp '^devin +3 +3 +100\.0 '
}

@test "--pull after a failed push still leaves the stats on the local ledger" {
  export HOME="$TEST_TMPDIR/home"
  H="$HOME/.claude/co-review"
  mkdir -p "$H"
  add_run "$H" ha r1
  export CO_REVIEW_LEDGER_REMOTE="file://$TEST_TMPDIR/missing.git"
  sync "$H" ha pull
  assert_success
  assert_output "$(printf 'ledger sync: ❌ unreachable\nledger sync: ❌ unreachable')"
  assert_file_not_exists "$H/shared"
  run python3 "$REPO_ROOT/scripts/co-review-stats.py"
  assert_success
  assert_output --partial "1 reconciled runs"
}

@test "--check reports off as a pass, and an unreachable remote as a warning" {
  unset CO_REVIEW_LEDGER_REMOTE
  sync "$A" ha check
  assert_success
  assert_output "PASS  co-review ledger sync — off (CO_REVIEW_LEDGER_REMOTE unset)"
  export CO_REVIEW_LEDGER_REMOTE="file://$TEST_TMPDIR/missing.git"
  sync "$A" ha check
  assert_failure 1
  assert_output --regexp '^WARN  co-review ledger sync — remote unreachable'
  refute_output --partial "missing.git"
}

@test "--check reports a remote that refuses the SSH key as refused, not unreachable" {
  local ssh="$TEST_TMPDIR/fake-ssh"
  printf '#!/bin/sh\necho "git@example.invalid: Permission denied (publickey)." >&2\nexit 255\n' >"$ssh"
  chmod +x "$ssh"
  export GIT_SSH_COMMAND="$ssh"
  export CO_REVIEW_LEDGER_REMOTE="git@example.invalid:org/ledgers.git"
  sync "$A" ha check
  assert_failure 1
  assert_output --regexp '^WARN  co-review ledger sync — remote refused access or the repository does not exist; check the URL and credentials'
  refute_output --partial "example.invalid"
}

@test "--check reports a repository the host cannot find as refused or missing" {
  local ssh="$TEST_TMPDIR/fake-ssh"
  printf '#!/bin/sh\necho "ERROR: Repository not found." >&2\nexit 128\n' >"$ssh"
  chmod +x "$ssh"
  export GIT_SSH_COMMAND="$ssh"
  export CO_REVIEW_LEDGER_REMOTE="git@example.invalid:org/ledgers.git"
  sync "$A" ha check
  assert_failure 1
  assert_output --regexp '^WARN  co-review ledger sync — remote refused access or the repository does not exist'
  refute_output --partial "example.invalid"
}

@test "--check reports an untrusted host key as unreachable, not refused" {
  local ssh="$TEST_TMPDIR/fake-ssh"
  printf '#!/bin/sh\necho "Host key verification failed." >&2\nexit 255\n' >"$ssh"
  chmod +x "$ssh"
  export GIT_SSH_COMMAND="$ssh"
  export CO_REVIEW_LEDGER_REMOTE="git@example.invalid:org/ledgers.git"
  sync "$A" ha check
  assert_failure 1
  assert_output --regexp '^WARN  co-review ledger sync — remote unreachable'
  refute_output --partial "example.invalid"
}

@test "--check passes on a good clone and warns on a broken one" {
  sync "$A" ha check
  assert_success
  assert_output "PASS  co-review ledger sync — no clone yet (the next flush makes one), remote reachable"
  add_run "$A" ha r1
  sync "$A" ha push
  sync "$A" ha check
  assert_output "PASS  co-review ledger sync — clone ok, remote reachable"
  mkdir "$A/shared/.git/rebase-merge"
  sync "$A" ha check
  assert_failure 1
  assert_output --partial "is not a usable clone; the next flush reclones it"
}

# timed <cmd...>: run <cmd> with stdout and stderr on pipes, wait until both
# close, then print the seconds that took and the exit code, then its stdout. A
# detached flush that kept either pipe open would hold this until it finished.
timed() {
  python3 -c '
import subprocess, sys, time
t = time.monotonic()
p = subprocess.run(sys.argv[1:], capture_output=True)
print(f"{time.monotonic() - t:.2f} rc={p.returncode}")
sys.stdout.write(p.stdout.decode())
' "$@"
}

# wait_for <file> <text>: up to 15 s for <file> to contain <text>.
wait_for() {
  local i
  for i in $(seq 1 150); do
    grep -qF "$2" "$1" 2>/dev/null && return 0
    sleep 0.1
  done
  return 1
}

@test "--detach returns within the 1.5 s hook budget against a hanging remote, printing nothing" {
  printf '#!/bin/sh\nsleep 30\n' >"$TEST_TMPDIR/fake-ssh"
  chmod +x "$TEST_TMPDIR/fake-ssh"
  add_run "$A" ha r1
  run timed env CO_REVIEW_HOST=ha GIT_SSH_COMMAND="$TEST_TMPDIR/fake-ssh" \
    CO_REVIEW_LEDGER_REMOTE="ssh://hang.invalid/ledgers.git" \
    python3 "$REPO_ROOT/$SCRIPT_REL" --dir "$A" --push --detach
  pkill -f "$TEST_TMPDIR" || true
  assert_success
  assert_equal "${#lines[@]}" 1
  assert_regex "${lines[0]}" ' rc=0$'
  run python3 -c 'import sys; sys.exit(float(sys.argv[1]) >= 1.5)' "${lines[0]%% *}"
  assert_success
}

@test "--detach finishes the push in the background and leaves its receipt in last-push.log" {
  add_run "$A" ha r1
  run env CO_REVIEW_HOST=ha python3 "$REPO_ROOT/$SCRIPT_REL" --dir "$A" --push --detach
  assert_success
  assert_output ""
  wait_for "$A/last-push.log" "ledger sync: pushed 1 runs"
  assert_equal "$(commits)" 1
  run cat "$A/last-push.log"
  assert_regex "${lines[0]}" '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$'
  assert_equal "${lines[1]}" "ledger sync: pushed 1 runs"
}

@test "--detach with sync off prints nothing, starts nothing, and writes no log" {
  unset CO_REVIEW_LEDGER_REMOTE
  add_run "$A" ha r1
  run python3 "$REPO_ROOT/$SCRIPT_REL" --dir "$A" --push --detach
  assert_success
  assert_output ""
  assert_file_not_exists "$A/last-push.log"
}

@test "--detach without --push is a usage error" {
  run python3 "$REPO_ROOT/$SCRIPT_REL" --dir "$A" --pull --detach
  assert_failure 2
  assert_output --partial "--detach needs --push"
}

@test "the plugin's SessionEnd hook runs the detached push" {
  run python3 -c '
import json, sys
hooks = json.load(open(sys.argv[1]))["hooks"]["SessionEnd"]
print("\n".join(h["command"] for group in hooks for h in group["hooks"]))
' "$REPO_ROOT/hooks/hooks.json"
  assert_success
  assert_output 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-ledger-sync.py" --push --detach'
}
