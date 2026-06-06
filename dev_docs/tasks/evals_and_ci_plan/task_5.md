---
title: "Document contributor workflow: CONTRIBUTING, README badge, PR template"
priority: medium
size: 3
status: done
created: 2026-05-29
source_branch: bestdan/skills-evals-ci
source_pr: 15
related_files:
  - CONTRIBUTING.md
  - README.md
  - .github/PULL_REQUEST_TEMPLATE.md
is_blocked_by: task_4
expires: 2026-06-28
tags:
  - docs
---

← [[evals_and_ci_plan|Overview]]

# Task 5 — Docs & contributor workflow

Tie the pieces together so a contributor (or future you) knows how to run the
gate, run evals, and what's required when adding a skill.

## Context

- After steps 1–4 the mechanics exist but are undocumented. The repo's audience
  is plugin authors who install via marketplace; they need a clear local loop.
- `README.md` is the front door; it already documents the skills but says nothing
  about development, testing, or contributing.

## Task

1. **`CONTRIBUTING.md`** (new):
   - Local dev loop: `just check` (deterministic gate), `just fmt`, `just eval`
     (needs `ANTHROPIC_API_KEY`).
   - **"Adding a new skill" checklist:** create `skills/<name>/SKILL.md` with
     valid frontmatter; keep body ≤500 lines; update the README count sentence;
     add an `evals/prompts/<name>.txt` trigger + manifest row; run `just check`
     and `just eval` before opening a PR.
   - What the CI gate enforces and how to reproduce a failure locally.
   - Version policy: bump `plugin.json.version` **and** the matching
     `marketplace.json` plugin-entry `version` together; they must stay equal
     (the `validate.py` sync assertion from step 2 enforces this).
2. **`README.md`:**
   - Add a CI status badge near the top.
   - Add a short "Development" section linking to `CONTRIBUTING.md`.
3. **`.github/PULL_REQUEST_TEMPLATE.md`** (new): a short checklist —
   ran `just check`; updated README counts if components changed; added/updated
   an eval prompt if a skill changed.
4. _(Optional)_ **`.github/ISSUE_TEMPLATE/`** for skill bug reports — only if you
   want it; not required by the gate.

## Acceptance Criteria

**Code-enforced:**

- `dprint check` and `scripts/validate.py` still pass with the new docs (the
  README-count assertion must still parse the updated sentence — re-verify the
  regex against the edited line).
- The CI badge URL points at the real `ci.yml` workflow.

**User-run:**

- Read `CONTRIBUTING.md` end-to-end and confirm the "adding a skill" checklist
  actually reproduces a green `just check` + `just eval`.
- Open a test PR and confirm the PR template renders.
