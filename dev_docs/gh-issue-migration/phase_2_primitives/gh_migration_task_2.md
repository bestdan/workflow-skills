---
title: Define the label vocabulary and an idempotent per-repo sync script
priority: high
size: 3
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
related_files:
  - commands/handlers/gh-issue-config.md
  - commands/handlers/assets/
tags: [labels, handler, prerequisite]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Define the label vocabulary and an idempotent per-repo sync script

## Context

GitHub label namespaces are **per-repo**, and two spike results make
provisioning load-bearing rather than hygiene:

- `gh` **rejects** undefined labels and exits `1`. So an unprovisioned repo
  breaks `/add-task` outright — a loud failure, but a total one.
- `gh issue transfer` **silently drops labels the target repo lacks.** Since
  labels _are_ the state model, transferring an issue into an unprovisioned repo
  destroys its status, priority and estimate.

The schema is 17 labels: 5 status, 2 routing, 4 priority, 6 estimate.

## Task

Add a checked-in `labels.yml` holding the vocabulary, and an idempotent sync
script that creates missing labels and reports (but does not delete) unknown
ones in a repo.

```yaml
status: [0_untriaged, 1_needs_refinement, 2_ready, 3_started, 4_needs_review]
auto: [eligible, human-review-needed]
prio: [0, 1, 2, 3]
est: [1, 2, 3, 5, 8, 13]
```

Wire the sync into `gh-issue-config.md` setup so configuring the handler
provisions the repo. Re-running must be a no-op. Do **not** auto-delete unknown
labels — report them; a repo may have unrelated labels of its own.

## Acceptance Criteria

**Code-enforced**

- A test asserts the sync script is idempotent: a second run creates nothing and exits 0
- A test asserts an unknown-but-present label is reported, not deleted
- `labels.yml` is the single source for the vocabulary — no label names hardcoded elsewhere in the handler

**User-run**

- Run the sync against a scratch repo; confirm 17 labels appear and a re-run reports no changes
