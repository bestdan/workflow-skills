---
title: "Atomic label write helper: validate, then full-set PATCH"
priority: high
size: 3
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_2
related_files:
  - commands/handlers/gh-issue-claim.md
  - commands/handlers/assets/
tags: [labels, handler, atomicity]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Atomic label write helper: validate, then full-set PATCH

## Context

Two spike results define this helper, and **neither half is sufficient alone**:

- `gh issue edit --add-label X --remove-label Y` produced **8 HTTP request
  lines** — multiple mutating calls. A crash between them strands an issue with
  two status labels, or none.
- `PATCH /repos/{owner}/{repo}/issues/{n}` with a full `labels` array replaces
  the set in **one request** — atomic, so no intermediate state exists.
- But raw REST **auto-creates unknown labels**. Measured: posting
  `zz-undefined-label` created it. The enum guarantee holds for the `gh` CLI
  path only, and the PATCH is a raw REST write.

So the helper must **validate against `labels.yml`, then PATCH the full set**. A
typo like `est:7` must fail loudly rather than silently entering the namespace.

## Task

Write the label-write helper as a handler asset. Signature takes an issue and a
desired complete label set. It must:

1. Reject any label not in `labels.yml`, non-zero, before any network call.
2. Enforce the invariants: exactly one `status:`, exactly one `auto:`, at most
   one `prio:`, at most one `est:`.
3. Issue a single `PATCH` with the full set.
4. Never use `--add-label` / `--remove-label` for state transitions.

Note the local `sandbox-network-guard` hook blocks non-GET `gh api`, so this
needs an allowlist entry locally. Actions and routines have no such hook.

## Acceptance Criteria

**Code-enforced**

- A test asserts an out-of-vocabulary label is rejected before any network call
- A test asserts two `status:` labels in one write are rejected
- A test asserts the write is a single PATCH carrying the complete set
- No `--add-label`/`--remove-label` remains on any state-transition path

**User-run**

- Confirm the `sandbox-network-guard` allowlist entry lets the PATCH through locally without disabling the guard wholesale
