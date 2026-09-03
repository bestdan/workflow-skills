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

# Bare `mktemp -d` (no template) ignores $TMPDIR on macOS, so the first arm
# isn't a real $TMPDIR attempt; try $TMPDIR explicitly before falling back to
# repo-local, where a sandboxed git init can't copy its hook templates.
BASE="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/validate-test.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.validate-test.XXXXXX")"
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
# add` calls the caller's repo/index. GIT_AUTHOR_*/GIT_COMMITTER_* outrank the
# gitconfig [user] pin above and are exported into hook and `git rebase -x`
# subprocesses. Unset the lot so no inherited env can redirect a fixture git op
# the same way the ceiling above blocks discovery. This is the explicit list,
# not `unset $(git rev-parse --local-env-vars)`: that dynamic form fails open —
# a missing or broken git yields empty output, the error is swallowed, and
# this line (whose whole purpose is to run before git is trusted) would
# silently grant zero isolation.
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT \
  GIT_OBJECT_DIRECTORY GIT_DIR GIT_WORK_TREE GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE \
  GIT_INDEX_FILE GIT_NO_REPLACE_OBJECTS GIT_REPLACE_REF_BASE GIT_PREFIX GIT_SHALLOW_FILE \
  GIT_COMMON_DIR GIT_TEMPLATE_DIR \
  GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_AUTHOR_DATE \
  GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE
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

make_plugin_fixture() {
  # make_plugin_fixture <dir> — the smallest plugin tree validate.py accepts
  # without unrelated errors. Copies validate.py into <dir>/scripts so ROOT
  # (Path(__file__).resolve().parent.parent) resolves to <dir> — this is how
  # the plugin-root-path check (which always runs against ROOT, unlike the
  # task-file checks above) gets exercised against a fixture instead of this
  # plugin's own tree. Callers add a single command md file under
  # <dir>/commands/ and must keep README.md's counts in sync with it.
  local dir="$1"
  mkdir -p "$dir/scripts" "$dir/skills" "$dir/agents" "$dir/dev_docs/tasks" \
    "$dir/commands" "$dir/.claude-plugin"
  cp "$SCRIPT" "$dir/scripts/validate.py"
  if [ -f "$ROOT/scripts/validate.py.lock" ]; then
    cp "$ROOT/scripts/validate.py.lock" "$dir/scripts/validate.py.lock"
  fi
  cat >"$dir/.claude-plugin/plugin.json" <<'JSON'
{
  "name": "fixture-plugin",
  "version": "1.0.0",
  "description": "fixture plugin for validate.py tests"
}
JSON
  cat >"$dir/.claude-plugin/marketplace.json" <<'JSON'
{
  "description": "fixture marketplace",
  "plugins": [
    { "name": "fixture-plugin", "version": "1.0.0", "description": "fixture plugin for validate.py tests" }
  ]
}
JSON
  cat >"$dir/README.md" <<'MD'
This fixture plugin has 0 skills, 1 command, and 0 subagents.
MD
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

# --- Fixture (e): ${CLAUDE_PLUGIN_ROOT}/<path> reference that resolves ------
DIR_E="$BASE/plugin-root-pass"
make_plugin_fixture "$DIR_E"
echo "#!/bin/sh" >"$DIR_E/scripts/real-thing.sh"
cat >"$DIR_E/commands/cmd.md" <<'MD'
---
description: fixture command
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/real-thing.sh` to do the thing.
MD

out_e="$(uv run "$DIR_E/scripts/validate.py" 2>&1)"
rc_e=$?
assert_not_contains "resolving plugin-root reference is not flagged" "$out_e" "does not exist"
if [ "$rc_e" -eq 0 ]; then
  ok "resolving plugin-root reference: exits 0"
else
  bad "resolving plugin-root reference: should exit 0, got $rc_e"
fi

# --- Fixture (f): stale ${CLAUDE_PLUGIN_ROOT}/<path> reference is flagged ---
# Also covers the three no-false-positive cases: a bare (unprefixed) script
# path, a placeholder with <angle-bracket> segments under a parent that does
# not exist, and a valid reference immediately followed by a sentence-ending
# period.
DIR_F="$BASE/plugin-root-fail"
make_plugin_fixture "$DIR_F"
echo "#!/bin/sh" >"$DIR_F/scripts/real-thing.sh"
cat >"$DIR_F/commands/cmd.md" <<'MD'
---
description: fixture command
---

Missing script: `${CLAUDE_PLUGIN_ROOT}/scripts/does-not-exist.sh`.

Bare reference (not flagged): scripts/something-absent.sh

Placeholder (not flagged): `${CLAUDE_PLUGIN_ROOT}/nonexistent/<name>.sh`

End of sentence (not flagged): see ${CLAUDE_PLUGIN_ROOT}/scripts/real-thing.sh.
MD

out_f="$(uv run "$DIR_F/scripts/validate.py" 2>&1)"
rc_f=$?
assert_contains "stale plugin-root reference names the file and line" "$out_f" \
  "cmd.md: line 5: \${CLAUDE_PLUGIN_ROOT}/scripts/does-not-exist.sh does not exist"
if [ "$rc_f" -eq 1 ]; then
  ok "stale plugin-root reference: exits 1"
else
  bad "stale plugin-root reference: should exit 1, got $rc_f"
fi
assert_not_contains "bare (unprefixed) script reference is not flagged" "$out_f" \
  "something-absent.sh does not exist"
# The placeholder sits under a parent that does NOT exist in the fixture, so a
# regex that truncated the match at "<" would report `nonexistent/` here. An
# assertion on the full "<name>.sh does not exist" string would pass whatever
# the code did, since that string can never be emitted.
assert_not_contains "placeholder under a missing parent is not flagged" "$out_f" \
  "nonexistent"
assert_not_contains "end-of-sentence reference is not flagged" "$out_f" \
  "real-thing.sh does not exist"

# --- Fixture (g): a fenced shell block carrying real logic is flagged ------
# Also covers the two shapes that must NOT be flagged: a guarded one-liner
# (one control-flow statement is still legible inline), and a prose "prompt
# payload" wearing a bash fence, which is where an unanchored keyword match
# produces false positives — repo-pr-execute.md scores 11 hits that way with
# zero lines of shell in it.
DIR_G="$BASE/shell-logic-fail"
make_plugin_fixture "$DIR_G"
cat >"$DIR_G/commands/cmd.md" <<'MD'
---
description: fixture command
---

Guarded one-liner (not flagged):

```bash
if [ -z "$REPO" ]; then REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); fi
```

A prompt payload (not flagged — English, not shell):

```bash
claude --remote "Read the task file.
if the acceptance criteria are unclear, ask.
for each file you touch, run the tests.
while the gate is red, keep going."
```

Real logic (flagged):

```bash
CURSOR=""
while :; do
  RESP=$(gh api graphql -f query="$Q" -F cursor="$CURSOR")
  for n in $(echo "$RESP" | jq -r '.data[]'); do
    echo "$n"
  done
  CURSOR=$(echo "$RESP" | jq -r '.pageInfo.endCursor')
done
```
MD

out_g="$(uv run "$DIR_G/scripts/validate.py" 2>&1)"
rc_g=$?
assert_contains "shell logic block names the file, line and count" "$out_g" \
  "cmd.md: line 22: fenced shell block carries 2 control-flow statements"
assert_contains "shell logic block points at the fix" "$out_g" \
  "move the logic to a typed file"
if [ "$rc_g" -eq 1 ]; then
  ok "fenced shell logic: exits 1"
else
  bad "fenced shell logic: should exit 1, got $rc_g"
fi
# Line 7 is the one-liner's block, line 13 the prompt payload's. Asserting on
# the line numbers is what distinguishes "the right block was flagged" from
# "something was flagged" — a check that flagged all three would still satisfy
# the assertions above.
assert_not_contains "guarded one-liner is not flagged" "$out_g" "cmd.md: line 7"
assert_not_contains "prose prompt payload is not flagged" "$out_g" "cmd.md: line 13"

# --- Fixture (h): a plugin with no fenced logic is clean -------------------
DIR_H="$BASE/shell-logic-pass"
make_plugin_fixture "$DIR_H"
cat >"$DIR_H/commands/cmd.md" <<'MD'
---
description: fixture command
---

Call the helper instead of inlining the loop:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/helper.py" --repo "<repo>"
```
MD
echo "#!/usr/bin/env python3" >"$DIR_H/scripts/helper.py"

out_h="$(uv run "$DIR_H/scripts/validate.py" 2>&1)"
rc_h=$?
assert_not_contains "a handler that shells out to a typed file is clean" "$out_h" \
  "control-flow statements"
if [ "$rc_h" -eq 0 ]; then
  ok "no fenced shell logic: exits 0"
else
  bad "no fenced shell logic: should exit 0, got $rc_h"
fi

# --- Default (no arg): still validates this plugin's own dev_docs/tasks --
# (preserves today's CI behavior — see validate.py module docstring)
out_default="$(uv run "$SCRIPT" 2>&1)"
assert_contains "no-arg default validates the plugin's own dev_docs/tasks" "$out_default" "autopilot_hardening"

echo
echo "test-validate: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-validate: OK"
