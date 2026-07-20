#!/usr/bin/env bash
# test-claim-scan.sh — fixture-based tests for scripts/claim-scan.sh.
#
# Hermetic: `gh` is stubbed with a local script (via claim-scan.sh's --gh
# flag) that serves canned `gh pr list --json ...` output from fixture JSON
# files keyed by --label, so nothing here touches the network. Task-file
# fixtures live under a mktemp dir. Covers the edges the module docstring
# calls out:
#   - the task_1/task_13 whole-line NON-BLEED — THE load-bearing case: a PR
#     claiming task_13 must never also read as claiming task_1, or vice
#     versa
#   - a clean no-WIP state
#   - a stale claim: a labeled task-claim PR with no matching in_progress
#     task file
#
# Run directly: bash scripts/test-claim-scan.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/claim-scan.sh"

command -v jq >/dev/null 2>&1 || {
  echo "test-claim-scan: jq is required but not found in PATH" >&2
  exit 2
}

BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.claim-scan-test.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT

fail=0
pass_count=0
fail_count=0

ok() {
  pass_count=$((pass_count + 1))
  echo "  ✔ $1"
}

bad() {
  fail_count=$((fail_count + 1))
  fail=1
  echo "  ✘ $1" >&2
}

assert_eq() {
  # assert_eq <description> <expected> <actual>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected '$2', got '$3')"
  fi
}

assert_contains() {
  # assert_contains <description> <haystack> <needle>
  if printf '%s\n' "$1" | grep -qF "$3"; then
    ok "$2"
  else
    bad "$2 (did not find '$3')"
  fi
}

assert_not_contains() {
  # assert_not_contains <description> <haystack> <needle>
  if printf '%s\n' "$1" | grep -qF "$3"; then
    bad "$2 (unexpectedly found '$3')"
  else
    ok "$2"
  fi
}

# --- gh stub -----------------------------------------------------------------
# Reads --label off argv and prints "$CLAIM_SCAN_FIXTURES/<label>.json", or an
# empty array if that fixture file is absent — so a test only has to write
# fixtures for the labels it cares about.
GH_STUB="$BASE/gh"
cat >"$GH_STUB" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
label=""
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
  if [ "${args[$i]}" = "--label" ]; then
    label="${args[$((i + 1))]}"
  fi
  i=$((i + 1))
done
fixture="${CLAIM_SCAN_FIXTURES:?CLAIM_SCAN_FIXTURES not set}/${label}.json"
if [ -f "$fixture" ]; then
  cat "$fixture"
else
  echo "[]"
fi
EOF
chmod +x "$GH_STUB"

write_task() {
  # write_task <path> <status>
  local path="$1" status="$2"
  mkdir -p "$(dirname "$path")"
  {
    echo "---"
    echo "title: Fixture"
    echo "status: $status"
    echo "---"
    echo "## Task"
    echo
    echo "Fixture."
  } >"$path"
}

run_scan() {
  # run_scan <fixture-dir> <task-dir> — sets $out, $rc.
  local fixture_dir="$1" task_dir="$2"
  out="$(CLAIM_SCAN_FIXTURES="$fixture_dir" "$SCRIPT" --gh "$GH_STUB" --repo test/repo --task-dir "$task_dir" 2>&1)"
  rc=$?
}

# --- Fixture 1: task_1 vs task_13 whole-line non-bleed ------------------------
F1="$BASE/f1"
mkdir -p "$F1"
cat >"$F1/task-claim.json" <<'EOF'
[
  {"number": 10, "headRefName": "session-a", "body": "Claiming task_1 for execution.\n\nClaims-task: task_1", "updatedAt": "2026-01-01T00:00:00Z"},
  {"number": 11, "headRefName": "session-b", "body": "Claiming task_13 for execution.\n\nClaims-task: task_13", "updatedAt": "2026-01-01T00:00:00Z"}
]
EOF
T1="$BASE/f1-tasks"
mkdir -p "$T1"

run_scan "$F1" "$T1"
assert_eq "task_1/task_13: script exits 0" "0" "$rc"
assert_contains "$out" "task_1/task_13: CLAIMED line for pr=10 slug=task_1 present" "slug=task_1 "
assert_contains "$out" "task_1/task_13: CLAIMED line for pr=11 slug=task_13 present" "slug=task_13 "
# The load-bearing assertion: a naive `grep -F "slug=task_1"` (no trailing
# space / no anchor) would match BOTH lines, because "task_1" is a substring
# of "task_13". Prove that isn't happening in either direction.
claimed_lines1="$(printf '%s\n' "$out" | grep '^CLAIMED ')"
count_task_1="$(printf '%s\n' "$claimed_lines1" | grep -c 'slug=task_1 ')"
assert_eq "task_1/task_13: exactly one line reads as claiming task_1 (no bleed from task_13)" "1" "$count_task_1"
count_task_13="$(printf '%s\n' "$claimed_lines1" | grep -c 'slug=task_13 ')"
assert_eq "task_1/task_13: exactly one line reads as claiming task_13 (no bleed from task_1)" "1" "$count_task_13"
# And the reverse-direction check: the pre-claim-check recipe (whole-line/
# whole-token match on a specific candidate slug) must not cross-hit.
assert_not_contains "$out" "task_1/task_13: task_13's CLAIMED line does not also satisfy a 'slug=task_1 ' query" "pr=11 slug=task_1 "
assert_not_contains "$out" "task_1/task_13: task_1's CLAIMED line does not also satisfy a 'slug=task_13 ' query" "pr=10 slug=task_13 "
# Both are counted as distinct WIP entries — neither collapsed into the other.
assert_contains "$out" "task_1/task_13: WIP_SLUGS lists task_1" "task_1"
assert_contains "$out" "task_1/task_13: WIP_SLUGS lists task_13" "task_13"
wip_line="$(printf '%s\n' "$out" | grep '^WIP_COUNT:')"
assert_eq "task_1/task_13: WIP_COUNT is 2 (both distinct)" "WIP_COUNT: 2" "$wip_line"

# --- Fixture 2: clean no-WIP state -------------------------------------------
F2="$BASE/f2"
mkdir -p "$F2"
echo "[]" >"$F2/task-claim.json"
echo "[]" >"$F2/task-loop.json"
echo "[]" >"$F2/task-blocked.json"
T2="$BASE/f2-tasks"
mkdir -p "$T2"
write_task "$T2/some-ready-task.md" "ready"

run_scan "$F2" "$T2"
assert_eq "clean state: script exits 0" "0" "$rc"
assert_not_contains "$out" "clean state: no CLAIMED lines" "CLAIMED "
assert_not_contains "$out" "clean state: no STALE lines" "STALE "
wip_line2="$(printf '%s\n' "$out" | grep '^WIP_COUNT:')"
assert_eq "clean state: WIP_COUNT is 0" "WIP_COUNT: 0" "$wip_line2"
wip_slugs2="$(printf '%s\n' "$out" | grep '^WIP_SLUGS:')"
assert_eq "clean state: WIP_SLUGS is empty" "WIP_SLUGS: " "$wip_slugs2"

# --- Fixture 3: stale claim (labeled PR with no matching in_progress file) --
F3="$BASE/f3"
mkdir -p "$F3"
cat >"$F3/task-claim.json" <<'EOF'
[
  {"number": 20, "headRefName": "abandoned-session", "body": "Claiming.\n\nClaims-task: orphan-slug", "updatedAt": "2020-01-01T00:00:00Z"},
  {"number": 21, "headRefName": "live-session", "body": "Claiming.\n\nClaims-task: active-slug", "updatedAt": "2026-01-01T00:00:00Z"}
]
EOF
T3="$BASE/f3-tasks"
mkdir -p "$T3"
write_task "$T3/active-slug.md" "in_progress"
# No file for orphan-slug at all — the stale case.

run_scan "$F3" "$T3"
assert_eq "stale claim: script exits 0" "0" "$rc"
assert_contains "$out" "stale claim: orphan-slug (no matching in_progress file) flagged STALE" "STALE pr=20 slug=orphan-slug"
stale_lines="$(printf '%s\n' "$out" | grep '^STALE ' || true)"
assert_not_contains "$stale_lines" "stale claim: active-slug (has a matching in_progress file) is NOT flagged STALE" "slug=active-slug"
wip_line3="$(printf '%s\n' "$out" | grep '^WIP_COUNT:')"
assert_eq "stale claim: WIP_COUNT counts both claimed slugs (orphan still in flight per PR signal)" "WIP_COUNT: 2" "$wip_line3"

echo
echo "test-claim-scan: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-claim-scan: OK"
