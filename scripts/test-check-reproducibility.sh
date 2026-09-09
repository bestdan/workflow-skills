#!/usr/bin/env bash
# test-check-reproducibility.sh — fixture-based tests for
# scripts/analysis-pipeline/check-reproducibility.sh.
#
# Copies skills/analysis-pipeline/example/ into a fresh `git init` fixture
# under a temp dir and asserts the card's three cases plus two memo-specific
# regression guards for the table-padding normalization:
#   - clean commit: verdict=pass
#   - a COMMITTED perturbation of model_output.json: verdict=fail, with the
#     diff, and a clean tree afterwards (the restore ran)
#   - an UNCOMMITTED edit: verdict=n/a (the clean check trips first — a
#     perturbation left uncommitted can never reach fail)
#   - a COMMITTED table-padding-only memo.filled.md change (what `dprint fmt`
#     does to a table row): verdict=pass
#   - a COMMITTED real memo.filled.md content change on a non-table line:
#     verdict=fail — proves the padding normalization stayed narrow enough
#     to still catch real drift
#
# Run directly: bash scripts/test-check-reproducibility.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/analysis-pipeline/check-reproducibility.sh"
EXAMPLE="$ROOT/skills/analysis-pipeline/example"

BASE="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/check-repro-test.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.check-repro-test.XXXXXX")"
[ -n "$BASE" ] && [ -d "$BASE" ] || {
  echo "test-check-reproducibility: could not create a temp dir" >&2
  exit 2
}
BASE="$(cd "$BASE" && pwd -P)" || exit 2
trap 'rm -rf "$BASE"' EXIT
export GIT_CEILING_DIRECTORIES="$BASE"

# Pin the fixture's git config to /dev/null so it cannot inherit the
# developer machine's global config (a global core.hooksPath with a
# pre-commit hook that refuses commits to "main" is exactly what a fresh
# fixture's first commit would trip). Per-command `git -c user.*` supplies
# the identity a commit needs; `-b main` fixes the branch name so the fixture
# never depends on init.defaultBranch.
export GIT_CONFIG_GLOBAL=/dev/null
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

assert_exit() {
  # assert_exit <description> <expected-rc> <actual-rc>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected exit $2, got $3)"
  fi
}

new_fixture() {
  # new_fixture <name> -> prints the repo path, with the example committed
  # under <repo>/example.
  local repo="$BASE/$1"
  mkdir -p "$repo/example"
  cp -R "$EXAMPLE/." "$repo/example/"
  (cd "$repo" && git init -q -b main && git add -A && git -c user.name=Test -c user.email=test@example.com commit -q -m init)
  echo "$repo"
}

# --- Case 1: clean commit -> pass ---------------------------------------
repo1="$(new_fixture repo1)"
out1="$("$SCRIPT" "$repo1/example" 2>&1)"
rc1=$?
assert_exit "clean commit: exits 0" 0 "$rc1"
case "$out1" in
  *"REPRO: verdict=pass"*) ok "clean commit: verdict=pass" ;;
  *) bad "clean commit: expected verdict=pass, got: $out1" ;;
esac
clean1="$(cd "$repo1" && git status --porcelain)"
if [ -z "$clean1" ]; then
  ok "clean commit: tree still clean after the check"
else
  bad "clean commit: tree left dirty: $clean1"
fi

# --- Case 2: a COMMITTED perturbation of model_output.json -> fail -------
repo2="$(new_fixture repo2)"
python3 -c '
import json
p = "'"$repo2"'/example/model_output.json"
with open(p) as f:
    data = json.load(f)
data["recommendation"]["monthly_cost"] = "$999.99"
with open(p, "w") as f:
    json.dump(data, f, indent=2)
'
(cd "$repo2" && git add -A && git -c user.name=Test -c user.email=test@example.com commit -q -m perturb)
out2="$("$SCRIPT" "$repo2/example" 2>&1)"
rc2=$?
assert_exit "committed perturbation: exits 1" 1 "$rc2"
case "$out2" in
  *"REPRO: verdict=fail"*) ok "committed perturbation: verdict=fail" ;;
  *) bad "committed perturbation: expected verdict=fail, got: $out2" ;;
esac
case "$out2" in
  *"999.99"*) ok "committed perturbation: diff shown on fail" ;;
  *) bad "committed perturbation: no diff in output: $out2" ;;
esac
clean2="$(cd "$repo2" && git status --porcelain)"
if [ -z "$clean2" ]; then
  ok "committed perturbation: tree restored to clean after fail"
else
  bad "committed perturbation: tree left dirty after fail: $clean2"
fi

# --- Case 3: an UNCOMMITTED edit -> n/a (clean check trips first) --------
repo3="$(new_fixture repo3)"
python3 -c '
import json
p = "'"$repo3"'/example/model_output.json"
with open(p) as f:
    data = json.load(f)
data["recommendation"]["monthly_cost"] = "$1.00"
with open(p, "w") as f:
    json.dump(data, f, indent=2)
'
out3="$("$SCRIPT" "$repo3/example" 2>&1)"
rc3=$?
assert_exit "uncommitted edit: exits 0" 0 "$rc3"
case "$out3" in
  *"REPRO: verdict=n/a"*) ok "uncommitted edit: verdict=n/a" ;;
  *) bad "uncommitted edit: expected verdict=n/a, got: $out3" ;;
esac
dirty3="$(cd "$repo3" && git status --porcelain)"
if [ -n "$dirty3" ]; then
  ok "uncommitted edit: left untouched by the n/a path"
else
  bad "uncommitted edit: the uncommitted change was clobbered"
fi

# --- Case 4: a COMMITTED table-padding-only memo change -> pass ---------
# Simulates what `dprint fmt` does to the checked-in memo: repad a table
# row's column widths with no content change. The padding normalization
# (scoped to lines starting with "|") must absorb this.
repo4="$(new_fixture repo4)"
python3 -c '
p = "'"$repo4"'/example/memo.filled.md"
with open(p) as f:
    text = f.read()
text = text.replace(
    "| Nimbus Cloud    | $209.20      | $2,510.40   | $116.40                           |",
    "|  Nimbus Cloud      |   $209.20        |   $2,510.40     |   $116.40                             |",
)
with open(p, "w") as f:
    f.write(text)
'
(cd "$repo4" && git add -A && git -c user.name=Test -c user.email=test@example.com commit -q -m repad)
out4="$("$SCRIPT" "$repo4/example" 2>&1)"
rc4=$?
assert_exit "committed table-padding-only memo change: exits 0" 0 "$rc4"
case "$out4" in
  *"REPRO: verdict=pass"*) ok "committed table-padding-only memo change: verdict=pass" ;;
  *) bad "committed table-padding-only memo change: expected verdict=pass, got: $out4" ;;
esac

# --- Case 5: a COMMITTED real memo content change -> fail ---------------
# A prose line (not a table row) changes meaning, not just padding. The
# padding normalization is scoped to table rows only, so this line is
# compared byte-exact and must still be caught.
repo5="$(new_fixture repo5)"
python3 -c '
p = "'"$repo5"'/example/memo.filled.md"
with open(p) as f:
    text = f.read()
text = text.replace("**Recommendation: CumuloStack**", "**Recommendation: WrongVendor**")
with open(p, "w") as f:
    f.write(text)
'
(cd "$repo5" && git add -A && git -c user.name=Test -c user.email=test@example.com commit -q -m mangle)
out5="$("$SCRIPT" "$repo5/example" 2>&1)"
rc5=$?
assert_exit "committed real memo content change: exits 1" 1 "$rc5"
case "$out5" in
  *"REPRO: verdict=fail"*) ok "committed real memo content change: verdict=fail" ;;
  *) bad "committed real memo content change: expected verdict=fail, got: $out5" ;;
esac
case "$out5" in
  *"WrongVendor"*) ok "committed real memo content change: diff shown on fail" ;;
  *) bad "committed real memo content change: no diff in output: $out5" ;;
esac
clean5="$(cd "$repo5" && git status --porcelain)"
if [ -z "$clean5" ]; then
  ok "committed real memo content change: tree restored to clean after fail"
else
  bad "committed real memo content change: tree left dirty after fail: $clean5"
fi

echo
echo "test-check-reproducibility: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-check-reproducibility: OK"
