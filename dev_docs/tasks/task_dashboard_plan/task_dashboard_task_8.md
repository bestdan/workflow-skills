---
title: task-dashboard — launchd install, stable launcher, /task-dashboard skill, /doctor check
priority: high
size: 5
status: new
created: 2026-10-23
source_branch: bestdan/task-dashboard-design
related_files:
  - dev_docs/designs/2026-10-23-task-dashboard.md
  - commands/doctor.md
  - README.md
  - evals/
  - .claude-plugin/plugin.json
is_blocked_by:
  - task_dashboard_task_6
  - task_dashboard_task_7
parent: task_dashboard
tags: [task-dashboard, launchd, skill]
---

Part of [[task_dashboard_plan]]. **Runs on: the Mac mini**, against the fake
`acli`. The real-login check is in [[task_dashboard_task_11]].

## Context

The dashboard runs always-on as a **gui-domain LaunchAgent**. The spike showed
that `acli` auth works there, because the login keychain is unlocked after
login. The design's **The launchd plist and the versioned plugin path**
section explains the problem this task solves. The plugin's install directory
carries its version, so a plist pointing into it breaks on every plugin
update. The plist points instead at a stable launcher in `~/.local/bin/`,
which resolves the current `installPath` from
`~/.claude/plugins/installed_plugins.json` at start.

The `launchd` environment is minimal. Use absolute binary paths for `uv`,
`acli` and `gh`, resolved at install time and written into the plist or
launcher. `HOME` must be set. The dashboard needs Python 3.11+ via
`uv run --no-project --python ">=3.11"`.

Under `launchd` that interpreter must already be cached. A cold `uv` would
try to download one at login, so `install` runs the same `uv` command once in
the foreground to warm it.

This adds a skill, so the repo's PR checklist applies:

- update the README component count
- add an `evals/prompts/*.txt` and a `manifest.tsv` row
- bump `plugin.json` and `marketplace.json` together

See `.github/PULL_REQUEST_TEMPLATE.md`.

## Task

- **`dashboard.py install`:**
  - write `~/.local/bin/task-dashboard-launch`, which reads
    `installed_plugins.json`, finds workflow-skills' `installPath` and execs
    `<abs uv> run --no-project --python ">=3.11" <installPath>/scripts/task-dashboard/dashboard.py serve`
  - write `~/Library/LaunchAgents/com.bestdan.task-dashboard.plist`
    (`RunAtLoad`, `KeepAlive`, stdout and stderr to
    `~/.local/state/task-dashboard/`)
  - `launchctl bootstrap gui/$(id -u)` the plist
  - run one `full` sync
- **`dashboard.py uninstall`:** `bootout` the agent and remove the plist and
  launcher. Keep the DB and config, and say so.
- **`dashboard.py status`:** whether the agent is loaded, the last sync, and
  the URL.
- Add `skills/task-dashboard/SKILL.md` (`/task-dashboard`) with subcommands
  `install | status | sync-now | open | export | import | uninstall`.
  - `install`, when run inside a repo, seeds `jira.site` and
    `jira.blocked_statuses` from `dev_docs/tasks/.task-config.yml`. Claude
    reads the YAML and writes the TOML config. It then prompts for
    `board_ids`, the field ids and `github.orgs` when they are missing.
  - It notes that every `acli` and `gh` call needs the sandbox escape when run
    from a Claude session (they reach Atlassian and GitHub hosts).
  - `open` prints `http://127.0.0.1:<port>/`. It is localhost only, so it
    must say the link opens only on this machine.
- Add a `/doctor` check: if the plist exists, it is loaded, and the last
  successful sync is newer than three intervals. Skip silently on machines
  without the plist.
- Satisfy the PR checklist items above.

## Acceptance Criteria

**Code-enforced**

- A test of the launcher's `installPath` resolution against a fixture
  `installed_plugins.json` picks the workflow-skills entry and errors clearly
  when the entry is absent.
- The skill's eval prompt and `manifest.tsv` row exist, and the
  `scripts/validate.py` component counts pass.
- `just check` passes.

**User-run**

- On the Mac mini, only if `launchctl print gui/$(id -u)` succeeds. A GUI
  login session must exist; over SSH alone there is no gui domain, so skip to
  [[task_dashboard_task_11]].
  - Run `/task-dashboard install` with the fake `acli` configured. The agent
    loads, the dashboard answers on `127.0.0.1:8765`, and a sync runs.
  - Simulate a plugin update by changing `installPath` in a copy of
    `installed_plugins.json` that the launcher is pointed at. The dashboard
    starts from the new path after
    `launchctl kickstart -k gui/$(id -u)/com.bestdan.task-dashboard`.
  - `/doctor` reports it healthy.
  - Then run `/task-dashboard uninstall`, and confirm the agent, plist and
    launcher are gone while the DB and config remain.
