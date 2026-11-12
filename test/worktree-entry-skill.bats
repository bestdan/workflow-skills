#!/usr/bin/env bats
# skills/worktree-entry/SKILL.md documents commands an agent will type, and it
# was moved from bestdan/dotfiles (agents/CLAUDE_ONLY.md), where the teardown
# half of the same material named `dli` recipes. validate.py checks the
# ${CLAUDE_PLUGIN_ROOT} references exist and, in its FORBIDDEN_WORDING table,
# that the skill names no such recipe; a bare `scripts/<file>` is not one of
# those and would pass everything. Modelled on
# test/worktree-teardown-skill.bats.

setup() {
  setup_test
  SKILL="$REPO_ROOT/skills/worktree-entry/SKILL.md"
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
