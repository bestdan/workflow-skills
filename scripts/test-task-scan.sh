#!/usr/bin/env bash
# test-task-scan.sh — fixture-based tests for scripts/task-scan.py.
#
# Builds fixture task directories under a temp dir (mktemp -d) so nothing
# pollutes the repo, runs task-scan.py against each, and asserts on the JSON
# it emits (via jq). Covers the edges called out in skills/task/SKILL.md that
# are easy to get wrong:
#   - a card missing `impact`/`size` ranks LAST within its tier, never dropped
#   - a blocker slug (`task_1`) must not substring-match a filename
#     (`task_13`) — whole-stem matching only
#   - multi-blocker readiness with mixed absent / done / still-open blockers
#   - epic rollup (parent: + plan-directory membership)
#   - expired detection (expires < today AND status non-terminal)
#   - malformed frontmatter fails closed (non-zero exit)
#   - --archive-candidates: three-way completion-date fallback (completed
#     field -> git-commit date -> today), older-than-N selection, non-done
#     statuses never selected
#
# Run directly: bash scripts/test-task-scan.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/task-scan.py"

command -v jq >/dev/null 2>&1 || {
  echo "test-task-scan: jq is required but not found in PATH" >&2
  exit 2
}

BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.task-scan-test.XXXXXX")"
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

# assert_eq <description> <expected> <actual>
assert_eq() {
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected '$2', got '$3')"
  fi
}

write_task() {
  # write_task <path> <frontmatter-body> <markdown-body>
  local path="$1" fm="$2" body="$3"
  mkdir -p "$(dirname "$path")"
  {
    echo "---"
    printf '%s\n' "$fm"
    echo "---"
    printf '%s\n' "$body"
  } >"$path"
}

std_body() {
  cat <<'EOF'
## Context

Fixture.

## Task

1. Do the thing.

## Acceptance Criteria

- It works
EOF
}

# --- Fixture 1: no-impact-sorts-last-not-dropped -----------------------------
DIR1="$BASE/rank"
write_task "$DIR1/has-impact.md" "title: Has impact
priority: medium
size: 2
impact: 5
status: ready
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR1/no-impact.md" "title: No impact
priority: medium
size: 2
status: ready
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"

out1="$("$SCRIPT" "$DIR1")" || bad "rank fixture: script exited non-zero"
ready_slugs="$(printf '%s' "$out1" | jq -r '.cards.ready | sort_by(.rank) | .[].slug')"
assert_eq "no-impact-sorts-last: 2 ready cards present" "2" "$(printf '%s' "$out1" | jq '.cards.ready | length')"
assert_eq "no-impact-sorts-last: scored card ranks first" "has-impact" "$(printf '%s' "$ready_slugs" | sed -n '1p')"
assert_eq "no-impact-sorts-last: unscored card ranks last (not dropped)" "no-impact" "$(printf '%s' "$ready_slugs" | sed -n '2p')"

# --- Fixture 2: task_1 vs task_13 slug non-bleed ----------------------------
DIR2="$BASE/slug-bleed"
write_task "$DIR2/task_13.md" "title: Task 13
priority: low
size: 1
status: new
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR2/dependent.md" "title: Depends on task_1 (which does not exist)
priority: low
size: 1
status: ready
created: 2026-01-01
source_branch: x
related_files: [a.md]
is_blocked_by: task_1
expires: 2099-01-01" "$(std_body)"

out2="$("$SCRIPT" "$DIR2")" || bad "slug-bleed fixture: script exited non-zero"
dep_ready="$(printf '%s' "$out2" | jq -r '.cards.ready[] | select(.slug=="dependent") | .dependency_ready')"
assert_eq "task_1 blocker does not match file task_13 (dependency_ready)" "true" "$dep_ready"

# --- Fixture 3: multi-blocker readiness, mixed absent/done/open ------------
DIR3="$BASE/multi-blocker"
write_task "$DIR3/done-blocker.md" "title: Done blocker
priority: low
size: 1
status: done
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR3/open-blocker.md" "title: Open blocker
priority: low
size: 1
status: in_progress
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR3/dependent.md" "title: Depends on absent, done, and open blockers
priority: low
size: 1
status: ready
created: 2026-01-01
source_branch: x
related_files: [a.md]
is_blocked_by: [absent-blocker, done-blocker, open-blocker]
expires: 2099-01-01" "$(std_body)"

out3="$("$SCRIPT" "$DIR3")" || bad "multi-blocker fixture: script exited non-zero"
dep3_ready="$(printf '%s' "$out3" | jq -r '.cards.ready[] | select(.slug=="dependent") | .dependency_ready')"
dep3_unresolved="$(printf '%s' "$out3" | jq -c '.cards.ready[] | select(.slug=="dependent") | .unresolved_blockers | sort')"
assert_eq "multi-blocker: not dependency_ready while one blocker is open" "false" "$dep3_ready"
assert_eq "multi-blocker: unresolved_blockers lists only the open one" '["open-blocker"]' "$dep3_unresolved"

# --- Fixture 4: epic rollup --------------------------------------------------
DIR4="$BASE/epic"
write_task "$DIR4/widget_plan/widget_plan.md" "type: epic
title: Widget epic
status: active
owner: dan
created: 2026-01-01" "# Widget epic"
write_task "$DIR4/widget_plan/widget_task_1.md" "title: Widget task 1 (in plan dir)
priority: low
size: 1
status: done
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR4/standalone-member.md" "title: Standalone epic member via parent
priority: low
size: 1
status: in_progress
created: 2026-01-01
source_branch: x
related_files: [a.md]
parent: widget
expires: 2099-01-01" "$(std_body)"
write_task "$DIR4/unrelated.md" "title: Not part of the epic
priority: low
size: 1
status: new
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"

out4="$("$SCRIPT" "$DIR4")" || bad "epic fixture: script exited non-zero"
epic_members="$(printf '%s' "$out4" | jq -c '.epics[0].members | sort')"
epic_total="$(printf '%s' "$out4" | jq -r '.epics[0].member_count')"
epic_done="$(printf '%s' "$out4" | jq -r '.epics[0].done')"
assert_eq "epic rollup: members drawn from plan dir + parent, unrelated excluded" '["standalone-member","widget_task_1"]' "$epic_members"
assert_eq "epic rollup: member_count is 2" "2" "$epic_total"
assert_eq "epic rollup: done count reflects status: done member" "1" "$epic_done"

# --- Fixture 5: expired detection -------------------------------------------
DIR5="$BASE/expired"
write_task "$DIR5/expired-open.md" "title: Expired and still open
priority: low
size: 1
status: ready
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2020-02-01" "$(std_body)"
write_task "$DIR5/expired-done.md" "title: Expired but done (terminal, not expired)
priority: low
size: 1
status: done
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2020-02-01" "$(std_body)"

out5="$("$SCRIPT" "$DIR5")" || bad "expired fixture: script exited non-zero"
expired_open="$(printf '%s' "$out5" | jq -r '.cards.ready[] | select(.slug=="expired-open") | .expired')"
expired_done="$(printf '%s' "$out5" | jq -r '.cards.done[] | select(.slug=="expired-done") | .expired')"
assert_eq "expired: non-terminal card past expires is expired" "true" "$expired_open"
assert_eq "expired: done card past expires is NOT expired (terminal)" "false" "$expired_done"

# --- Fixture 6: malformed frontmatter fails closed --------------------------
DIR6="$BASE/malformed"
mkdir -p "$DIR6"
printf -- '---\ntitle: "unterminated string\npriority: low\n---\n\nbody\n' >"$DIR6/bad.md"

if "$SCRIPT" "$DIR6" >/dev/null 2>"$BASE/malformed.stderr"; then
  bad "malformed frontmatter: script should exit non-zero, exited 0"
else
  ok "malformed frontmatter: script exits non-zero"
fi
if [ -s "$BASE/malformed.stderr" ]; then
  ok "malformed frontmatter: stderr has a message"
else
  bad "malformed frontmatter: stderr was empty"
fi

# --- Fixture 7: promote gate (deterministic HIGH checks) --------------------
DIR7="$BASE/promote"
write_task "$DIR7/high.md" "title: Passes all deterministic checks
priority: medium
size: 2
status: new
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR7/low-tbd.md" "title: Has unresolved TBD
priority: medium
size: 2
status: new
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(
  cat <<'EOF'
## Context

Fixture.

## Task

1. Do the thing.

## Acceptance Criteria

- It works

## TBD

Still deciding the approach.
EOF
)"

out7="$("$SCRIPT" "$DIR7")" || bad "promote fixture: script exited non-zero"
high_gate="$(printf '%s' "$out7" | jq -r '.cards.new[] | select(.slug=="high") | .promote_gate.high')"
low_gate="$(printf '%s' "$out7" | jq -r '.cards.new[] | select(.slug=="low-tbd") | .promote_gate.high')"
assert_eq "promote gate: clean card scores HIGH on deterministic checks" "true" "$high_gate"
assert_eq "promote gate: card with TBD content scores not-HIGH" "false" "$low_gate"

# --- Fixture 8: a present blocker with no `status` is unresolved -------------
# (absent OR status: done satisfies a blocker; a present card missing its
# status field must NOT be treated the same as an absent file).
DIR8="$BASE/missing-status-blocker"
write_task "$DIR8/statusless-blocker.md" "title: Blocker with no status field
priority: low
size: 1
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR8/dependent.md" "title: Depends on a present-but-statusless blocker
priority: low
size: 1
status: ready
created: 2026-01-01
source_branch: x
related_files: [a.md]
is_blocked_by: statusless-blocker
expires: 2099-01-01" "$(std_body)"

out8="$("$SCRIPT" "$DIR8")" || bad "missing-status fixture: script exited non-zero"
dep8_ready="$(printf '%s' "$out8" | jq -r '.cards.ready[] | select(.slug=="dependent") | .dependency_ready')"
assert_eq "present blocker with no status is unresolved (not treated as absent)" "false" "$dep8_ready"

# --- Fixture 9: a missing task dir is an empty scan, not an error ------------
out9="$("$SCRIPT" "$BASE/does-not-exist")" || bad "missing-dir: script should exit 0 on an absent dir"
assert_eq "missing dir: cards is an empty object" "{}" "$(printf '%s' "$out9" | jq -c '.cards')"
assert_eq "missing dir: epics is an empty array" "[]" "$(printf '%s' "$out9" | jq -c '.epics')"

# --- Fixture 10: a level-1 (# ) section heading is ignored -------------------
# body_sections keys on ## (or deeper) only, per SKILL.md's section contract.
# A card whose Acceptance Criteria heading is an h1 must NOT satisfy the gate.
DIR10="$BASE/heading-level"
write_task "$DIR10/h1-ac.md" "title: Acceptance Criteria as an h1
priority: medium
size: 2
status: new
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(
  cat <<'EOF'
## Context

Fixture.

## Task

1. Do the thing.

# Acceptance Criteria

- It works
EOF
)"

out10="$("$SCRIPT" "$DIR10")" || bad "heading-level fixture: script exited non-zero"
h1_ac="$(printf '%s' "$out10" | jq -r '.cards.new[] | select(.slug=="h1-ac") | .promote_gate.checks.has_acceptance_criteria')"
assert_eq "h1 '# Acceptance Criteria' is ignored (needs ##)" "false" "$h1_ac"

# --- Fixture 11: a done same-slug duplicate must not clear an active blocker -
# Slugs resolve by filename stem across the tree, so two files can share one.
# Readiness must fail toward "still blocked": a later done duplicate cannot
# overwrite an earlier active status (a/ sorts before b/, so without the guard
# the done copy would win and wrongly mark the dependent ready).
DIR11="$BASE/dup-slug"
write_task "$DIR11/a/dup.md" "title: Active copy of dup (sorts first)
priority: low
size: 1
status: in_progress
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR11/b/dup.md" "title: Done copy of dup (sorts last)
priority: low
size: 1
status: done
created: 2026-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
write_task "$DIR11/dependent.md" "title: Depends on dup
priority: low
size: 1
status: ready
created: 2026-01-01
source_branch: x
related_files: [a.md]
is_blocked_by: dup
expires: 2099-01-01" "$(std_body)"

out11="$("$SCRIPT" "$DIR11")" || bad "dup-slug fixture: script exited non-zero"
dep11_ready="$(printf '%s' "$out11" | jq -r '.cards.ready[] | select(.slug=="dependent") | .dependency_ready')"
assert_eq "done duplicate does not clear an active same-slug blocker" "false" "$dep11_ready"

# --- Fixture 12: --archive-candidates, three-way completion-date fallback --
# Per repo-pr-archive.md §2: completed field, else the file's last git-commit
# date, else (uncommitted/untracked) today's date. Only status: done cards
# whose resolved date is more than N days before today are candidates.
DIR12="$BASE/archive"
mkdir -p "$DIR12"
git -C "$DIR12" init -q
git -C "$DIR12" config user.email "test@example.com"
git -C "$DIR12" config user.name "Test"

# Rung 1: explicit `completed` field, far in the past -> selected.
write_task "$DIR12/old-done.md" "title: Old done, explicit completed
priority: low
size: 1
status: done
completed: 2020-01-01
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"

# Rung 1 (not selected): explicit `completed` field, recent -> NOT selected.
write_task "$DIR12/new-done.md" "title: New done, explicit completed recent
priority: low
size: 1
status: done
completed: $(date -v-5d +%Y-%m-%d 2>/dev/null || date -d '5 days ago' +%Y-%m-%d)
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"

# Non-done status is never a candidate, whatever its age.
write_task "$DIR12/old-not-done.md" "title: Old but not done
priority: low
size: 1
status: ready
completed: 2020-01-01
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"

git -C "$DIR12" add -A
GIT_AUTHOR_DATE="2020-02-01T00:00:00" GIT_COMMITTER_DATE="2020-02-01T00:00:00" \
  git -C "$DIR12" commit -q -m "rung 1 fixtures"

# Rung 2: no `completed` field -> falls through to the file's last git-commit
# date, which is old here -> selected.
write_task "$DIR12/git-dated-done.md" "title: Git-dated done, no completed field
priority: low
size: 1
status: done
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"
git -C "$DIR12" add -A
GIT_AUTHOR_DATE="2020-03-01T00:00:00" GIT_COMMITTER_DATE="2020-03-01T00:00:00" \
  git -C "$DIR12" commit -q -m "rung 2 fixture"

# Rung 3: no `completed` field AND uncommitted/untracked -> falls through to
# today's date (age 0) -> NOT selected, even under a large --older-than.
write_task "$DIR12/untracked-done.md" "title: Untracked done, no completed field
priority: low
size: 1
status: done
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01" "$(std_body)"

out12="$("$SCRIPT" --archive-candidates --older-than 30 "$DIR12")" || bad "archive fixture: script exited non-zero"
cand_slugs="$(printf '%s' "$out12" | jq -r '.candidates[].slug' | sort)"
expected_slugs="$(printf 'git-dated-done\nold-done')"
assert_eq "archive-candidates: selects only the older-than-N done cards" "$expected_slugs" "$cand_slugs"

completed_source="$(printf '%s' "$out12" | jq -r '.candidates[] | select(.slug=="old-done") | .completion_date_source')"
assert_eq "archive-candidates: explicit completed field is used when present" "completed" "$completed_source"

git_source="$(printf '%s' "$out12" | jq -r '.candidates[] | select(.slug=="git-dated-done") | .completion_date_source')"
assert_eq "archive-candidates: falls through to git-commit date when completed is absent" "git_commit_date" "$git_source"

echo
echo "test-task-scan: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-task-scan: OK"
