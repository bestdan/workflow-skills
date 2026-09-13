#!/usr/bin/env bats
# scripts/coreview-conventions.sh, and the assembled <INPUT> it feeds.
#
# The first half pins the script's contract against fixture plugin roots under
# a throwaway HOME. The second half runs each reviewer's DOCUMENTED assembly
# line — copied from skills/co-review/reviewers/*.md, --local shape — against
# stub binaries that record what they were handed, and asserts the conventions
# landed after the rubric and before the diff. agy and devin come first and are
# the point: they do not read stdin, so a segment wired into the pipe reaches
# codex/copilot/crush and silently misses them.

setup() {
  setup_test
  SCRIPT="$REPO_ROOT/scripts/coreview-conventions.sh"
  SKILL="$REPO_ROOT/skills/co-review"
  H="$TEST_TMPDIR/home"
  SAW="$TEST_TMPDIR/saw"
  INPUT_DIR="$TEST_TMPDIR/co-review-input"
  NEUTRAL="$TEST_TMPDIR/neutral"
  mkdir -p "$H" "$SAW" "$INPUT_DIR"
  unset AGENT_GUIDANCE_DIR
}
teardown() { teardown_test; }
load test_helper

# A plugin root the resolver accepts, shipping the named convention files. Each
# carries a marker line no other part of <INPUT> contains.
mkroot() { # dir file...
  local dir="$1"
  shift
  mkdir -p "$dir/.claude-plugin"
  printf '{"name":"agent-guidance"}\n' >"$dir/.claude-plugin/plugin.json"
  printf '# portable\n' >"$dir/portable.md"
  for f in "$@"; do
    printf '# %s\nMARK-%s\n' "$f" "$f" >"$dir/$f"
  done
}

# The script, resolving nothing but what the test set up.
conventions() { run env HOME="$H" "$@" bash "$SCRIPT"; }

# A repo with one uncommitted change, so `git diff HEAD` has a hunk to append.
mkfixture() {
  FIX="$TEST_TMPDIR/repo"
  mkdir -p "$FIX"
  git -C "$FIX" init -q
  printf 'before\n' >"$FIX/a.txt"
  git -C "$FIX" add a.txt
  git -C "$FIX" commit -q -m init
  printf 'MARK-DIFF\n' >"$FIX/a.txt"
}

# The <CONVENTIONS> substitution: the script's stdout, each line double-quoted,
# as extra arguments to the assembling cat. Empty when the script listed nothing.
conv_args() { # env...
  local line out=""
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    out="$out \"$line\""
  done < <(env HOME="$H" "$@" bash "$SCRIPT" 2>/dev/null)
  printf '%s' "$out"
}

# Reviewer stubs. Each reads its input the way the real binary does and copies
# what it read to $SAW/<name>, then emits the completion sentinel.
stub_reviewers() {
  make_stub agy \
    'p=""; while [ $# -gt 0 ]; do case "$1" in -p) shift; p="$1" ;; esac; shift; done' \
    'f="${p#*file at }"; f="${f%% (a review*}"' \
    "cat \"\$f\" >\"$SAW/agy\"" \
    'echo "REVIEW_COMPLETE: PASS"'
  make_stub devin \
    'f=""; while [ $# -gt 0 ]; do case "$1" in --prompt-file) shift; f="$1" ;; esac; shift; done' \
    "cat \"\$f\" >\"$SAW/devin\"" \
    'echo "REVIEW_COMPLETE: PASS"'
  make_stub codex \
    "cat >\"$SAW/codex\"" \
    'echo "REVIEW_COMPLETE: PASS"'
}

line_of() { grep -n -m1 -F -- "$2" "$1" | cut -d: -f1; }

# assert_assembled <file> — rubric, then both conventions in order, then diff.
assert_assembled() {
  local f="$1" rubric writing reviewing diff
  rubric="$(line_of "$f" '# co-review reviewer rubric')"
  writing="$(line_of "$f" 'MARK-writing_about_code.md')"
  reviewing="$(line_of "$f" 'MARK-reviewing.md')"
  diff="$(line_of "$f" '+MARK-DIFF')"
  [ -n "$rubric" ] && [ -n "$writing" ] && [ -n "$reviewing" ] && [ -n "$diff" ]
  [ "$rubric" -lt "$writing" ]
  [ "$writing" -lt "$reviewing" ]
  [ "$reviewing" -lt "$diff" ]
}

# The documented --local assembly line for each reviewer, with the placeholders
# substituted. CONV is the <CONVENTIONS> argument string (possibly empty).
agy_line() {
  local in="$INPUT_DIR/co-review-input.agy"
  printf '%s' "cat \"$SKILL/review_prompt.md\"$CONV > \"$in\" && git diff HEAD >> \"$in\" && agy --sandbox --add-dir \"$INPUT_DIR\" -p \"Your entire input is the file at $in (a review rubric followed by a diff). Read that file and review ONLY it. Do NOT explore any other file, run commands, or retrieve any prior conversation or memory. If that file is missing or empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\" --model \"Gemini 3.6 Flash (High)\""
}
devin_line() {
  local in="$INPUT_DIR/co-review-input.devin"
  printf '%s' "cat \"$SKILL/review_prompt.md\"$CONV > \"$in\"; git diff HEAD >> \"$in\"; [ -s \"$in\" ] && mkdir -p \"$NEUTRAL\" && cd \"$NEUTRAL\" && devin -p --prompt-file \"$in\" --permission-mode auto --respect-workspace-trust false --model \"swe-1.6\""
}
codex_line() {
  local in="$INPUT_DIR/co-review-input.codex"
  printf '%s' "cat \"$SKILL/review_prompt.md\"$CONV > \"$in\"; git diff HEAD >> \"$in\"; cat \"$in\" | codex exec --sandbox read-only --model \"gpt-5.6-terra\" \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\""
}

dispatch() { run bash -c "cd \"$FIX\" && $1"; }

# ------------------------------------------------------ the script's contract

@test "lists writing_about_code.md then reviewing.md from the resolved root" {
  mkroot "$TEST_TMPDIR/plugin" writing_about_code.md reviewing.md
  conventions AGENT_GUIDANCE_DIR="$TEST_TMPDIR/plugin"
  assert_success
  assert_line --index 0 "$TEST_TMPDIR/plugin/writing_about_code.md"
  assert_line --index 1 "$TEST_TMPDIR/plugin/reviewing.md"
  [ "${#lines[@]}" -eq 2 ]
}

@test "an installed copy missing one file lists the other and names the gap" {
  mkroot "$TEST_TMPDIR/plugin" writing_about_code.md
  conventions AGENT_GUIDANCE_DIR="$TEST_TMPDIR/plugin"
  assert_success
  # stderr is unbuffered and `run` merges the streams, so the note can land
  # before the listing; assert on membership, not position.
  assert_line "$TEST_TMPDIR/plugin/writing_about_code.md"
  assert_output --partial 'has no reviewing.md'
  refute_line "$TEST_TMPDIR/plugin/reviewing.md"
}

@test "an installed copy with neither file exits 3 with an empty stdout" {
  mkroot "$TEST_TMPDIR/plugin"
  run env HOME="$H" AGENT_GUIDANCE_DIR="$TEST_TMPDIR/plugin" bash -c "'$SCRIPT' 2>/dev/null"
  assert_failure 3
  assert_output ""
  conventions AGENT_GUIDANCE_DIR="$TEST_TMPDIR/plugin"
  assert_output --partial 'nothing to attach'
}

@test "no plugin anywhere exits 3 with an empty stdout" {
  run env HOME="$H" bash -c "'$SCRIPT' 2>/dev/null"
  assert_failure 3
  assert_output ""
}

@test "a set-but-invalid AGENT_GUIDANCE_DIR exits 1, not 3" {
  conventions AGENT_GUIDANCE_DIR="$TEST_TMPDIR/nonexistent"
  assert_failure 1
  assert_output --partial 'AGENT_GUIDANCE_DIR is set but is not a plugin root'
}

@test "an unknown argument is a usage error, exit 2" {
  run env HOME="$H" bash "$SCRIPT" --bogus
  assert_failure 2
}

# ----------------------------------------- the assembled <INPUT>, per reviewer

@test "agy's <INPUT> carries the conventions between rubric and diff" {
  mkroot "$TEST_TMPDIR/plugin" writing_about_code.md reviewing.md
  mkfixture
  stub_reviewers
  CONV="$(conv_args AGENT_GUIDANCE_DIR="$TEST_TMPDIR/plugin")"
  [ -n "$CONV" ]
  dispatch "$(agy_line)"
  assert_success
  assert_output --partial 'REVIEW_COMPLETE: PASS'
  assert_assembled "$SAW/agy"
}

@test "devin's <INPUT> carries the conventions between rubric and diff" {
  mkroot "$TEST_TMPDIR/plugin" writing_about_code.md reviewing.md
  mkfixture
  stub_reviewers
  CONV="$(conv_args AGENT_GUIDANCE_DIR="$TEST_TMPDIR/plugin")"
  dispatch "$(devin_line)"
  assert_success
  assert_output --partial 'REVIEW_COMPLETE: PASS'
  assert_assembled "$SAW/devin"
}

@test "a stdin reviewer's <INPUT> carries the conventions too" {
  mkroot "$TEST_TMPDIR/plugin" writing_about_code.md reviewing.md
  mkfixture
  stub_reviewers
  CONV="$(conv_args AGENT_GUIDANCE_DIR="$TEST_TMPDIR/plugin")"
  dispatch "$(codex_line)"
  assert_success
  assert_assembled "$SAW/codex"
}

@test "an unresolvable plugin still yields a valid <INPUT> and a dispatch" {
  mkfixture
  stub_reviewers
  CONV="$(conv_args)"
  [ -z "$CONV" ]
  dispatch "$(agy_line)"
  assert_success
  assert_output --partial 'REVIEW_COMPLETE: PASS'
  dispatch "$(devin_line)"
  assert_success
  assert_output --partial 'REVIEW_COMPLETE: PASS'
  for r in agy devin; do
    [ "$(line_of "$SAW/$r" '# co-review reviewer rubric')" -lt "$(line_of "$SAW/$r" '+MARK-DIFF')" ]
    run grep -c 'MARK-writing_about_code.md\|MARK-reviewing.md' "$SAW/$r"
    assert_output "0"
  done
}

# -------------------------------------------------------- the documented shape

@test "every reviewer's assembling cat carries <CONVENTIONS>" {
  for f in "$SKILL"/reviewers/*.md; do
    assert_doc_contains "$f" 'cat "<this skill dir>/review_prompt.md" <CONVENTIONS>'
  done
  assert_doc_contains "$SKILL/SKILL.md" 'scripts/coreview-conventions.sh'
}
