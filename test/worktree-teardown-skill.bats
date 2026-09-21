#!/usr/bin/env bats
# skills/worktree-teardown/SKILL.md documents commands an agent will type, and
# it was ported from a repo where those commands were `just` recipes
# (`dli git worktree-remove`, `dli git branch-remove`). Nothing else in the gate
# compares prose to the scripts that ship: validate.py checks the
# ${CLAUDE_PLUGIN_ROOT} references exist, which catches a path that moved, but a
# sentence telling the reader to run a recipe this plugin does not have is not a
# path reference at all and would pass everything.

setup() {
  setup_test
  SKILL="$REPO_ROOT/skills/worktree-teardown/SKILL.md"
}
teardown() { teardown_test; }
load test_helper

@test "every scripts/<file> the skill names exists" {
  run python3 -c '
import re, sys
from pathlib import Path
root, skill = Path(sys.argv[1]), Path(sys.argv[2])
named = sorted(set(re.findall(r"scripts/[A-Za-z0-9._-]+\.(?:sh|py)", skill.read_text())))
missing = [n for n in named if not (root / n).exists()]
print("named:", len(named))
print("missing:", missing)
sys.exit(1 if missing or not named else 0)
' "$REPO_ROOT" "$SKILL"
  assert_success
  refute_output --partial "named: 0"
}

# `assert_failure 1`, not a bare one: grep exits 2 on a regex or file error, and
# a bare assertion reads that as "no retired recipe found". The pattern spells
# its own word boundaries for the same reason — `\b` is a GNU extension, and BSD
# grep on the macOS runner would not error on it, it would match differently,
# which is a vacuous pass on the one platform that differs.
@test "the skill names no retired dli/just recipe" {
  run grep -nE '(^|[^A-Za-z0-9_])dli([^A-Za-z0-9_]|$)|just --justfile' "$SKILL"
  assert_failure 1
}
