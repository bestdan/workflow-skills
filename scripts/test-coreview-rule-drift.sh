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
#   - the shared rules are checked for coverage up to a whole shell token, one
#     diff source suffices, and a leftover plugin-cache prefix rule is DEAD
#     (issue #475)
#   - every plugin script co-review runs has an allowed-tools grant in its
#     SKILL.md, since no settings rule can approve one (issue #848)
#   - under --teardown, a teardown rule pinned to a stale plugin version is
#     DEAD, one naming the installed path is live, and no rule is not drift
#     (issue #954)
#
# Run directly: bash scripts/test-coreview-rule-drift.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/coreview-rule-drift.py"

command -v jq >/dev/null 2>&1 || {
  echo "test-coreview-rule-drift: jq is required but not found in PATH" >&2
  exit 2
}

# A bare `mktemp -d` ignores $TMPDIR on macOS (see scripts/lint-mktemp.sh), so
# use a $TMPDIR template, falling back to
# repo-local.
BASE="$(mktemp -d "${TMPDIR:-/tmp}/coreview-drift-test.XXXXXX" 2>/dev/null \
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

# run_drift_home <home> <settings> — same, under a controlled HOME, so a rule
# written with a literal `$HOME` expands into a tree this test owns.
run_drift_home() {
  OUT="$(HOME="$1" "$SCRIPT" --plugin-root "$PLUGIN" --settings "$2" --json 2>&1)"
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

# --- 4d. a `$HOME`-form rule is a substitution, and it resolves ------------
# The portable form: one rule for every machine, because neither the rule nor
# the command is expanded before the permission matcher compares them. It must
# be read as a substitution of the template, or it is DEAD on a config that
# works.
#
# What this case pins is the WIDENED PLACEHOLDER_VALUE, not the expansion: a
# `$HOME/…` string starts with neither `/` nor anything on disk, so the
# off-machine check skips it either way and `offmachine=0` proves nothing on its
# own. Case 4e is the one that fails without a real `expandvars`.

FAKE_HOME="$BASE/fakehome"
mkdir -p "$FAKE_HOME/co-review-input"

make_settings "$BASE/homeform.json" \
  'Bash(agy --sandbox --add-dir "$HOME/co-review-input" -p "read $HOME/co-review-input/in.agy" --model "M1")' \
  "Bash(agy models)"
run_drift_home "$FAKE_HOME" "$BASE/homeform.json"
if [ "$RC" -eq 0 ] && [ "$(count agy dead)" = "0" ] \
  && [ "$(count agy missing)" = "0" ] && [ "$(count agy offmachine)" = "0" ]; then
  pass "a \$HOME-form rule covers its template and resolves after expansion"
else
  fail "\$HOME-form rule misclassified (rc=$RC, dead=$(count agy dead), missing=$(count agy missing), offmachine=$(count agy offmachine))"
fi

# --- 4e. a `$HOME`-form rule pointing nowhere is still off-machine ---------
# Expanding narrows the check; it must not disable it. Under the same HOME, a
# path two-plus levels from anything on disk cannot be written or read here, so
# the rule cannot fire — exactly as for a `/`-rooted one.
#
# This is the discriminating case for `expandvars`: drop it and the unexpanded
# `$HOME/nope/…` string is skipped by the off-machine check, so the rule counts
# as LIVE coverage of a template it can never actually satisfy.

make_settings "$BASE/homeform-nope.json" \
  'Bash(agy --sandbox --add-dir "$HOME/nope/deeper/inputs" -p "read $HOME/nope/deeper/inputs/in.agy" --model "M1")' \
  "Bash(agy models)"
run_drift_home "$FAKE_HOME" "$BASE/homeform-nope.json"
if [ "$(count agy offmachine)" = "1" ] && [ "$(count agy missing)" = "1" ]; then
  pass "a \$HOME-form rule that resolves nowhere is still off-machine"
else
  fail "\$HOME-form off-machine rule not flagged (offmachine=$(count agy offmachine), missing=$(count agy missing))"
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
#
# Count, don't just match: two file-wide greps joined by && would pass a file
# that grew a SECOND, unflagged code block while the flag stayed in the first.
# Requiring every invocation to carry the flag within two following lines (the
# documented form wraps the flag onto the next line) is what makes a
# reintroduced bug fail, not merely a wholly removed flag.
#
# A `Bash(...)` line naming the script is a permission rule, not a call site —
# SKILL.md's allowed-tools front-matter self-grants the pre-flight — so it is
# excluded from the count. This mirrors coreview-rule-drift.py itself, which
# only treats a string inside a ```json fence as a rule.
for doc in "$ROOT/skills/co-review/SKILL.md" "$ROOT/commands/doctor.md"; do
  calls=$(grep 'coreview-rule-drift\.py' "$doc" | grep -vc 'Bash(')
  flagged=$(grep -A2 'coreview-rule-drift\.py' "$doc" \
    | grep -c -- '--plugin-root "${CLAUDE_PLUGIN_ROOT}"')
  if [ "$calls" -gt 0 ] && [ "$calls" -eq "$flagged" ]; then
    pass "$(basename "$doc"): all $calls invocation(s) pass --plugin-root"
  else
    fail "$(basename "$doc"): $flagged of $calls invocation(s) pass --plugin-root"
  fi
done

# --- 10. shared rules --------------------------------------------------------
# The shared rules live in references/permissions.md. They are prefix rules
# over a class of commands, so they are checked by asking whether a settings
# rule approves the command, matching up to a whole shell token as the
# permission matcher does. The fixture copies the real shared-rules file, so
# these cases check the shipped templates.

SPLUGIN="$BASE/splugin"
make_plugin "$SPLUGIN"
mkdir -p "$SPLUGIN/skills/co-review/references"
cp "$ROOT/skills/co-review/references/permissions.md" \
  "$SPLUGIN/skills/co-review/references/permissions.md"

SHARED_OK=(
  "Bash(cat:*)" "Bash(gh pr diff:*)" "Bash(gh pr view:*)" "Bash(git diff:*)"
  "Bash(git ls-remote:*)" "Bash(git status:*)"
)

run_shared() {
  OUT="$("$SCRIPT" --plugin-root "$SPLUGIN" --settings "$1" --json 2>&1)"
  RC=$?
}
shared_count() { printf '%s' "$OUT" | jq ".shared.$1 | length"; }

make_settings "$BASE/shared-ok.json" "${SHARED_OK[@]}"
run_shared "$BASE/shared-ok.json"
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r '.shared.templates')" = "6" ] \
  && [ "$(shared_count missing)" = "0" ] && [ "$(shared_count dead)" = "0" ]; then
  pass "the shipped shared rules cover every shared template"
else
  fail "shipped shared rules did not cover their templates (rc=$RC): $OUT"
fi

# A missing shared rule is drift once co-review is set up at all.
make_settings "$BASE/shared-missing.json" "${SHARED_OK[@]:0:5}"
run_shared "$BASE/shared-missing.json"
if [ "$RC" -eq 1 ] && [ "$(printf '%s' "$OUT" | jq -r '.shared.missing[0]')" = "Bash(git status:*)" ]; then
  pass "a missing shared rule is reported MISSING and is drift"
else
  fail "a missing shared rule was not reported (rc=$RC): $OUT"
fi

# One diff source suffices: permissions.md says to add only the one you use.
make_settings "$BASE/shared-onediff.json" \
  "Bash(cat:*)" "Bash(gh pr view:*)" "Bash(git diff:*)" \
  "Bash(git ls-remote:*)" "Bash(git status:*)"
run_shared "$BASE/shared-onediff.json"
if [ "$RC" -eq 0 ] && [ "$(shared_count missing)" = "0" ]; then
  pass "git diff alone satisfies the diff-source pair"
else
  fail "a single diff source reported missing (rc=$RC): $OUT"
fi

# With neither diff source covered, the text report names the pair once as
# alternatives, so the operator is not told to add both.
make_settings "$BASE/shared-nodiff.json" \
  "Bash(cat:*)" "Bash(gh pr view:*)" "Bash(git ls-remote:*)" "Bash(git status:*)"
TEXT="$("$SCRIPT" --plugin-root "$SPLUGIN" --settings "$BASE/shared-nodiff.json" 2>&1)"
if [ "$(grep -c '^  MISSING ' <<<"$TEXT")" = "1" ] \
  && grep -q 'one suffices' <<<"$TEXT" \
  && grep -q '^shared: 4/5 required rules covered' <<<"$TEXT"; then
  pass "an uncovered diff-source pair is reported once, as alternatives"
else
  fail "the diff-source pair was not reported as alternatives: $TEXT"
fi

# A broader rule approves the command too, so it is coverage. One ending
# mid-token approves nothing.
make_settings "$BASE/shared-broad.json" "Bash(agy models)" \
  "Bash(cat:*)" "Bash(gh:*)" "Bash(git *)"
run_shared "$BASE/shared-broad.json"
# The agy probe rule keeps this a configured machine; agy's own missing rule
# makes the exit code 1, so assert on the shared result directly.
if [ "$(printf '%s' "$OUT" | jq -r .shared.configured)" = "true" ] \
  && [ "$(shared_count missing)" = "0" ]; then
  pass "broader Bash(gh:*) and Bash(git *) rules cover the shared templates"
else
  fail "a broader rule was not counted as coverage (rc=$RC): $OUT"
fi
make_settings "$BASE/shared-midtoken.json" "${SHARED_OK[@]:0:5}" "Bash(git stat:*)"
run_shared "$BASE/shared-midtoken.json"
if [ "$RC" -eq 1 ] && [ "$(shared_count missing)" = "1" ]; then
  pass "a rule ending mid-token (Bash(git stat:*)) is not coverage"
else
  fail "a mid-token rule was counted as coverage (rc=$RC): $OUT"
fi

# The plugin-cache prefix rules permissions.md used to prescribe end inside the
# script's path token, so they never fired. Every spelling is DEAD, so the
# operator deletes it. Another plugin-path rule, such as a worktree teardown
# grant, belongs to no co-review template and is left alone.
make_settings "$BASE/shared-cache.json" "${SHARED_OK[@]}" \
  "Bash(/c/workflow-skills/workflow-skills/:*)" \
  "Bash(python3 /c/workflow-skills/workflow-skills/:*)" \
  "Bash(python3 \"/c/workflow-skills/workflow-skills/:*)" \
  "Bash(/c/workflow-skills/workflow-skills/*/scripts/worktree-remove.sh *)"
run_shared "$BASE/shared-cache.json"
if [ "$RC" -eq 1 ] && [ "$(shared_count dead)" = "3" ]; then
  pass "every plugin-cache prefix rule is DEAD; a teardown rule is not"
else
  fail "plugin-cache rules misclassified (rc=$RC, dead=$(shared_count dead)): $OUT"
fi

# No sign of co-review at all is "not configured", as for a reviewer.
make_settings "$BASE/shared-none.json" "Bash(ls:*)"
run_shared "$BASE/shared-none.json"
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .shared.configured)" = "false" ]; then
  pass "no shared or reviewer rule at all is not configured, and not drift"
else
  fail "unconfigured shared rules reported as drift (rc=$RC): $OUT"
fi

# A generic rule is common on machines that never ran co-review, so on its own
# it is no sign of setup.
make_settings "$BASE/shared-generic.json" "Bash(cat:*)" "Bash(git:*)"
run_shared "$BASE/shared-generic.json"
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .shared.configured)" = "false" ]; then
  pass "generic Bash(cat:*) and Bash(git:*) alone are not configured, and not drift"
else
  fail "generic rules alone reported as drift (rc=$RC): $OUT"
fi

# An exact rule approves only the bare command, and co-review always passes
# arguments (`git status --porcelain`), so it covers no shared template. The
# reviewer probe rule marks co-review as set up, making the gap drift.
make_settings "$BASE/shared-exact.json" "Bash(agy models)" \
  "Bash(cat)" "Bash(gh pr diff)" "Bash(gh pr view)" "Bash(git diff)" \
  "Bash(git ls-remote)" "Bash(git status)"
run_shared "$BASE/shared-exact.json"
if [ "$RC" -eq 1 ] && [ "$(shared_count missing)" = "6" ]; then
  pass "exact rules cover no shared template"
else
  fail "exact rules were counted as coverage (rc=$RC): $OUT"
fi

# A configured reviewer with no shared rule at all is set up, so every shared
# template is missing and that is drift.
make_settings "$BASE/shared-revonly.json" "Bash(agy models)"
run_shared "$BASE/shared-revonly.json"
if [ "$RC" -eq 1 ] && [ "$(printf '%s' "$OUT" | jq -r .shared.configured)" = "true" ] \
  && [ "$(shared_count missing)" = "6" ]; then
  pass "a configured reviewer with no shared rule reports every shared template"
else
  fail "a reviewer-only setup did not report the shared rules (rc=$RC): $OUT"
fi

# --- 11. every plugin script co-review runs is granted by the skill ---------
# No settings rule can approve a plugin script: the quoted path is one shell
# token carrying the version, and the matcher only compares whole tokens. So
# each invocation must have an `allowed-tools` entry in SKILL.md whose prefix
# is exactly the invocation's program and path. A script added without one is
# denied silently under `--non-interactive`.
#
# Every reference to a plugin script is extracted with whatever single word
# precedes it, quoted or not. An unquoted path or a `bash …` prefix is a form no
# grant can match, so it surfaces as "no grant" rather than slipping past. The
# `.sh|.py` anchor keeps prose placeholders like `scripts/<name>` out. Known
# limit: prose putting a bare word right before a quoted path fails loudly;
# backtick the path to fix it.

SKILL_MD="$ROOT/skills/co-review/SKILL.md"
invocations=$(grep -ohE \
  '([A-Za-z0-9_./-]+ )?"?\$\{CLAUDE_PLUGIN_ROOT\}/scripts/[A-Za-z0-9_.-]+\.(sh|py)"?' \
  "$SKILL_MD" "$ROOT"/skills/co-review/reviewers/*.md \
  "$ROOT"/skills/co-review/references/*.md | sort -u)
ungranted=0
while IFS= read -r inv; do
  [ -n "$inv" ] || continue
  if ! grep -qxF "  - Bash($inv:*)" "$SKILL_MD"; then
    fail "no allowed-tools grant in SKILL.md (unquoted, other interpreter, or missing) for: $inv"
    ungranted=$((ungranted + 1))
  fi
done <<EOF
$invocations
EOF
count=$(printf '%s\n' "$invocations" | grep -c .)
if [ "$ungranted" -eq 0 ] && [ "$count" -gt 0 ]; then
  pass "all $count plugin-script invocations have an allowed-tools grant"
fi

# --- 12. --teardown: live, stale-version, and absent teardown rules ---------
# A teardown rule names the full installed path, version directory included, so
# it dies at the next plugin update. The check reports a rule pinned to another
# version as DEAD, naming the installed one (issue #954).
TD_CACHE="$BASE/cache/workflow-skills/workflow-skills"
TD_PLUGIN="$TD_CACHE/2.0.0"
mkdir -p "$TD_PLUGIN/scripts"
touch "$TD_PLUGIN/scripts/worktree-remove.sh" "$TD_PLUGIN/scripts/branch-remove.sh"

run_teardown() {
  OUT="$("$SCRIPT" --plugin-root "$TD_PLUGIN" --settings "$1" --teardown --json 2>&1)"
  RC=$?
}
# td <script> <field> — how many entries that teardown script has in that field.
td() {
  printf '%s' "$OUT" | jq --arg s "$1" --arg f "$2" \
    '[.teardown.scripts[] | select(.script == $s)][0][$f] | if . == null then 0 elif type == "string" then 1 else length end'
}

make_settings "$BASE/td-live.json" \
  "Bash(\"$TD_PLUGIN/scripts/worktree-remove.sh\":*)" \
  "Bash(\"$TD_PLUGIN/scripts/branch-remove.sh\":*)"
run_teardown "$BASE/td-live.json"
if [ "$RC" -eq 0 ] && [ "$(td worktree-remove.sh live)" = "1" ] \
  && [ "$(td branch-remove.sh live)" = "1" ] && [ "$(td worktree-remove.sh missing)" = "0" ]; then
  pass "teardown: rules naming the installed path are live, no drift"
else
  fail "teardown: live rules were not recognised (rc=$RC): $OUT"
fi

make_settings "$BASE/td-stale.json" \
  "Bash(\"$TD_CACHE/1.9.0/scripts/worktree-remove.sh\":*)" \
  "Bash(\"$TD_PLUGIN/scripts/branch-remove.sh\":*)"
run_teardown "$BASE/td-stale.json"
if [ "$RC" -eq 1 ] && [ "$(td worktree-remove.sh dead)" = "1" ] \
  && [ "$(td worktree-remove.sh missing)" = "1" ] && [ "$(td branch-remove.sh dead)" = "0" ] \
  && [ "$(printf '%s' "$OUT" | jq -r '.teardown.scripts[0].dead[0].version')" = "1.9.0" ]; then
  pass "teardown: a rule pinned to a stale version is DEAD and its script MISSING"
else
  fail "teardown: a stale-version rule was not reported dead (rc=$RC): $OUT"
fi

# The human report names the installed version beside the dead rule, which is
# what the issue's user-run check reads.
report=$("$SCRIPT" --plugin-root "$TD_PLUGIN" --settings "$BASE/td-stale.json" --teardown 2>&1)
if grep -qF "pinned to 1.9.0; installed is 2.0.0" <<<"$report"; then
  pass "teardown: the report names the installed version beside a dead rule"
else
  fail "teardown: the report did not name the installed version: $report"
fi

# A `*` in the version segment was never measured to fire, so it is not
# coverage — but it is not dead either.
make_settings "$BASE/td-wild.json" \
  "Bash(/Users/*/.claude/plugins/cache/workflow-skills/workflow-skills/*/scripts/worktree-remove.sh *)"
run_teardown "$BASE/td-wild.json"
if [ "$RC" -eq 1 ] && [ "$(td worktree-remove.sh unverified)" = "1" ] \
  && [ "$(td worktree-remove.sh dead)" = "0" ] && [ "$(td worktree-remove.sh missing)" = "1" ]; then
  pass "teardown: a wildcard-version rule is UNVERIFIED, not coverage"
else
  fail "teardown: a wildcard-version rule was misclassified (rc=$RC): $OUT"
fi

make_settings "$BASE/td-none.json" "Bash(git status:*)"
run_teardown "$BASE/td-none.json"
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .teardown.configured)" = "false" ] \
  && [ "$(td worktree-remove.sh missing)" = "1" ] && [ "$(td branch-remove.sh missing)" = "1" ]; then
  pass "teardown: no rule is 'not configured' and names both rules to add, not drift"
else
  fail "teardown: the no-rule case was reported as drift or named nothing (rc=$RC): $OUT"
fi

# A checkout's copy of a teardown script is not the installed plugin's, so a
# rule naming it is never attributed — neither coverage nor dead.
make_settings "$BASE/td-checkout.json" \
  "Bash(\"$HOME/src/workflow-skills/scripts/worktree-remove.sh\":*)"
run_teardown "$BASE/td-checkout.json"
if [ "$RC" -eq 0 ] && [ "$(printf '%s' "$OUT" | jq -r .teardown.configured)" = "false" ]; then
  pass "teardown: a rule naming a checkout's script is not attributed"
else
  fail "teardown: a checkout-path rule was attributed (rc=$RC): $OUT"
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "test-coreview-rule-drift: all checks passed"
  exit 0
fi
echo "test-coreview-rule-drift: $fails check(s) failed"
exit 1
