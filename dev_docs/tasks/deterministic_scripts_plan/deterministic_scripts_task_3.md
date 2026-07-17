---
title: Extend validate.py for doctor field/expiry checks + fix consumer-repo path bug
priority: high
size: 3
status: ready
created: 2026-07-17
expires: 2026-08-16
source_branch: claude/sleepy-ride-8d4bjx
related_files:
  - scripts/validate.py # extend; parameterize task dir; keep frontmatter-shape authority
  - commands/doctor.md # check 1 (~48), checks 4 (~112-133), check 5 (~136-138) — the path bug + hand-sim fallback
  - skills/task/SKILL.md # Field reference (the required-fields source of truth)
  - scripts/check.sh
parent: deterministic_scripts
impact: 5
tags: [scripts, doctor, bugfix]
---

# Task 3 — Extend `validate.py` for doctor + fix the consumer-repo path bug

Part of [[deterministic_scripts_plan]]. Audit Finding #3. **Fixes a real defect**
— prioritize accordingly. Independent of task 1 (they stay separate authorities:
`validate.py` owns frontmatter shape, `task-scan.py` owns scan/rank).

## Context

`/doctor` already delegates its check 4.1 to `scripts/validate.py`. Two problems:

**1. Latent path bug (the defect).** `doctor.md` invokes
`uv run "$(git rev-parse --show-toplevel)/scripts/validate.py"` (around line
118, with `ROOT="$(git rev-parse --show-toplevel)"` at ~48). In a **consumer
repo** — the plugin's actual deployment target — there is no
`scripts/validate.py` at the repo root, so check 4.1 silently can't run. And
`validate.py`'s own `ROOT = Path(__file__).resolve().parent.parent` means even
when found via the plugin install dir it validates the **plugin's own**
`dev_docs/tasks/`, not the user's. Check 4.1 only works today inside the
`workflow-skills` repo itself.

**2. Hand-simulation fallback (the anti-pattern).** `doctor.md`'s fallback
clause (~line 138) says "if `uv` is unavailable, read the frontmatter shape
rules from `scripts/validate.py` and apply them yourself" — an instruction to
hand-simulate a script, the purest form of the re-derivation this audit is
about. And check 4.2 (missing required fields), `expires` semantics (never
checked by `validate.py` at all), and check 5's expired-task computation
(`expires < today && status` non-terminal) are deterministic frontmatter
arithmetic re-derived per card.

Checks 2 (MCP reachability), 3 (shared migration procedure reference), and 6
(config advice) are correctly prose — leave them.

## Task

1. **Fix the path bug in `validate.py`:** take the task dir as an **argument**
   (default preserves today's behavior for the repo's own CI), so `/doctor` can
   pass the consumer's task dir. Stop deriving the validated tree from
   `__file__`'s parent when an explicit dir is given.
2. **Fix `doctor.md`:** resolve the script via the **plugin root**, not
   `git rev-parse --show-toplevel`, and pass the consumer's task dir as the
   argument. Remove the "read the rules and apply them yourself" hand-sim
   fallback (~line 138) — if `uv`/the script is genuinely unavailable, report
   that check as **skipped**, don't simulate it.
3. **Add to `validate.py`:** check 4.2 missing-required-fields (per
   `skills/task/SKILL.md` Field reference — the single source of truth) and
   `expires` semantics, plus check 5's expired-task computation, so doctor
   delegates all of 4.x/5 instead of re-deriving.
4. **Tests:** extend `validate.py`'s test coverage (in `scripts/check.sh`) with
   fixtures for: a missing-required-field card, an expired non-terminal card, an
   expired-but-`done` card (not flagged), and the explicit-task-dir argument
   (validates the passed dir, not the plugin's own).

## Acceptance Criteria

**Code-enforced:**

- `scripts/check.sh` passes with the new fixtures.
- `scripts/validate.py <some-dir>` validates `<some-dir>`, provably not the
  plugin's own `dev_docs/tasks` (a fixture in a temp dir asserts this).
- `rg -n "git rev-parse --show-toplevel" commands/doctor.md` no longer appears
  in the validate.py invocation (resolved via plugin root instead).
- `rg -n "apply them yourself|read the frontmatter shape rules" commands/doctor.md`
  returns nothing (hand-sim fallback removed).

**User-run:**

- Simulate a consumer repo (a checkout with a `dev_docs/tasks/` but no
  `scripts/validate.py`): run `/doctor`'s check 4 flow and confirm it validates
  the **consumer's** task files, or cleanly reports "skipped" if the script is
  unreachable — never silently validates the wrong tree.
