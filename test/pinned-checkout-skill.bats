#!/usr/bin/env bats
# skills/pinned-checkout/SKILL.md was written from a user-level copy of the same
# reference that described one machine's own setup, and it is read by plugin
# users who have none of it. Nothing else in the gate compares this prose to
# what ships: validate.py checks the ${CLAUDE_PLUGIN_ROOT} references exist, but
# not a bare `scripts/<file>`, a stray owner-specific name, or a worktree route
# copied in from worktree-entry, which is that material's one home (#933).
# Modelled on test/worktree-entry-skill.bats.

setup() {
  setup_test
  SKILL="$REPO_ROOT/skills/pinned-checkout/SKILL.md"
}
teardown() { teardown_test; }
load test_helper

@test "every scripts/<file> the skill names exists" {
  run python3 -c '
import re, sys
from pathlib import Path
root, skill = Path(sys.argv[1]), Path(sys.argv[2])
named = sorted(set(re.findall(r"scripts/[A-Za-z0-9._/-]+\.(?:sh|py)", skill.read_text())))
missing = [n for n in named if not (root / n).exists()]
print("named:", len(named))
print("missing:", missing)
sys.exit(1 if missing or not named else 0)
' "$REPO_ROOT" "$SKILL"
  assert_success
  refute_output --partial "named: 0"
}

# `assert_failure 1`, not a bare one: grep exits 2 on a regex or file error, and
# a bare assertion would read that as "nothing found".
@test "the skill names no owner-specific setup" {
  run grep -rnE 'dotfiles|bestdan|DOTFILES_' "$REPO_ROOT/skills/pinned-checkout/"
  assert_failure 1
}

@test "the skill links to worktree-entry and repeats none of its routes" {
  run grep -c 'worktree-entry/SKILL.md' "$SKILL"
  assert_success
  run grep -rnE 'switch -C|--no-track' "$REPO_ROOT/skills/pinned-checkout/"
  assert_failure 1
}
