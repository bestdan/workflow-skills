#!/usr/bin/env bash
# test-research-spike.sh — fixture-based tests for scripts/research-spike.py's
# skeleton: the --root default, tree discovery, the fenced-block grammar, and
# the exit-code contract (0 clean / 1 tree-content violations / 2 caller
# errors).
#
# Every fixture tree is built under a temp dir (mktemp -d) and passed via
# --root, so no test ever reads the real repository. That matters more than
# usual for this script: its central assertion is "this path exists", and a
# test scanning the real tree would pass or fail on whatever happens to be
# checked in.
#
# Run directly: bash scripts/test-research-spike.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/research-spike.py"

BASE="$(mktemp -d 2>/dev/null || mktemp -d "$ROOT/.research-spike-test.XXXXXX")"
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

assert_exit() {
  # assert_exit <description> <actual> <expected>
  if [ "$2" -eq "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected exit $3, got $2)"
  fi
}

write_file() {
  # write_file <path> <contents>
  mkdir -p "$(dirname "$1")"
  printf '%s\n' "$2" >"$1"
}

# --- Fixture (a): an empty root is clean, not an error -------------------
DIR_A="$BASE/empty-root"
mkdir -p "$DIR_A"
out_a="$(python3 "$SCRIPT" --root "$DIR_A" validate 2>&1)"
exit_a=$?
assert_exit "empty root (no dev_docs/research) exits 0" "$exit_a" 0
if [ -z "$out_a" ]; then
  ok "empty root produces no output"
else
  bad "empty root should be silent, got: $out_a"
fi

# --- Fixture (b): a two-project tree discovers both and their tracks -----
DIR_B="$BASE/two-projects"
write_file "$DIR_B/dev_docs/research/alpha/PROJECT.md" "# alpha"
write_file "$DIR_B/dev_docs/research/alpha/tracks/account/questions.md" '# account

### Q1. Does the account need an isolated uid domain?

```question
id: uid-domain-isolation
status: open
blocks: account-provisioning
```'
write_file "$DIR_B/dev_docs/research/alpha/tracks/watcher/questions.md" '# watcher'
write_file "$DIR_B/dev_docs/research/beta/PROJECT.md" '# beta

```decision
id: stop-semantics
state: pending
```'

out_b="$(python3 "$SCRIPT" --root "$DIR_B" validate 2>&1)"
exit_b=$?
assert_exit "two-project tree exits 0" "$exit_b" 0
assert_contains "both projects counted" "$out_b" "2 projects, 2 tracks, 2 records"
assert_contains "alpha's tracks are discovered" "$out_b" "alpha — tracks: account, watcher"
assert_contains "a project with no tracks says so" "$out_b" "beta — tracks: none"

# --- Fixture (b2): --verbose locates records and qualifies their ids -----
out_b2="$(python3 "$SCRIPT" --root "$DIR_B" --verbose validate 2>&1)"
assert_contains "question record is located in its track and id-qualified" "$out_b2" \
  "question @ dev_docs/research/alpha/tracks/account/questions.md:5 [alpha/account] id=alpha/account/uid-domain-isolation"
# A decision qualifies project-wide even when filed inside a track, so
# promoting a proposed one into decisions.md cannot change its id.
assert_contains "project-level decision has no track and qualifies as project/id" "$out_b2" \
  "decision @ dev_docs/research/beta/PROJECT.md:3 [beta] id=beta/stop-semantics"

# --- Fixture (b3): question-section discovery ----------------------------
# The `### Q<n>.` convention `init` installs: a section ends at the next
# heading of the same level or shallower, and a heading written inside a
# fenced sample is not a heading. Task 4's coverage rule walks these.
DIR_B3="$BASE/sections"
write_file "$DIR_B3/dev_docs/research/alpha/tracks/account/questions.md" '# account

### Q1. Does the account need an isolated uid domain?

#### Evidence

Measured on the reference host.

### Q2. What does a trip do when it cannot get the registry lock?

```markdown
### Q99. A heading inside a fence is not a heading.
```

## Retired

### Q3. Should the ceiling be a cgroup?'
out_b3="$(python3 "$SCRIPT" --root "$DIR_B3" --verbose validate 2>&1)"
assert_contains "a sub-heading stays inside its question section" "$out_b3" \
  "section @ dev_docs/research/alpha/tracks/account/questions.md:3-8 Q1 'Does the account need an isolated uid domain?'"
assert_contains "a section ends at the next same-or-shallower heading" "$out_b3" \
  "section @ dev_docs/research/alpha/tracks/account/questions.md:9-14 Q2"
assert_contains "sections are found after a shallower heading too" "$out_b3" \
  "Q3 'Should the ceiling be a cgroup?'"
assert_not_contains "a heading inside a fenced sample is not a section" "$out_b3" "Q99"

# --- Fixture (c): the --root default is the cwd, not the plugin tree -----
# The regression guard for the plugin-vs-consumer default: an __file__-anchored
# default would scan this plugin's checkout (which has no dev_docs/research/)
# and report success — silently green.
out_c="$(cd "$DIR_B" && python3 "$SCRIPT" validate 2>&1)"
exit_c=$?
assert_exit "no --root: scan from inside a fixture tree exits 0" "$exit_c" 0
assert_contains "no --root: finds the cwd tree's projects" "$out_c" "alpha — tracks: account, watcher"
assert_contains "no --root: finds the cwd tree's second project" "$out_c" "beta — tracks: none"

# --- Fixture (c2): no --root from a tree-less dir stays silent -----------
DIR_C2="$BASE/no-research-dir"
mkdir -p "$DIR_C2"
out_c2="$(cd "$DIR_C2" && python3 "$SCRIPT" validate 2>&1)"
exit_c2=$?
assert_exit "no --root: a dir without dev_docs/research exits 0" "$exit_c2" 0
if [ -z "$out_c2" ]; then
  ok "no --root: a dir without dev_docs/research is silent (never scans the plugin tree)"
else
  bad "no --root: expected silence outside a research tree, got: $out_c2"
fi

# --- Fixture (d): an unknown key is an error naming the key --------------
DIR_D="$BASE/unknown-key"
write_file "$DIR_D/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
id: ceiling-receipt
owes: the receipt and its durability contract
desination: dev_docs/tasks/x.md
status: open
```'
out_d="$(python3 "$SCRIPT" --root "$DIR_D" validate 2>&1)"
exit_d=$?
assert_exit "unknown key exits 1 (a tree-content violation)" "$exit_d" 1
assert_contains "unknown key is an error naming the key" "$out_d" "unknown key 'desination'"
assert_contains "unknown key error is located" "$out_d" \
  "dev_docs/research/alpha/tracks/account/questions.md:6:"

# --- Fixture (e): grammar — no comment stripping, comma lists, `none` ----
DIR_E="$BASE/grammar"
write_file "$DIR_E/dev_docs/research/alpha/tracks/account/questions.md" '# account

```question
id: uid-domain-isolation
status: open # temporarily
blocks: account-provisioning, account-tooling
```

```obligation
id: keychain-invariant
owes: the keychain invariant, spelled out
blocking: none: it gates nothing, and probably never will
```'
out_e="$(python3 "$SCRIPT" --root "$DIR_E" --verbose validate 2>&1)"
assert_contains "an inline '#' is part of the value, not a comment" "$out_e" \
  "status = 'open # temporarily'"
assert_contains "a comma-separated list parses to multiple entries" "$out_e" \
  "blocks = 'account-provisioning, account-tooling' items=['account-provisioning', 'account-tooling']"
assert_contains "a 'none' declaration keeps its reason verbatim" "$out_e" \
  "blocking = none reason='it gates nothing, and probably never will'"
assert_not_contains "a 'none' declaration is not comma-split" "$out_e" \
  "blocking = none reason='it gates nothing'"
assert_contains "the first colon is the only split point" "$out_e" \
  "owes = 'the keychain invariant, spelled out'"

# --- Fixture (f): a malformed fence is an error, not a crash -------------
DIR_F="$BASE/malformed-fence"
write_file "$DIR_F/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
id: keychain-invariant
owes: the keychain invariant
destination: dev_docs/tasks/x.md
status: open'
out_f="$(python3 "$SCRIPT" --root "$DIR_F" validate 2>&1)"
exit_f=$?
assert_exit "an unterminated fence exits 1, not a crash" "$exit_f" 1
assert_contains "an unterminated fence is reported" "$out_f" "unterminated fenced block"
assert_not_contains "an unterminated fence does not traceback" "$out_f" "Traceback"

# --- Fixture (g): a nested doc example is not mistaken for a record ------
# The design and SKILL docs show record blocks inside four-backtick markdown
# fences; a parser that ignored fence run length would parse those samples as
# records, so every documentation example would become a validation failure.
DIR_G="$BASE/nested-fence"
write_file "$DIR_G/dev_docs/research/alpha/PROJECT.md" '# alpha

````markdown
```obligation
desination: this is a typo inside a documentation sample
```
````'
out_g="$(python3 "$SCRIPT" --root "$DIR_G" validate 2>&1)"
exit_g=$?
assert_exit "a record inside a four-backtick fenced sample is not parsed" "$exit_g" 0
assert_contains "the nested sample yields no records" "$out_g" "0 records"

# --- Fixture (h): a record outside any project directory is an error -----
DIR_H="$BASE/stray-record"
write_file "$DIR_H/dev_docs/research/README.md" '# research

```obligation
id: homeless
owes: something nobody counts
destination: dev_docs/tasks/x.md
status: open
```'
out_h="$(python3 "$SCRIPT" --root "$DIR_H" validate 2>&1)"
exit_h=$?
assert_exit "a record outside any project exits 1" "$exit_h" 1
assert_contains "a record outside any project is reported" "$out_h" \
  "outside any project directory"

# --- Fixture (i): the exit-code contract's caller-error half -------------
out_i="$(python3 "$SCRIPT" bogus 2>&1)"
exit_i=$?
assert_exit "an unknown subcommand exits 2" "$exit_i" 2
assert_contains "an unknown subcommand says so" "$out_i" "invalid choice: 'bogus'"

out_i2="$(python3 "$SCRIPT" --root "$BASE/does-not-exist" validate 2>&1)"
exit_i2=$?
assert_exit "a nonexistent --root exits 2 (a caller error)" "$exit_i2" 2
assert_contains "a nonexistent --root says so" "$out_i2" "is not a directory"

out_i3="$(python3 "$SCRIPT" --root "$DIR_A" ledger 2>&1)"
exit_i3=$?
assert_exit "an unimplemented verb exits 2, never a silent 0" "$exit_i3" 2
assert_contains "an unimplemented verb names the task that lands it" "$out_i3" "task 7"

# --- Fixture (j): --help lists all six subcommands -----------------------
out_j="$(python3 "$SCRIPT" --help 2>&1)"
for verb in init validate ledger write-ledger status suggest; do
  assert_contains "--help lists '$verb'" "$out_j" "$verb"
done

echo
echo "test-research-spike: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-research-spike: OK"
