---
type: epic
title: task-dashboard — local read-only Jira + GitHub dashboard
status: active
owner: bestdan
created: 2026-10-23
---

# task-dashboard plan

**Goal:** build the dashboard specified in
[`dev_docs/designs/2026-10-23-task-dashboard.md`](../../designs/2026-10-23-task-dashboard.md).
It is a `launchd`-hosted, localhost-only dashboard that mirrors my Jira work
(via `acli`) and GitHub PRs (via `gh`) into SQLite, then ranks the work and
shows who is waiting on whom. It runs on the work laptop. The spike results it
builds on are in
[`dev_docs/research/2026-10-23-task-dashboard-acli-spike.md`](../../research/2026-10-23-task-dashboard-acli-spike.md).

## Scope / non-goals

Not in this plan:

- Any write to Jira or GitHub.
- Notifications.
- A tailnet link, or a port to the Mac mini.
- Linear or repo-pr sources (the adapter seam leaves room for them).
- Per-status time in status. That needs the REST changelog and a separate API
  token; v1 measures time in status category.
- An LLM "what next" summary.

## Approach

A single stdlib-only Python process lives under `scripts/task-dashboard/`. It
requires Python 3.11+ and runs under `uv run --no-project --python ">=3.11"`,
so it doesn't depend on the `python3` macOS ships. Config is TOML, read with
`tomllib`.

The process has two threads:

- **A sync thread** writes a mirror (`issues`, `links`, `sprints`, `prs`,
  `sync_runs`). Each Jira fetch is two steps: `acli search --paginate` for
  keys, then `acli view` per key, 8 in parallel, with serial retry.
- **An HTTP thread** reads the mirror and writes only the overlay (`pins`,
  `order`, `snoozes`, `notes`).

Every module is unit-tested against recorded, redacted `acli`/`gh` fixtures
with a fake runner, so CI never needs Jira. Live behaviour is checked by
user-run steps on the work laptop.

**The main tradeoff:** the `view` fan-out makes a full refresh about 100s, in
exchange for using only `acli`'s existing OAuth login (no API token to manage).
REST `/search/jql` is plan C if the fan-out proves flaky.

## Tasks

1. [[task_dashboard_task_1]]: package skeleton, TOML config loader, SQLite schema, and test/typecheck wiring.
2. [[task_dashboard_task_2]]: `acli` adapter. Runner, search/view, field parsing, retry, auth-error detection.
3. [[task_dashboard_task_3]]: `gh` adapter. GraphQL PR searches per org, per-PR CI, Jira-key join.
4. [[task_dashboard_task_4]]: sync engine. Scopes, JQL builder, incremental/reconcile/full modes, one transaction per run.
5. [[task_dashboard_task_5]]: HTTP server core. Host/Origin/token checks, sync thread and **Sync now**, header status, Board view.
6. [[task_dashboard_task_6]]: prioritization score and the Now, Waiting on me, I'm waiting on, Stale and Epics views.
7. [[task_dashboard_task_7]]: overlay. Pins, order, snooze, notes, plus `export`/`import`.
8. [[task_dashboard_task_8]]: install. Stable launcher, `launchd` plist, `/task-dashboard` skill, `/doctor` check.
9. [[task_dashboard_task_9]]: graduate the design into `dev_docs/task-dashboard.md`, then delete the design and this plan.

Order: 1 → {2, 3} → 4 → 5 → {6, 7} → 8 → 9. Tasks 2 and 3 can run in parallel.

## Open questions

## Decisions

- **The plan is committed.** A `.gitignore` negation tracks this folder, so the
  work laptop gets it with the branch. Task 9 removes both the folder and the
  negation.
- **Python 3.11+ via `uv`, with a TOML config.** This was chosen over the
  3.9/JSON floor of macOS's bundled `python3`, at the cost of needing `uv`
  on the build machine.
- **Fixtures are redacted captures, committed.** Real `acli`/`gh` output is
  captured on the laptop and committed, keeping keys, shapes and field ids,
  with summaries, names, emails, the site and the org replaced. A test fails
  the build if an email or site domain leaks. Tasks 2 and 3 own the capture.
