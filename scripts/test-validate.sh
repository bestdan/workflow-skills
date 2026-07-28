#!/usr/bin/env bash
# test-validate.sh — fixture-based tests for scripts/validate.py's task-file
# checks: missing required fields, expires shape, and the check-5 expired
# computation (expires < today AND status non-terminal), plus the explicit
# task_dir argument that fixes the consumer-repo path bug (validate.py must
# validate the *passed* dir, not always fall back to this plugin's own
# dev_docs/tasks).
#
# Builds fixture task directories under a temp dir (mktemp -d) so nothing
# pollutes the repo, runs validate.py against each, and asserts on its
# plain-text `path: message` output.
#
# Run directly: bash scripts/test-validate.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/validate.py"

BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.validate-test.XXXXXX")"
trap 'rm -rf "$BASE"' EXIT
# Canonicalize to the physical path (mktemp -d can land under macOS's
# /var -> /private/var symlink) and stop git's upward repo-discovery walk at
# BASE, so a git op inside a fixture dir can never resolve to the caller's
# repo when the mktemp fallback above lands BASE inside this checkout. If the
# cd fails, exit rather than falling through with an empty BASE (which would
# make every later "$BASE/x" resolve to a root-relative /x).
BASE="$(cd "$BASE" && pwd -P)" || exit 2
export GIT_CEILING_DIRECTORIES="$BASE"

# A developer's global/system git config leaks into these fixture repos too:
# core.hooksPath (whose pre-commit hook blocks commits to main, and git init
# names the initial branch main) can silently veto fixture commits, and
# init.templateDir/commit.gpgsign/aliases are other injection routes. Pin the
# config env instead of nulling it, so `git init` still deterministically
# produces branch "main" on stock upstream git. GIT_CONFIG_COUNT/PARAMETERS are
# command-scope env config that outranks GIT_CONFIG_GLOBAL, and GIT_DIR/
# GIT_INDEX_FILE/etc are exported by git into every hook subprocess — so a
# check.sh invoked from a pre-commit hook would otherwise hand fixture `git
# add` calls the caller's repo/index. Unset the lot so no inherited env can
# redirect a fixture git op the same way the ceiling above blocks discovery.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_TEMPLATE_DIR GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT
printf '[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n' >"$BASE/gitconfig"
export GIT_CONFIG_GLOBAL="$BASE/gitconfig"
export GIT_CONFIG_SYSTEM=/dev/null

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

assert_contains() {
  # assert_contains <description> <haystack> <needle>
  if [ "${2#*"$3"}" != "$2" ]; then
    ok "$1"
  else
    bad "$1 (did not find '$3')"
  fi
}

assert_not_contains() {
  # assert_not_contains <description> <haystack> <needle>
  if [ "${2#*"$3"}" = "$2" ]; then
    ok "$1"
  else
    bad "$1 (unexpectedly found '$3')"
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
    echo "body"
  } >"$path"
}

# --- Fixture (a): missing required field -------------------------------
DIR_A="$BASE/missing-field"
write_task "$DIR_A/missing-title.md" "priority: low
size: 1
status: new
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01"

out_a="$(uv run "$SCRIPT" "$DIR_A" 2>&1)"
assert_contains "missing required field is flagged" "$out_a" "missing-title.md: missing required field 'title'"

# --- Fixture (b): expired non-terminal card is flagged ------------------
DIR_B="$BASE/expired"
write_task "$DIR_B/expired-open.md" "title: Expired and still open
priority: low
size: 1
status: new
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2020-02-01"
write_task "$DIR_B/expired-done.md" "title: Expired but done
priority: low
size: 1
status: done
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2020-02-01"

out_b="$(uv run "$SCRIPT" "$DIR_B" 2>&1)"
assert_contains "expired non-terminal card is flagged" "$out_b" "expired-open.md: expired:"
# --- Fixture (c): expired-but-done card is NOT flagged -------------------
assert_not_contains "expired but done card is not flagged" "$out_b" "expired-done.md: expired:"

# --- Fixture (b2): unquoted timestamp expires is rejected, not crashed -----
DIR_B2="$BASE/timestamp-expires"
write_task "$DIR_B2/ts-expires.md" "title: Timestamp expires
priority: low
size: 1
status: new
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2020-02-01T12:00:00"
out_b2="$(uv run "$SCRIPT" "$DIR_B2" 2>&1)"
assert_contains "unquoted timestamp expires reported as invalid ISO date" "$out_b2" "ts-expires.md: expires"

# --- Fixture (d): explicit task_dir validates the PASSED dir, not the ----
# plugin's own dev_docs/tasks (proves the consumer-repo path-bug fix).
DIR_D="$BASE/consumer-repo-tasks"
write_task "$DIR_D/distinctive-consumer-card.md" "title: Distinctive consumer card
priority: low
size: 1
status: new
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01"

out_d="$(uv run "$SCRIPT" "$DIR_D" 2>&1)"
exit_d=$?
assert_contains "explicit dir: this repo's own plugin structural checks still ran" "$out_d" "validate.py"
if [ "$exit_d" -eq 0 ]; then
  ok "explicit dir: clean fixture card exits 0"
else
  bad "explicit dir: clean fixture card should exit 0, got $exit_d"
fi
# Prove it's the PASSED dir under validation, not this plugin's own
# dev_docs/tasks: a card that only exists in $DIR_D must appear in the
# output, and this plugin's own real task cards (which have pre-existing
# `missing required field 'expires'` warnings — see dev_docs/tasks/
# autopilot_hardening_plan/) must NOT.
DIR_D_EMPTY_TITLE="$BASE/consumer-repo-tasks-2"
write_task "$DIR_D_EMPTY_TITLE/needs-a-flag.md" "priority: low
size: 1
status: new
created: 2020-01-01
source_branch: x
related_files: [a.md]
expires: 2099-01-01"
out_d2="$(uv run "$SCRIPT" "$DIR_D_EMPTY_TITLE" 2>&1)"
assert_contains "explicit dir: validates the passed dir's own card" "$out_d2" "needs-a-flag.md: missing required field 'title'"
assert_not_contains "explicit dir: does NOT validate the plugin's own dev_docs/tasks" "$out_d2" "autopilot_hardening"

# --- Default (no arg): still validates this plugin's own dev_docs/tasks --
# (preserves today's CI behavior — see validate.py module docstring)
out_default="$(uv run "$SCRIPT" 2>&1)"
assert_contains "no-arg default validates the plugin's own dev_docs/tasks" "$out_default" "autopilot_hardening"

echo
echo "test-validate: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-validate: OK"
