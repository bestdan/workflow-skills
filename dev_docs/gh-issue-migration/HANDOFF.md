# Handoff — migrating the task loop from Linear to GitHub Issues

**Updated 2026-08-30.** Read this first, then
[`gh_migration_plan.md`](gh_migration_plan.md) (the epic) and
[`2026-08-24-requirements-and-evidence.md`](2026-08-24-requirements-and-evidence.md)
(the measured record).

## Where this lives, and why it moved

This plan used to live under `dev_docs/tasks/gh_migration_plan/`, which
`.gitignore:31` excludes — so it existed in exactly one git worktree, uncommitted, with
no history and no remote. Tearing that worktree down would have destroyed it. It is now
committed under `dev_docs/gh-issue-migration/`, per the `.gitignore` comment block's own
advice: "Prefer graduating durable wisdom to a top-level `dev_docs/<name>.md` (never
ignored) over keeping it here."

## State: phases 1–2 are done

**PR #415 merged as `d7aa23a`.** It shipped:

- `commands/handlers/assets/labels.yml` — the 17-label vocabulary (`status:`, `auto:`,
  `prio:`, `est:`), provisioned live on `bestdan/workflow-skills`
- `commands/handlers/assets/_labels.py` — the stdlib-only vocabulary reader
- `commands/handlers/assets/gh-label-sync.py` — idempotent per-repo provisioning
- `commands/handlers/assets/gh-issue-state.py` — validate, then ONE full-set PATCH
  carrying labels and open/closed together
- `dev_docs/decisions/2026-08-24-routine-claim-channel.md` — the routine-channel evidence
- 24 hermetic tests

## The one thing that will trip you up

**Those 17 labels are inert. Nothing reads or writes them.** The handler still uses a
different, older vocabulary, and the two do not overlap:

| Handler applies today        | The provisioned vocabulary |
| ---------------------------- | -------------------------- |
| `auto-eligible`              | `auto:eligible`            |
| `human-approval-requested`   | `auto:human-review-needed` |
| `needs-review`               | `status:4_needs_review`    |
| `priority:urgent\|high\|...` | `prio:0`–`prio:3`          |

That is deliberate — PR #415 landed primitives only, so the handler transitions migrate
in one move rather than half-now. **Task 5 is that move**, and it is the natural next
task: it gives `gh-issue-state.py` its first caller.

Two consequences:

- Filing the remaining tasks as GitHub issues **today** would file them under the old
  scheme, which task 5 then has to migrate — including the issue tracking task 5 itself.
- `gh-issue-state.py` shells out to `gh api`. A cloud routine has no `gh`, so task 5 owes
  an MCP branch that reuses `labels.yml` for the same validate-then-replace rule.

## Do not re-derive these — they were measured, not read

Full evidence in
[`dev_docs/decisions/2026-08-24-routine-claim-channel.md`](../decisions/2026-08-24-routine-claim-channel.md)
(committed) and §10b of the requirements record.

- A cloud routine has **no `gh`** and **no credential on raw HTTP**. The GitHub MCP
  connector is the credentialed channel.
- On both channels a label write **replaces** the whole set and **auto-creates** unknown
  names. Hence validate-then-replace, always, before any network call.
- A routine **can** acquire the claim ref (`mcp__github__create_branch`, create-only) but
  **cannot release** it — no delete-ref tool, and `git push --delete` 403s. So routines
  stay on the comment election. Task 12 (the stale-ref sweep) gates flipping that.
- The connector has **no dependency-edge tool**, so task 8 is local-only.

This file was wrong twice by asserting routine behaviour from documentation. Probe it.

## Open blockers, and who owns them

- **`sandbox-network-guard` blocks non-GET `gh api`.** Until an allowlist entry exists in
  the operator's dotfiles, `gh-issue-state.py`'s PATCH cannot run locally — so task 5
  cannot test its own write path. **Outside this repo; needs the operator.**
- **`/auto-pilot` does not support `gh-issue`** (task 13, new). It stops outright rather
  than degrading. Switching `.task-config.yml` to `gh-issue` takes this repo out of
  unattended operation until that is fixed.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic. Every
  other task has one under a `phase_*/` directory.

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`, with four Linear projects.
Nothing has switched yet, and nothing should until task 5 lands and task 13 is at least
scoped.
