#!/usr/bin/env bats

setup() {
  setup_test
  ct_init
}
teardown() { teardown_test; }
load test_helper

# Copy the script into a fake repo layout (fake/scripts, fake/dev_docs) so
# ROOT resolves inside TEST_TMPDIR and the real ledger is never touched.
ct_init() {
  FAKE="$TEST_TMPDIR/fake"
  mkdir -p "$FAKE/scripts" "$FAKE/dev_docs"
  cp "$REPO_ROOT/scripts/complexity_trend.py" "$FAKE/scripts/complexity_trend.py"
  SCRIPT="$FAKE/scripts/complexity_trend.py"
  LEDGER="$FAKE/dev_docs/auto-pilot-complexity-ledger.jsonl"
}

ct_run() { run python3 "$SCRIPT" "$@"; }

ledger_meta() {
  printf '%s\n' '{"type": "meta", "schema": 1, "created": "2026-07-13", "spec": "test"}'
}

write_ledger() {
  {
    ledger_meta
    printf '%s\n' "$@"
  } >"$LEDGER"
}

write_raw_ledger() { printf '%s' "$*" >"$LEDGER"; }

# Point the copy's NEW_CONTROLLER_PATHS at $1 (relative to $FAKE).
set_controller_paths() {
  sed -i.bak "s#^NEW_CONTROLLER_PATHS: list\[str\] = \[\]#NEW_CONTROLLER_PATHS: list[str] = [\"$1\"]#" "$SCRIPT"
  rm -f "$SCRIPT.bak"
}

write_controller() {
  local rel="$1"
  shift
  mkdir -p "$(dirname "$FAKE/$rel")"
  printf '%s\n' "$@" >"$FAKE/$rel"
}

@test "check passes on a valid meta-only ledger" {
  write_ledger
  ct_run check
  assert_success
  assert_output --partial 'VERDICT: OK'
}

@test "a non-dict meta line is invalid" {
  write_raw_ledger '[]'
  ct_run check
  assert_failure 2
  assert_output --partial 'INVALID'
}

@test "a missing meta line or empty file is invalid" {
  for content in '' '{"foo": "bar"}'; do
    write_raw_ledger "$content"
    ct_run check
    assert_failure 2
    assert_output --partial 'INVALID'
  done
}

@test "an event with an unparseable date is invalid" {
  write_ledger '{"type": "event", "date": "2026-99-99", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n"}'
  ct_run check
  assert_failure 2
  assert_output --partial 'INVALID'
  assert_output --partial 'date'
}

@test "an event with delta 0 is invalid" {
  write_ledger '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 0, "category": "progress", "note": "n"}'
  ct_run check
  assert_failure 2
  assert_output --partial 'INVALID'
  assert_output --partial 'delta'
}

@test "an event with an unknown category, unknown side, or empty note is invalid" {
  for event in \
    '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 1, "category": "bogus", "note": "n"}' \
    '{"type": "event", "date": "2026-07-01", "pr": null, "side": "sideways", "delta": 1, "category": "progress", "note": "n"}' \
    '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": ""}'; do
    write_ledger "$event"
    ct_run check
    assert_failure 2
    assert_output --partial 'INVALID'
  done
}

@test "a category mismatch between the ledger and the marker census names the category" {
  write_ledger '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n"}'
  write_controller "controller.txt" '# SPECIAL-CASE(adapter): reason (task-1)'
  set_controller_paths "controller.txt"
  ct_run check
  assert_failure 2
  assert_output --partial 'INVALID'
  assert_output --partial 'new-side progress'
  assert_output --partial 'new-side adapter'
}

@test "a same-category count mismatch between the ledger and the marker census is invalid" {
  write_ledger \
    '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n1"}' \
    '{"type": "event", "date": "2026-07-02", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n2"}'
  write_controller "controller.txt" '# SPECIAL-CASE(progress): reason (task-1)'
  set_controller_paths "controller.txt"
  ct_run check
  assert_failure 2
  assert_output --partial 'new-side progress: ledger net 2 != live marker census 1'
}

@test "5 new-side adds with 5 matching markers and no old-side sheds trips" {
  write_ledger \
    '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n1"}' \
    '{"type": "event", "date": "2026-07-02", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n2"}' \
    '{"type": "event", "date": "2026-07-03", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n3"}' \
    '{"type": "event", "date": "2026-07-04", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n4"}' \
    '{"type": "event", "date": "2026-07-05", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n5"}'
  write_controller "controller.txt" \
    '# SPECIAL-CASE(progress): a' '# SPECIAL-CASE(progress): b' '# SPECIAL-CASE(progress): c' \
    '# SPECIAL-CASE(progress): d' '# SPECIAL-CASE(progress): e'
  set_controller_paths "controller.txt"
  ct_run check
  assert_failure 1
  assert_output --partial 'VERDICT: TRIP'
}

@test "shedding 6 on the old side clears the trip for the same 5 new-side adds" {
  write_ledger \
    '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n1"}' \
    '{"type": "event", "date": "2026-07-02", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n2"}' \
    '{"type": "event", "date": "2026-07-03", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n3"}' \
    '{"type": "event", "date": "2026-07-04", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n4"}' \
    '{"type": "event", "date": "2026-07-05", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n5"}' \
    '{"type": "event", "date": "2026-07-06", "pr": null, "side": "old", "delta": -6, "category": "progress", "note": "shed"}'
  write_controller "controller.txt" \
    '# SPECIAL-CASE(progress): a' '# SPECIAL-CASE(progress): b' '# SPECIAL-CASE(progress): c' \
    '# SPECIAL-CASE(progress): d' '# SPECIAL-CASE(progress): e'
  set_controller_paths "controller.txt"
  ct_run check
  assert_success
  assert_output --partial 'VERDICT: OK'
}

@test "2 new-side adds under the trip floor warn instead of tripping" {
  write_ledger \
    '{"type": "event", "date": "2026-07-01", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n1"}' \
    '{"type": "event", "date": "2026-07-02", "pr": null, "side": "new", "delta": 1, "category": "progress", "note": "n2"}'
  write_controller "controller.txt" '# SPECIAL-CASE(progress): a' '# SPECIAL-CASE(progress): b'
  set_controller_paths "controller.txt"
  ct_run check
  assert_success
  assert_output --partial 'VERDICT: WARN'
}

@test "add appends a well-formed event that a subsequent check accepts" {
  write_ledger
  ct_run add --side old --delta 3 --category progress --note "shed some old special cases"
  assert_success
  assert_output --partial 'Appended:'
  ct_run check
  assert_success
  assert_output --partial 'VERDICT: OK'
}

@test "add with delta 0 fails and leaves the ledger unchanged" {
  write_ledger
  before="$(cat "$LEDGER")"
  ct_run add --side new --delta 0 --category progress --note "n"
  assert_failure 2
  after="$(cat "$LEDGER")"
  [ "$before" = "$after" ]
}
