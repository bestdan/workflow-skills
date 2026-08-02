---
title: "research-spike: packaging — README count, trigger eval, claude.ai zip"
priority: medium
size: 2
status: done
created: 2026-08-01
completed: 2026-08-02
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - README.md
  - evals/manifest.tsv
  - evals/prompts/research-spike.txt
  - scripts/build-claude-ai-zips.sh
  - CONTRIBUTING.md
is_blocked_by: research_spike_task_9
parent: research_spike
expires: 2026-08-31
tags: [research-spike, packaging, evals]
---

[[research_spike_plan]]

## Context

`CONTRIBUTING.md` §"Adding a skill" is the checklist this task discharges, and
two of its items are gated in CI: `scripts/validate.py` asserts the README
"N skills, M commands, K subagent" sentence matches reality, so a new skill
that skips it turns the gate red for everyone.

The trigger eval is the part that is easy to do badly. The prompt must be a
**realistic prompt that does not name the skill** — it proves the skill
auto-routes, and a prompt containing "research spike" proves only that string
matching works.

## Task

- **`README.md`** — bump the component-count sentence: `13 skills` → `14
  skills`, with the command and subagent counts **unchanged** (plan decision 1
  ships no `commands/` file). Add `research-spike` to the workflow section it
  belongs in, with a one-or-two-line description matching the skill's actual
  scope: spike management whose instrument is the obligation ledger.
- **`evals/prompts/research-spike.txt`** — a naive prompt that must route here
  without naming the skill. Write it from the origin symptom, e.g. a maintainer
  asking why a research repo feels stuck when the open-question count keeps
  going down. Avoid the words "research spike", "obligation", and "ledger".
- **`evals/manifest.tsv`** — add the row (`research-spike`,
  `prompts/research-spike.txt`, `max_turns`), keeping the file's tab-separated
  shape.
- **`scripts/build-claude-ai-zips.sh`** — confirm the default `skills/<name>`
  copy is sufficient. It is, unless the skill grows an out-of-tree dependency;
  it does depend on `scripts/research-spike.py`, so add the special-bundling
  case (as `review-facts` and `task` already have) so the claude.ai zip is not
  a skill whose every procedure references a script that was never shipped.
- Sanity-check the description against `scripts/validate.py`'s 1024-char cap
  and the 1536-char description+when_to_use truncation noted in its docstring.

## Acceptance Criteria

**Code-enforced:**

- `uv run scripts/validate.py` passes, including the README component-count
  check.
- `bash scripts/check.sh` green.
- `scripts/build-claude-ai-zips.sh` produces `research-spike.zip` containing
  `SKILL.md`, `references/`, **and** the script it calls; unzip in the test or
  assert on `zipinfo` output.

**User-run:**

- `just eval` (needs `ANTHROPIC_API_KEY`) shows the new prompt auto-invoking
  `research-spike`. If it routes elsewhere, tune the `description` — not the
  prompt; a prompt edited until it passes proves nothing.
