#!/usr/bin/env bats

setup() {
  setup_test
  unset CO_REVIEW_HOST
  LEDGER="$TEST_TMPDIR/ledger/findings.jsonl"
  cat >"$TEST_TMPDIR/map.json" <<'EOF'
{"Reviewer A": {"name": "devin", "kind": "agent"},
 "Reviewer B": {"name": "bot:copilot-pull-request-reviewer", "kind": "agent"},
 "Reviewer D": {"kind": "human"}}
EOF
  write_run run-1
  cat >"$TEST_TMPDIR/recon.json" <<'EOF'
[{"file": "a.py", "line": 3, "issue": "off by one", "sources": ["Reviewer A", "Reviewer D"],
  "verdict": "high", "recommended_fix": "use <=", "rationale": "loop skips last"},
 {"file": "b.py", "line": 9, "issue": "style", "sources": ["Reviewer B"],
  "verdict": "wrong", "recommended_fix": "", "rationale": "matches house style"},
 {"file": "c.py", "line": 1, "issue": "typo", "sources": ["Reviewer D"],
  "verdict": "medium", "recommended_fix": "fix", "rationale": "human only"}]
EOF
}
teardown() { teardown_test; }
load test_helper

write_run() {
  printf '{"run_id": "%s", "pr": 7, "reviewers": [{"name": "devin", "duration_s": %s}, {"name": "bot:copilot-pull-request-reviewer", "duration_s": null}]}\n' \
    "$1" "${2:-42.5}" >"$TEST_TMPDIR/run-$1.json"
}

record() {
  run python3 "$REPO_ROOT/scripts/co-review-record.py" --run "$TEST_TMPDIR/run-${1:-run-1}.json" \
    --findings "${2:-$TEST_TMPDIR/recon.json}" --mapping "$TEST_TMPDIR/map.json" --ledger "$LEDGER"
}

# jq-free field reads keep the suite dependency-light.
ledger_py() { python3 -c "import json,sys; recs=[json.loads(l) for l in open('$LEDGER')]; $1"; }

@test "happy path appends a run and its findings and prints only the receipt" {
  record
  assert_success
  assert_output 'RECORDED: 2 findings, 2 reviewers'
  assert_equal "$(wc -l <"$LEDGER" | tr -d ' ')" 3
  run ledger_py 'print(recs[0]["type"], recs[0]["schema"], recs[0]["run_id"], recs[1]["type"])'
  assert_output 'run 1 run-1 finding'
}

@test "neutral labels are unblinded to identities" {
  record
  run ledger_py 'print(recs[1]["sources"], recs[2]["sources"])'
  assert_output "['devin'] ['bot:copilot-pull-request-reviewer']"
}

@test "a human source is stripped and marks the finding corroborated; human-only findings drop" {
  record
  run ledger_py 'print(recs[1]["human_corroborated"], recs[2]["human_corroborated"], [r.get("file") for r in recs])'
  assert_output "True False [None, 'a.py', 'b.py']"
}

@test "each reviewer's duration_s is recorded on the run record" {
  record
  run ledger_py 'print([r["duration_s"] for r in recs[0]["reviewers"]])'
  assert_output "[42.5, None]"
}

@test "the run record names its host: the machine's by default, the caller's if given" {
  record
  run ledger_py 'import socket; print(recs[0]["host"] == socket.gethostname())'
  assert_output "True"
  printf '{"run_id": "run-h", "host": "laptop-2", "reviewers": []}\n' >"$TEST_TMPDIR/run-run-h.json"
  record run-h
  run ledger_py 'print([r["host"] for r in recs if r["run_id"] == "run-h"])'
  assert_output "['laptop-2', 'laptop-2', 'laptop-2']"
}

@test "CO_REVIEW_HOST overrides the hostname, and a host in the run still wins" {
  CO_REVIEW_HOST=vm-clone record
  assert_success
  run ledger_py 'print(sorted({r["host"] for r in recs}))'
  assert_output "['vm-clone']"
  printf '{"run_id": "run-h", "host": "laptop-2", "reviewers": []}\n' >"$TEST_TMPDIR/run-run-h.json"
  CO_REVIEW_HOST=vm-clone record run-h
  run ledger_py 'print([r["host"] for r in recs if r["run_id"] == "run-h"])'
  assert_output "['laptop-2', 'laptop-2', 'laptop-2']"
}

@test "an empty host exits non-zero and writes nothing" {
  printf '{"run_id": "run-e", "host": "", "reviewers": []}\n' >"$TEST_TMPDIR/run-run-e.json"
  record run-e
  assert_failure
  assert_output --partial "host must be a non-empty string"
  assert_file_not_exists "$LEDGER"
}

@test "a negative or non-numeric duration_s exits non-zero and writes nothing" {
  write_run run-neg -1
  record run-neg
  assert_failure
  assert_output --partial "duration_s must be a non-negative number or null"
  write_run run-str '"slow"'
  record run-str
  assert_failure
  write_run run-nan NaN
  record run-nan
  assert_failure
  write_run run-inf Infinity
  record run-inf
  assert_failure
  assert_file_not_exists "$LEDGER"
}

@test "a repeated run_id appends nothing and says so" {
  record
  before="$(shasum "$LEDGER")"
  record
  assert_success
  assert_output 'RECORDED: duplicate, skipped'
  assert_equal "$(shasum "$LEDGER")" "$before"
}

@test "an unterminated last line is closed off before the next run is appended" {
  mkdir -p "$(dirname "$LEDGER")"
  printf '{"type": "run", "run_id": "torn"' >"$LEDGER"
  record
  assert_success
  record
  assert_output 'RECORDED: duplicate, skipped'
  run ledger_py 'pass'
  assert_failure
  run python3 -c "import json; ls=open('$LEDGER').read().splitlines(); print(len(ls), [json.loads(l)['type'] for l in ls[1:]])"
  assert_output "4 ['run', 'finding', 'finding']"
}

@test "a write that fails partway leaves the ledger as it was and exits 1" {
  mkdir -p "$(dirname "$LEDGER")"
  # A 1 KiB file-size limit makes the append fail with EFBIG after a partial
  # write; Python ignores SIGXFSZ, so the error surfaces as OSError.
  python3 -c "import json; print(json.dumps({'type': 'run', 'run_id': 'old', 'pad': 'x' * 850}))" >"$LEDGER"
  before="$(shasum "$LEDGER")"
  run bash -c "ulimit -f 1; python3 '$REPO_ROOT/scripts/co-review-record.py' --run '$TEST_TMPDIR/run-run-1.json' \
    --findings '$TEST_TMPDIR/recon.json' --mapping '$TEST_TMPDIR/map.json' --ledger '$LEDGER'"
  assert_failure
  assert_output --partial "cannot write"
  assert_equal "$(shasum "$LEDGER")" "$before"
}

@test "the same run_id from another host is a new run, not a duplicate" {
  printf '{"run_id": "run-1", "host": "laptop-2", "reviewers": []}\n' >"$TEST_TMPDIR/run-other.json"
  record
  record other
  assert_success
  assert_output 'RECORDED: 2 findings, 0 reviewers'
  run ledger_py 'print(sorted(r["host"] for r in recs if r["type"] == "run") == sorted([__import__("socket").gethostname(), "laptop-2"]))'
  assert_output "True"
}

@test "input fields cannot override the ledger's own type, run_id or schema" {
  printf '{"run_id": "run-o", "type": "finding", "schema": 99, "reviewers": []}\n' >"$TEST_TMPDIR/run-run-o.json"
  printf '[{"sources": ["Reviewer A"], "verdict": "high", "type": "run", "run_id": "forged", "schema": 99}]' \
    >"$TEST_TMPDIR/forged.json"
  record run-o "$TEST_TMPDIR/forged.json"
  assert_success
  run ledger_py 'print([(r["type"], r["run_id"], r.get("schema")) for r in recs])'
  assert_output "[('run', 'run-o', 1), ('finding', 'run-o', None)]"
}

@test "concurrent runs leave a well-formed ledger with each run's records contiguous" {
  for i in 1 2 3 4 5 6 7 8; do write_run "c$i"; done
  for i in 1 2 3 4 5 6 7 8; do
    python3 "$REPO_ROOT/scripts/co-review-record.py" --run "$TEST_TMPDIR/run-c$i.json" \
      --findings "$TEST_TMPDIR/recon.json" --mapping "$TEST_TMPDIR/map.json" --ledger "$LEDGER" >/dev/null &
  done
  wait
  run ledger_py '
ids = [r["run_id"] for r in recs]
runs = [r["run_id"] for r in recs if r["type"] == "run"]
blocks = [ids[i:i+3] for i in range(0, len(ids), 3)]
print(len(recs), len(set(runs)), all(len(set(b)) == 1 for b in blocks))'
  assert_output '24 8 True'
}

@test "an unknown verdict exits non-zero and leaves the ledger unchanged" {
  record
  before="$(shasum "$LEDGER")"
  write_run run-2
  printf '[{"file": "a.py", "line": 1, "issue": "x", "sources": ["Reviewer A"], "verdict": "maybe"}]' \
    >"$TEST_TMPDIR/bad.json"
  record run-2 "$TEST_TMPDIR/bad.json"
  assert_failure
  assert_output --partial "unknown verdict 'maybe'"
  assert_equal "$(shasum "$LEDGER")" "$before"
}

@test "a source missing from the mapping, or malformed JSON, writes nothing" {
  printf '[{"sources": ["Reviewer Z"], "verdict": "high"}]' >"$TEST_TMPDIR/unmapped.json"
  record run-1 "$TEST_TMPDIR/unmapped.json"
  assert_failure
  assert_output --partial "'Reviewer Z' is not in the mapping"
  printf '[{"sources": [["Reviewer A"]], "verdict": "high"}]' >"$TEST_TMPDIR/nested.json"
  record run-1 "$TEST_TMPDIR/nested.json"
  assert_failure
  assert_output "co-review-record: findings[0]: source ['Reviewer A'] is not in the mapping"
  printf '[{"sources": [], "verdict": "high"}]' >"$TEST_TMPDIR/nosources.json"
  record run-1 "$TEST_TMPDIR/nosources.json"
  assert_failure
  assert_output --partial "sources must be a non-empty list"
  printf '[{' >"$TEST_TMPDIR/torn.json"
  record run-1 "$TEST_TMPDIR/torn.json"
  assert_failure
  assert_file_not_exists "$LEDGER"
}
