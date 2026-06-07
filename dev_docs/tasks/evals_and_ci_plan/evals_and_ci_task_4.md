---
title: Add flag-gated behavioral eval harness
priority: medium
size: 5
status: done
created: 2026-05-29
source_branch: bestdan/skills-evals-ci
source_pr: 15
related_files:
  - evals/manifest.tsv
  - scripts/eval.sh
  - .github/workflows/evals.yml
  - evals/README.md
is_blocked_by: evals_and_ci_task_3
expires: 2026-06-28
tags:
  - ci
  - tests
---

← [[evals_and_ci_plan|Overview]]

# Task 4 — Behavioral eval harness (flag-gated)

Add an opt-in harness that verifies Claude actually **invokes** the right skill
when given a naive trigger prompt that never names it. Non-blocking: it costs API
credits and is nondeterministic, so it runs only via an explicit flag locally and
a manual `workflow_dispatch` job in CI.

## Context

- Pattern adapted from `obra/superpowers` `tests/skill-triggering/` (the only
  automatable eval artifact in the canonical large skills repo): run the `claude`
  CLI headless with `--output-format stream-json`, then grep the log for a
  `Skill` tool invocation matching the expected skill.
- This asserts **routing/invocation**, not output quality. LLM-as-judge quality
  scoring (promptfoo / `bkper/claude-eval`, Anthropic's JSON-rubric format) is an
  explicit non-goal here — leave a documented extension point.
- Only `user-invocable: true` (or unset) skills are realistically auto-routable
  from a prompt. `analysis-conventions` is `user-invocable: false` (context-load
  only) — exclude it or test it differently. Decide per skill which trigger
  prompt is fair.
- Auth: CI uses the `ANTHROPIC_API_KEY` secret. **Deviation from the original
  plan:** the harness does _not_ hard-fail when the env var is unset, because a
  locally logged-in CLI (OAuth/subscription) is also valid auth — it prints a
  note and proceeds, letting `claude` surface a real auth error if there's none.

## Task

1. **`evals/` directory:**
   - `evals/prompts/<skill>.txt` — one naive trigger prompt per testable skill
     (e.g. `co-review.txt`: "Can you review PR #123 for me, reconcile it with the
     bot comments, and fix the easy stuff?" — never says "co-review").
   - `evals/manifest.tsv` (or `.json`) — maps each prompt file → expected skill
     name (and optional `max_turns`). Single source the runner iterates.
   - `evals/README.md` — what the harness asserts, how to add a case, and the
     documented LLM-judge extension point.
2. **`scripts/eval.sh`** (bash, mirrors superpowers' `run-test.sh` + `run-all.sh`):
   - For each manifest row: `timeout 300 claude -p "$(cat prompt)"
     --plugin-dir "$PWD" --dangerously-skip-permissions --max-turns N
     --output-format stream-json > log`.
   - Pass iff the log contains `"name":"Skill"` and a skill field matching the
     expected name (regex tolerant of the `plugin:` prefix). Print which skills
     _were_ triggered on failure, for debugging.
   - Tally PASS/FAIL; `exit 1` if any failed. Fail fast with a clear message if
     `ANTHROPIC_API_KEY` is unset.
3. **Wire the flag:** `scripts/check.sh --with-evals` calls `scripts/eval.sh`
   after the deterministic checks (replacing the step-2 placeholder). `just eval`
   already maps to this.
4. **`.github/workflows/evals.yml`:**
   - Trigger: `workflow_dispatch` (manual) — optionally also `pull_request` gated
     on a `run-evals` label so it stays opt-in.
   - Installs `claude` CLI, exports `ANTHROPIC_API_KEY` from repo secrets, runs
     `scripts/eval.sh`.
   - **Non-blocking:** not a required check; never gates merges.

## Acceptance Criteria

**Code-enforced:**

- Detection logic verified against synthetic `stream-json` fixtures (no API
  spend): correct skill (incl. `workflow-skills:` prefix) → PASS; different
  skill → no match; no `Skill` invocation → no match.
- `evals.yml` is `workflow_dispatch`-only — not a required check, can't block a
  merge.
- `scripts/check.sh --with-evals` routes to the (executable) `scripts/eval.sh`;
  plain `scripts/check.sh` (what the blocking CI runs) does not.

**User-run (opt-in — costs API credits, intentionally not run during build):**

- Run `just eval` locally and review the tally; for any skill that fails to
  trigger, decide whether the prompt is unfair or the skill `description` needs
  tightening (feeds back into skill authoring).
- Add `ANTHROPIC_API_KEY` to repo Actions secrets, then trigger the **Evals**
  workflow manually and confirm it runs.
- `analysis-conventions` is excluded from the manifest (non-user-invocable).
