---
name: task-dashboard
description: Install, check, sync, open, back up, read, clean up, or remove the local task dashboard — the always-on, localhost-only page that mirrors your Jira work (through acli) and GitHub PRs (through gh) into SQLite and ranks what to do next. Subcommands install | status | sync-now | open | export | import | tune | view | scopes | items | refresh | overlay | clean | uninstall. Use when the user wants that dashboard set up as a login agent on their Mac, asks whether it is running or how stale it is, wants it refreshed now, wants its link, wants to back up or restore their pins, order, snoozes and notes, wants the fleet Now's ranking tuned from their "why not now" answers, asks what a tab shows ("what's on Stale?"), wants the open work in one repo, workstream, Jira project or label as compact records ("what's open on FinPlan?", "small tasks I could do in half an hour"), asks to clean a tab up ("look at the Stale tab and clean it up"), wants an item ignored or snoozed, or wants it taken off the machine.
argument-hint: "install | status | sync-now [--reconcile] | open | export <file> | import <file> | tune [--days N] | view <name> [--json] [--all] [--mode M] | scopes [query] | items [--scope X] [--kind K] [--state S] [--only F] [--fields F] [--limit N] | refresh <owner/repo#N> [--json] | overlay <action> | clean <view> | uninstall"
---

# task-dashboard

The dashboard is one stdlib Python process: an HTTP server on `127.0.0.1` and a
sync thread that mirrors Jira and GitHub into
`~/.local/state/task-dashboard/dashboard.db`. On macOS it runs as the
gui-domain LaunchAgent `com.bestdan.task-dashboard`, through a stable launcher
at `~/.local/bin/task-dashboard-launch` that finds the current plugin install
at every start. How it is built, including why the plist never names the
plugin's versioned directory, is
[`dev_docs/task-dashboard.md`](../../dev_docs/task-dashboard.md).

Every subcommand below except `clean` is one `dashboard.py` call (`open` is an
alias of `url`); `clean` is the procedure in
[`references/clean.md`](references/clean.md). Spell the call as:

```bash
uv run --no-project --python ">=3.11" "${CLAUDE_PLUGIN_ROOT}/scripts/task-dashboard/dashboard.py" <subcommand> [args]
```

Call that `DASH <subcommand>` below. Relay its output; it already names the
fix for each failure it knows.

**Run `install`, `uninstall`, `status`, `sync-now` and `refresh` unsandboxed**,
each for its own reason. `install` runs a full sync, and **every `acli` and `gh` call
needs the sandbox escape** because they reach Atlassian and GitHub hosts.
`install` and `uninstall` also load and unload the LaunchAgent. `status` reads
the agent's launchd state; a sandbox refusal there is reported as "cannot read
the agent's state", never as "not loaded". `sync-now` makes an HTTP request to
the server on `127.0.0.1`, which the sandbox's network filter may refuse.
`refresh` makes one `gh` call, plus the `review-requested:@me` search (one
call per page) for someone else's PR with a team review request, and one
`gh pr view` for a PR with more than 100 checks.
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
   and every default are `DEFAULTS` in
   `${CLAUDE_PLUGIN_ROOT}/scripts/task-dashboard/config.py`; the required
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
   - A fleet instance's Workstreams tab shows milestones and proposed designs
     on its own. Linear projects join it only with `enabled = true` under
     `[linear]` and a key. The key never goes in config.toml: it goes in
     `~/.config/workflow-skills/linear_api_key`, mode 600, or in
     `$LINEAR_API_KEY` (`linear_api.py` has the full ladder). Without one the
     sync records `linear: no key` and the tab shows the other sources.
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
include filter, the fleet Now's "why not now" answers and its saved mode) is the one thing on this machine that cannot be
rebuilt from Jira or GitHub.

- `DASH export <file>` writes it as JSON. With no file it prints to stdout.
- `DASH import <file>` **replaces** the whole overlay with the file's
  contents, and refuses a malformed file without changing anything. Confirm
  with the user before importing over an overlay that has rows.
- The Now base rate `tune` compares answers with (`now_shown`) is not backed
  up; it rebuilds as Now is viewed.

## tune

On every instance, snoozing a card on Now asks "why not now?" and records
the answer with the item's score terms and other features; each day's shown
items are kept as the base rate. `tune` turns those into proposals. **It only
proposes: you never apply a change the user has not approved, and the
dashboard itself never calls a model.**

1. Run `DASH tune --json` (last 30 days; `--days N` for another window). It
   reads the DB and the config's `[score.categories]` and `[score.terms]`
   weights and writes nothing.
2. Read what it prints:
   - `weights`: a term with `suggest` set was the largest term of most
     "wrong priority" answers. Its `suggest.weight` is a starting point, and
     its `table` names where the weight lives: `[score.categories]` for a
     category, `[score.terms]` for priority, deadline, unblocks, stuck and
     age.
     Weights order items only within Now's fixed Ship tiers (asked, close
     out, ready to merge, review queue, started, the rest, blocked), so no
     weight moves an item into another tier.
   - `candidates`: feature values that snoozes lean towards, against what Now
     showed, with the `free_text` and `reasons` of those answers. When
     `scored_by` is set, a term already reads that feature: propose a weight.
     Otherwise propose a new `[score.terms]` term: its name, the field it
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

## view, scopes, items, overlay

`DASH view <name> --json` prints one tab's lanes and items as its page shows
them, read from the DB with no server: the include filter, ignores and snoozes
apply as there, and `--all` adds what the page hides, each marked `hidden`
with why. The names are the nav's, and an instance serves only those its nav
shows: `now` and `stale` always; `needs`, `prs` and `issues` only on a fleet
instance; `board` and `epics` only with Jira on; `waiting-on-me` and
`waiting-on` only with Jira on and no fleet. Any other name exits 2.
Each item carries its `source`, `key`, `kind`, `lane`, `title`, `url`, `host`
and `path`, its `evidence` and `risks`, and its `action` as
`{"review": ..., "command": [...]}`; a Now item adds its `tier` and, for
a close-out, its `checklist` of steps ticked from evidence. Now is one
ranked list on every instance, Jira tickets included, built in a mode:
`--mode ship|little|deep|tidy` picks one, and without
it the mode the owner last saved with Now's mode button is used (ship when
none is), so you see what they see; `notes.mode` names it, outside Ship
`notes.more_in_mode` counts what the list left out, and in Little
`notes.closed_today` is the day's count. `freshness` says when each sync last
succeeded and when each host last snapshotted. Without `--json` it prints one
line per item.

`view` is a whole tab; for a question about a slice of the work, use `scopes`
and `items`, unsandboxed (inside the sandbox SQLite cannot open the WAL file
beside the DB). Both print compact JSON and read the DB read-only.

- `DASH scopes <word>` says what a word can mean: each matching scope id
  (`repo:`, `ws:`, `jira:`, `epic:`, `label:`, `host:`) with its open-item
  count. For a repo it also lists `related` workstreams, those with members
  in the repo whatever their name. **Show `related` to the owner and pass the
  ones they confirm as `--scope ws:<id>`; never add them yourself.** Say what
  the word matched.
- `DASH items --scope <id or word>` (repeatable, a union) prints one record
  per open item, every source in one shape: its state, prio, age, labels,
  workstreams, linked `pr` readiness, live `activity` (sessions and worktrees
  on its branch), blockers, the owner's `overlay` marks, Now's `now` placing,
  and `size` (`{bucket, est_min, from}`, null when unsized — **never treat
  an unsized item as small**). `--state todo,started,done` filters the status
  category (default open); `--only blocked,waiting,stale` filters derived
  facts, so "started and blocked" is both flags. `--fields` takes `brief`
  (default; null and empty fields left out), `full` (adds evidence, risks,
  action, branch, host, path) or exact names, which print only those plus
  identity. `--limit`/`--offset` page it; `total` and `returned` say when a
  list was cut. `--scope all` is no filter and is never matched as a word.
  The order is newest activity first, not a priority.

After acting on a PR or issue (merging, closing, labelling, pushing a fix), run
`DASH refresh <owner/repo#N>`, unsandboxed, rather than `sync-now`: it re-reads
that one item and rewrites its rows in seconds, with no server and no sync, and
prints `updated`, `merged (removed)`, `closed (removed)`, `archived repo
(removed)`, `not found (removed)` or `moved outside github.orgs (removed)`;
`--json` adds the item as each view's `view --json` lists it. It exits 2,
making no call, for a config error, a malformed ref or one outside
`github.orgs`, and 1 when
GitHub or the DB fails, with the mirror unchanged.

`DASH overlay <action> --source S --key K` makes the write a card's button
makes (`ignore`, `unignore`, `snooze --until YYYY-MM-DD`, `pin`, `unpin`,
`note --text`, `mode --mode little`, ...), through the same validation,
straight to the DB. Exit 1
means it was refused and nothing changed; the message is the server's.

## clean

`clean <view>` ("look at the Stale tab and clean it up") is a cleanup an agent
runs with the owner. **Act alone only on an item with merged-PR evidence, a
clean tree, no unpushed commits and no active session, re-checked live just
before acting, and only through the plugin's own teardown scripts.** Propose
everything else, grouped by suggested action, and wait for the owner's yes.
For an item on another host, hand the owner a script rather than writing over
ssh. Record each "leave it" as an ignore with `DASH overlay ignore`, and after
acting on a PR or issue, `DASH refresh` it rather than `sync-now`. The full
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
