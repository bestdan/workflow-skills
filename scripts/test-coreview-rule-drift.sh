#!/usr/bin/env bash
# test-coreview-rule-drift.sh — fixture-based tests for
# scripts/coreview-rule-drift.py.
#
# Builds fake plugin roots and settings files under a temp dir (mktemp -d) so
# nothing reads the developer's real ~/.claude/settings.json, and asserts on the
# JSON the script emits. Covers the classifications that are easy to get wrong:
#   - rules that match their templates exactly report NOTHING
#   - a reordered flag is DEAD (the match is byte-for-byte outside placeholders)
#   - a path that does not resolve here is OFF-MACHINE, not coverage — a
#     placeholder wildcard alone cannot tell it from a live substitution
#   - but an off-machine rule is NOT drift: a settings file shared across hosts
#     with different usernames carries every host's rules, so each host sees the
#     others' as unresolvable, and that is correct
#   - a path that merely does not exist YET (one level) is not off-machine,
#     because the dispatch's own `cat >` creates it
#   - a reviewer with no rule at all is "not configured", not drift
#   - a general-purpose `Bash(cd ...)` is never attributed to a reviewer
#   - a missing plugin root exits 2, distinct from the exit 1 that means drift
#   - the script is runnable AS DOCUMENTED, with CLAUDE_PLUGIN_ROOT unset, and
#     both documented call sites still pass --plugin-root (issue #607)
#
# Run directly: bash scripts/test-coreview-rule-drift.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/coreview-rule-drift.py"

command -v jq >/dev/null 2>&1 || {
  echo "test-coreview-rule-drift: jq is required but not found in PATH" >&2
  exit 2
}

# Bare `mktemp -d` (no template) ignores $TMPDIR on macOS, so the first arm
# isn't a real $TMPDIR attempt; try $TMPDIR explicitly before falling back to
# repo-local.
BASE="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/coreview-drift-test.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.coreview-drift-test.XXXXXX")"
# Fail closed: an empty BASE would make the `cd` below a no-op (bash `cd ""`
# exits 0), leaving BASE pointing at the repo root for the EXIT trap to delete.
[ -n "$BASE" ] && [ -d "$BASE" ] || {
  echo "test-coreview-rule-drift: could not create a temp dir" >&2
  exit 2
}
BASE="$(cd "$BASE" && pwd -P)" || exit 2
trap 'rm -rf "$BASE"' EXIT

fails=0
pass() { echo "  ok   $1"; }
fail() {
  echo "  FAIL $1"
  fails=$((fails + 1))
}

# A directory that really exists, so a substituted path built under it is
# plausible. The plausibility rule allows one missing trailing level.
REAL_DIR="$BASE/inputs"
mkdir -p "$REAL_DIR"

# --- fixture builders ------------------------------------------------------

# make_plugin <dir> — a plugin root shipping one agy-shaped reviewer with two
# rules (a path-bearing command and a bare probe) plus a devin-shaped one whose
# first segment is a generic `mkdir`.
make_plugin() {
  local dir="$1"
  mkdir -p "$dir/skills/co-review/reviewers"
  cat >"$dir/skills/co-review/reviewers/agy.md" <<'EOF'
# agy

Prose that quotes "Bash(agy never-a-rule)" outside a fence must be ignored.

A bash fence holds the invocation, not a rule:

```bash
"Bash(agy also-never-a-rule)"
```

```json
"Bash(agy --sandbox --add-dir \"<INPUT-DIR>\" -p \"read <INPUT>\" --model \"M1\")",
"Bash(agy models)"
```
EOF
  cat >"$dir/skills/co-review/reviewers/devin.md" <<'EOF'
# devin

```json
"Bash(mkdir -p \"<NEUTRAL>\")",
"Bash(devin -p --prompt-file \"<INPUT>\" --permission-mode auto)"
```
EOF
}

# make_settings <file> <rule>... — a settings.json carrying the given allow rules.
make_settings() {
  local file="$1"
  shift
  local json
  json="$(printf '%s\n' "$@" | jq -R . | jq -s '{permissions: {allow: .}}')"
  printf '%s\n' "$json" >"$file"
}

# run_drift <settings> — emit the script's JSON; record its exit code in $RC.
run_drift() {
  OUT="$("$SCRIPT" --plugin-root "$PLUGIN" --settings "$1" --json 2>&1)"
  RC=$?
}

# count <reviewer> <field> — how many entries that reviewer has in that field.
count() {
  printf '%s' "$OUT" | jq --arg r "$1" --arg f "$2" \
    '[.reviewers[] | select(.reviewer == $r)][0][$f] | length'
}

PLUGIN="$BASE/plugin"
make_plugin "$PLUGIN"

echo "test-coreview-rule-drift:"

# --- 1. exact rules report nothing ----------------------------------------

make_settings "$BASE/clean.json" \
  "Bash(agy --sandbox --add-dir \"$REAL_DIR\" -p \"read $REAL_DIR/in.agy\" --model \"M1\")" \
  "Bash(agy models)" \
  "Bash(mkdir -p \"$REAL_DIR/neutral\")" \
  "Bash(devin -p --prompt-file \"$REAL_DIR/in.devin\" --permission-mode auto)"
run_drift "$BASE/clean.json"
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .drift)" = "false" ]; then
  pass "matching rules report no drift (exit 0)"
else
  fail "matching rules reported drift (rc=$RC): $OUT"
fi

# --- 2. a reordered flag is dead ------------------------------------------

make_settings "$BASE/reordered.json" \
  "Bash(agy --sandbox --add-dir \"$REAL_DIR\" --model \"M1\" -p \"read $REAL_DIR/in.agy\")" \
  "Bash(agy models)"
run_drift "$BASE/reordered.json"
if [ "$RC" -eq 1 ] && [ "$(count agy dead)" = "1" ] && [ "$(count agy missing)" = "1" ]; then
  pass "a reordered flag is dead, and its template is missing"
else
  fail "reordered flag misclassified (rc=$RC, dead=$(count agy dead), missing=$(count agy missing))"
fi

# --- 3. an off-machine path is not coverage -------------------------------
# A rule whose paths don't resolve here cannot fire here, so it must not count
# as covering its template — otherwise another host's rule masks the fact that
# THIS host has no working rule.

make_settings "$BASE/offmachine.json" \
  "Bash(agy --sandbox --add-dir \"$BASE/nope/deeper/inputs\" -p \"read $BASE/nope/deeper/inputs/in.agy\" --model \"M1\")" \
  "Bash(agy models)"
run_drift "$BASE/offmachine.json"
if [ "$RC" -eq 1 ] && [ "$(count agy offmachine)" = "1" ] && [ "$(count agy missing)" = "1" ]; then
  pass "an off-machine rule is not coverage; its template is still missing"
else
  fail "off-machine rule misclassified (rc=$RC, offmachine=$(count agy offmachine), missing=$(count agy missing))"
fi

# --- 3b. an off-machine rule is NOT drift on its own -----------------------
# The load-bearing case: a settings file shared across hosts with different
# usernames must carry BOTH hosts' rules. Every host then sees the other host's
# rules as unresolvable — correct, not drift. Counting them would cry wolf on
# every machine forever, and "fixing" one here breaks it there.

make_settings "$BASE/twohosts.json" \
  "Bash(agy --sandbox --add-dir \"$REAL_DIR\" -p \"read $REAL_DIR/in.agy\" --model \"M1\")" \
  "Bash(agy --sandbox --add-dir \"$BASE/other-host/inputs\" -p \"read $BASE/other-host/inputs/in.agy\" --model \"M1\")" \
  "Bash(agy models)"
run_drift "$BASE/twohosts.json"
if [ "$RC" -eq 0 ] && [ "$(count agy offmachine)" = "1" ] && [ "$(count agy missing)" = "0" ]; then
  pass "the other host's rule is reported but is not drift (exit 0)"
else
  fail "two-host config reported drift (rc=$RC, offmachine=$(count agy offmachine), missing=$(count agy missing))"
fi

# --- 4. one not-yet-created level is fine ----------------------------------
# `mkdir -p` and `cat >` each create the final component, so a single missing
# trailing level must NOT be flagged — otherwise a correct rule reads as broken
# on any machine that has not run a review yet.

make_settings "$BASE/notyet.json" \
  "Bash(agy --sandbox --add-dir \"$REAL_DIR/fresh\" -p \"read $REAL_DIR/in.agy\" --model \"M1\")" \
  "Bash(agy models)"
run_drift "$BASE/notyet.json"
if [ "$(count agy offmachine)" = "0" ] && [ "$(count agy missing)" = "0" ]; then
  pass "a single not-yet-created level is not off-machine"
else
  fail "not-yet-created path wrongly flagged (offmachine=$(count agy offmachine), missing=$(count agy missing))"
fi

# --- 4b. a deep, not-yet-created <NEUTRAL> is exempt -----------------------
# devin's dispatch creates <NEUTRAL> with `mkdir -p`, which makes EVERY missing
# parent, so any depth may legitimately be absent before a first run. And its
# only requirement is to be a dedicated empty directory, so even a mistyped one
# works — there is nothing to detect. A single global depth threshold reported
# this valid config as unusable, which made devin's template MISSING and warned.

make_settings "$BASE/deepneutral.json" \
  "Bash(mkdir -p \"$BASE/fresh/co-review/devin/cwd\")" \
  "Bash(devin -p --prompt-file \"$REAL_DIR/in.devin\" --permission-mode auto)"
run_drift "$BASE/deepneutral.json"
if [ "$RC" -eq 0 ] && [ "$(count devin offmachine)" = "0" ] && [ "$(count devin missing)" = "0" ]; then
  pass "a deep not-yet-created <NEUTRAL> is exempt, not off-machine"
else
  fail "deep <NEUTRAL> wrongly flagged (rc=$RC, offmachine=$(count devin offmachine), missing=$(count devin missing))"
fi

# --- 4c. <INPUT> is NOT exempt --------------------------------------------
# The exemption is per-placeholder, not a blanket relaxation: <INPUT> is opened
# with `cat >`, which cannot create directories, so a deep one is still unusable.

make_settings "$BASE/deepinput.json" \
  "Bash(mkdir -p \"$REAL_DIR/neutral\")" \
  "Bash(devin -p --prompt-file \"$BASE/nope/deeper/in.devin\" --permission-mode auto)"
run_drift "$BASE/deepinput.json"
if [ "$(count devin offmachine)" = "1" ] && [ "$(count devin missing)" = "1" ]; then
  pass "a deep <INPUT> is still off-machine (the exemption is per-placeholder)"
else
  fail "deep <INPUT> not flagged (offmachine=$(count devin offmachine), missing=$(count devin missing))"
fi

# --- 5. no rules at all is "not configured", not drift ---------------------

make_settings "$BASE/none.json" "Bash(git diff:*)"
run_drift "$BASE/none.json"
if [ "$RC" -eq 0 ] \
  && [ "$(printf '%s' "$OUT" | jq -r '[.reviewers[] | select(.configured)] | length')" = "0" ]; then
  pass "a reviewer with no rules is not configured, and not drift"
else
  fail "unconfigured reviewers reported as drift (rc=$RC): $OUT"
fi

# --- 5b. a machine with NO settings file reports, it does not fail ---------
# A fresh install has zero allow-rules, which is "not configured" — the answer
# the report already gives well. Exiting 2 would render in /doctor as "the check
# could not run", when it ran fine. Exit 2 stays for a file that exists but is
# unreadable or invalid, which test 8 covers for the plugin-root case.

run_drift "$BASE/does-not-exist.json"
if [ "$RC" -eq 0 ] \
  && [ "$(printf '%s' "$OUT" | jq -r '[.reviewers[] | select(.configured)] | length')" = "0" ]; then
  pass "no settings file at all reports not-configured (exit 0), not a failure"
else
  fail "absent settings file did not report cleanly (rc=$RC): $OUT"
fi

# --- 6. a general-purpose cd/mkdir rule is never called dead ---------------
# `Bash(cd "$(git rev-parse --show-toplevel)")` is ordinary shell config. It
# must neither satisfy devin's `<NEUTRAL>` template (the placeholder stands for
# an absolute path, not a command substitution) nor be reported as devin's dead
# rule.

make_settings "$BASE/generic.json" \
  "Bash(cd \"\$(git rev-parse --show-toplevel)\")" \
  "Bash(mkdir -p /some/other/place)" \
  "Bash(devin -p --prompt-file \"$REAL_DIR/in.devin\" --permission-mode auto)"
run_drift "$BASE/generic.json"
if [ "$(count devin dead)" = "0" ] && [ "$(count devin missing)" = "1" ]; then
  pass "a generic cd/mkdir rule is neither coverage nor a dead reviewer rule"
else
  fail "generic rule misattributed (dead=$(count devin dead), missing=$(count devin missing))"
fi

# --- 7. only a json fence holds templates ---------------------------------
# agy.md quotes a bogus rule twice: once in prose, once inside a `bash` fence.
# Reading either as a template would invent a rule the reviewer never ships and
# report it missing forever. Only the two inside the `json` fence count.

run_drift "$BASE/clean.json"
if [ "$(printf '%s' "$OUT" | jq -r '[.reviewers[] | select(.reviewer == "agy")][0].templates')" = "2" ]; then
  pass "only a json fence holds templates (prose and bash fences ignored)"
else
  fail "a non-json rule was counted as a template: $OUT"
fi

# --- 8. a bad plugin root exits 2, not 1 ----------------------------------
# Exit 1 means drift and exit 2 means the check could not run. Collapsing the
# two would let a mis-resolved plugin root read as a clean bill of health's
# opposite — or worse, as drift nobody can act on.

"$SCRIPT" --plugin-root "$BASE/not-a-plugin" --settings "$BASE/clean.json" --json >/dev/null 2>&1
if [ "$?" -eq 2 ]; then
  pass "a bad plugin root exits 2, distinct from drift's exit 1"
else
  fail "bad plugin root did not exit 2"
fi

# --- 9. the documented invocation runs with CLAUDE_PLUGIN_ROOT unset -------
# Every other case here hands the script a root explicitly, so none of them can
# see the failure #607 reported: the docs spell the root into the *path* via
# ${CLAUDE_PLUGIN_ROOT}, but that variable is never exported into the Bash
# subprocess, so the script's own env lookup found nothing and it exited 2 from
# inside the plugin root. The fix is that both documented sites also pass
# --plugin-root. This case pins the shape of that invocation, not just the
# script's behavior — an env-only form would regress silently again.

env -u CLAUDE_PLUGIN_ROOT "$SCRIPT" \
  --plugin-root "$PLUGIN" --settings "$BASE/clean.json" --json >/dev/null 2>&1
if [ "$?" -eq 0 ]; then
  pass "documented form (--plugin-root, CLAUDE_PLUGIN_ROOT unset) runs"
else
  fail "documented form failed with CLAUDE_PLUGIN_ROOT unset"
fi

# The bug itself: without the flag and without the env var, exit 2.
env -u CLAUDE_PLUGIN_ROOT "$SCRIPT" --settings "$BASE/clean.json" --json >/dev/null 2>&1
if [ "$?" -eq 2 ]; then
  pass "no flag and no env var still exits 2 (the #607 failure)"
else
  fail "rootless invocation did not exit 2"
fi

# And the docs must keep passing it. The script cannot enforce its own call
# sites, so assert them here — this is the half that actually stays fixed.
for doc in "$ROOT/skills/co-review/SKILL.md" "$ROOT/commands/doctor.md"; do
  if grep -q 'coreview-rule-drift\.py' "$doc" \
    && grep -q -- '--plugin-root "${CLAUDE_PLUGIN_ROOT}"' "$doc"; then
    pass "$(basename "$doc") passes --plugin-root"
  else
    fail "$(basename "$doc") invokes coreview-rule-drift.py without --plugin-root"
  fi
done

echo
if [ "$fails" -eq 0 ]; then
  echo "test-coreview-rule-drift: all checks passed"
  exit 0
fi
echo "test-coreview-rule-drift: $fails check(s) failed"
exit 1
