# Evals & CI — overview

## Goal

Give the `workflow-skills` plugin repo an automated quality gate. Today there is
no CI and no tests: `claude plugin validate . --strict` fails (missing
marketplace description), `dprint check` fails (15 unformatted files), and
`plugin.json` (0.6.0) and `marketplace.json` (0.5.1) versions have drifted.
This plan makes the repo green, then adds a **deterministic, blocking** PR gate
(manifest validation, formatting, repo-native structural/consistency checks,
link integrity) plus a **flag-gated, non-blocking** behavioral eval harness that
checks Claude actually invokes each skill from a naive trigger prompt.

## Scope / non-goals

- **In scope:** a self-contained `scripts/` validation layer, a `justfile`
  local entrypoint, a blocking GitHub Actions workflow that reuses that
  entrypoint, a skill-triggering eval harness runnable via flag (local + manual
  CI), and the docs to operate it.
- **Non-goals:**
  - LLM-as-judge *quality* scoring of skill output (promptfoo / `claude-eval`
    style rubrics). The harness here only asserts skill **invocation**, not
    output quality. Noted as a documented extension point, not built.
  - Nightly/scheduled eval runs. Evals are opt-in via flag, per decision.
  - Publishing/release automation, version-bump tooling, changelog generation.
  - Testing the handler MCP integrations (Linear/Jira/GH) end-to-end.

## Approach

**Repo-native scripts as the source of truth, minimal third-party actions.**
The deterministic checks live in `scripts/` (bash orchestrator + a Python
validator for frontmatter/consistency) so every check is runnable locally and
CI just calls the same entrypoint — they can never drift. CI adds only a small
number of well-known actions (checkout, dprint, link-check) plus the `claude`
CLI for `plugin validate`.

Two tiers:

- **Tier 1 — deterministic, blocking, free, fast** (runs on every PR):
  `claude plugin validate --strict`, `dprint check`, `scripts/validate.py`
  (frontmatter rules, name=dirname, version-sync, README counts, body line
  caps), link integrity.
- **Tier 2 — behavioral, flag-gated, costs API credits, non-blocking**:
  per-skill naive trigger prompt → `claude -p … --output-format stream-json` →
  assert the expected `Skill` tool invocation appears. Runnable via
  `just eval` / `scripts/check.sh --with-evals` locally and a manual
  `workflow_dispatch` CI job that needs `ANTHROPIC_API_KEY`.

Main tradeoff considered: wiring marketplace GitHub Actions directly
(frontmatter-json-schema, markdownlint) vs. a repo-native Python validator. Chose
repo-native to honor the "no new deps without discussing" rule and to keep every
check locally reproducible. We still allow the two generic actions that are
painful to reimplement well (`dprint/check`, `lychee` link-check).

## Steps

1. [[step_1]] — **Make the repo green.** Fix the three current failures
   (dprint formatting, missing marketplace description, version drift) and the
   stale README command count, before adding any gate that would enforce them.
2. [[step_2]] — **Repo-native validation + local entrypoint.** Add
   `scripts/validate.py`, `scripts/check.sh`, and a `justfile` so contributors
   run the full deterministic gate locally with one command.
3. [[step_3]] — **Blocking CI workflow.** Add `.github/workflows/ci.yml` that
   installs the toolchain and runs the same `scripts/check.sh` on every PR/push,
   plus link integrity.
4. [[step_4]] — **Behavioral eval harness (flag-gated).** Add `evals/` prompts,
   `scripts/eval.sh` (skill-triggering via `claude -p` stream-json), wire it
   behind a flag locally and a manual `workflow_dispatch` CI job.
5. [[step_5]] — **Docs & contributor workflow.** `CONTRIBUTING.md`, README CI
   badge, PR template, and a "what to do when you add a skill" checklist that
   ties the pieces together.

## Resolved decisions

- **Version drift → keep both & sync.** `plugin.json` and the `marketplace.json`
  plugin entry both carry the version, synced to `0.6.0`; `scripts/validate.py`
  asserts they stay **equal** to prevent re-drift. ([[step_1]], [[step_2]])
- **`claude plugin validate` auth in CI → verify on first run.** Accepted as a
  known unknown: CI includes the step with a flag-note, and the first CI run
  confirms it runs headless; documented fallback is to fold manifest-schema
  checks into `validate.py`. ([[step_3]])
- **Validator dependency → real YAML parser, dev-only, hash-pinned.**
  `scripts/validate.py` is dev/CI-only tooling (never shipped to plugin
  consumers), so a `pyyaml` (`safe_load`) dependency is approved; pin an exact
  security-checked version and hash-lock it via `uv lock --script`. ([[step_2]])
- **`just` in CI → no.** Canonical entrypoint is `scripts/check.sh` (pure bash);
  the `justfile` is a local convenience wrapper. ([[step_2]], [[step_3]])

## Open questions

- None outstanding. Smaller in-flight calls (description length threshold;
  excluding non-user-invocable `analysis-conventions` from trigger evals) are
  documented inline in the relevant steps and easy to revisit during execution.
