---
name: task-dashboard
description: Install, check, sync, open, back up, read, clean up, or remove the local task dashboard — the always-on, localhost-only page that mirrors your Jira work (through acli) and GitHub PRs (through gh) into SQLite and ranks what to do next. Subcommands install | status | sync-now | open | export | import | tune | view | overlay | clean | uninstall. Use when the user wants that dashboard set up as a login agent on their Mac, asks whether it is running or how stale it is, wants it refreshed now, wants its link, wants to back up or restore their pins, order, snoozes and notes, wants the fleet Now's ranking tuned from their "why not now" answers, asks what a tab shows ("what's on Stale?"), asks to clean a tab up ("look at the Stale tab and clean it up"), wants an item ignored or snoozed, or wants it taken off the machine.
argument-hint: "install | status | sync-now [--reconcile] | open | export <file> | import <file> | tune [--days N] | view <name> [--json] [--all] | overlay <action> | clean <view> | uninstall"
---

# task-dashboard

The dashboard is one stdlib Python process: an HTTP server on `127.0.0.1` and a
sync thread that mirrors Jira and GitHub into
`~/.local/state/task-dashboard/dashboard.db`. On macOS it runs as the
gui-domain LaunchAgent `com.bestdan.task-dashboard`, through a stable launcher
at `~/.local/bin/task-dashboard-launch` that finds the current plugin install
at every start. The design, including why the plist never names the plugin's
versioned directory, is
[`dev_docs/designs/2026-10-23-task-dashboard.md`](../../dev_docs/designs/2026-10-23-task-dashboard.md).

Every subcommand below is one `dashboard.py` call (`open` is an alias of
`url`). Spell it as:

```bash
uv run --no-project --python ">=3.11" "${CLAUDE_PLUGIN_ROOT}/scripts/task-dashboard/dashboard.py" <subcommand> [args]
```

Call that `DASH <subcommand>` below. Relay its output; it already names the
fix for each failure it knows.

**Run `install`, `uninstall`, `status` and `sync-now` unsandboxed**, each for
its own reason. `install` runs a full sync, and **every `acli` and `gh` call
needs the sandbox escape** because they reach Atlassian and GitHub hosts.
`install` and `uninstall` also load and unload the LaunchAgent. `status` reads
the agent's launchd state; a sandbox refusal there is reported as "cannot read
the agent's state", never as "not loaded". `sync-now` makes an HTTP request to
the server on `127.0.0.1`, which the sandbox's network filter may refuse.
`open`, `export`, `import`, `tune`, `view` and `overlay` touch only local files.

`status`, `sync-now`, `open` and `uninstall` use the config and DB paths that
`install` recorded in the plist, unless a flag or a `TASK_DASHBOARD_*` variable
names others. So after an install with `--config` or `--db`, they need neither
flag again, and a reinstall keeps them too.

## install

1. **Check for a GUI login session.** Run `launchctl print gui/$(id -u)`
   unsandboxed. If it fails, stop: there is no gui domain (an SSH-only login),
   so a LaunchAgent cannot load. Say so; the user installs from a desktop
   session. `install` itself refuses on anything but macOS.
2. **Make sure the config exists.** The daemon reads
   `~/.config/task-dashboard/config.toml` by default; `install --config <path>`
   (or `TASK_DASHBOARD_CONFIG`) records another path in the LaunchAgent, which
   later commands read back. Read that file if it exists. Its schema
   and every default are in the design's **Config** section; the required
   keys are `jira.site`, `jira.board_ids`, `jira.fields.sprint` and
   `jira.fields.flagged`, unless `jira.enabled = false`. Never overwrite a
   key the file already sets.
   - **Seed from the repo.** When the session is in a repo whose
     `dev_docs/tasks/.task-config.yml` (with any `.task-config.local.yml`
     overlaid) has a `jira:` block, read its `site` and `blocked_statuses` and
     use them for `jira.site` and `jira.blocked_statuses`. You read the YAML
     and write TOML: the daemon never reads YAML. After this the two files are
     independent.
   - **Ask for what is still missing**, with `AskUserQuestion`: `board_ids`
     (integers; a board's id is the `boardId` inside the sprint field of an
     issue already in that sprint), the per-site custom field ids for the
     sprint and Flagged fields (acli cannot list fields; read them off
     `acli jira workitem view <KEY> --json`, unsandboxed), and `github.orgs`
     (the org logins whose PRs count as work). Offer `github.enabled = false`
     for someone who wants Jira only. `jira.me`, the user's Jira display name,
     is optional.
   - To run without Jira, set `jira.enabled = false` under `[jira]` and skip
     the Jira questions above (`board_ids` and the sprint and Flagged field
     ids); still ask for `github.orgs`. The four `jira.*` keys are then not
     required, install does not look for acli, and the sync makes no acli
     call. Epics, Board, Waiting on me and I'm waiting on leave the nav. (A
     fleet instance never shows the last two: its Pull requests and Issues
     tabs replace them.) To try the Jira views without
     a Jira login, point `jira.acli_path` at the fake acli,
     `${CLAUDE_PLUGIN_ROOT}/scripts/task-dashboard/dev/fake-acli`, instead.
     That path is inside the versioned plugin directory and moves on a plugin
     update, so it is not for daily use.
   - Write the file with `Write`, then check it loads: `DASH url` prints the URL
     only when the config is valid, and names the bad key otherwise.
3. **Install.** `DASH install --dry-run` prints every file and command first,
   when the user wants to see them. Then `DASH install`, unsandboxed. It boots
   out any older copy of the agent, writes the launcher and the plist, runs
   `init-db` and a `full` sync in the foreground through the launcher (that
   warms uv's Python, which a cold uv under launchd would try to download at
   login), and then bootstraps the agent. A first full sync takes about 100s
   against real Jira.
   - Exit 1 means a step failed: the bootout, `init-db`, the first sync or
     the bootstrap. Install undoes itself: it removes the plist, and the
     launcher unless another agent runs it, so nothing loads at the next
     login. The output names the failed step. When it is an acli auth
     failure, the fix is `acli jira auth login` in the user's own terminal;
     then run install again.
   - Exit 2 means install refused before writing anything (a config error, or
     uv, acli or gh not found); the message names which.
4. Finish with the URL and the localhost caveat from **open**.

`DASH install --collector-only` is the other install: it schedules only the
fleet collector, as a LaunchAgent on macOS or a `systemd --user` timer on
Linux, for a machine that runs no server. It renders the same launcher, and
`uninstall` leaves that launcher in place while the collector's agent uses it.

After a plugin update the agent keeps running the old copy until it restarts.
Restart it with `launchctl kickstart -k gui/$(id -u)/com.bestdan.task-dashboard`,
or let the next login do it. No reinstall is needed.

## status

`DASH status`, unsandboxed: whether the agent is loaded (and its pid), when the
last successful sync finished, the last run's error if it failed, and the URL.
Exit 0 is healthy, 1 lists each problem with its fix, 3 means not installed.
`--json` gives the same as JSON.

## sync-now

`DASH sync-now`, unsandboxed: the same request as the header's **Sync now**
button, to the running server, then it waits for the run to finish (up to
`--wait`, default 300s). `--reconcile` runs the hourly reconcile instead, which
drops issues that left scope. Exit 3 means the run is still going; it finishes
in the server. Without a running server, `DASH sync --mode incremental` runs a
sync in the foreground instead.

## open

`DASH open` (or `DASH url`) prints `http://127.0.0.1:<port>/`. Give it to the user and **say it
opens only on this machine**: the server is bound to localhost and refuses any
other `Host`, and v1 has no tailnet link. Do not rewrite it as a tailnet or
LAN address.

## export, import

The overlay (pins, order, snoozes, notes, kind tags, ignores, each view's
include filter, and the fleet Now's "why not now" answers) is the one thing on this machine that cannot be
rebuilt from Jira or GitHub.

- `DASH export <file>` writes it as JSON. With no file it prints to stdout.
- `DASH import <file>` **replaces** the whole overlay with the file's
  contents, and refuses a malformed file without changing anything. Confirm
  with the user before importing over an overlay that has rows.
- The Now base rate `tune` compares answers with (`now_shown`) is not backed
  up; it rebuilds as Now is viewed.

## tune

On the fleet instance, snoozing a card on Now asks "why not now?" and records
the answer with the item's score terms and other features; each day's shown
items are kept as the base rate. `tune` turns those into proposals. **It only
proposes: you never apply a change the user has not approved, and the
dashboard itself never calls a model.**

1. Run `DASH tune --json` (last 30 days; `--days N` for another window). It
   reads the DB and the config's `[score.fleet]` weights and writes nothing.
2. Read what it prints:
   - `weights`: a term with `suggest` set was the largest term of most
     "wrong priority" answers. Its `suggest.weight` is a starting point.
   - `candidates`: feature values that snoozes lean towards, against what Now
     showed, with the `free_text` and `reasons` of those answers. When
     `scored_by` is set, a term already reads that feature: propose a weight.
     Otherwise propose a new `[score.fleet]` term: its name, the field it
     would read (the feature), its sign and a starting weight.
   - `rows`: every answer. Read the free text for patterns no feature names.
3. Put each proposal to the user, one at a time, with the evidence from the
   output. Apply only what they approve:
   - a weight edit goes in the config file (`~/.config/task-dashboard/config.toml`
     unless install recorded another), and the server reads it at its next
     start: `launchctl kickstart -k gui/$(id -u)/com.bestdan.task-dashboard`,
     unsandboxed;
   - a new term is a code change to `score.py` and `fleet_now.py`, made as an
     ordinary pull request, never a config edit.

With few answers `tune` suggests nothing; say so rather than guessing.

## view, overlay

`DASH view <name> --json` prints one tab's lanes and items as its page shows
them, read from the DB with no server: the include filter, ignores and snoozes
apply as there, and `--all` adds what the page hides, each marked `hidden`
with why. The names are the nav's: `now`, `needs`, `prs`, `issues`, `stale`,
and on a Jira instance `board`, `waiting-on-me`, `waiting-on` and `epics`.
Each item carries its `source`, `key`, `kind`, `lane`, `title`, `url`, `host`
and `path`, its `evidence` and `risks`, and its `action` as
`{"review": ..., "command": [...]}`. `freshness` says when each sync last
succeeded and when each host last snapshotted. Without `--json` it prints one
line per item.

`DASH overlay <action> --source S --key K` makes the write a card's button
makes (`ignore`, `unignore`, `snooze --until YYYY-MM-DD`, `pin`, `unpin`,
`note --text`, ...), through the same validation, straight to the DB. Exit 1
means it was refused and nothing changed; the message is the server's.

## clean

`clean <view>` ("look at the Stale tab and clean it up") is a cleanup an agent
runs with the owner. **Act alone only on an item with merged-PR evidence, a
clean tree, no unpushed commits and no active session, re-checked live just
before acting, and only through the plugin's own teardown scripts.** Propose
everything else, grouped by suggested action, and wait for the owner's yes.
For an item on another host, hand the owner a script rather than writing over
ssh. Record each "leave it" as an ignore with `DASH overlay ignore`. The full
procedure is [`references/clean.md`](references/clean.md); read it before
starting.

## uninstall

`DASH uninstall`, unsandboxed, on macOS: boots the server's agent out, then
removes its plist and the launcher. It removes only the server's LaunchAgent: a
`--collector-only` install (its LaunchAgent, or its `systemd --user` timer) is
left alone, and there is no collector uninstall yet. It keeps the launcher when another LaunchAgent still runs it
(the fleet collector shares it), and it never touches the DB, the config or
the logs. Say which paths it kept, as its output does. A user who wants
everything gone runs `DASH export` first, then deletes those paths themselves.
