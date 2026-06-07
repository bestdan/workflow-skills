---
title: Add repo-native validator and local check entrypoint
priority: medium
size: 5
status: done
created: 2026-05-29
source_branch: bestdan/skills-evals-ci
source_pr: 15
related_files:
  - scripts/validate.py
  - scripts/check.sh
  - justfile
is_blocked_by: evals_and_ci_task_1
expires: 2026-06-28
tags:
  - tooling
  - tests
---

← [[evals_and_ci_plan|Overview]]

# Task 2 — Repo-native validation + local entrypoint

Add the deterministic checks as self-contained scripts plus a `justfile`, so the
full gate runs locally with one command. No GitHub Actions yet — this PR is
verifiable purely by running the entrypoint.

## Context

- The repo already configures `dprint` (`dprint.json`) and the `claude` CLI
  ships `plugin validate --strict`. Those two are off-the-shelf; everything
  _repo-specific_ needs a custom validator.
- Component layout the validator must understand:
  - `skills/<name>/SKILL.md` — frontmatter: `name?`, `description`,
    `user-invocable?`. Directory name is the canonical invocation name.
  - `commands/*.md` — frontmatter: `description`, `allowed-tools?`,
    `argument-hint?`.
  - `commands/handlers/*.md` — **reference procedures bundled into the `todo`
    skill, NOT slash commands; they have no frontmatter** and are excluded from
    validation. (The original plan wrongly assumed they carried frontmatter.)
  - `agents/*.md` — frontmatter: `name`, `description`, `tools`, `model`,
    `color?`.
  - `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`.
- Authoritative frontmatter rules to encode (from Anthropic skill-authoring
  best practices + Claude Code plugin reference):
  - `name`: `^[a-z0-9-]+$`, ≤64 chars, must not contain `claude`/`anthropic`.
  - `description`: non-empty, ≤1024 chars (Anthropic) — warn approaching the
    1536-char combined Claude Code cap.
  - SKILL.md body ≤500 lines (all current skills pass; largest is
    `skills/todo/SKILL.md` at 212).
- User convention: scripts are Python or `*.sh`; the command runner is `just`;
  Python scripts run via `uv run`.
- **Dependency note:** `scripts/validate.py` is **dev/CI-only tooling** — it is
  never loaded by Claude Code as a plugin component and never reaches plugin
  consumers at runtime (the plugin ships only `skills/`, `commands/`, `agents/`,
  `.claude-plugin/`). So a YAML parser here is a test/dev dependency, which is
  approved. Use a real parser (`yaml.safe_load`) rather than hand-rolling
  frontmatter parsing. **Pin it:** choose an exact, security-checked version
  (PyYAML's `safe_load` avoids the historical `yaml.load` RCE class; verify the
  pinned release has no open CVEs) and **hash-pin** it via `uv lock --script
  scripts/validate.py` (commits a `*.lock` next to the script) so CI installs a
  hash-verified, reproducible dependency. Declare the dep in PEP 723 inline
  metadata at the top of the script.

## Task

1. **`scripts/validate.py`** (run via `uv run`; PEP 723 inline metadata with a
   pinned, hash-locked `pyyaml`) — repo-specific structural & consistency checks. Each failure prints `path: message` and the script exits
   non-zero if any failure occurred:
   - Every `skills/*/SKILL.md` has parseable YAML frontmatter with a non-empty
     `description`; if `name` is present it equals the directory name and matches
     the slug rules above.
   - `description` length within bounds (error >1024 hard cap is Anthropic's;
     pick error at >1536 to match Claude Code, warn at >1024 — decide one and
     document it in the script header).
   - SKILL.md body (after frontmatter) ≤500 lines.
   - Every top-level `commands/*.md` has a non-empty `description`; if
     `allowed-tools` present, it's a string or list. (`commands/handlers/*.md`
     excluded — no frontmatter.) Note: three `argument-hint` values
     (`claim-todo`, `process-todo`, `promote-todos`) were invalid YAML
     (trailing text after `]`); they were quoted so the block parses strictly.
   - Every `agents/*.md` has `name`, `description`, `tools`.
   - **Version sync:** assert `plugin.json.version` is present and semver-shaped
     and **equals** the `marketplace.json` plugin entry `version` (step 1 synced
     them to `0.6.0`). This is the guard that prevents future drift.
   - **Manifest cross-consistency:** `plugin.json.name` == marketplace plugin
     entry `name`; descriptions present in both.
   - **README counts:** parse `README.md:14`'s "N skills, M commands, K
     subagent(s)" claim and assert it matches the real counts
     (`skills/*/`, `commands/*.md`, `agents/*.md`). This is what caught the stale
     "4 commands". Make the regex tolerant; if it can't find the sentence, fail
     loudly rather than silently passing.
2. **`scripts/check.sh`** (pure bash, `set -euo pipefail`) — the canonical gate.
   Runs, in order, failing fast but reporting all: `dprint check`,
   `claude plugin validate . --strict`, `uv run scripts/validate.py`. Accepts a
   `--with-evals` flag that additionally calls `scripts/eval.sh` (added in
   step 4; until then the flag is a documented no-op / "not yet implemented"
   message). This is the single entrypoint CI will reuse in step 3.
3. **`justfile`** (repo root) — thin local wrappers so `just` users get
   ergonomics without CI needing `just`:
   - `check` → `scripts/check.sh`
   - `fmt` → `dprint fmt`
   - `validate` → `uv run scripts/validate.py`
   - `eval` → `scripts/check.sh --with-evals` (functional after step 4)
     Note: these wrap the scripts; the scripts remain the source of truth.

## Acceptance Criteria

**Code-enforced:**

- `uv run scripts/validate.py` exits 0 on the current (post-step-1) repo.
- `scripts/check.sh` exits 0 and runs all three deterministic checks.
- Negative tests (do these as throwaway local edits, then revert): break a SKILL
  description to empty → validator fails with a clear message; rename a skill's
  `name` to mismatch its dir → fails; desync the two `version` values → fails;
  change README count → fails.

**User-run:**

- `just check` runs the gate locally and is green.
- Confirm `uv run scripts/validate.py` resolves the hash-locked `pyyaml` from the
  committed `*.lock` (reproducible install, no version surprise).
