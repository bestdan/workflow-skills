#!/usr/bin/env bats
# skills/pinned-checkout/SKILL.md was written from a user-level copy of the same
# reference that described one machine's own setup, and it is read by plugin
# users who have none of it. validate.py checks the ${CLAUDE_PLUGIN_ROOT}
# references exist and, in its FORBIDDEN_WORDING table, that the skill names no
# owner-specific setup and repeats no worktree-entry route (#933); it does not
# check a bare `scripts/<file>`, which is what this file is for. Modelled on
# test/worktree-entry-skill.bats.

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
