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
# The decision the question above names. Filed because `blocks:` resolves —
# naming a decision that does not exist is the "track that did not exist" bug,
# and every tree that means to pass has to declare what it gates.
write_file "$DIR_B/dev_docs/research/alpha/decisions.md" '# alpha — decisions

```decision
id: account-provisioning
state: pending
```'
write_file "$DIR_B/dev_docs/research/beta/PROJECT.md" '# beta

```decision
id: stop-semantics
state: pending
```'

out_b="$(python3 "$SCRIPT" --root "$DIR_B" validate 2>&1)"
exit_b=$?
assert_exit "two-project tree exits 0" "$exit_b" 0
assert_contains "both projects counted" "$out_b" "2 projects, 2 tracks, 4 records"
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
write_file "$DIR_K4/dev_docs/research/alpha/decisions.md" '# alpha — decisions

```decision
id: stop-semantics
state: pending
```'
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
write_file "$DIR_K4B/dev_docs/research/alpha/decisions.md" '# alpha — decisions

```decision
id: stop-semantics
state: pending
```'
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

# `blocking:` is accepted and recorded here; that it names a real decision is
# the referential check's business (fixture (r)), so the decision is declared.
DIR_L18="$BASE/blocking-accepted"
status_fixture "$DIR_L18" "status: open
blocking: stop-semantics"
write_file "$DIR_L18/dev_docs/research/alpha/decisions.md" '# alpha — decisions

```decision
id: stop-semantics
state: pending
```'
python3 "$SCRIPT" --root "$DIR_L18" validate >/dev/null 2>&1
exit_l18=$?
assert_exit "an obligation carrying blocking: is accepted" "$exit_l18" 0
out_l18v="$(python3 "$SCRIPT" --root "$DIR_L18" --verbose validate 2>&1)"
assert_contains "blocking: is recorded for task 5's referential check" "$out_l18v" \
  "blocking = 'stop-semantics'"

# A written `blocking:` that names nothing is the same defect as fixture
# (n17d)'s `blocks: ,` — truthy text, not the sentinel, zero ids — and the
# field being optional is exactly why it slipped: an intended gate silently
# dropped reads as wired up while gating nothing.
DIR_L18B="$BASE/blocking-separator-only"
status_fixture "$DIR_L18B" "status: open
blocking: ,"
out_l18b="$(python3 "$SCRIPT" --root "$DIR_L18B" validate 2>&1)"
exit_l18b=$?
assert_exit "a separator-only 'blocking: ,' exits 1" "$exit_l18b" 1
assert_contains "the empty blocking: is reported as naming no decision" "$out_l18b" \
  "'blocking:' is written but names no decision"
assert_contains "the empty blocking: says dropping the line is legitimate" "$out_l18b" \
  "it is meant to be scarce"

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
  # One track, one `### Q1.` section, and one real file to point at. The
  # decisions these questions name are declared too: `blocks:` resolves, so a
  # fixture that meant to isolate the coverage rule would otherwise fail on a
  # dangling reference instead.
  mkdir -p "$1/dev_docs/tasks" "$1/dev_docs/research/alpha/tracks/account"
  printf '# a task card that exists\n' >"$1/dev_docs/tasks/real_task.md"
  printf '# alpha — decisions\n\n```decision\nid: account-provisioning\nstate: pending\n```\n' \
    >"$1/dev_docs/research/alpha/decisions.md"
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

# An extra block is a misplacement *and* a record. Checking only the section's
# first question left the extra's fields unread and its id out of the
# uniqueness map, so a duplicate id declared in one went unreported entirely —
# reproduced. Both findings must land alongside the placement error.
DIR_N17B="$BASE/second-question-still-checked"
question_fixture "$DIR_N17B" "id: uid-domain-isolation
status: open
blocks: account-provisioning" "none: nothing is owed yet"
cat >>"$DIR_N17B/dev_docs/research/alpha/tracks/account/questions.md" <<'MD'

```question
id: uid-domain-isolation
status: half-answered
blocks: account-provisioning
```
MD
out_n17b="$(python3 "$SCRIPT" --root "$DIR_N17B" validate 2>&1)"
exit_n17b=$?
assert_exit "a second question block carrying its own errors exits 1" "$exit_n17b" 1
assert_contains "the extra block is still reported as misplaced" "$out_n17b" \
  "one section is one question"
assert_contains "the extra block's id still enters the uniqueness map" "$out_n17b" \
  "duplicate question id 'alpha/account/uid-domain-isolation'"
assert_contains "the extra block's own fields are still checked" "$out_n17b" \
  "question status 'half-answered' is not one of"

# The same holds outside a section: the placement is wrong, but the record
# still claims an id the rest of the tree can collide with.
DIR_N17C="$BASE/stray-question-still-checked"
write_file "$DIR_N17C/dev_docs/research/alpha/tracks/account/questions.md" '# account

### Q1. Does the account need an isolated uid domain?

```question
id: uid-domain-isolation
status: open
blocks: account-provisioning
```

```obligation
none: nothing is owed yet
```

## Notes

```question
id: uid-domain-isolation
status: open
blocks: account-provisioning
```'
out_n17c="$(python3 "$SCRIPT" --root "$DIR_N17C" validate 2>&1)"
exit_n17c=$?
assert_exit "an out-of-section question with a duplicate id exits 1" "$exit_n17c" 1
assert_contains "the out-of-section block is reported as misplaced" "$out_n17c" \
  "question block outside a \`### Q<n>.\` section"
assert_contains "the out-of-section block's id still enters the uniqueness map" \
  "$out_n17c" "duplicate question id 'alpha/account/uid-domain-isolation'"
# No coverage error stacks on the placement error: the section that would
# carry the declaration is the thing that is missing, so restating it would
# just be a second finding over one wrong line.
assert_not_contains "no coverage error is stacked on the placement error" \
  "$out_n17c" "declares nothing it owes"

# --- Fixture (n17d): a separator-only `blocks:` names no decision ---------
# `blocks: ,` has text in it, parses to zero ids, and is not the sentinel — a
# raw-emptiness test let the typo through while the question named no decision
# at all, which is exactly what `blocks:` exists to prevent.
DIR_N17D="$BASE/blocks-separator-only"
question_fixture "$DIR_N17D" "id: uid-domain-isolation
status: open
blocks: ," "none: nothing is owed yet"
out_n17d="$(python3 "$SCRIPT" --root "$DIR_N17D" validate 2>&1)"
exit_n17d=$?
assert_exit "a separator-only 'blocks: ,' exits 1" "$exit_n17d" 1
assert_contains "the empty blocks list is reported as missing" "$out_n17d" \
  "'blocks:' is required"

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

# Every file, not just *.md — the same treatment obligations/ gets. Globbing
# markdown alone left a preconditions.txt stating preconditions that no parser
# reads and no ledger counts: a labeled hiding place inside the one directory
# whose whole purpose is that preconditions cannot hide in it.
DIR_O5="$BASE/contract-stray-file"
contracts_fixture "$DIR_O5" '# host contract

```obligation
none: every precondition below is already asserted by the deploy check
```'
O5_DIR="$DIR_O5/dev_docs/research/alpha/tracks/account/contracts"
printf 'This component must not be deployed on a shared host.\n' \
  >"$O5_DIR/preconditions.txt"
out_o5="$(python3 "$SCRIPT" --root "$DIR_O5" validate 2>&1)"
exit_o5=$?
assert_exit "a non-markdown file under contracts/ exits 1" "$exit_o5" 1
assert_contains "the stray contract error names the file" "$out_o5" \
  "contracts/preconditions.txt"
assert_contains "the stray contract error says nothing can read it" "$out_o5" \
  "not a markdown contract"

# Dotfiles stay exempt, exactly as under obligations/: Finder drops .DS_Store
# into anything it opens, and a dotfile cannot plausibly be a contract.
DIR_O6="$BASE/contract-dotfiles"
contracts_fixture "$DIR_O6" '# host contract

```obligation
none: every precondition below is already asserted by the deploy check
```'
O6_DIR="$DIR_O6/dev_docs/research/alpha/tracks/account/contracts"
: >"$O6_DIR/.gitkeep"
printf '\x00\x01macOS junk\n' >"$O6_DIR/.DS_Store"
out_o6="$(python3 "$SCRIPT" --root "$DIR_O6" validate 2>&1)"
exit_o6=$?
assert_exit "dotfiles under contracts/ are exempt (.gitkeep, .DS_Store)" "$exit_o6" 0
assert_not_contains "a dotfile is never reported as an uncovered contract" \
  "$out_o6" ".gitkeep"

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

# --- Fixture (r): decisions and referential integrity --------------------
# The convergence hook. `blocks:` and `blocking:` are only worth having if the
# names resolve, and a decision is only worth calling decided if nothing is
# still open against it — so every fixture below is one tree in which exactly
# one of those two things is true or false.

decision_fixture() {
  # decision_fixture <dir> <decisions.md block body> <question block body> \
  #                  [coverage block body]
  # One project, one track, one `### Q1.` section, and the durable files a
  # `decided_in:` may legitimately point at.
  mkdir -p "$1/dev_docs/adr" "$1/dev_docs/research/alpha/tracks/account/obligations"
  printf '# ADR 7 — stop semantics\n' >"$1/dev_docs/adr/0007-stop-semantics.md"
  printf '# handoff\n\n```card\nkind: receipt\nurl: https://example.invalid/PRE-1\n```\n' \
    >"$1/dev_docs/research/alpha/tracks/account/obligations/handoff.md"
  printf '# alpha — decisions\n\n```decision\n%s\n```\n' "$2" \
    >"$1/dev_docs/research/alpha/decisions.md"
  {
    printf '# account\n\n### Q1. Must the baseline stop contain an escapee?\n\n'
    printf '```question\n%s\n```\n' "$3"
    printf '\n```obligation\n%s\n```\n' \
      "${4:-none: nothing is owed until the decision is taken}"
  } >"$1/dev_docs/research/alpha/tracks/account/questions.md"
}

# A `blocks:` naming a decision nobody filed is the "track that did not exist"
# bug wearing a different hat: it reads as wired up and gates nothing.
DIR_R1="$BASE/blocks-dangling"
decision_fixture "$DIR_R1" "id: stop-semantics
state: pending" "id: baseline-stop-escapee
status: open
blocks: stop-semantiks"
out_r1="$(python3 "$SCRIPT" --root "$DIR_R1" validate 2>&1)"
exit_r1=$?
assert_exit "a blocks: naming a nonexistent decision exits 1" "$exit_r1" 1
assert_contains "the dangling reference names the decision it could not find" "$out_r1" \
  "blocks names decision 'stop-semantiks', which does not exist in project 'alpha'"
assert_contains "the dangling reference says why it matters" "$out_r1" \
  "reads as gating something while gating nothing"
assert_contains "the dangling reference states both ways to fix it" "$out_r1" \
  "state: proposed"

# A track that discovers it needs a decision files it as `proposed` in its own
# questions.md, and a `blocks:` naming that is valid — promotion is an
# organizer act, so requiring promotion first would make the track wait on one.
DIR_R2="$BASE/blocks-proposed"
decision_fixture "$DIR_R2" "id: stop-semantics
state: pending" "id: baseline-stop-escapee
status: open
blocks: stop-semantics, stop-tooling"
cat >>"$DIR_R2/dev_docs/research/alpha/tracks/account/questions.md" <<'PROPOSED'

```decision
id: stop-tooling
state: proposed
```
PROPOSED
out_r2="$(python3 "$SCRIPT" --root "$DIR_R2" validate 2>&1)"
exit_r2=$?
assert_exit "a blocks: naming a proposed decision in the same track passes" "$exit_r2" 0
assert_contains "the proposed-decision tree reports OK" "$out_r2" "research-spike: OK"

# The other half of the same rule: `proposed` is a track's state, and
# decisions.md is where the organizer's promoted decisions live.
DIR_R3="$BASE/proposed-in-decisions"
decision_fixture "$DIR_R3" "id: stop-semantics
state: proposed" "id: baseline-stop-escapee
status: open
blocks: stop-semantics"
out_r3="$(python3 "$SCRIPT" --root "$DIR_R3" validate 2>&1)"
exit_r3=$?
assert_exit "a proposed decision inside decisions.md exits 1" "$exit_r3" 1
assert_contains "the misplaced proposed decision says where it belongs" "$out_r3" \
  "files the \`state: proposed\` block in its own questions.md"
assert_contains "the misplaced proposed decision names promotion as an organizer act" \
  "$out_r3" "promote-decision"

# Readiness is derived on every run, so there is no key to store it in — the
# record format itself is what stops a stale number disagreeing with the truth.
for stored in ready blocked; do
  DIR_R4="$BASE/decision-stored-$stored"
  decision_fixture "$DIR_R4" "id: stop-semantics
state: pending
$stored: yes" "id: baseline-stop-escapee
status: open
blocks: stop-semantics"
  out_r4="$(python3 "$SCRIPT" --root "$DIR_R4" validate 2>&1)"
  exit_r4=$?
  assert_exit "a stored '$stored:' on a decision exits 1" "$exit_r4" 1
  assert_contains "a stored '$stored:' is rejected as an unknown key" "$out_r4" \
    "decision block: unknown key '$stored'"
done

# --- Fixture (r5): `decided` and its evidence ----------------------------
DIR_R5="$BASE/decided-no-evidence"
decision_fixture "$DIR_R5" "id: stop-semantics
state: decided" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics"
out_r5="$(python3 "$SCRIPT" --root "$DIR_R5" validate 2>&1)"
exit_r5=$?
assert_exit "a decided decision with no decided_in exits 1" "$exit_r5" 1
assert_contains "the missing evidence error says what the pointer is for" "$out_r5" \
  "a decided decision requires 'decided_in:'"
assert_contains "the missing evidence error names the durable forms" "$out_r5" \
  "an ADR, a permanent design doc, or a receipt card"

# The durability rule, structurally: /push-plan deletes plan directories after
# migrating them to a tracker, so evidence filed in one is scheduled for
# deletion. Unlike an obligation's destination, this is an error.
DIR_R6="$BASE/decided-in-plan-dir"
decision_fixture "$DIR_R6" "id: stop-semantics
state: decided
decided_in: dev_docs/tasks/foo_plan/foo_plan.md" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics"
write_file "$DIR_R6/dev_docs/tasks/foo_plan/foo_plan.md" '# foo plan'
out_r6="$(python3 "$SCRIPT" --root "$DIR_R6" validate 2>&1)"
exit_r6=$?
assert_exit "a decided_in inside a *_plan/ directory exits 1" "$exit_r6" 1
assert_contains "the plan-directory error names the directory" "$out_r6" \
  "decided_in 'dev_docs/tasks/foo_plan/foo_plan.md' is inside a plan directory ('foo_plan')"
assert_contains "the plan-directory error names the /push-plan hazard" "$out_r6" \
  "/push-plan deletes plan directories"
assert_contains "the plan-directory error says why a decision differs from an obligation" \
  "$out_r6" "a decision's evidence has to outlive the work"

# It is rejected at any depth, not just as the parent directory.
DIR_R6B="$BASE/decided-in-plan-dir-deep"
decision_fixture "$DIR_R6B" "id: stop-semantics
state: decided
decided_in: dev_docs/tasks/foo_plan/notes/decision.md" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics"
write_file "$DIR_R6B/dev_docs/tasks/foo_plan/notes/decision.md" '# a note inside a plan'
out_r6b="$(python3 "$SCRIPT" --root "$DIR_R6B" validate 2>&1)"
exit_r6b=$?
assert_exit "a decided_in nested deeper inside a *_plan/ directory exits 1" "$exit_r6b" 1
assert_contains "the nested plan directory is still named" "$out_r6b" \
  "is inside a plan directory ('foo_plan')"

# The declared path is checked too, not only the resolved one: a pointer
# written *into* a plan directory that symlinks out to a durable file resolves
# somewhere safe and is deleted anyway, because /push-plan removes the folder
# with the symlink in it.
DIR_R6E="$BASE/decided-in-plan-dir-symlink"
decision_fixture "$DIR_R6E" "id: stop-semantics
state: decided
decided_in: dev_docs/tasks/foo_plan/decision.md" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics"
mkdir -p "$DIR_R6E/dev_docs/tasks/foo_plan"
ln -s ../../adr/0007-stop-semantics.md "$DIR_R6E/dev_docs/tasks/foo_plan/decision.md"
out_r6e="$(python3 "$SCRIPT" --root "$DIR_R6E" validate 2>&1)"
exit_r6e=$?
assert_exit "a decided_in symlinked out of a *_plan/ directory still exits 1" "$exit_r6e" 1
assert_contains "the symlinked plan-directory pointer names the directory it sits in" \
  "$out_r6e" "is inside a plan directory ('foo_plan')"
assert_contains "the symlinked plan-directory pointer names the /push-plan hazard" \
  "$out_r6e" "/push-plan deletes plan directories"

# `decided_in:` shares task 3's containment rules, one implementation: a
# missing file is a pointer to nothing wherever it is written.
DIR_R6C="$BASE/decided-in-missing"
decision_fixture "$DIR_R6C" "id: stop-semantics
state: decided
decided_in: dev_docs/adr/never-written.md" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics"
out_r6c="$(python3 "$SCRIPT" --root "$DIR_R6C" validate 2>&1)"
exit_r6c=$?
assert_exit "a decided_in pointing at a nonexistent file exits 1" "$exit_r6c" 1
assert_contains "the missing decided_in is named" "$out_r6c" \
  "decided_in 'dev_docs/adr/never-written.md' does not exist"
assert_contains "the missing decided_in says what evidence is for" "$out_r6c" \
  "instead of re-litigating it"

DIR_R6D="$BASE/decided-in-traversal"
decision_fixture "$DIR_R6D" "id: stop-semantics
state: decided
decided_in: ../outside-adr.md" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics"
printf '# outside the root\n' >"$BASE/outside-adr.md"
out_r6d="$(python3 "$SCRIPT" --root "$DIR_R6D" validate 2>&1)"
exit_r6d=$?
assert_exit "a decided_in escaping with '../' exits 1 even when the target exists" \
  "$exit_r6d" 1
assert_contains "the shared containment rule fires for decided_in too" "$out_r6d" \
  "decided_in '../outside-adr.md' escapes the repository with '../'"

# The passing shapes: an ADR, and a receipt card under obligations/.
for evidence in "dev_docs/adr/0007-stop-semantics.md" \
  "dev_docs/research/alpha/tracks/account/obligations/handoff.md"; do
  DIR_R7="$BASE/decided-in-durable-$(basename "$evidence" .md)"
  decision_fixture "$DIR_R7" "id: stop-semantics
state: decided
decided_in: $evidence" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics"
  out_r7="$(python3 "$SCRIPT" --root "$DIR_R7" validate 2>&1)"
  exit_r7=$?
  assert_exit "a decided_in pointing at $evidence passes" "$exit_r7" 0
  assert_contains "the durable-evidence tree reports OK" "$out_r7" "research-spike: OK"
done

# --- Fixture (r8): `decided` requires zero open blockers -----------------
# Error, not warning: this is what makes hand-editing the files safe to allow.
DIR_R8="$BASE/decided-open-question"
decision_fixture "$DIR_R8" "id: stop-semantics
state: decided
decided_in: dev_docs/adr/0007-stop-semantics.md" "id: baseline-stop-escapee
status: open
blocks: stop-semantics"
out_r8="$(python3 "$SCRIPT" --root "$DIR_R8" validate 2>&1)"
exit_r8=$?
assert_exit "a decided decision with an open question blocking it exits 1" "$exit_r8" 1
assert_contains "the open-blocker error names both ends" "$out_r8" \
  "this question is open and blocks decision 'stop-semantics', which is already decided"
assert_contains "the open-blocker error states the invariant" "$out_r8" \
  "a decided decision must have zero open blockers"
assert_contains "the open-blocker error offers the reopen route" "$out_r8" \
  "reopened_because:"

DIR_R9="$BASE/decided-open-obligation"
decision_fixture "$DIR_R9" "id: stop-semantics
state: decided
decided_in: dev_docs/adr/0007-stop-semantics.md" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics" "id: stop-instrumentation
owes: the instrumentation the answer implies
destination: dev_docs/research/alpha/tracks/account/obligations/handoff.md
status: open
blocking: stop-semantics"
out_r9="$(python3 "$SCRIPT" --root "$DIR_R9" validate 2>&1)"
exit_r9=$?
assert_exit "a decided decision with an open obligation blocking it exits 1" "$exit_r9" 1
assert_contains "the open obligation is reported as the blocker" "$out_r9" \
  "this obligation is open and blocking decision 'stop-semantics', which is already decided"

# A historical, already-closed reference to a decided decision is clean —
# otherwise deciding anything would mean rewriting the records that led to it.
DIR_R10="$BASE/decided-closed-blockers"
decision_fixture "$DIR_R10" "id: stop-semantics
state: decided
decided_in: dev_docs/adr/0007-stop-semantics.md" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics" "id: stop-instrumentation
owes: the instrumentation the answer implies
destination: dev_docs/research/alpha/tracks/account/obligations/handoff.md
status: discharged
discharged_by: PR #412
blocking: stop-semantics"
out_r10="$(python3 "$SCRIPT" --root "$DIR_R10" validate 2>&1)"
exit_r10=$?
assert_exit "closed blockers against a decided decision stay clean" "$exit_r10" 0
assert_contains "the settled tree reports OK" "$out_r10" "research-spike: OK"

# --- Fixture (r11): reopening --------------------------------------------
# The same tree twice, differing only in the decision block: a new open blocker
# is rejected against a decided decision and accepted against a reopened one.
reopen_fixture() {
  # reopen_fixture <dir> <decision block body>
  decision_fixture "$1" "$2" "id: baseline-stop-escapee
status: answered
answer: no — the baseline stop is measured with the escapee excluded
blocks: stop-semantics" "id: stop-instrumentation
owes: the instrumentation the new evidence implies
destination: dev_docs/research/alpha/tracks/account/obligations/handoff.md
status: open
blocking: stop-semantics"
}

DIR_R11="$BASE/new-blocker-on-decided"
reopen_fixture "$DIR_R11" "id: stop-semantics
state: decided
decided_in: dev_docs/adr/0007-stop-semantics.md"
out_r11="$(python3 "$SCRIPT" --root "$DIR_R11" validate 2>&1)"
exit_r11=$?
assert_exit "a new open blocker against a decided decision exits 1" "$exit_r11" 1
assert_contains "the new blocker is reported against the decided decision" "$out_r11" \
  "which is already decided"

DIR_R12="$BASE/reopened-accepts-blocker"
reopen_fixture "$DIR_R12" "id: stop-semantics
state: pending
reopened_because: the escapee showed up on a second host
decided_in: dev_docs/adr/0007-stop-semantics.md"
out_r12="$(python3 "$SCRIPT" --root "$DIR_R12" validate 2>&1)"
exit_r12=$?
assert_exit "the same blocker against a reopened decision passes" "$exit_r12" 0
assert_contains "the reopened tree reports OK" "$out_r12" "research-spike: OK"

# The retained pointer is the whole mechanism. This validator reads one
# snapshot with no history, so without it a reopened decision is
# byte-identical to one that never decided anything.
DIR_R13="$BASE/reopened-no-retained-evidence"
reopen_fixture "$DIR_R13" "id: stop-semantics
state: pending
reopened_because: the escapee showed up on a second host"
out_r13="$(python3 "$SCRIPT" --root "$DIR_R13" validate 2>&1)"
exit_r13=$?
assert_exit "a reopened_because with no retained decided_in exits 1" "$exit_r13" 1
assert_contains "the reopen error says there is nothing to reopen" "$out_r13" \
  "'reopened_because:' with no retained 'decided_in:'"
assert_contains "the reopen error says why the pointer is the evidence" "$out_r13" \
  "the only structural evidence that there was a prior decision"

# The pending exemption is for reopen evidence only — otherwise `decided ⇒
# decided_in` would be satisfiable by a decision that never says it decided.
DIR_R14="$BASE/pending-with-evidence"
decision_fixture "$DIR_R14" "id: stop-semantics
state: pending
decided_in: dev_docs/adr/0007-stop-semantics.md" "id: baseline-stop-escapee
status: open
blocks: stop-semantics"
out_r14="$(python3 "$SCRIPT" --root "$DIR_R14" validate 2>&1)"
exit_r14=$?
assert_exit "a decided_in on a pending decision with no reopened_because exits 1" \
  "$exit_r14" 1
assert_contains "the pending-with-evidence error names the state it saw" "$out_r14" \
  "'decided_in:' on a decision whose state is 'pending'"
assert_contains "the pending-with-evidence error names the one exemption" "$out_r14" \
  "the only exemption is reopen evidence"

# And the exemption is the whole reopen *shape*, not one field: keyed on
# `reopened_because:` alone, a `proposed` decision could carry the evidence of
# a decision nobody ever took and validate clean.
DIR_R14B="$BASE/proposed-masquerading-as-reopened"
decision_fixture "$DIR_R14B" "id: stop-semantics
state: pending" "id: baseline-stop-escapee
status: open
blocks: stop-semantics, stop-tooling"
cat >>"$DIR_R14B/dev_docs/research/alpha/tracks/account/questions.md" <<'MASQUERADE'

```decision
id: stop-tooling
state: proposed
reopened_because: it looks like a reopen from here
decided_in: dev_docs/adr/0007-stop-semantics.md
```
MASQUERADE
out_r14b="$(python3 "$SCRIPT" --root "$DIR_R14B" validate 2>&1)"
exit_r14b=$?
assert_exit "a proposed decision carrying reopen evidence exits 1" "$exit_r14b" 1
assert_contains "the masquerading reopen names the state it saw" "$out_r14b" \
  "'decided_in:' on a decision whose state is 'proposed'"
assert_contains "the masquerading reopen says one field does not earn the exemption" \
  "$out_r14b" "\`reopened_because:\` on its own does not earn it"

# --- Fixture (r15): a decision nothing references ------------------------
# A warning, not an error: a decision with no blockers left is a normal end
# state, and failing the tree over it would punish convergence.
DIR_R15="$BASE/dead-decision"
decision_fixture "$DIR_R15" "id: stop-semantics
state: pending" "id: baseline-stop-escapee
status: open
blocks: none: it picks between options the decision already allows"
out_r15="$(python3 "$SCRIPT" --root "$DIR_R15" validate 2>&1)"
exit_r15=$?
assert_exit "a decision nothing references still exits 0" "$exit_r15" 0
assert_contains "the unreferenced decision is warned about" "$out_r15" \
  "decision 'alpha/stop-semantics' is referenced by nothing"
assert_contains "the dead-decision warning says what the report would show" "$out_r15" \
  "can only ever show it as ready"
assert_contains "a warning does not fail the tree" "$out_r15" "research-spike: OK"

# --- Fixture (r16): references never cross a project boundary ------------
# Cross-project destinations are forbidden by construction (fixture (l8)), so a
# reference reaching into a sibling project has no ledger to appear in and is
# dangling — asserted rather than assumed.
DIR_R16="$BASE/blocks-sibling-project"
decision_fixture "$DIR_R16" "id: stop-tooling
state: pending" "id: baseline-stop-escapee
status: open
blocks: stop-semantics"
write_file "$DIR_R16/dev_docs/research/beta/decisions.md" '# beta — decisions

```decision
id: stop-semantics
state: pending
```'
out_r16="$(python3 "$SCRIPT" --root "$DIR_R16" validate 2>&1)"
exit_r16=$?
assert_exit "a blocks: naming a decision only in a sibling project exits 1" "$exit_r16" 1
assert_contains "the sibling-project reference is reported as dangling" "$out_r16" \
  "blocks names decision 'stop-semantics', which does not exist in project 'alpha'"
assert_contains "the sibling-project reference says the scope is deliberate" "$out_r16" \
  "a decision of the same name in a sibling project is not visible here"

# --- Fixture (j): --help lists all six subcommands -----------------------
out_j="$(python3 "$SCRIPT" --help 2>&1)"
for verb in init validate ledger write-ledger status suggest; do
  assert_contains "--help lists '$verb'" "$out_j" "$verb"
done
assert_contains "--help says suggest is advisory" "$out_j" "Advisory"
assert_contains "--help says suggest always exits 0" "$out_j" "always exits 0"

# --- Fixture (p): suggest — the advisory lexical scan ---------------------
# Task 8. Reports unregistered deferral prose with path and line; stays quiet
# next to a registered obligation or an explicit `none:`; never returns
# non-zero, including over a file that fails the block parser; writes
# nothing; and its own usage errors still exit 2 (the dispatcher's contract,
# not the scan's exit-0 guarantee).

DIR_P1="$BASE/suggest-report"
write_file "$DIR_P1/dev_docs/research/alpha/PROJECT.md" '# alpha

This work is deferred to the watcher track.'
out_p1="$(python3 "$SCRIPT" --root "$DIR_P1" suggest 2>&1)"
exit_p1=$?
assert_exit "suggest exits 0 on a tree full of hits" "$exit_p1" 0
assert_contains "suggest reports the phrase with path and line" "$out_p1" \
  "dev_docs/research/alpha/PROJECT.md:3: 'deferred to'"
assert_contains "suggest includes the surrounding line text" "$out_p1" \
  "This work is deferred to the watcher track."

# A hit inside a `question` section that already carries a registered
# obligation is suppressed — the "done correctly" case the design says would
# otherwise dominate the output.
DIR_P2="$BASE/suggest-quiet-obligation"
write_file "$DIR_P2/dev_docs/research/alpha/tracks/account/questions.md" '# account

### Q1. Does the account need an isolated uid domain?

This work is deferred to the watcher track.

```question
id: uid-domain-isolation
status: open
blocks: account-provisioning
```

```obligation
id: uid-domain-provisioning
owes: the provisioning steps this answer implies
destination: dev_docs/tasks/real_task.md
status: open
```'
out_p2="$(python3 "$SCRIPT" --root "$DIR_P2" suggest 2>&1)"
exit_p2=$?
assert_exit "suggest exits 0 next to a registered obligation" "$exit_p2" 0
if [ -z "$out_p2" ]; then
  ok "suggest stays quiet next to a registered obligation"
else
  bad "suggest should be quiet next to a registered obligation, got: $out_p2"
fi

# Same, but the section declares `none:` instead of registering work.
DIR_P3="$BASE/suggest-quiet-none"
write_file "$DIR_P3/dev_docs/research/alpha/tracks/account/questions.md" '# account

### Q1. Does the account need an isolated uid domain?

This work is deferred to the watcher track.

```question
id: uid-domain-isolation
status: open
blocks: account-provisioning
```

```obligation
none: nothing is owed until the option is chosen
```'
out_p3="$(python3 "$SCRIPT" --root "$DIR_P3" suggest 2>&1)"
exit_p3=$?
assert_exit "suggest exits 0 next to an explicit none:" "$exit_p3" 0
if [ -z "$out_p3" ]; then
  ok "suggest stays quiet next to an explicit none: declaration"
else
  bad "suggest should be quiet next to an explicit none:, got: $out_p3"
fi

# A clean tree (no dev_docs/research at all) is silent, like `validate`.
DIR_P4="$BASE/suggest-clean"
mkdir -p "$DIR_P4"
out_p4="$(python3 "$SCRIPT" --root "$DIR_P4" suggest 2>&1)"
exit_p4=$?
assert_exit "suggest exits 0 on a clean (research-less) tree" "$exit_p4" 0
if [ -z "$out_p4" ]; then
  ok "suggest is silent on a clean tree"
else
  bad "suggest should be silent on a clean tree, got: $out_p4"
fi

# The scan always exits 0 — even over a file that fails the block parser (an
# unterminated fence). This is structural, not a policy the scan can opt out
# of: a parse error is a `validate` finding, not a reason for `suggest` to
# stop or crash.
DIR_P5="$BASE/suggest-malformed-fence"
write_file "$DIR_P5/dev_docs/research/alpha/tracks/account/questions.md" '# account

This work is deferred to the watcher track.

```obligation
id: keychain-invariant
owes: the keychain invariant
status: open'
out_p5="$(python3 "$SCRIPT" --root "$DIR_P5" suggest 2>&1)"
exit_p5=$?
assert_exit "suggest exits 0 even when a file fails the block parser" "$exit_p5" 0
assert_not_contains "suggest does not traceback over a malformed fence" "$out_p5" "Traceback"
# "report it and continue" (the spec), not silent absorption: the file is
# named, and a hit sitting before the broken fence is still reported rather
# than the whole file reading as clean.
assert_contains "suggest reports the unterminated fence by name" "$out_p5" \
  "dev_docs/research/alpha/tracks/account/questions.md:5: unterminated fence"
assert_contains "a hit before the unterminated fence is still reported" "$out_p5" \
  "questions.md:3: 'deferred to' — This work is deferred to the watcher track."

# The dispatcher's usage contract is not suppressed by the scan's exit-0
# guarantee: a genuinely malformed invocation is still a caller error.
out_p6="$(python3 "$SCRIPT" suggest --bogus-flag 2>&1)"
exit_p6=$?
assert_exit "suggest --bogus-flag exits 2" "$exit_p6" 2
assert_contains "suggest --bogus-flag names the bad flag" "$out_p6" "unrecognized arguments"

# suggest writes nothing: the tree is byte-identical before and after a run.
DIR_P7="$BASE/suggest-no-write"
write_file "$DIR_P7/dev_docs/research/alpha/PROJECT.md" '# alpha

This work is deferred to the watcher track, and gated on the account track.'
cp -r "$DIR_P7/dev_docs" "$BASE/suggest-no-write-before"
python3 "$SCRIPT" --root "$DIR_P7" suggest >/dev/null 2>&1
if diff -r "$BASE/suggest-no-write-before" "$DIR_P7/dev_docs" >"$BASE/suggest.diff" 2>&1; then
  ok "suggest writes nothing (tree unchanged after a run)"
else
  bad "suggest modified the tree: $(cat "$BASE/suggest.diff")"
fi

# A phrase inside a fenced sample or an HTML comment is inert, same as
# `validate`'s own record scanning — a worked example is not a deferral.
DIR_P8="$BASE/suggest-inert-regions"
write_file "$DIR_P8/dev_docs/research/alpha/PROJECT.md" '# alpha

```markdown
This example shows "deferred to" inside a fenced sample.
```

<!--
This one is deferred to a comment nobody reads.
-->

Once the watcher lands, revisit this.'
out_p8="$(python3 "$SCRIPT" --root "$DIR_P8" suggest 2>&1)"
exit_p8=$?
assert_exit "suggest exits 0 over fenced/commented samples" "$exit_p8" 0
assert_not_contains "a phrase inside a fenced sample is not reported" "$out_p8" \
  "inside a fenced sample"
assert_not_contains "a phrase inside an HTML comment is not reported" "$out_p8" \
  "a comment nobody reads"
assert_contains "the 'once … lands' pattern is still caught outside a fence" "$out_p8" \
  "Once the watcher lands"

# The same "report it and continue" treatment for an unterminated HTML
# comment — the other door to the same silent-swallowing failure.
DIR_P9="$BASE/suggest-unterminated-comment"
write_file "$DIR_P9/dev_docs/research/alpha/tracks/account/questions.md" '# account

This work is left to a future track.

<!-- an example nobody closed

More prose that is now inert.'
out_p9="$(python3 "$SCRIPT" --root "$DIR_P9" suggest 2>&1)"
exit_p9=$?
assert_exit "suggest exits 0 even when a comment never closes" "$exit_p9" 0
assert_contains "suggest reports the unterminated comment by name" "$out_p9" \
  "dev_docs/research/alpha/tracks/account/questions.md:5: unterminated HTML comment"
assert_contains "a hit before the unterminated comment is still reported" "$out_p9" \
  "questions.md:3: 'left to' — This work is left to a future track."

# --- Fixture (q): the exit-0 guarantee survives an unreadable directory --
# `discover`'s own `iterdir()` calls (unlike `rglob`/`is_file`, which already
# swallow `PermissionError` on the Python versions this runs on) raise on a
# directory with no read permission. `verb_suggest` wraps its whole body in
# one `try`/`except OSError` so this stays exit-0 and reported, not a bare
# traceback indistinguishable from a real finding.
if [ "$(id -u)" -eq 0 ]; then
  echo "  … skipped: running as root — chmod 000 does not restrict access"
else
  DIR_Q1="$BASE/suggest-unreadable-dir"
  write_file "$DIR_Q1/dev_docs/research/alpha/PROJECT.md" '# alpha'
  RESEARCH_Q1="$DIR_Q1/dev_docs/research"
  chmod 000 "$RESEARCH_Q1"
  out_q1="$(python3 "$SCRIPT" --root "$DIR_Q1" suggest 2>&1)"
  exit_q1=$?
  chmod 755 "$RESEARCH_Q1"
  assert_exit "suggest exits 0 over an unreadable directory" "$exit_q1" 0
  assert_contains "suggest reports the scan failure" "$out_q1" "cannot scan —"
  assert_not_contains "suggest does not traceback over an unreadable directory" "$out_q1" \
    "Traceback"
fi

echo
echo "test-research-spike: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-research-spike: OK"
