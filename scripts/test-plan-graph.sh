#!/usr/bin/env bash
# test-plan-graph.sh — fixture-based tests for scripts/plan-graph.py.
#
# Builds fixture plan directories under a temp dir (mktemp -d) so nothing
# pollutes the repo, runs plan-graph.py against each (and once via stdin), and
# asserts on the JSON it emits (via jq). Covers the edges called out in
# commands/push-plan.md §4.3/§5.3/§5b.3 that are easy to get wrong:
#   - a clean DAG gets a valid topological order (blockers before dependents)
#   - a cycle exits non-zero and names the slugs involved
#   - an is_blocked_by entry already shaped like a tracker id is classified
#     tracker-id and is NOT an ordering edge / NOT warned
#   - a typo'd slug is classified unknown-slug and warned, but does not fail
#   - each --id-shape (linear / gh / jira) uses its own id regex
#
# Run directly: bash scripts/test-plan-graph.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/plan-graph.py"

command -v jq >/dev/null 2>&1 || {
  echo "test-plan-graph: jq is required but not found in PATH" >&2
  exit 2
}

BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.plan-graph-test.XXXXXX")"
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

write_task() {
  # write_task <path> <frontmatter-body>
  local path="$1" fm="$2"
  mkdir -p "$(dirname "$path")"
  {
    echo "---"
    printf '%s\n' "$fm"
    echo "---"
    echo
    echo "## Context"
    echo
    echo "Fixture."
  } >"$path"
}

# --- Fixture 1: clean DAG — valid topological order --------------------------
DIR1="$BASE/clean-dag"
write_task "$DIR1/a.md" "title: A
status: ready"
write_task "$DIR1/b.md" "title: B
status: ready
is_blocked_by: a"
write_task "$DIR1/c.md" "title: C
status: ready
is_blocked_by: [a, b]"

out1="$("$SCRIPT" "$DIR1" --id-shape linear)" || bad "clean-dag: script exited non-zero"
order1="$(printf '%s' "$out1" | jq -c '.order')"
cycles1="$(printf '%s' "$out1" | jq -c '.cycles')"
assert_eq "clean-dag: no cycles" "[]" "$cycles1"
a_idx="$(printf '%s' "$out1" | jq '.order | index("a")')"
b_idx="$(printf '%s' "$out1" | jq '.order | index("b")')"
c_idx="$(printf '%s' "$out1" | jq '.order | index("c")')"
if [ "$a_idx" -lt "$b_idx" ] && [ "$b_idx" -lt "$c_idx" ]; then
  ok "clean-dag: order respects a before b before c"
else
  bad "clean-dag: order does not respect blockers (order=$order1)"
fi

# --- Fixture 2: cycle — non-zero exit, names the slugs ------------------------
DIR2="$BASE/cycle"
write_task "$DIR2/x.md" "title: X
status: ready
is_blocked_by: y"
write_task "$DIR2/y.md" "title: Y
status: ready
is_blocked_by: x"

out2="$("$SCRIPT" "$DIR2" --id-shape linear 2>"$BASE/cycle.stderr")"
rc2=$?
if [ "$rc2" -ne 0 ]; then
  ok "cycle: script exits non-zero"
else
  bad "cycle: script should exit non-zero, exited 0"
fi
cycles2="$(printf '%s' "$out2" | jq -c '.cycles | sort')"
assert_eq "cycle: cycles names both slugs" '["x","y"]' "$cycles2"
if grep -q "x" "$BASE/cycle.stderr" && grep -q "y" "$BASE/cycle.stderr"; then
  ok "cycle: stderr names the cycle slugs"
else
  bad "cycle: stderr does not name both cycle slugs"
fi

# --- Fixture 3: bare tracker-id edge — classified tracker-id, not warned -----
DIR3="$BASE/tracker-id-edge"
write_task "$DIR3/dependent.md" "title: Depends on an already-migrated issue
status: ready
is_blocked_by: PRE-42"

out3="$("$SCRIPT" "$DIR3" --id-shape linear 2>"$BASE/tracker-id.stderr")" || bad "tracker-id fixture: script exited non-zero"
kind3="$(printf '%s' "$out3" | jq -r '.is_blocked_by.dependent[0].kind')"
edges3="$(printf '%s' "$out3" | jq -c '.edges')"
assert_eq "tracker-id: classified tracker-id" "tracker-id" "$kind3"
assert_eq "tracker-id: not an ordering edge" "[]" "$edges3"
if [ -s "$BASE/tracker-id.stderr" ]; then
  bad "tracker-id: should not warn, but stderr is non-empty: $(cat "$BASE/tracker-id.stderr")"
else
  ok "tracker-id: no warning emitted"
fi

# --- Fixture 4: typo'd slug — unknown-slug, warned but not failed ------------
DIR4="$BASE/typo"
write_task "$DIR4/real-blocker.md" "title: The real blocker
status: ready"
write_task "$DIR4/dependent.md" "title: Depends on a typo of real-blocker
status: ready
is_blocked_by: real-blokcer"

out4="$("$SCRIPT" "$DIR4" --id-shape linear 2>"$BASE/typo.stderr")"
rc4=$?
assert_eq "typo: script still exits 0 (warns, does not fail)" "0" "$rc4"
kind4="$(printf '%s' "$out4" | jq -r '.is_blocked_by.dependent[0].kind')"
assert_eq "typo: classified unknown-slug" "unknown-slug" "$kind4"
if grep -q "real-blokcer" "$BASE/typo.stderr"; then
  ok "typo: stderr names the offending slug"
else
  bad "typo: stderr does not name the offending slug"
fi

# --- Fixture 5: each --id-shape uses its own id regex ------------------------
DIR5="$BASE/id-shapes"
write_task "$DIR5/linear-dep.md" "title: Linear id
status: ready
is_blocked_by: ENG-7"
write_task "$DIR5/gh-dep.md" "title: gh id
status: ready
is_blocked_by: \"#7\""
write_task "$DIR5/jira-dep.md" "title: jira id
status: ready
is_blocked_by: PLAT-7"

out5="$("$SCRIPT" "$DIR5" --id-shape linear)" || bad "id-shapes(linear): script exited non-zero"
k5_linear="$(printf '%s' "$out5" | jq -r '.is_blocked_by["linear-dep"][0].kind')"
assert_eq "id-shape linear: ENG-7 classified tracker-id" "tracker-id" "$k5_linear"

out5g="$("$SCRIPT" "$DIR5" --id-shape gh)" || bad "id-shapes(gh): script exited non-zero"
k5_gh="$(printf '%s' "$out5g" | jq -r '.is_blocked_by["gh-dep"][0].kind')"
assert_eq "id-shape gh: #7 classified tracker-id" "tracker-id" "$k5_gh"

out5j="$("$SCRIPT" "$DIR5" --id-shape jira)" || bad "id-shapes(jira): script exited non-zero"
k5_jira="$(printf '%s' "$out5j" | jq -r '.is_blocked_by["jira-dep"][0].kind')"
assert_eq "id-shape jira: PLAT-7 classified tracker-id" "tracker-id" "$k5_jira"

# Cross-check: under --id-shape linear, the gh-shaped "#7" entry does NOT match
# the linear regex, so it warns as unknown-slug. (The jira-shaped "PLAT-7" *does*
# match ^[A-Z]+-\d+$, so under linear it classifies as tracker-id, not checked here.)
k5_gh_under_linear="$(printf '%s' "$out5" | jq -r '.is_blocked_by["gh-dep"][0].kind')"
assert_eq "id-shape linear: #7 (gh shape) is NOT a linear id" "unknown-slug" "$k5_gh_under_linear"

# --- Fixture 6: malformed frontmatter fails closed ---------------------------
DIR6="$BASE/malformed"
mkdir -p "$DIR6"
printf -- '---\ntitle: "unterminated string\nstatus: ready\n---\n\nbody\n' >"$DIR6/bad.md"

if "$SCRIPT" "$DIR6" --id-shape linear >/dev/null 2>"$BASE/malformed.stderr"; then
  bad "malformed frontmatter: script should exit non-zero, exited 0"
else
  ok "malformed frontmatter: script exits non-zero"
fi
if [ -s "$BASE/malformed.stderr" ]; then
  ok "malformed frontmatter: stderr has a message"
else
  bad "malformed frontmatter: stderr was empty"
fi

# --- Fixture 7: JSON on stdin (no plan dir) — same shape as the file path ----
out7="$(printf '%s' '[{"slug":"a","is_blocked_by":[]},{"slug":"b","is_blocked_by":"a"}]' | "$SCRIPT" --id-shape linear)" || bad "stdin fixture: script exited non-zero"
order7="$(printf '%s' "$out7" | jq -c '.order')"
assert_eq "stdin: order is [a, b]" '["a","b"]' "$order7"

# --- Fixture 8: --rewrite seeds/overrides the tracker map --------------------
out8="$(printf '%s' '[{"slug":"a","is_blocked_by":[]}]' | "$SCRIPT" --id-shape linear --rewrite a=PRE-99)" || bad "rewrite fixture: script exited non-zero"
map8="$(printf '%s' "$out8" | jq -r '.tracker_map.a')"
assert_eq "rewrite: tracker_map reflects --rewrite" "PRE-99" "$map8"

# --- Fixture 9: epic file (type: epic) is excluded from the graph -----------
DIR9="$BASE/epic-excluded"
write_task "$DIR9/widget_plan.md" "type: epic
title: Widget epic
status: active"
write_task "$DIR9/task-a.md" "title: Task A
status: ready"

out9="$("$SCRIPT" "$DIR9" --id-shape linear)" || bad "epic-excluded fixture: script exited non-zero"
order9="$(printf '%s' "$out9" | jq -c '.order')"
assert_eq "epic-excluded: order contains only the task, not the epic" '["task-a"]' "$order9"

# --- Fixture 10: duplicate slug across phase dirs fails closed --------------
DIR10="$BASE/dup-slug"
write_task "$DIR10/phase1/setup.md" "title: Setup one
status: ready"
write_task "$DIR10/phase2/setup.md" "title: Setup two
status: ready"

if "$SCRIPT" "$DIR10" --id-shape linear >/dev/null 2>"$BASE/dup.stderr"; then
  bad "duplicate slug: script should exit non-zero, exited 0"
else
  ok "duplicate slug: script exits non-zero"
fi
if grep -q "setup" "$BASE/dup.stderr"; then
  ok "duplicate slug: names the offending slug"
else
  bad "duplicate slug: stderr did not name the slug"
fi

echo
echo "test-plan-graph: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-plan-graph: OK"
