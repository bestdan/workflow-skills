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

Every module is unit-tested against fixtures with a fake runner, so CI never
needs Jira. A fake `acli` executable (task 4) replays those fixtures, so the
whole dashboard can run end-to-end on the Mac mini with no Jira access.

**The main tradeoff:** the `view` fan-out makes a full refresh about 100s, in
exchange for using only `acli`'s existing OAuth login (no API token to manage).
REST `/search/jql` is plan C if the fan-out proves flaky.

## Tasks

Only Jira needs the work laptop, and its connection is poor. So the laptop
gets two short, batched sessions (tasks 10 and 11), and everything else is
built and checked on the Mac mini.

| #  | Task                                                                                                           | Runs on    |
| -- | -------------------------------------------------------------------------------------------------------------- | ---------- |
| 1  | [[task_dashboard_task_1]]: package skeleton, TOML config, SQLite schema, test and type-check wiring.           | Mac mini   |
| 2  | [[task_dashboard_task_2]]: `acli` adapter, against synthetic fixtures.                                         | Mac mini   |
| 3  | [[task_dashboard_task_3]]: `gh` adapter, live against the personal account.                                    | Mac mini   |
| 4  | [[task_dashboard_task_4]]: sync engine and the fake `acli`.                                                    | Mac mini   |
| 5  | [[task_dashboard_task_5]]: HTTP server, security checks, **Sync now**, header, Board view.                     | Mac mini   |
| 6  | [[task_dashboard_task_6]]: score, plus the Now, Waiting on me, I'm waiting on, Stale and Epics views.          | Mac mini   |
| 7  | [[task_dashboard_task_7]]: overlay (pins, order, snooze, notes) and `export`/`import`.                         | Mac mini   |
| 8  | [[task_dashboard_task_8]]: launcher, `launchd` plist, `/task-dashboard` skill, `/doctor` check.                | Mac mini   |
| 10 | [[task_dashboard_task_10]]: capture real `acli` fixtures and fix the parser against them.                      | **Laptop** |
| 11 | [[task_dashboard_task_11]]: real install, then the live checks and weight tuning.                              | **Laptop** |
| 9  | [[task_dashboard_task_9]]: graduate the design into `dev_docs/task-dashboard.md`, then delete the scaffolding. | Mac mini   |

Order:

- **Mac mini:** 1 → {2, 3} → 4 → 5 → {6, 7} → 8
- **Laptop:** 10 can run any time after 2, ideally early, so real shapes
  correct the parser before tasks 4–8 build on it. 11 waits for 8 and 10.
- **Cleanup:** 9 runs last, after 11.

## Decisions

- **The plan is committed.** A `.gitignore` negation tracks this folder, so the
  work laptop gets it with the branch. Task 9 removes both the folder and the
  negation.
- **Python 3.11+ via `uv`, with a TOML config.** This was chosen over the
  3.9/JSON floor of macOS's bundled `python3`, at the cost of needing `uv`
  on the build machine.
- **Fixtures start synthetic, and real captures are added.**
  - Task 2 hand-writes `acli` fixtures from the spike's shapes.
  - Task 3 captures real `gh` output from the personal account; it's public,
    so there's no work data.
  - Task 10 adds redacted real `acli` captures on the laptop, keeping keys,
    shapes and field ids, and replacing summaries, names, emails, the site and
    the org.

  A test fails the build if an email or site domain leaks.
- **Laptop work is batched to suit its connection.** Session 1 (task 10) is a
  few small `acli` calls. Session 2 (task 11) holds the one heavy step, the
  first full sync of about 100s.
