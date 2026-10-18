#!/usr/bin/env bats

setup() {
  setup_test
  unset CO_REVIEW_LEDGER_REMOTE
  LEDGER="$TEST_TMPDIR/ledger/findings.jsonl"
  mkdir -p "$TEST_TMPDIR/ledger"
}
teardown() { teardown_test; }
load test_helper

# rec_run <host> <run_id> <ts> <repo> <schema> <reviewers-json>
rec_run() {
  printf '{"type": "run", "schema": %s, "run_id": "%s", "host": "%s", "ts": "%s", "repo": "%s", "pr": 1, "reviewers": %s}\n' \
    "$5" "$2" "$1" "$3" "$4" "$6" >>"$LEDGER"
}

# rev <name> <disposition> <duration_s|null>
rev() { printf '{"name": "%s", "disposition": "%s", "duration_s": %s}' "$1" "$2" "$3"; }

# rec_finding <host> <run_id> <sources-json> <verdict> [human_corroborated]
rec_finding() {
  printf '{"type": "finding", "run_id": "%s", "host": "%s", "sources": %s, "human_corroborated": %s, "verdict": "%s"}\n' \
    "$2" "$1" "$3" "${5:-false}" "$4" >>"$LEDGER"
}

stats() { run python3 "$REPO_ROOT/scripts/co-review-stats.py" --ledger "$LEDGER" "$@"; }

TS=2026-10-01T00:00:00Z

# claude ran in r1..r4; devin ran only in r1, r2 (skipped in r3, r4).
#   claude: 6 findings, 3 wrong overall (50.0%); in the r1+r2 cohort 4 findings, 1 wrong (25.0%).
#   devin:  3 findings, 1 wrong (33.3%).
#   marginal delta = 33.3 - 50.0 = -16.7; paired delta = 33.3 - 25.0 = +8.3.
paired_fixture() {
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 5), $(rev devin ran 10)]"
  rec_run h r2 $TS org/a 1 "[$(rev claude ran 5), $(rev devin ran 20)]"
  rec_run h r3 $TS org/a 1 "[$(rev claude ran 5), $(rev devin skipped null)]"
  rec_run h r4 $TS org/a 1 "[$(rev claude ran 5), $(rev devin skipped null)]"
  rec_finding h r1 '["claude"]' wrong
  rec_finding h r1 '["claude"]' high
  rec_finding h r2 '["claude"]' high
  rec_finding h r2 '["claude"]' high
  rec_finding h r3 '["claude"]' wrong
  rec_finding h r4 '["claude"]' wrong
  rec_finding h r1 '["devin"]' wrong
  rec_finding h r2 '["devin"]' high
  rec_finding h r2 '["devin"]' medium
}

@test "delta claude is paired over common runs, not marginal" {
  paired_fixture
  stats
  assert_success
  assert_line --regexp '^devin +2 +3 +33\.3 +\+8\.3 \(n=2\) +0\.0 +0\.0 +1 +1 +1\.50 +10\.0 +20\.0 +0\.0$'
  refute_output --partial '-16.7'
}

@test "claude is its own row, first, with no delta against itself" {
  paired_fixture
  stats
  assert_line --regexp '^claude +4 +6 +50\.0 +— +0\.0 +0\.0 +3 +3 +1\.50 +5\.0 +5\.0 +0\.0$'
  assert_equal "$(printf '%s\n' "${lines[@]}" | sed -n '3p' | cut -d' ' -f1)" claude
  assert_equal "$(printf '%s\n' "${lines[@]}" | sed -n '4p' | cut -d' ' -f1)" devin
}

@test "rows are ordered claude first, then by name" {
  rec_run h r1 $TS org/a 1 "[$(rev zed ran 1), $(rev claude ran 1), $(rev alpha ran 1)]"
  stats
  assert_equal "$(printf '%s\n' "${lines[@]}" | sed -n '3,5p' | cut -d' ' -f1 | tr '\n' ' ')" 'claude alpha zed '
}

@test "unique-high needs sole source and no human corroboration" {
  rec_run h u1 $TS org/a 1 "[$(rev claude ran 1), $(rev devin ran 1), $(rev bot:copilot ran 1)]"
  rec_finding h u1 '["devin"]' high false
  rec_finding h u1 '["devin"]' high true
  rec_finding h u1 '["devin", "bot:copilot"]' high false
  stats
  assert_success
  # devin: 3 highs, only the first is unique. Claude has no findings, so the paired delta is undefined.
  assert_line --regexp '^devin +1 +3 +0\.0 +— \(n=1\) +0\.0 +0\.0 +3 +1 +3\.00 '
  # The two-source finding is a high for the bot too, but never unique.
  assert_line --regexp '^bot:copilot +1 +1 +0\.0 +— \(n=1\) +0\.0 +0\.0 +1 +0 +1\.00 '
}

@test "an empty paired cohort prints a dash, not zero" {
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1), $(rev grok skipped null)]"
  rec_run h r2 $TS org/a 1 "[$(rev claude skipped null), $(rev grok ran 1)]"
  rec_finding h r2 '["grok"]' wrong
  stats
  assert_success
  assert_line --regexp '^grok +1 +1 +100\.0 +— +0\.0 +0\.0 +0 +0 +1\.00 '
  refute_output --partial '(n=0)'
}

@test "timed-out durations are censored, failed ones are measured, both count toward fail %" {
  rec_run h d1 $TS org/a 1 "[$(rev devin ran 10)]"
  rec_run h d2 $TS org/a 1 "[$(rev devin ran 30)]"
  rec_run h d3 $TS org/a 1 "[$(rev devin failed 20)]"
  rec_run h d4 $TS org/a 1 "[$(rev devin timed-out 600)]"
  rec_run h d5 $TS org/a 1 "[$(rev devin ran null)]"
  rec_run h d6 $TS org/a 1 "[$(rev devin skipped null)]"
  stats
  assert_success
  # runs: 3 ran. Percentiles (nearest-rank) over [10, 20, 30]: p50=20, p90=30; 600 excluded.
  # fail %: (1 failed + 1 timed-out) / (3 ran + 1 failed + 1 timed-out) = 40.0; skipped is not a dispatch.
  assert_line --regexp '^devin +3 +0 +— +— +— +— +0 +0 +0\.00 +20\.0 +30\.0 +40\.0$'
}

@test "a reviewer with no ran or failed durations prints dashes for p50 and p90" {
  rec_run h t1 $TS org/a 1 "[$(rev devin timed-out 600)]"
  stats
  assert_line --regexp '^devin +0 +0 +— +— +— +— +0 +0 +— +— +— +100\.0$'
}

@test "a run is keyed on host and run_id together" {
  rec_run h1 x $TS org/a 1 "[$(rev claude ran 1), $(rev devin ran 1)]"
  rec_run h2 x $TS org/a 1 "[$(rev claude ran 1), $(rev devin skipped null)]"
  rec_finding h1 x '["claude"]' wrong
  rec_finding h2 x '["claude"]' high
  rec_finding h1 x '["devin"]' high
  stats
  assert_success
  assert_output --partial '2 reconciled runs'
  assert_line --regexp '^claude +2 +2 +50\.0 '
  # Cohort is only (h1, x): claude 1/1 wrong = 100.0, devin 0/1 = 0.0 -> -100.0
  assert_line --regexp '^devin +1 +1 +0\.0 +-100\.0 \(n=1\) '
}

@test "unknown-schema runs and their findings are skipped and counted" {
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_run h r2 $TS org/a 2 "[$(rev claude ran 1)]"
  rec_finding h r1 '["claude"]' wrong
  rec_finding h r2 '["claude"]' high
  stats
  assert_success
  assert_line --regexp '^claude +1 +1 +100\.0 '
  assert_output --partial '1 reconciled runs'
  assert_output --partial 'Skipped across the whole ledger: 1 run(s) with unknown schema, 0 unparseable line(s).'
}

@test "a valid run does not inherit an earlier unknown-schema run's findings" {
  rec_run h r1 $TS org/a 2 "[$(rev claude ran 1)]"
  rec_finding h r1 '["claude"]' wrong
  rec_finding h r1 '["claude"]' wrong
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h r1 '["claude"]' high
  stats
  assert_success
  assert_line --regexp '^claude +1 +1 +0\.0 '
}

@test "a repeated run counts once, and so do the findings that follow it" {
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h r1 '["claude"]' wrong
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h r1 '["claude"]' wrong
  rec_run h r2 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h r2 '["claude"]' high
  stats
  assert_success
  # Two distinct runs, one finding each; the repeated r1 block adds nothing.
  assert_line --regexp '^claude +2 +2 +50\.0 '
  assert_output --partial '2 reconciled runs'
}

@test "a torn line is skipped and counted, not fatal" {
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  printf '{"type": "finding", "run_id": "r1", "ho\n' >>"$LEDGER"
  stats
  assert_success
  assert_line --regexp '^claude +1 +0 '
  assert_output --partial '0 run(s) with unknown schema, 1 unparseable line(s).'
}

@test "the footer says the denominator is reconciled runs" {
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  stats
  assert_output --partial 'aborted before reconciliation are absent by design'
}

@test "a missing ledger prints one clean line and exits 0" {
  stats
  assert_success
  assert_output "No co-review ledger data at $LEDGER."
}

@test "an empty ledger prints one clean line and exits 0" {
  : >"$LEDGER"
  stats
  assert_success
  assert_output "No co-review ledger data at $LEDGER."
}

@test "--since keeps only runs at or after the date, and their findings" {
  rec_run h old 2026-09-01T10:00:00Z org/a 1 "[$(rev claude ran 1)]"
  rec_run h new 2026-10-05T10:00:00Z org/a 1 "[$(rev claude ran 1)]"
  rec_finding h old '["claude"]' wrong
  rec_finding h new '["claude"]' high
  rec_finding h new '["claude"]' high
  stats --since 2026-10-01
  assert_success
  assert_line --regexp '^claude +1 +2 +0\.0 +— +0\.0 +0\.0 +2 +2 +2\.00 '
  assert_output --partial '1 reconciled runs'
}

@test "--since rejects a value that is not a YYYY-MM-DD date" {
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  stats --since 10/01/2026
  assert_failure 2
  assert_output --partial 'not a YYYY-MM-DD date'
  stats --since yesterday
  assert_failure 2
}

@test "--repo keeps only that repo's runs, and their findings" {
  rec_run h a1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_run h b1 $TS org/b 1 "[$(rev claude ran 1)]"
  rec_finding h a1 '["claude"]' wrong
  rec_finding h b1 '["claude"]' high
  rec_finding h b1 '["claude"]' high
  stats --repo org/a
  assert_success
  assert_line --regexp '^claude +1 +1 +100\.0 '
  stats --repo org/none
  assert_success
  assert_output --partial 'No reconciled runs match'
}

@test "the ledger is never modified" {
  paired_fixture
  before="$(cksum <"$LEDGER")"
  stats
  assert_success
  assert_equal "$(cksum <"$LEDGER")" "$before"
}

# Several ledgers: the local file first, then a directory of per-host files.
multi_fixture() {
  LOCAL="$LEDGER"
  SHARED="$TEST_TMPDIR/shared/ledgers"
  mkdir -p "$SHARED"
  rec_run h1 r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h1 r1 '["claude"]' wrong
  # h1's own redacted copy of r1, plus h2's runs, one of them repeated in a
  # second file.
  LEDGER="$SHARED/h1.jsonl"
  rec_run h1 r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h1 r1 '["claude"]' wrong
  LEDGER="$SHARED/h2.jsonl"
  rec_run h2 x1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h2 x1 '["claude"]' high
  LEDGER="$SHARED/h3.jsonl"
  rec_run h2 x1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h2 x1 '["claude"]' high
  rec_run h3 y1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h3 y1 '["claude"]' high
  LEDGER="$LOCAL"
}

@test "--ledger repeats, a directory reads every *.jsonl, and a repeated run counts once" {
  multi_fixture
  echo 'not a ledger' >"$SHARED/README"
  stats --ledger "$SHARED"
  assert_success
  # r1 (local and its copy), x1 (in two files) and y1: three runs, one finding each.
  assert_output --partial '3 reconciled runs'
  assert_line --regexp '^claude +3 +3 +33\.3 '
  assert_output --partial '0 unparseable line(s)'
}

@test "with sync on, the default is the local ledger followed by the shared clone" {
  export HOME="$TEST_TMPDIR/home"
  mkdir -p "$HOME/.claude/co-review/shared/ledgers"
  LEDGER="$HOME/.claude/co-review/findings.jsonl"
  rec_run h1 r1 $TS org/a 1 "[$(rev claude ran 1)]"
  LEDGER="$HOME/.claude/co-review/shared/ledgers/h2.jsonl"
  rec_run h2 x1 $TS org/a 1 "[$(rev claude ran 1)]"
  run python3 "$REPO_ROOT/scripts/co-review-stats.py"
  assert_output --partial '1 reconciled runs'
  CO_REVIEW_LEDGER_REMOTE=file:///nowhere run python3 "$REPO_ROOT/scripts/co-review-stats.py"
  assert_output --partial '2 reconciled runs'
}

@test "a missing shared directory is not an error, and the empty message names every path" {
  export HOME="$TEST_TMPDIR/home"
  CO_REVIEW_LEDGER_REMOTE=file:///nowhere run python3 "$REPO_ROOT/scripts/co-review-stats.py"
  assert_success
  assert_output "No co-review ledger data at $HOME/.claude/co-review/findings.jsonl, $HOME/.claude/co-review/shared/ledgers."
}

@test "the drop-after-a-repeat state restarts at each file" {
  # The local file ends on a repeated run, so its trailing findings are
  # dropped. The next file's findings for its own first run must still count.
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_run h r1 $TS org/a 1 "[$(rev claude ran 1)]"
  rec_finding h r1 '["claude"]' wrong
  OTHER="$TEST_TMPDIR/other.jsonl"
  printf '{"type": "finding", "run_id": "r1", "host": "h", "sources": ["claude"], "human_corroborated": false, "verdict": "high"}\n' >"$OTHER"
  stats --ledger "$OTHER"
  assert_success
  assert_line --regexp '^claude +1 +1 +0\.0 '
}
