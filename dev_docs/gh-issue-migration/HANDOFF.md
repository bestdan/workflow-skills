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

## State: phase 1–2 and tasks 4 and 5 are done

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

**One verb is still on the old vocabulary: `reoptimize`.** Tasks 4 and 5 moved add /
list / promote / complete / claim onto the namespaced names in `labels.yml`, and task 4
deleted the bridges that let both spellings coexist. `gh-issue-reoptimize.md` was not in
either scope and still reads `auto-eligible` / `auto-claimed` / `priority:*`, so **on a
migrated board it classifies every issue as `new`**. It carries a scope note saying so;
migrating it belongs with task 8, which is the other reoptimize work.

The rename, for reference:

| Pre-migration                | The provisioned vocabulary |
| ---------------------------- | -------------------------- |
| `auto-eligible`              | `auto:eligible`            |
| `human-approval-requested`   | `auto:human-review-needed` |
| `auto-claimed`               | `status:3_started`         |
| `needs-review`               | `status:4_needs_review`    |
| `priority:urgent\|high\|...` | `prio:0`–`prio:3`          |

**Task 5** — PR #439, merged `a4815d7` — moved `/add-task`, `/list-tasks`,
`/promote-tasks` and `/complete-task`, and added `gh-issue-ready.py` for
dependency-readiness. **Task 4** — PR #442 — moved `/do-tasks`'s claim lifecycle, added
`gh-issue-claim.py` for the parts two racing sessions must perform identically, taught
claim to consult native `blocked_by`, and removed task 5's bridges.

**Tasks 14 and 15 are next, then 6.** The 2026-08-31 audit of the open PRs turned up two
things that outrank the next planned task:

- **Task 14** — a defect task 4 shipped. `/do-tasks --no-claim` still checks out
  `task/<n>`, a branch the claim no longer creates. Size 1.
- **Task 15** — dependency-blocking is **inert**. Tasks 4 and 5 both taught the loop to
  read GitHub's native `blocked_by` graph; nothing writes one, so both checks pass
  everything. Fixing the write side is what makes two already-shipped read paths do
  anything.

The same audit produced the epic's **In-flight PRs against files this plan owns**
section. Read it before touching `gh-issue-claim.md` or `gh-issue-promote.md` — three
open PRs edit those files against the old vocabulary, and they all apply almost cleanly,
which is exactly what makes them dangerous.

One consequence still open: `gh-issue-state.py` shells out to `gh api`. A cloud routine
has no `gh`, so the loop still owes an MCP branch that reuses `labels.yml` for the same
validate-then-replace rule.

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
- **`gh-issue-reoptimize.md` has not migrated** — see above. Owned by task 8.
- **Nothing creates a native dependency edge**, so `/list-tasks`'s and `/do-tasks`'s
  dependency checks are correct and inert. Owned by task 15, which also retires the
  "GitHub Issues have no native dependency edge" claim still asserted in three files —
  a claim that was true when written, is false now, and has already misled one open PR
  (#426) into building a body-footer parser.
- **Tasks 12 and 13 have no task file** — they exist only as entries in the epic. Every
  other task has one under a `phase_*/` directory.

## This repo is still on Linear

`dev_docs/tasks/.task-config.yml` says `handler: linear`, with four Linear projects.
Nothing has switched yet, and nothing should until task 13 is at least scoped —
switching today would end unattended operation rather than degrade it.

A consequence worth naming: **task 4's acceptance criteria are not all met yet.** Its
code-enforced ones are (hermetic tests for the race, the branch parser and the WIP cap),
but its user-run one — two `/do-tasks` sessions against the same ready issue, confirming
exactly one proceeds — needs a repo actually on the `gh-issue` handler. Run it when one
is.
