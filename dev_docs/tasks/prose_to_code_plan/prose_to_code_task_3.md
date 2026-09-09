---
title: Guard the research-spike tutorial's scratch root with a script
priority: high
size: 1
impact: 3
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - skills/research-spike-tutorial/SKILL.md:57-65
  - skills/research-spike-tutorial/SKILL.md:422-429
  - scripts/lint-bash4.sh
  - scripts/test-shell.sh
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - safety
---

Plan: [[prose_to_code_plan]] · Index row 11.

## Context

The tutorial creates a scratch project under `$WORK` and ends with `rm -rf`
on it. Twice the prose asks the agent to hand-check that the path is
non-empty, absolute, and not inside the learner's repo — "It has already
happened once, during this skill's own development." Three path predicates are
a script, not a reading exercise.

## Task

1. Add `scripts/tutorial-root-guard.sh <path>`: exit 0 when the path is
   non-empty, absolute, exists, and is not equal to or under
   `git rev-parse --show-toplevel` of the caller's cwd; otherwise print the
   failing predicate to stderr and exit 1. Bash 3.2-clean (passes
   `scripts/lint-bash4.sh`).
2. Add `scripts/test-tutorial-root-guard.sh` following `scripts/test-shell.sh`'s
   fixture convention (`GIT_CONFIG_GLOBAL=/dev/null`): empty path, relative
   path, path inside a fixture repo, path outside it.
3. Rewrite the two SKILL.md passages to run the guard before the first command
   and before the final `rm -rf`; keep the "stop and re-derive" sentence as
   the failure branch.

## Acceptance Criteria

- Code-enforced: `just check` passes; the test proves the inside-repo case
  exits 1.
- Code-enforced: `rg -n 'rm -rf' skills/research-spike-tutorial/SKILL.md`
  shows the guard invoked on the line before.
