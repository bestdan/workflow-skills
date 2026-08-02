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
```

```obligation
none: nothing is owed until the option is chosen
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
assert_contains "both projects counted" "$out_b" "2 projects, 2 tracks, 3 records"
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

# --- Fixture (b2b): a non-decision outside a track has no qualified id ---
# `project/id` is the decision form. Handing it to a stray obligation would
# make it indistinguishable from a decision id — which is what task 5 resolves
# `blocks:`/`blocking:` against — so the rule declines to place it. It stays
# named in the dump: task 3 rejects the placement, and a dropped record is the
# invisible accrual this instrument exists to prevent.
DIR_B2B="$BASE/track-less-obligation"
write_file "$DIR_B2B/dev_docs/research/alpha/PROJECT.md" '# alpha

```obligation
id: homeless
owes: something filed outside any track
destination: dev_docs/tasks/x.md
status: open
```

```decision
id: stop-semantics
state: pending
```'
out_b2b="$(python3 "$SCRIPT" --root "$DIR_B2B" --verbose validate 2>&1)"
assert_contains "a track-less obligation is not given the decision id form" "$out_b2b" \
  "id=homeless (unplaceable: outside any track)"
assert_not_contains "a track-less obligation never qualifies as project/id" "$out_b2b" \
  "id=alpha/homeless"
assert_contains "a project-level decision still qualifies as project/id" "$out_b2b" \
  "id=alpha/stop-semantics"

# --- Fixture (b3): question-section discovery ----------------------------
# The `### Q<n>.` convention `init` installs: a section ends at the next
# heading of the same level or shallower, and a heading written inside a
# fenced sample is not a heading. The coverage rule walks these, so this
# prose-only tree also fails validation — deliberately not asserted here, where
# the subject is discovery; the rules themselves are fixture (n)'s.
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

# --- Fixture (e2): a bare `none:` block parses as a none declaration -----
# The coverage rule's explicit declaration (task 4). It must reach that rule in
# the same shape as the field-position sentinel, with its reason verbatim — a
# reason comma-split into "ids" is unusable, and two shapes for one concept is
# how tasks 3-5 end up reading only half of them.
DIR_E2="$BASE/bare-none"
write_file "$DIR_E2/dev_docs/research/alpha/tracks/account/questions.md" '# account

### Q1. Which option observes least?

```question
id: which-option-observes-least
status: open
blocks: none: it picks between options the decision already allows
```

```obligation
none: option (A) adds no observation, and owes no tooling
```'
out_e2="$(python3 "$SCRIPT" --root "$DIR_E2" --verbose validate 2>&1)"
exit_e2=$?
assert_exit "a bare 'none:' block is a valid record shape" "$exit_e2" 0
assert_contains "a bare 'none:' block keeps its reason verbatim" "$out_e2" \
  "none = none reason='option (A) adds no observation, and owes no tooling'"
# The tail of the comma-split form: quoted on its own, it can only be an item
# of a list. (A blanket "items=" assertion would now trip on the question
# block's own ordinary fields, which do render lists.)
assert_not_contains "a bare 'none:' reason is never comma-split" "$out_e2" \
  "'and owes no tooling'"

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

# --- Fixture (g2): an inline code span does not open a fence -------------
# CommonMark forbids backticks in a backtick fence's info string precisely so a
# paragraph opening with a code span stays a paragraph. Read as a fence, the
# line below opens one that closes on the *first real record's* closing fence
# — swallowing that record whole with no error and exit 0. Silent record loss
# is the failure this instrument exists to prevent, so it must not come from
# the parser: assert both records survive and the typo inside the first is
# still reported.
DIR_G2="$BASE/code-span"
write_file "$DIR_G2/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation``` blocks are opened next to the prose that creates them.

```obligation
id: first
desination: dev_docs/tasks/x.md
status: open
```

```obligation
id: second
owes: the second thing
status: open
```'
out_g2="$(python3 "$SCRIPT" --root "$DIR_G2" --verbose validate 2>&1)"
exit_g2=$?
assert_exit "a code-span line does not swallow the records after it" "$exit_g2" 1
assert_contains "the record a bogus fence would have swallowed is still checked" "$out_g2" \
  "unknown key 'desination'"
# The inventory prints on a failing run too, so the swallowed record can be
# seen surviving in the same output that reports the finding.
assert_contains "the inventory prints on a failing run" "$out_g2" \
  "id=alpha/account/second"
# Same shape without the typo, so the clean-run record count is visible.
DIR_G3="$BASE/code-span-clean"
write_file "$DIR_G3/dev_docs/tasks/x.md" '# a task card that exists'
write_file "$DIR_G3/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation``` blocks are opened next to the prose that creates them.

```obligation
id: first
owes: the first thing
destination: dev_docs/tasks/x.md
status: open
```

```obligation
id: second
owes: the second thing
destination: dev_docs/tasks/x.md
status: open
```'
out_g3="$(python3 "$SCRIPT" --root "$DIR_G3" --verbose validate 2>&1)"
exit_g3=$?
assert_exit "a code-span line leaves an otherwise-clean tree clean" "$exit_g3" 0
assert_contains "both records after a code-span line are parsed" "$out_g3" "2 records"
assert_contains "the second record survives the code-span line" "$out_g3" \
  "id=alpha/account/second"

# --- Fixture (g4): an undecodable file is a finding, not an abort --------
# Files are decoded as UTF-8 explicitly (the platform default is the ANSI
# codepage on Windows, and this script ships to consumers). A file that still
# will not decode must be reported and skipped: a traceback would exit 1 —
# which this script's contract reserves for tree-content violations — and
# would hide every file the scan never reached.
DIR_G4="$BASE/undecodable"
mkdir -p "$DIR_G4/dev_docs/research/alpha/tracks/account"
printf '# caf\xe9\n' >"$DIR_G4/dev_docs/research/alpha/PROJECT.md"
write_file "$DIR_G4/dev_docs/research/alpha/tracks/account/questions.md" '# account

```question
id: still-scanned
status: open
```'
out_g4="$(python3 "$SCRIPT" --root "$DIR_G4" validate 2>&1)"
exit_g4=$?
assert_exit "an undecodable file exits 1, not a crash" "$exit_g4" 1
assert_contains "an undecodable file is reported as a located finding" "$out_g4" \
  "dev_docs/research/alpha/PROJECT.md: cannot read:"
assert_not_contains "an undecodable file does not traceback" "$out_g4" "Traceback"
# A UTF-8 file in this repo's own prose style (an `owes:` line is "one line, in
# the author's own words", so em dashes and curly quotes are expected) must
# decode whatever the caller's locale. PYTHONUTF8=0 disables PEP 540's UTF-8
# mode, reproducing the platform-default decoding this script would otherwise
# get on Windows.
#
# PYTHONIOENCODING=utf-8 overrides *stdio* encoding only and leaves read_text's
# platform-default decoding as the variable under test. Without it this fixture
# is vacuous twice over: the run dies printing the script's own `—`/`⚠` glyphs
# before the assertion means anything, and the assertion itself can never trip,
# because read_md catches UnicodeDecodeError and formats it as `cannot read:
# {e}` — and str(UnicodeDecodeError) carries no class name. Proven by mutation:
# with `encoding="utf-8"` removed from read_md, the old form still passed.
# Assert on what actually moves — the exit code and the OK summary.
DIR_G5="$BASE/utf8-prose"
write_file "$DIR_G5/dev_docs/tasks/x.md" '# a task card that exists'
write_file "$DIR_G5/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
id: ceiling-receipt
owes: the receipt — and its durability contract
destination: dev_docs/tasks/x.md
status: open
```'
out_g5="$(LC_ALL=C LANG=C PYTHONUTF8=0 PYTHONIOENCODING=utf-8 python3 "$SCRIPT" --root "$DIR_G5" validate 2>&1)"
exit_g5=$?
assert_exit "UTF-8 prose decodes under a platform-default (ASCII) codec" "$exit_g5" 0
assert_contains "UTF-8 prose yields a clean scan" "$out_g5" "research-spike: OK"
assert_not_contains "UTF-8 prose is never reported as unreadable" "$out_g5" "cannot read:"

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

# validate's scoping surface is task 7's (`validate [<project>] [--track <t>]
# [--strict]`). Accepting the flags here while ignoring them would be worse
# than not having them: `--track mine` would silently scan every track and
# report OK. They must be rejected until their semantics land.
for opt in --track=account --strict --project=alpha; do
  out_i4="$(python3 "$SCRIPT" --root "$DIR_B" validate "$opt" 2>&1)"
  exit_i4=$?
  assert_exit "validate rejects '$opt' until task 7 lands its semantics" "$exit_i4" 2
  assert_contains "validate says why it rejected '$opt'" "$out_i4" "unrecognized arguments"
done

# --- Fixture (k): init scaffolds a project ------------------------------
DIR_K="$BASE/init"
mkdir -p "$DIR_K"
out_k="$(python3 "$SCRIPT" --root "$DIR_K" init foo 2>&1)"
exit_k=$?
assert_exit "init foo exits 0" "$exit_k" 0
K_PROJECT="$DIR_K/dev_docs/research/foo"
for path in PROJECT.md decisions.md LEDGER.md; do
  if [ -f "$K_PROJECT/$path" ]; then
    ok "init foo creates $path"
  else
    bad "init foo did not create $path"
  fi
done
assert_contains "init names what it created" "$out_k" "dev_docs/research/foo/PROJECT.md"
assert_contains "the scaffolded LEDGER.md carries the ledger markers" \
  "$(cat "$K_PROJECT/LEDGER.md")" "<!-- research-spike:ledger -->"
assert_contains "the scaffolded LEDGER.md carries zeroed roll-up lines" \
  "$(cat "$K_PROJECT/LEDGER.md")" "- **Questions:** 0 answered, 0 open, 0 retired"
assert_contains "decisions.md says it is organizer-owned" \
  "$(cat "$K_PROJECT/decisions.md")" "Organizer-owned"
assert_contains "decisions.md points track agents at 'proposed' in their own file" \
  "$(cat "$K_PROJECT/decisions.md")" "state: proposed"

# Bare init never overwrites; --track is how a project grows.
out_k2="$(python3 "$SCRIPT" --root "$DIR_K" init foo 2>&1)"
exit_k2=$?
assert_exit "a second bare 'init foo' exits 2" "$exit_k2" 2
assert_contains "the refusal names the existing path" "$out_k2" \
  "project already exists: dev_docs/research/foo"
assert_contains "the refusal points at the way to grow the project" "$out_k2" \
  "init foo --track"

python3 "$SCRIPT" --root "$DIR_K" init foo --track bar >/dev/null 2>&1
exit_k3=$?
assert_exit "init --track on an existing project succeeds" "$exit_k3" 0
K_BAR="$K_PROJECT/tracks/bar"
if [ -f "$K_BAR/questions.md" ]; then
  ok "init --track creates the track's questions.md"
else
  bad "init --track did not create questions.md"
fi
assert_contains "the track's questions.md carries the ledger markers" \
  "$(cat "$K_BAR/questions.md")" "<!-- research-spike:ledger -->"
assert_contains "the track's questions.md carries zeroed ledger lines" \
  "$(cat "$K_BAR/questions.md")" \
  "- **Obligations:** 0 discharged, 0 open (0 blocking, 0 stubs, 0 external)"
if [ -f "$K_BAR/obligations/.gitkeep" ]; then
  ok "init --track creates obligations/.gitkeep (an empty dir does not survive git)"
else
  bad "init --track did not create obligations/.gitkeep"
fi
# contracts/ is optional, and an eagerly created one would be an empty directory
# subject to the contracts coverage rule (task 4).
if [ -d "$K_BAR/contracts" ]; then
  bad "init --track must not create contracts/"
else
  ok "init --track does not create contracts/"
fi
assert_contains "the new track is added to PROJECT.md's index" \
  "$(cat "$K_PROJECT/PROJECT.md")" "- [bar](tracks/bar/questions.md)"
assert_contains "the roll-up gains a per-track section for the new track" \
  "$(cat "$K_PROJECT/LEDGER.md")" "### bar"

# A second track lands beside the first; a repeat of an existing one is refused.
bar_before="$(cat "$K_BAR/questions.md")"
python3 "$SCRIPT" --root "$DIR_K" init foo --track baz >/dev/null 2>&1
exit_k4=$?
assert_exit "a second --track on the same project succeeds" "$exit_k4" 0
if [ "$(cat "$K_BAR/questions.md")" = "$bar_before" ]; then
  ok "adding a second track leaves the first untouched"
else
  bad "adding a second track rewrote the first track's questions.md"
fi
assert_contains "the roll-up lists both tracks" "$(cat "$K_PROJECT/LEDGER.md")" "### baz"

out_k5="$(python3 "$SCRIPT" --root "$DIR_K" init foo --track bar 2>&1)"
exit_k5=$?
assert_exit "repeating an existing --track exits 2" "$exit_k5" 2
assert_contains "the track refusal names the existing path" "$out_k5" \
  "track already exists: dev_docs/research/foo/tracks/bar"

# --- Fixture (k2): a malformed name is rejected before any mkdir ---------
# The name becomes the `project/track/` id-qualification prefix, so a path
# separator or `..` would corrupt every qualified id derived from it — and
# `init ../outside` would scaffold outside the tree entirely.
DIR_K2="$BASE/init-names"
mkdir -p "$DIR_K2"
for name in ../outside /abs/path 'foo/bar' 'Foo' 'foo bar' '..'; do
  out_k6="$(python3 "$SCRIPT" --root "$DIR_K2" init "$name" 2>&1)"
  exit_k6=$?
  assert_exit "init '$name' exits 2" "$exit_k6" 2
  assert_contains "init '$name' says why" "$out_k6" "is not a valid id shape"
done
# Python's `$` also matches immediately before a final newline, so a
# `$`-anchored check accepted `foo\n` and scaffolded a directory whose name
# carries the whitespace the rule exists to forbid.
# $'...' keeps the newline; "$(printf ...)" would strip it and test nothing.
out_k6n="$(python3 "$SCRIPT" --root "$DIR_K2" init $'foo\n' 2>&1)"
exit_k6n=$?
assert_exit "a name with a trailing newline exits 2" "$exit_k6n" 2
assert_contains "a trailing-newline name says why" "$out_k6n" "is not a valid id shape"
for name in '..' 'a/b' 'Bad'; do
  out_k7="$(python3 "$SCRIPT" --root "$DIR_K2" init good --track "$name" 2>&1)"
  exit_k7=$?
  assert_exit "init good --track '$name' exits 2" "$exit_k7" 2
  assert_contains "init good --track '$name' says why" "$out_k7" "is not a valid id shape"
done
# Nothing above may have touched the filesystem — names are checked first.
if [ -e "$DIR_K2/dev_docs" ] || [ -e "$BASE/outside" ] || [ -e "$DIR_K2/../outside" ]; then
  bad "a rejected init created something on disk"
else
  ok "a rejected init creates no directory anywhere"
fi
# The bare form still needs a project name at all.
out_k8="$(python3 "$SCRIPT" --root "$DIR_K2" init 2>&1)"
exit_k8=$?
assert_exit "init with no project name exits 2" "$exit_k8" 2
assert_contains "init with no project name says which argument is missing" "$out_k8" \
  "the following arguments are required: project"

# --- Fixture (k3): a freshly-initialized tree passes its own gate --------
# An init that emits a tree failing `validate` is the worst possible first
# impression. This assertion is wired now and must keep passing as tasks 3-5
# land the rest of the rules.
DIR_K3="$BASE/init-clean"
mkdir -p "$DIR_K3"
python3 "$SCRIPT" --root "$DIR_K3" init demo --track account >/dev/null 2>&1
out_k9="$(python3 "$SCRIPT" --root "$DIR_K3" --verbose validate 2>&1)"
exit_k9=$?
assert_exit "a freshly-initialized tree passes validate" "$exit_k9" 0
assert_contains "the fresh tree reports its project and track" "$out_k9" \
  "demo — tracks: account"
# The scaffolded `### Q<n>.` worked example lives inside an HTML comment, so it
# installs the convention without registering as a question: a section there
# would fail task 4's coverage rule and make init emit a tree failing its own
# gate. Task 6's `status` asserts the same fact as a count.
assert_contains "the scaffolded worked example registers no records" "$out_k9" "0 records"
assert_not_contains "the scaffolded worked example registers no question section" \
  "$out_k9" "section @"
assert_contains "the worked example is present, not merely documented" \
  "$(cat "$DIR_K3/dev_docs/research/demo/tracks/account/questions.md")" \
  "### Q1. Does the account need an isolated uid domain?"

# --- Fixture (k4): an HTML comment is inert, and never swallows silently -
DIR_K4="$BASE/comments"
write_file "$DIR_K4/dev_docs/tasks/x.md" '# a task card that exists'
write_file "$DIR_K4/dev_docs/research/alpha/tracks/account/questions.md" '# account

<!--
### Q9. A heading inside a comment is not a heading.

```obligation
desination: a typo inside an inert example
```
-->

### Q1. A real one.

```question
id: a-real-one
status: open
blocks: stop-semantics
```

```obligation
id: real
owes: the real thing
destination: dev_docs/tasks/x.md
status: open
```'
out_k10="$(python3 "$SCRIPT" --root "$DIR_K4" --verbose validate 2>&1)"
exit_k10=$?
assert_exit "a commented-out example leaves an otherwise-clean tree clean" "$exit_k10" 0
assert_not_contains "a typo inside a comment is not a record" "$out_k10" "desination"
assert_not_contains "a heading inside a comment is not a section" "$out_k10" "Q9"
assert_contains "the record after the comment survives" "$out_k10" "id=alpha/account/real"
assert_contains "the section after the comment is still found" "$out_k10" "Q1 'A real one.'"

# An unterminated comment runs to end of file and makes every record after it
# inert — the same silent-loss failure as an unterminated fence, by a different
# door, so it is reported the same way.
DIR_K5="$BASE/unterminated-comment"
write_file "$DIR_K5/dev_docs/research/alpha/tracks/account/questions.md" '# account

<!-- an example nobody closed

```obligation
id: swallowed
owes: everything after this point
status: open
```'
out_k11="$(python3 "$SCRIPT" --root "$DIR_K5" validate 2>&1)"
exit_k11=$?
assert_exit "an unterminated comment exits 1, not a silent 0" "$exit_k11" 1
assert_contains "an unterminated comment is reported" "$out_k11" "unterminated HTML comment"
assert_not_contains "an unterminated comment does not traceback" "$out_k11" "Traceback"

# --- Fixture (k4b): an indented `<!--` is a code sample, not a comment ---
# CommonMark allows at most three spaces before an HTML block opener; at four
# it is an indented code block. Recognizing any indentation let prose that
# *shows* an opener in an indented sample open a real comment region and
# swallow every record and heading after it — reported, on top, as an
# unterminated comment in a file that had none.
DIR_K4B="$BASE/indented-comment"
write_file "$DIR_K4B/dev_docs/tasks/x.md" '# a task card that exists'
write_file "$DIR_K4B/dev_docs/research/alpha/tracks/account/questions.md" '# account

The convention wraps the example in an opener, shown here indented:

    <!-- the opener line, on its own

### Q2. A real question.

```question
id: a-real-question
status: open
blocks: stop-semantics
```

```obligation
id: first
owes: the real thing
destination: dev_docs/tasks/x.md
status: open
```'
out_k11b="$(python3 "$SCRIPT" --root "$DIR_K4B" --verbose validate 2>&1)"
exit_k11b=$?
assert_exit "an indented '<!--' sample does not swallow the file" "$exit_k11b" 0
assert_contains "the record after an indented '<!--' survives" "$out_k11b" \
  "id=alpha/account/first"
assert_contains "the section after an indented '<!--' survives" "$out_k11b" \
  "Q2 'A real question.'"
assert_not_contains "an indented '<!--' is not reported as unterminated" "$out_k11b" \
  "unterminated HTML comment"

# --- Fixture (k6): the generated tree survives dprint --------------------
# A generated block the formatter rewrites puts the freshness check and the
# formatter in a fight neither can win. Assert it against the repo's own
# dprint.json rather than by eye. dprint's exit codes distinguish the two
# outcomes that matter: 20 is "these files are not formatted", anything else
# non-zero is the tool failing to run at all (12 = plugins unresolvable, e.g.
# offline), which is a skip rather than a false pass.
if command -v dprint >/dev/null 2>&1; then
  # The glob must be **relative to the cwd** — dprint resolves an absolute
  # pattern against its config directory, finds nothing, and exits 14. That
  # form passed this fixture while checking zero files, so 14 is a failure
  # here, not a skip: a vacuous formatter assertion is worse than none.
  (cd "$DIR_K3" && dprint check --config "$ROOT/dprint.json" --incremental=false \
    "dev_docs/**/*.md") >"$BASE/dprint.out" 2>&1
  dprint_exit=$?
  case "$dprint_exit" in
    0) ok "dprint leaves the generated markdown untouched" ;;
    20) bad "dprint rewrites the generated markdown: $(cat "$BASE/dprint.out")" ;;
    12)
      # 12 is specifically "could not resolve a plugin" — a bare machine with
      # no network, which is the one case this fixture may skip so the harness
      # stays runnable there. CI installs dprint from mise and resolves the
      # pinned plugins, so this is never how the assertion passes in the gate.
      echo "  … skipped: dprint cannot resolve its plugins (offline?): $(head -1 "$BASE/dprint.out")"
      ;;
    *)
      # Every other status is dprint being handed something it could not do —
      # a bad config, a bad invocation, no matching files (14), a crash. Those
      # must fail: a formatter assertion that "passes" without reading a
      # generated file is worse than not having one, and the absolute-glob
      # form of this very fixture did exactly that.
      bad "dprint failed to run the check (exit $dprint_exit): $(head -1 "$BASE/dprint.out")"
      ;;
  esac
else
  echo "  … skipped: dprint is not on PATH — install it via mise (see CONTRIBUTING.md)"
fi

# --- Fixture (k7): the fresh tree is ledger-fresh, not marker-bearing ----
# `write-ledger` over a just-initialized tree must produce **no diff**: init
# emits exactly what the derivation emits for an empty project, so a fresh
# track is not born stale (task 7 makes a stale stored ledger an error). The
# guard self-activates the moment task 7 lands the verb.
DIR_K7="$BASE/ledger-fresh"
mkdir -p "$DIR_K7"
python3 "$SCRIPT" --root "$DIR_K7" init demo --track account >/dev/null 2>&1
cp -r "$DIR_K7/dev_docs" "$BASE/ledger-fresh-before"
out_k12="$(python3 "$SCRIPT" --root "$DIR_K7" write-ledger 2>&1)"
exit_k12=$?
if [ "$exit_k12" -eq 2 ] && [ "${out_k12#*not implemented}" != "$out_k12" ]; then
  echo "  … deferred: write-ledger lands in task 7 — the no-diff round-trip is asserted there"
else
  assert_exit "write-ledger over a freshly-initialized tree exits 0" "$exit_k12" 0
  if diff -r "$BASE/ledger-fresh-before" "$DIR_K7/dev_docs" >"$BASE/ledger.diff" 2>&1; then
    ok "a freshly-initialized tree is ledger-fresh (write-ledger produces no diff)"
  else
    bad "init emits a stale ledger: $(cat "$BASE/ledger.diff")"
  fi
fi

# --- Fixture (k8): adding a track never zeroes a stored roll-up ----------
# Re-rendering the whole block from zero counts erased every other track's
# stored numbers — silently, since nothing flags a roll-up that agrees with a
# derivation nobody ran. The insert is surgical instead: an empty track
# contributes zero to every total, so the stored numbers and the decisions
# section must come through untouched.
DIR_K8="$BASE/rollup-preserved"
mkdir -p "$DIR_K8"
python3 "$SCRIPT" --root "$DIR_K8" init demo --track account >/dev/null 2>&1
K8_LEDGER="$DIR_K8/dev_docs/research/demo/LEDGER.md"
# Stand in for what task 7's write-ledger leaves once the project has records.
python3 - "$K8_LEDGER" <<'PY'
import sys
from pathlib import Path

p = Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
t = t.replace("_None yet._", "- **stop-semantics** — BLOCKED by 2 questions")
t = t.replace(
  "- **Questions:** 0 answered, 0 open, 0 retired\n"
  "- **Obligations:** 0 discharged, 0 open (0 blocking, 0 stubs, 0 external)",
  "- **Questions:** 8 answered, 4 open, 1 retired\n"
  "- **Obligations:** 3 discharged, 10 open (2 blocking, 1 stub, 1 external)",
)
p.write_text(t, encoding="utf-8")
PY
python3 "$SCRIPT" --root "$DIR_K8" init demo --track zebra >/dev/null 2>&1
k8_out="$(cat "$K8_LEDGER")"
assert_contains "an existing track's stored numbers survive adding a track" "$k8_out" \
  "- **Questions:** 8 answered, 4 open, 1 retired"
assert_contains "the stored obligation subtotals survive too" "$k8_out" \
  "- **Obligations:** 3 discharged, 10 open (2 blocking, 1 stub, 1 external)"
assert_contains "the generated decisions section survives" "$k8_out" \
  "- **stop-semantics** — BLOCKED by 2 questions"
assert_contains "the new track is added with zero counts" "$k8_out" "### zebra"
assert_not_contains "the roll-up placeholder is gone once a track exists" "$k8_out" \
  "_No tracks yet._"

# The inserted form must be byte-identical to the fully-rendered form, or a
# tree grown one track at a time reads as stale the moment write-ledger runs.
DIR_K8B="$BASE/insert-equals-render"
mkdir -p "$DIR_K8B"
python3 "$SCRIPT" --root "$DIR_K8B" init one --track alpha >/dev/null 2>&1
python3 "$SCRIPT" --root "$DIR_K8B" init two >/dev/null 2>&1
python3 "$SCRIPT" --root "$DIR_K8B" init two --track alpha >/dev/null 2>&1
ledger_block() {
  sed -n '/research-spike:ledger -->/,/\/research-spike:ledger/p' "$1"
}
if [ "$(ledger_block "$DIR_K8B/dev_docs/research/one/LEDGER.md")" \
  = "$(ledger_block "$DIR_K8B/dev_docs/research/two/LEDGER.md")" ]; then
  ok "a track inserted into an existing roll-up matches the fully-rendered form"
else
  bad "inserting a track produces a different roll-up than rendering one"
fi

# The same equality holds for PROJECT.md's tracks index: an index grown by
# index_track must be byte-identical to the freshly-rendered one (modulo the
# project's own name), or the grown form drifts into a shape init never writes.
tracks_index() {
  # tracks_index <PROJECT.md> <project-name>
  sed -n '/^## Tracks$/,$p' "$1" | sed "s/init $2 /init PROJ /"
}
if [ "$(tracks_index "$DIR_K8B/dev_docs/research/one/PROJECT.md" one)" \
  = "$(tracks_index "$DIR_K8B/dev_docs/research/two/PROJECT.md" two)" ]; then
  ok "a track indexed into an existing PROJECT.md matches the fully-rendered form"
else
  bad "indexing a track produces a different PROJECT.md index than rendering one"
fi
# Entries stay adjacent — a blank line spliced between them would be a loose
# list the fresh render never emits.
python3 "$SCRIPT" --root "$DIR_K8B" init one --track beta >/dev/null 2>&1
assert_contains "a grown tracks index keeps its entries adjacent" \
  "$(cat "$DIR_K8B/dev_docs/research/one/PROJECT.md")" \
  '- [alpha](tracks/alpha/questions.md)
- [beta](tracks/beta/questions.md)'

# --- Fixture (k9): a track is never left half-made -----------------------
# The track used to be written before PROJECT.md and LEDGER.md were read, so a
# roll-up with no markers left the track behind and exited 2 — and the retry
# then failed on "track already exists", stranding the caller.
DIR_K9="$BASE/preflight"
mkdir -p "$DIR_K9"
python3 "$SCRIPT" --root "$DIR_K9" init demo >/dev/null 2>&1
K9_PROJECT="$DIR_K9/dev_docs/research/demo"
grep -v 'research-spike:ledger' "$K9_PROJECT/LEDGER.md" >"$K9_PROJECT/LEDGER.tmp"
mv "$K9_PROJECT/LEDGER.tmp" "$K9_PROJECT/LEDGER.md"
out_k13="$(python3 "$SCRIPT" --root "$DIR_K9" init demo --track bar 2>&1)"
exit_k13=$?
assert_exit "a marker-less LEDGER.md makes init exit 2" "$exit_k13" 2
assert_contains "the refusal names the missing markers" "$out_k13" "carries no ledger markers"
if [ -e "$K9_PROJECT/tracks/bar" ]; then
  bad "a refused init left a half-made track behind"
else
  ok "a refused init creates no track (the retry is not blocked by its own debris)"
fi

# The same holds for an unreadable PROJECT.md, which used to traceback.
DIR_K10="$BASE/preflight-project-md"
mkdir -p "$DIR_K10"
python3 "$SCRIPT" --root "$DIR_K10" init demo >/dev/null 2>&1
rm "$DIR_K10/dev_docs/research/demo/PROJECT.md"
out_k14="$(python3 "$SCRIPT" --root "$DIR_K10" init demo --track bar 2>&1)"
exit_k14=$?
assert_exit "a missing PROJECT.md makes init exit 2, not traceback" "$exit_k14" 2
assert_not_contains "a missing PROJECT.md does not traceback" "$out_k14" "Traceback"
if [ -e "$DIR_K10/dev_docs/research/demo/tracks/bar" ]; then
  bad "a refused init left a half-made track behind"
else
  ok "a missing PROJECT.md leaves no half-made track either"
fi

# --- Fixture (l): the destination rules ----------------------------------
# The load-bearing rule of the instrument: a deferral stayed visible in the
# origin repo exactly when its destination was a file that already existed.
# One fixture per rule, and each asserts the *reason* in the message as well
# as the exit code — the message is part of the mechanism, so a rule that
# fires with an unreadable explanation is only half-landed.

obligation_fixture() {
  # obligation_fixture <dir> <destination>
  # One track, one obligation, and one real file to point at.
  mkdir -p "$1/dev_docs/tasks" "$1/dev_docs/research/alpha/tracks/account"
  printf '# a task card that exists\n' >"$1/dev_docs/tasks/real_task.md"
  printf '# account\n\n```obligation\nid: keychain-invariant\nowes: the keychain invariant, spelled out\ndestination: %s\nstatus: open\n```\n' \
    "$2" >"$1/dev_docs/research/alpha/tracks/account/questions.md"
}

DIR_L1="$BASE/dest-valid"
obligation_fixture "$DIR_L1" "dev_docs/tasks/real_task.md"
out_l1="$(python3 "$SCRIPT" --root "$DIR_L1" validate 2>&1)"
exit_l1=$?
assert_exit "a destination pointing at an existing regular file passes" "$exit_l1" 0
assert_contains "the valid tree reports OK" "$out_l1" "research-spike: OK"

DIR_L2="$BASE/dest-missing"
obligation_fixture "$DIR_L2" "dev_docs/tasks/never_written.md"
out_l2="$(python3 "$SCRIPT" --root "$DIR_L2" validate 2>&1)"
exit_l2=$?
assert_exit "a nonexistent destination exits 1" "$exit_l2" 1
assert_contains "a nonexistent destination is named" "$out_l2" \
  "destination 'dev_docs/tasks/never_written.md' does not exist"
assert_contains "the message says why it matters, not just what broke" "$out_l2" \
  "this is how deferred work goes dark"
assert_contains "the message says what to do instead" "$out_l2" \
  "stub card under tracks/<track>/obligations/"

# The one non-fatal destination rule. Pointing at an in-flight plan is
# legitimate; the warning is what stops the pointer outliving the folder,
# since /push-plan deletes plan directories after tracker migration.
DIR_L3="$BASE/dest-plan-dir"
obligation_fixture "$DIR_L3" "dev_docs/tasks/foo_plan/foo_plan.md"
write_file "$DIR_L3/dev_docs/tasks/foo_plan/foo_plan.md" '# foo plan'
out_l3="$(python3 "$SCRIPT" --root "$DIR_L3" validate 2>&1)"
exit_l3=$?
assert_exit "a destination under a *_plan/ directory still exits 0" "$exit_l3" 0
assert_contains "a plan-directory destination warns" "$out_l3" "is inside a plan directory"
assert_contains "the plan-directory warning names the deletion hazard" "$out_l3" "/push-plan"
assert_contains "the plan-directory warning points at the receipt-card route" "$out_l3" \
  "receipt card under tracks/<track>/obligations/"
assert_contains "a warning does not fail the run" "$out_l3" "research-spike: OK"

DIR_L4="$BASE/dest-directory"
obligation_fixture "$DIR_L4" "dev_docs/tasks"
out_l4="$(python3 "$SCRIPT" --root "$DIR_L4" validate 2>&1)"
exit_l4=$?
assert_exit "a directory destination exits 1" "$exit_l4" 1
assert_contains "a directory destination says a directory says nothing" "$out_l4" \
  "a directory can exist while saying nothing about the work"
assert_contains "a directory destination states the fix" "$out_l4" \
  "point at a specific file that already exists"

DIR_L5="$BASE/dest-absolute"
obligation_fixture "$DIR_L5" "/etc/hosts"
out_l5="$(python3 "$SCRIPT" --root "$DIR_L5" validate 2>&1)"
exit_l5=$?
assert_exit "an absolute destination exits 1" "$exit_l5" 1
assert_contains "an absolute destination says why repo-relative matters" "$out_l5" \
  "is an absolute path"

DIR_L6="$BASE/dest-traversal"
obligation_fixture "$DIR_L6" "../outside.md"
printf '# outside the root\n' >"$BASE/outside.md"
out_l6="$(python3 "$SCRIPT" --root "$DIR_L6" validate 2>&1)"
exit_l6=$?
assert_exit "a '../' traversal destination exits 1 even when the target exists" "$exit_l6" 1
assert_contains "a traversal destination says it escapes the repository" "$out_l6" \
  "escapes the repository with '../'"

# A symlink is the other door out of the tree, and the lexical check cannot see
# it: resolve, then re-check containment.
DIR_L7="$BASE/dest-symlink"
obligation_fixture "$DIR_L7" "dev_docs/tasks/escape.md"
printf '# somewhere else entirely\n' >"$BASE/symlink-target.md"
ln -s "$BASE/symlink-target.md" "$DIR_L7/dev_docs/tasks/escape.md"
out_l7="$(python3 "$SCRIPT" --root "$DIR_L7" validate 2>&1)"
exit_l7=$?
assert_exit "a symlink pointing outside the root exits 1" "$exit_l7" 1
assert_contains "a symlink escape is reported as one" "$out_l7" \
  "resolves through a symlink"
assert_contains "a symlink escape says no clone contains the file" "$out_l7" \
  "outside the repository"

DIR_L8="$BASE/dest-cross-project"
obligation_fixture "$DIR_L8" "dev_docs/research/beta/tracks/x/obligations/handoff.md"
write_file "$DIR_L8/dev_docs/research/beta/tracks/x/obligations/handoff.md" '# handoff

```card
kind: stub
superseded_when: the x track files its own measurement card
```'
out_l8="$(python3 "$SCRIPT" --root "$DIR_L8" validate 2>&1)"
exit_l8=$?
assert_exit "a destination inside a sibling project exits 1" "$exit_l8" 1
assert_contains "a cross-project destination names the other project" "$out_l8" \
  "lands in another research project ('beta')"
assert_contains "a cross-project destination names the receipt-card route" "$out_l8" \
  "receipt card under tracks/<track>/obligations/"

DIR_L9="$BASE/dest-absent"
mkdir -p "$DIR_L9/dev_docs/research/alpha/tracks/account"
write_file "$DIR_L9/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
id: keychain-invariant
owes: the keychain invariant, spelled out
status: open
```'
out_l9="$(python3 "$SCRIPT" --root "$DIR_L9" validate 2>&1)"
exit_l9=$?
assert_exit "an obligation with no destination at all exits 1" "$exit_l9" 1
assert_contains "the missing-destination error says why the field exists" "$out_l9" \
  "'destination:' is required"
assert_contains "the missing-destination error names the accrual it prevents" "$out_l9" \
  "accrues invisibly"

# --- Fixture (l2): ids are kebab-case and unique once qualified ----------
# Across two files, because two files each internally consistent is exactly how
# the reference implementation ended up with an ambiguous reference.
DIR_L10="$BASE/duplicate-ids"
write_file "$DIR_L10/dev_docs/tasks/x.md" '# a task card that exists'
write_file "$DIR_L10/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
id: keychain-invariant
owes: the first claim on the name
destination: dev_docs/tasks/x.md
status: open
```'
write_file "$DIR_L10/dev_docs/research/alpha/tracks/account/contracts/keychain.md" '# keychain

```obligation
id: keychain-invariant
owes: the second claim on the same name
destination: dev_docs/tasks/x.md
status: open
```'
out_l10="$(python3 "$SCRIPT" --root "$DIR_L10" validate 2>&1)"
exit_l10=$?
assert_exit "a duplicate id across two files exits 1" "$exit_l10" 1
assert_contains "the duplicate is reported by its qualified id" "$out_l10" \
  "duplicate obligation id 'alpha/account/keychain-invariant'"
# Both source locations, because a duplicate reported at one end is a report
# the reader has to go looking for the other half of. Files are walked in
# sorted order, so contracts/keychain.md is the first declaration here and
# questions.md is where the finding lands.
assert_contains "the duplicate error locates the second declaration" "$out_l10" \
  "dev_docs/research/alpha/tracks/account/questions.md:4:"
assert_contains "the duplicate error also names the first declaration" "$out_l10" \
  "already declared at dev_docs/research/alpha/tracks/account/contracts/keychain.md:4"

# Qualification is what prevents the collision, so the same bare id in two
# tracks is not one.
DIR_L11="$BASE/same-id-two-tracks"
write_file "$DIR_L11/dev_docs/tasks/x.md" '# a task card that exists'
for track in account watcher; do
  write_file "$DIR_L11/dev_docs/research/alpha/tracks/$track/questions.md" "# $track

\`\`\`obligation
id: keychain-invariant
owes: the keychain invariant for this track
destination: dev_docs/tasks/x.md
status: open
\`\`\`"
done
out_l11="$(python3 "$SCRIPT" --root "$DIR_L11" validate 2>&1)"
exit_l11=$?
assert_exit "the same bare id in two tracks is accepted" "$exit_l11" 0
assert_not_contains "no duplicate is reported for two differently-qualified ids" \
  "$out_l11" "duplicate obligation id"

DIR_L12="$BASE/bad-id-shape"
mkdir -p "$DIR_L12/dev_docs/research/alpha/tracks/account"
write_file "$DIR_L12/dev_docs/tasks/x.md" '# a task card that exists'
write_file "$DIR_L12/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
id: Keychain_Invariant
owes: the keychain invariant
destination: dev_docs/tasks/x.md
status: open
```'
out_l12="$(python3 "$SCRIPT" --root "$DIR_L12" validate 2>&1)"
exit_l12=$?
assert_exit "a non-kebab id exits 1" "$exit_l12" 1
assert_contains "a non-kebab id says what the shape is" "$out_l12" "is not a valid id shape"

# --- Fixture (l3): status, discharged_by, and owes -----------------------
status_fixture() {
  # status_fixture <dir> <extra block lines>
  mkdir -p "$1/dev_docs/tasks" "$1/dev_docs/research/alpha/tracks/account"
  printf '# a task card that exists\n' >"$1/dev_docs/tasks/real_task.md"
  printf '# account\n\n```obligation\nid: keychain-invariant\nowes: the keychain invariant, spelled out\ndestination: dev_docs/tasks/real_task.md\n%s\n```\n' \
    "$2" >"$1/dev_docs/research/alpha/tracks/account/questions.md"
}

DIR_L13="$BASE/discharged-no-by"
status_fixture "$DIR_L13" "status: discharged"
out_l13="$(python3 "$SCRIPT" --root "$DIR_L13" validate 2>&1)"
exit_l13=$?
assert_exit "discharged without discharged_by exits 1" "$exit_l13" 1
assert_contains "the discharged_by error says the claim is uncheckable without it" \
  "$out_l13" "nobody can check"

DIR_L14="$BASE/open-with-by"
status_fixture "$DIR_L14" "status: open
discharged_by: https://github.com/o/r/pull/1"
out_l14="$(python3 "$SCRIPT" --root "$DIR_L14" validate 2>&1)"
exit_l14=$?
assert_exit "discharged_by while open exits 1" "$exit_l14" 1
assert_contains "the open+discharged_by error says one of the two is wrong" "$out_l14" \
  "is set while status is open"

# discharged_by is free text and deliberately never path-checked — the
# discharging artifact almost always lives outside the tree.
DIR_L15="$BASE/discharged-by-url"
status_fixture "$DIR_L15" "status: discharged
discharged_by: https://github.com/o/r/pull/1234"
out_l15="$(python3 "$SCRIPT" --root "$DIR_L15" validate 2>&1)"
exit_l15=$?
assert_exit "a PR URL in discharged_by passes (it is never path-checked)" "$exit_l15" 0
assert_not_contains "discharged_by is never reported as a missing path" "$out_l15" \
  "does not exist"

DIR_L16="$BASE/bad-status"
status_fixture "$DIR_L16" "status: in-progress"
out_l16="$(python3 "$SCRIPT" --root "$DIR_L16" validate 2>&1)"
exit_l16=$?
assert_exit "a status outside open|discharged exits 1" "$exit_l16" 1
assert_contains "the status error says why there is no in-between" "$out_l16" \
  "no in-between state"

DIR_L17="$BASE/no-owes"
mkdir -p "$DIR_L17/dev_docs/research/alpha/tracks/account"
write_file "$DIR_L17/dev_docs/tasks/x.md" '# a task card that exists'
write_file "$DIR_L17/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
id: keychain-invariant
destination: dev_docs/tasks/x.md
status: open
```'
out_l17="$(python3 "$SCRIPT" --root "$DIR_L17" validate 2>&1)"
exit_l17=$?
assert_exit "an obligation with no owes exits 1" "$exit_l17" 1
assert_contains "the owes error says an address with no letter in it" "$out_l17" \
  "an address with no letter in it"

# `blocking:` is accepted and recorded here; whether it names a real decision
# is task 5's referential check, so it must not fail on its own yet.
DIR_L18="$BASE/blocking-accepted"
status_fixture "$DIR_L18" "status: open
blocking: stop-semantics"
python3 "$SCRIPT" --root "$DIR_L18" validate >/dev/null 2>&1
exit_l18=$?
assert_exit "an obligation carrying blocking: is accepted" "$exit_l18" 0
out_l18v="$(python3 "$SCRIPT" --root "$DIR_L18" --verbose validate 2>&1)"
assert_contains "blocking: is recorded for task 5's referential check" "$out_l18v" \
  "blocking = 'stop-semantics'"

# The unknown-key rule (fixture (d)) is what catches a `desination:` typo: the
# constraint must not be silently dropped because the key was misspelled.

# --- Fixture (m): cards --------------------------------------------------
card_fixture() {
  # card_fixture <dir> <card block body>
  mkdir -p "$1/dev_docs/research/alpha/tracks/account/obligations"
  printf '# a card\n\n```card\n%s\n```\n' "$2" \
    >"$1/dev_docs/research/alpha/tracks/account/obligations/keychain.md"
}

DIR_M1="$BASE/card-stub-ok"
card_fixture "$DIR_M1" "kind: stub
superseded_when: the account track files its two measurement cards"
python3 "$SCRIPT" --root "$DIR_M1" validate >/dev/null 2>&1
exit_m1=$?
assert_exit "a stub card carrying superseded_when passes" "$exit_m1" 0

DIR_M2="$BASE/card-stub-no-condition"
card_fixture "$DIR_M2" "kind: stub"
out_m2="$(python3 "$SCRIPT" --root "$DIR_M2" validate 2>&1)"
exit_m2=$?
assert_exit "a stub card without superseded_when exits 1" "$exit_m2" 1
assert_contains "the stub error says why the condition is required" "$out_m2" \
  "a new place for work to hide"

DIR_M3="$BASE/card-receipt-no-url"
card_fixture "$DIR_M3" "kind: receipt
handler: linear"
out_m3="$(python3 "$SCRIPT" --root "$DIR_M3" validate 2>&1)"
exit_m3=$?
assert_exit "a receipt card without url exits 1" "$exit_m3" 1
assert_contains "the receipt error names the field" "$out_m3" "requires 'url:'"

# The validator is offline by contract: a url is content, never a request. An
# unroutable host must pass cleanly, or an outage of somebody else's tracker
# fails this gate. Deliberately no `timeout` wrapper — coreutils is not on a
# stock macOS, and a missing binary would fail this fixture for a reason that
# has nothing to do with the rule. A validator that did fetch would hang the
# harness on this address, which is a loud enough failure.
DIR_M4="$BASE/card-receipt-unroutable"
card_fixture "$DIR_M4" "kind: receipt
url: https://198.51.100.1/never-routable/ISSUE-1
handler: linear
tracker_id: ISSUE-1"
out_m4="$(python3 "$SCRIPT" --root "$DIR_M4" validate 2>&1)"
exit_m4=$?
assert_exit "a receipt's url is never fetched (an unroutable host passes)" "$exit_m4" 0
assert_contains "the unroutable receipt tree reports OK" "$out_m4" "research-spike: OK"

DIR_M5="$BASE/card-bad-kind"
card_fixture "$DIR_M5" "kind: memo"
out_m5="$(python3 "$SCRIPT" --root "$DIR_M5" validate 2>&1)"
exit_m5=$?
assert_exit "a card kind outside stub|receipt exits 1" "$exit_m5" 1
assert_contains "the card-kind error names the two kinds" "$out_m5" "stub | receipt"

DIR_M6="$BASE/card-missing-block"
mkdir -p "$DIR_M6/dev_docs/research/alpha/tracks/account/obligations"
write_file "$DIR_M6/dev_docs/research/alpha/tracks/account/obligations/keychain.md" \
  '# a note somebody left here without a block'
out_m6="$(python3 "$SCRIPT" --root "$DIR_M6" validate 2>&1)"
exit_m6=$?
assert_exit "a file under obligations/ with no card block exits 1" "$exit_m6" 1
assert_contains "the missing-card error says it is counted nowhere" "$out_m6" \
  "no card block in this file"

DIR_M7="$BASE/card-outside-obligations"
write_file "$DIR_M7/dev_docs/research/alpha/tracks/account/questions.md" '# account

```card
kind: stub
superseded_when: never, because this card is filed where nothing walks
```'
out_m7="$(python3 "$SCRIPT" --root "$DIR_M7" validate 2>&1)"
exit_m7=$?
assert_exit "a card block outside obligations/ exits 1" "$exit_m7" 1
assert_contains "the misplaced-card error says no ledger will show it" "$out_m7" \
  "card block outside tracks/<track>/obligations/"

# --- Fixture (m8): a `none:` line is not a way to switch the checks off --
# The exemption is for a *bare* `none:` block — the coverage rule's explicit
# declaration. Exempting any block that merely carries the line let a record
# with a real destination skip every check in the file, missing destination
# included, which would have made `none:` the off switch.
DIR_M8="$BASE/none-mixed"
mkdir -p "$DIR_M8/dev_docs/research/alpha/tracks/account"
write_file "$DIR_M8/dev_docs/research/alpha/tracks/account/questions.md" '# account

```obligation
none: nothing is owed here
id: keychain-invariant
owes: except for this, apparently
destination: dev_docs/tasks/never_written.md
status: open
```'
out_m8="$(python3 "$SCRIPT" --root "$DIR_M8" validate 2>&1)"
exit_m8=$?
assert_exit "a none: line alongside real fields does not exempt the block" "$exit_m8" 1
assert_contains "the mixed block is reported as trying to be two records" "$out_m8" \
  "declares 'none:' alongside other fields"
assert_contains "the mixed block's destination is still checked" "$out_m8" \
  "destination 'dev_docs/tasks/never_written.md' does not exist"

# --- Fixture (m9): an unresolvable destination is a finding, not a crash -
# pathlib raises RuntimeError (not OSError) on a symlink loop, so catching
# only OSError left this tracebacking out of the middle of the walk — and a
# traceback exits 1, which is indistinguishable from a real finding.
DIR_M9="$BASE/dest-symlink-loop"
obligation_fixture "$DIR_M9" "dev_docs/tasks/loop.md"
ln -s loop.md "$DIR_M9/dev_docs/tasks/loop.md"
out_m9="$(python3 "$SCRIPT" --root "$DIR_M9" validate 2>&1)"
exit_m9=$?
assert_exit "a symlink loop destination exits 1" "$exit_m9" 1
assert_not_contains "a symlink loop does not traceback" "$out_m9" "Traceback"
assert_contains "a symlink loop is reported as an address nobody can follow" "$out_m9" \
  "cannot be resolved"

# --- Fixture (m10): a file directly under dev_docs/research/ is not a project
# With the containment test at `>= 1`, the file's own name landed in
# research_parts[0] and README.md was reported as "another research project" —
# a rule firing on a name it invented.
DIR_M10="$BASE/dest-research-root"
obligation_fixture "$DIR_M10" "dev_docs/research/README.md"
printf '# the research tree\n' >"$DIR_M10/dev_docs/research/README.md"
out_m10="$(python3 "$SCRIPT" --root "$DIR_M10" validate 2>&1)"
exit_m10=$?
assert_exit "a destination directly under dev_docs/research/ passes" "$exit_m10" 0
assert_not_contains "a research-root file is never called another project" "$out_m10" \
  "another research project"

# --- Fixture (m11): one card per file ------------------------------------
DIR_M11="$BASE/card-two-blocks"
mkdir -p "$DIR_M11/dev_docs/research/alpha/tracks/account/obligations"
write_file "$DIR_M11/dev_docs/research/alpha/tracks/account/obligations/keychain.md" \
  '# two cards in one file

```card
kind: stub
superseded_when: the account track files its measurement card
```

```card
kind: receipt
url: https://example.invalid/ISSUE-1
```'
out_m11="$(python3 "$SCRIPT" --root "$DIR_M11" validate 2>&1)"
exit_m11=$?
assert_exit "a second card block in one file exits 1" "$exit_m11" 1
assert_contains "the second-card error says a file holds exactly one card" "$out_m11" \
  "a card file holds exactly one card"
assert_contains "the second-card error names the first block's line" "$out_m11" \
  "the first is at line 3"
assert_contains "the second-card error is located at the second block" "$out_m11" \
  "obligations/keychain.md:8:"

# --- Fixture (m12): obligations/ holds cards, not stray files ------------
# Globbing only *.md left a notes.txt holding deferred work that no parser
# reads and no ledger counts — a hiding place inside the one directory whose
# whole purpose is that work cannot hide in it. Dotfiles stay exempt: `init`
# writes .gitkeep, and Finder drops .DS_Store into anything it opens.
DIR_M12="$BASE/obligations-stray-file"
card_fixture "$DIR_M12" "kind: stub
superseded_when: the account track files its measurement card"
M12_DIR="$DIR_M12/dev_docs/research/alpha/tracks/account/obligations"
printf 'a deferral somebody typed into a text file\n' >"$M12_DIR/notes.txt"
out_m12="$(python3 "$SCRIPT" --root "$DIR_M12" validate 2>&1)"
exit_m12=$?
assert_exit "a non-markdown file under obligations/ exits 1" "$exit_m12" 1
assert_contains "the stray-file error names the file" "$out_m12" "obligations/notes.txt"
assert_contains "the stray-file error says it would be counted nowhere" "$out_m12" \
  "not a markdown card"

DIR_M13="$BASE/obligations-dotfiles"
card_fixture "$DIR_M13" "kind: stub
superseded_when: the account track files its measurement card"
M13_DIR="$DIR_M13/dev_docs/research/alpha/tracks/account/obligations"
: >"$M13_DIR/.gitkeep"
printf '\x00\x01macOS junk\n' >"$M13_DIR/.DS_Store"
out_m13="$(python3 "$SCRIPT" --root "$DIR_M13" validate 2>&1)"
exit_m13=$?
assert_exit "dotfiles under obligations/ are exempt (.gitkeep, .DS_Store)" "$exit_m13" 0
assert_not_contains "a dotfile is never reported as a card" "$out_m13" ".gitkeep"

# --- Fixture (n): question records and the coverage rule -----------------
# The half that matters more. Records alone catch only *malformed* deferrals;
# the coverage rule is what catches the deferral nobody registered. Every
# fixture below is one section of one track, so the rule is exercised where it
# is enforced — per section, not per file.

question_fixture() {
  # question_fixture <dir> <question block body> [coverage block body]
  # One track, one `### Q1.` section, and one real file to point at.
  mkdir -p "$1/dev_docs/tasks" "$1/dev_docs/research/alpha/tracks/account"
  printf '# a task card that exists\n' >"$1/dev_docs/tasks/real_task.md"
  {
    printf '# account\n\n### Q1. Does the account need an isolated uid domain?\n\n'
    printf '```question\n%s\n```\n' "$2"
    if [ -n "${3:-}" ]; then
      printf '\n```obligation\n%s\n```\n' "$3"
    fi
  } >"$1/dev_docs/research/alpha/tracks/account/questions.md"
}

DIR_N1="$BASE/coverage-declares-nothing"
question_fixture "$DIR_N1" "id: uid-domain-isolation
status: open
blocks: account-provisioning"
out_n1="$(python3 "$SCRIPT" --root "$DIR_N1" validate 2>&1)"
exit_n1=$?
assert_exit "a question section declaring nothing exits 1" "$exit_n1" 1
assert_contains "the coverage error names the section" "$out_n1" \
  "Q1 declares nothing it owes"
assert_contains "the coverage error says why forgetting is the failure" "$out_n1" \
  "accrue invisibly"
assert_contains "the coverage error states both ways to satisfy it" "$out_n1" \
  "bare \`none: <reason>\` block saying why nothing is owed"
assert_contains "the coverage error is located at the section heading" "$out_n1" \
  "dev_docs/research/alpha/tracks/account/questions.md:3:"

DIR_N2="$BASE/coverage-none"
question_fixture "$DIR_N2" "id: uid-domain-isolation
status: open
blocks: account-provisioning" "none: option (A) adds no observation and owes no tooling"
out_n2="$(python3 "$SCRIPT" --root "$DIR_N2" validate 2>&1)"
exit_n2=$?
assert_exit "an explicit 'none: <reason>' satisfies coverage" "$exit_n2" 0
assert_contains "the covered tree reports OK" "$out_n2" "research-spike: OK"

# An obligation block satisfies coverage the same way — the rule is that the
# section declared, not that it owes nothing.
DIR_N3="$BASE/coverage-obligation"
question_fixture "$DIR_N3" "id: uid-domain-isolation
status: open
blocks: account-provisioning" "id: uid-domain-provisioning
owes: the provisioning steps this answer implies
destination: dev_docs/tasks/real_task.md
status: open"
python3 "$SCRIPT" --root "$DIR_N3" validate >/dev/null 2>&1
exit_n3=$?
assert_exit "a registered obligation satisfies coverage" "$exit_n3" 0

DIR_N4="$BASE/coverage-none-no-reason"
question_fixture "$DIR_N4" "id: uid-domain-isolation
status: open
blocks: account-provisioning" "none:"
out_n4="$(python3 "$SCRIPT" --root "$DIR_N4" validate 2>&1)"
exit_n4=$?
assert_exit "a 'none' with no reason exits 1" "$exit_n4" 1
assert_contains "the reasonless none says why a reason is the point" "$out_n4" \
  "gives no reason"
assert_contains "the reasonless none names what a reviewer loses" "$out_n4" \
  "challenge it"

# A `none:` carrying any other field is trying to be two records at once — it
# is a declaration, not a partially-filled record. (Fixture (m8) asserts the
# other half of the same rule: the block is still checked in full.)
DIR_N5="$BASE/coverage-none-with-fields"
question_fixture "$DIR_N5" "id: uid-domain-isolation
status: open
blocks: account-provisioning" "none: nothing is owed here
owes: except for this, apparently"
out_n5="$(python3 "$SCRIPT" --root "$DIR_N5" validate 2>&1)"
exit_n5=$?
assert_exit "a 'none' carrying other fields exits 1" "$exit_n5" 1
assert_contains "the mixed none block is reported as two records at once" "$out_n5" \
  "declares 'none:' alongside other fields"

# --- Fixture (n6): the `answered` gate -----------------------------------
# `answered` requires both a recorded conclusion and coverage. Coverage cannot
# be satisfied by prose alone, and the status cannot be reached without saying
# what the answer is.
DIR_N6="$BASE/answered-no-answer"
question_fixture "$DIR_N6" "id: uid-domain-isolation
status: answered
blocks: account-provisioning" "none: the answer creates no new work"
out_n6="$(python3 "$SCRIPT" --root "$DIR_N6" validate 2>&1)"
exit_n6=$?
assert_exit "answered without an answer: field exits 1" "$exit_n6" 1
assert_contains "the missing-answer error names the field" "$out_n6" \
  "requires 'answer:'"
assert_contains "the missing-answer error says a prose conclusion is not enough" \
  "$out_n6" "two readers can read differently"

DIR_N7="$BASE/answered-no-coverage"
question_fixture "$DIR_N7" "id: uid-domain-isolation
status: answered
answer: yes — the account needs its own uid domain
blocks: account-provisioning"
out_n7="$(python3 "$SCRIPT" --root "$DIR_N7" validate 2>&1)"
exit_n7=$?
assert_exit "answered with an answer but no coverage exits 1" "$exit_n7" 1
assert_contains "the uncovered-answer error names the missing declaration" "$out_n7" \
  "'answered' while its section declares no obligations"
assert_contains "the uncovered-answer error says prose cannot satisfy coverage" \
  "$out_n7" "cannot be satisfied by prose alone"

DIR_N8="$BASE/answered-complete"
question_fixture "$DIR_N8" "id: uid-domain-isolation
status: answered
answer: yes — the account needs its own uid domain
blocks: account-provisioning" "id: uid-domain-provisioning
owes: the provisioning steps this answer implies
destination: dev_docs/tasks/real_task.md
status: open"
out_n8="$(python3 "$SCRIPT" --root "$DIR_N8" validate 2>&1)"
exit_n8=$?
assert_exit "answered with both an answer and coverage passes" "$exit_n8" 0
assert_contains "the answered tree reports OK" "$out_n8" "research-spike: OK"

# --- Fixture (n9): retirement ---------------------------------------------
DIR_N9="$BASE/retired-no-because"
question_fixture "$DIR_N9" "id: uid-domain-isolation
status: retired
blocks: account-provisioning" "none: the premise died, so nothing is owed"
out_n9="$(python3 "$SCRIPT" --root "$DIR_N9" validate 2>&1)"
exit_n9=$?
assert_exit "retired without retired_because exits 1" "$exit_n9" 1
assert_contains "the retirement error names the field" "$out_n9" \
  "requires 'retired_because:'"
assert_contains "the retirement error says questions leave without pretending" \
  "$out_n9" "without pretending to be answered"

DIR_N10="$BASE/retired-with-because"
question_fixture "$DIR_N10" "id: uid-domain-isolation
status: retired
retired_because: the shared-host option was dropped, so the premise is gone
blocks: account-provisioning" "none: the premise died, so nothing is owed"
python3 "$SCRIPT" --root "$DIR_N10" validate >/dev/null 2>&1
exit_n10=$?
assert_exit "retired with retired_because passes" "$exit_n10" 0

# --- Fixture (n11): the `blocks:` sentinel --------------------------------
# `blocks: none` bare is refused: a question that gates nothing is worth
# noticing, and a sentinel with no reason cannot be told from one nobody wired
# up.
DIR_N11="$BASE/blocks-bare-none"
question_fixture "$DIR_N11" "id: uid-domain-isolation
status: open
blocks: none" "none: nothing is owed yet"
out_n11="$(python3 "$SCRIPT" --root "$DIR_N11" validate 2>&1)"
exit_n11=$?
assert_exit "a bare 'blocks: none' exits 1" "$exit_n11" 1
assert_contains "the bare blocks-none error asks for a reason" "$out_n11" \
  "'blocks: none' gives no reason"
assert_contains "the bare blocks-none error says why it is worth noticing" \
  "$out_n11" "gates no decision is worth noticing"

DIR_N12="$BASE/blocks-none-reason"
question_fixture "$DIR_N12" "id: uid-domain-isolation
status: open
blocks: none: it picks between options the decision already allows" \
  "none: nothing is owed yet"
out_n12="$(python3 "$SCRIPT" --root "$DIR_N12" validate 2>&1)"
exit_n12=$?
assert_exit "'blocks: none: <reason>' passes" "$exit_n12" 0
assert_contains "the sentinel tree reports OK" "$out_n12" "research-spike: OK"

# A reason containing a comma is **one** reason and **zero** decision ids. The
# sentinel exempts it from the comma-list rule (task 1); without that the tail
# becomes a dangling decision reference in task 5's referential check.
DIR_N13="$BASE/blocks-none-comma"
question_fixture "$DIR_N13" "id: uid-domain-isolation
status: open
blocks: none: it gates nothing, and probably never will" "none: nothing is owed yet"
out_n13="$(python3 "$SCRIPT" --root "$DIR_N13" --verbose validate 2>&1)"
exit_n13=$?
assert_exit "a blocks-none reason containing a comma passes" "$exit_n13" 0
assert_contains "the comma stays inside one verbatim reason" "$out_n13" \
  "blocks = none reason='it gates nothing, and probably never will'"
assert_not_contains "a comma in the reason yields no decision ids" "$out_n13" \
  "'and probably never will'"

# Both sentinels in one section, meaning different things: `blocks: none:` says
# the question gates no decision, the bare `none:` block says it owes no
# obligations. Collapsing them would make the coverage rule undecidable.
DIR_N14="$BASE/both-sentinels"
question_fixture "$DIR_N14" "id: uid-domain-isolation
status: open
blocks: none: it picks between options the decision already allows" \
  "none: option (A) adds no observation and owes no tooling"
out_n14="$(python3 "$SCRIPT" --root "$DIR_N14" --verbose validate 2>&1)"
exit_n14=$?
assert_exit "both sentinels in one section validate clean" "$exit_n14" 0
assert_contains "the gates-nothing meaning survives" "$out_n14" \
  "blocks = none reason='it picks between options the decision already allows'"
assert_contains "the owes-nothing meaning survives alongside it" "$out_n14" \
  "none = none reason='option (A) adds no observation and owes no tooling'"

# --- Fixture (n15): a section is one question, and a question is in one ---
DIR_N15="$BASE/section-without-question"
write_file "$DIR_N15/dev_docs/research/alpha/tracks/account/questions.md" '# account

### Q3. Should the ceiling be a cgroup?

Some prose, and nothing else at all.'
out_n15="$(python3 "$SCRIPT" --root "$DIR_N15" validate 2>&1)"
exit_n15=$?
assert_exit "a Q3 section with no question block exits 1" "$exit_n15" 1
assert_contains "the missing-block error names the section" "$out_n15" \
  "Q3 carries no \`question\` block"
assert_contains "the missing-block error says the heading is only prose" "$out_n15" \
  "the heading is prose"

DIR_N16="$BASE/question-outside-section"
write_file "$DIR_N16/dev_docs/research/alpha/tracks/account/questions.md" '# account

```question
id: uid-domain-isolation
status: open
blocks: account-provisioning
```'
out_n16="$(python3 "$SCRIPT" --root "$DIR_N16" validate 2>&1)"
exit_n16=$?
assert_exit "a question block outside any section exits 1" "$exit_n16" 1
assert_contains "the stray-question error says coverage is per section" "$out_n16" \
  "question block outside a \`### Q<n>.\` section"

DIR_N17="$BASE/two-questions-one-section"
question_fixture "$DIR_N17" "id: uid-domain-isolation
status: open
blocks: account-provisioning" "none: nothing is owed yet"
cat >>"$DIR_N17/dev_docs/research/alpha/tracks/account/questions.md" <<'MD'

```question
id: uid-domain-shape
status: open
blocks: account-provisioning
```
MD
out_n17="$(python3 "$SCRIPT" --root "$DIR_N17" validate 2>&1)"
exit_n17=$?
assert_exit "a second question block in one section exits 1" "$exit_n17" 1
assert_contains "the second-question error says one section is one question" \
  "$out_n17" "one section is one question"

# --- Fixture (n18): a question id shares the tree-wide id namespace -------
# One namespace across record kinds, not one per kind: `blocks:`/`blocking:`
# resolve by bare id and the status report prints both kinds under the same
# project/track/id form, so a name meaning a question here and an obligation
# there is exactly the ambiguity the uniqueness rule removes.
DIR_N18="$BASE/id-namespace-shared"
question_fixture "$DIR_N18" "id: uid-domain-isolation
status: open
blocks: account-provisioning" "id: uid-domain-isolation
owes: the provisioning steps this answer implies
destination: dev_docs/tasks/real_task.md
status: open"
out_n18="$(python3 "$SCRIPT" --root "$DIR_N18" validate 2>&1)"
exit_n18=$?
assert_exit "a question and an obligation sharing one id exits 1" "$exit_n18" 1
assert_contains "the cross-kind duplicate is reported by qualified id" "$out_n18" \
  "duplicate question id 'alpha/account/uid-domain-isolation'"
assert_contains "the cross-kind duplicate says the namespace spans kinds" "$out_n18" \
  "across record kinds"

# --- Fixture (o): contracts/ coverage ------------------------------------
# Contract prose states preconditions constantly, and a precondition is work
# somebody owes. That class hid the worst offender in the origin repo — a
# precondition that was on no list at all. Unlike arbitrary prose, contracts/
# is a bounded, opt-in directory the skill owns.
contracts_fixture() {
  # contracts_fixture <dir> <contract file body>
  mkdir -p "$1/dev_docs/tasks" "$1/dev_docs/research/alpha/tracks/account/contracts"
  printf '# a task card that exists\n' >"$1/dev_docs/tasks/real_task.md"
  printf '# account\n' >"$1/dev_docs/research/alpha/tracks/account/questions.md"
  printf '%s\n' "$2" >"$1/dev_docs/research/alpha/tracks/account/contracts/host.md"
}

DIR_O1="$BASE/contract-undeclared"
contracts_fixture "$DIR_O1" '# host contract

This component must not be deployed on a shared host.'
out_o1="$(python3 "$SCRIPT" --root "$DIR_O1" validate 2>&1)"
exit_o1=$?
assert_exit "a contract file declaring nothing exits 1" "$exit_o1" 1
assert_contains "the contracts error names the precondition class" "$out_o1" \
  "must not be deployed on a shared host"
assert_contains "the contracts error says a contract is not a backlog" "$out_o1" \
  "a contract document is not a backlog"
assert_contains "the contracts error names the file" "$out_o1" \
  "dev_docs/research/alpha/tracks/account/contracts/host.md"

DIR_O2="$BASE/contract-obligation"
contracts_fixture "$DIR_O2" '# host contract

This component must not be deployed on a shared host.

```obligation
id: shared-host-precondition
owes: the deployment guard this precondition implies
destination: dev_docs/tasks/real_task.md
status: open
```'
out_o2="$(python3 "$SCRIPT" --root "$DIR_O2" validate 2>&1)"
exit_o2=$?
assert_exit "a contract file carrying an obligation passes" "$exit_o2" 0
assert_contains "the covered contract tree reports OK" "$out_o2" "research-spike: OK"

DIR_O3="$BASE/contract-none"
contracts_fixture "$DIR_O3" '# host contract

The preconditions here are all already enforced in code.

```obligation
none: every precondition below is already asserted by the deploy check
```'
out_o3="$(python3 "$SCRIPT" --root "$DIR_O3" validate 2>&1)"
exit_o3=$?
assert_exit "a file-level 'none: <reason>' covers a contract file" "$exit_o3" 0
assert_contains "the declared contract tree reports OK" "$out_o3" "research-spike: OK"

# --- Fixture (o4): coverage is deliberately not universal ----------------
# Widening the rule to every markdown file would turn the discipline into
# noise and train people to satisfy it mechanically (design §"What the skill
# must not do"). A note beside a track, and the project's own charter, declare
# nothing and are clean.
DIR_O4="$BASE/coverage-scope"
write_file "$DIR_O4/dev_docs/research/alpha/PROJECT.md" '# alpha

A charter that defers nothing and declares nothing.'
write_file "$DIR_O4/dev_docs/research/alpha/tracks/account/questions.md" '# account'
write_file "$DIR_O4/dev_docs/research/alpha/tracks/account/notes.md" '# scratch notes

Measurements, half-thoughts, and no declaration of any kind.'
out_o4="$(python3 "$SCRIPT" --root "$DIR_O4" validate 2>&1)"
exit_o4=$?
assert_exit "a markdown file outside questions/contracts needs no declaration" "$exit_o4" 0
assert_contains "the un-covered tree reports OK" "$out_o4" "research-spike: OK"

# --- Fixture (j): --help lists all six subcommands -----------------------
out_j="$(python3 "$SCRIPT" --help 2>&1)"
for verb in init validate ledger write-ledger status suggest; do
  assert_contains "--help lists '$verb'" "$out_j" "$verb"
done

echo
echo "test-research-spike: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-research-spike: OK"
