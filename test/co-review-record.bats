#!/usr/bin/env bats

setup() {
  setup_test
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
  printf '{"run_id": "%s", "pr": 7, "reviewers": [{"name": "devin"}, {"name": "bot:copilot-pull-request-reviewer"}]}\n' \
    "$1" >"$TEST_TMPDIR/run-$1.json"
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

@test "a repeated run_id appends nothing and says so" {
  record
  before="$(shasum "$LEDGER")"
  record
  assert_success
  assert_output 'RECORDED: duplicate, skipped'
  assert_equal "$(shasum "$LEDGER")" "$before"
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
  printf '[{' >"$TEST_TMPDIR/torn.json"
  record run-1 "$TEST_TMPDIR/torn.json"
  assert_failure
  assert_file_not_exists "$LEDGER"
}
