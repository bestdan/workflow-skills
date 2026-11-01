---
name: task-dashboard
description: Install, check, sync, open, back up, read, prioritize, clean up, or remove the local task dashboard — the always-on localhost page that mirrors Jira and GitHub into SQLite and ranks what to do next. Subcommands install | status | sync-now | open | export | import | tune | view | scopes | items | item | brief | prioritize | refresh | overlay | plan | clean | uninstall. Use when the user wants that dashboard set up as a login agent on their Mac, asks if it is running or how stale it is, wants it refreshed now, wants its link, wants to back up or restore its pins, snoozes and notes, wants Now's ranking tuned from their "why not now" answers, asks what a tab shows ("what's on Stale?"), wants the open work in one repo, workstream, project or label as compact records ("what's open on FinPlan?"), asks what to work on next ("help me prioritize my FinPlan tasks", "half an hour, what small tasks should I bang out?"), asks to clean a tab up, wants an item ignored or snoozed, or wants it taken off the machine.
argument-hint: "install | status | sync-now [--reconcile] | open | export <file> | import <file> | tune [--days N] | view <name> [--json] [--all] [--mode M] | scopes [query] | items [--scope X] [--kind K] [--state S] [--only F] [--fields F] [--limit N] [--offset N] [--jsonl] | item <source:key> [--sections S] | brief [--scope X] [--mode M] [--minutes N] [--sections S] | prioritize [scope] | refresh <owner/repo#N> [--json] | overlay <action> | plan set|show|clear|outcomes | clean <view> | uninstall"
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

Every subcommand below except `clean` and `prioritize` is one `dashboard.py`
call (`open` is an alias of `url`); `clean` is the procedure in
[`references/clean.md`](references/clean.md) and `prioritize` the one in
[`references/prioritize.md`](references/prioritize.md). Spell the call as:

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
`open`, `export`, `import`, `tune`, `view`, `overlay` and `plan` touch only local files.

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
   - `enabled = true` under `[linear]`, with a key, puts the owner's open
     assigned Linear issues on Now on every instance, and on a fleet instance
     adds Linear projects to the Workstreams tab, which otherwise shows
     milestones and proposed designs. If the key was made on a different
     Linear account from the one the owner's issues are assigned to, set
     `me = "<that account's email>"` under `[linear]` too: without it Now
     reads only the key's own account's issues. The key never goes in config.toml: it
     goes in `~/.config/workflow-skills/linear_api_key`, mode 600, or in
     `$LINEAR_API_KEY` (`linear_api.py` has the full ladder). Without one the
     Linear issues step fails with `no Linear key` and Workstreams records
     `linear: no key`; everything else still syncs.
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
include filter, the fleet Now's "why not now" answers and its saved mode, and
the plans) is the one thing on this machine that cannot be
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

## view, scopes, items, item, brief, overlay

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
`--mode ship|little|deep|tidy|plan` picks one, and without
it the mode the owner last saved with Now's mode button is used (ship when
none is), so you see what they see; `notes.mode` names it, outside Ship
`notes.more_in_mode` counts what the list left out, and in Little
`notes.closed_today` is the day's count. `plan` lists the live plan (below)
in its order: each item's `plan` carries its `reason` and `state`, `done`
(ticked on a closed signal), `open`, or `gone` (left the mirror with nothing
saying it closed, so **not** done; with `github.scope` "owner", syncs re-read
gone GitHub items, up to 10 a sync, and tick those GitHub says closed); `notes.plan` is the plan with `counts`
per state, or null with `notes.no_plan` when none is live. `freshness` says when each sync last
succeeded and when each host last snapshotted. Without `--json` it prints one
line per item.

`view` is a whole tab; for a question about a slice of the work, use `scopes`,
`items` and `item`, unsandboxed (inside the sandbox SQLite cannot open the WAL file
beside the DB). All print compact JSON and never write, except to migrate
a DB that is behind the current schema, once, as `view` does.

- `DASH scopes <word>` says what a word can mean: each matching scope id
  (`repo:`, `ws:`, `jira:`, `linear:`, `epic:`, `label:`, `host:`) with its
  open-item count. For a repo it also lists `related` workstreams, those with open
  members in the repo whatever their name, counting only open members. A session or worktree knows its repo only
  by directory name, so it counts under every owner's repo of that name.
  **Show `related` to the owner and pass the
  ones they confirm as `--scope ws:<id>`; never add them yourself.** Say what
  the word matched. `linear:<TEAM>` is a Linear team's mirrored tickets; a Linear project is
  the `ws:linear:<id>` workstream, which holds the project's tickets as well
  as the PRs and issues naming them, in `items` and `scopes` alike.
- `DASH items --scope <id or word>` (repeatable, a union) prints one record
  per open item, every source in one shape: its state, prio, age, labels,
  workstreams, linked `pr` readiness, live `activity` (sessions and worktrees
  on its branch), blockers, the owner's `overlay` marks, Now's `now` placing,
  and `size` (`{bucket, est_min, from}`, null when unsized — **never treat
  an unsized item as small**; a bot PR is `bot_pr` by its author, whatever
  its size). A ticket is a Jira or a Linear issue, in one shape; a Linear
  estimate sizes it as `est:N` when no `est:` label does. A ticket's `mine`
  is `false` when it is unassigned or someone else's — say so before
  treating it as the owner's — and absent when the mirror cannot tell.
  `--state todo,started,done` filters the status
  category (default open); `--only blocked,waiting,stale` filters derived
  facts, so "started and blocked" is both flags. `--fields` takes `brief`
  (the default) or `full` (adds repo, branch, host, path, parent, assignee,
  cycle, created, updated, evidence, risks, action) — both leave out null and empty fields —
  or exact names, which print every named field plus identity.
  `--limit`/`--offset` page it; `total` and `returned` say when a list was
  cut. `--jsonl` prints the header on line one, then one item a line. `--scope all` is no filter and is never matched as a word.
  The order is newest activity first, not a priority.
- `DASH item <source:key>` prints one item's dossier, open or done: read it
  before saying "close this" or "this one first". Pass `source:key` as
  `items` prints them (`github:<url>`, `jira:PAY-7`, `claude-hook:<id>`);
  a bare key or ref works only when it names one item, and otherwise it
  exits 2 with every match as JSON on stdout — pick one and pass its
  `source:key`. Its sections, in this order and spelling for `--sections`:
  `tracker` (the `full` record plus `detail`, what the mirrored row holds
  beyond it), `prs` (every linked PR, newest first, with CI, review,
  co-review misses and how it links), `sessions` (on its branch, with the
  capped first prompt and last ask), `worktrees` (with `dirty` and
  `unpushed`), `workstreams` (with why it is in each), `links` and
  `overlay` (pin, snooze, note, tag and every why-not answer). `links.form`
  is `refs` for Jira (both directions), Linear and PRs, but `counts` for a
  GitHub issue: the mirror holds only how many open issues block it, so say
  so rather than naming blockers.
- `DASH brief [--scope X ...] [--mode ship|little|deep|tidy|plan] [--minutes N]
  [--sections S]` is one scope's state for a prioritization, within about 8k
  tokens: `freshness`, `in_flight` (PRs by readiness, waiting sessions,
  worktrees with no PR), `blocked`, `candidates` (the Now mode's items in its
  order, uncapped), `signals` (the owner's pins, snoozes, notes, why-nots)
  and `gaps` (sources not mirrored). A list the budget cut gives `omitted`
  and the `more` command that lists it. Read it through `prioritize`.

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

`DASH plan` keeps the ordered answer to "what next": a list of items for one
scope, a reason each. The overlay carries it, so export and import do too.

- `DASH plan set --scope X --by <author> --file <list.json|-> [--kind
  standing|session] [--minutes N] [--mode M]`. The file is a JSON list, in
  order, of `{"source","key","reason"}` objects (reason: one line, 500
  characters at most), each optionally with `action` (`do`, `finish`,
  `unblock`, `review`, `defer`, `drop`) and `est_min`. `source` and `key` are
  as `items` prints them. Every entry must be in the mirror, open or done;
  the refusal names each key that is not.
- A plan belongs to exactly **one** scope id, or to `all`, and `--scope` is
  required, so a plan never lands on `all` by omission. A
  word that matches several scopes is refused, naming them; pass one id from
  `scopes`. There is one plan per scope and kind, so `set` replaces.
- `standing` (the default) stays until cleared. `--minutes N` (the time box,
  in minutes), or `--kind session`, makes a session plan, which expires at the next 04:00 host local
  time (a plan set between midnight and 04:00 expires that morning). `show`
  still lists an expired plan, with `expired: true`.
- `--by` names the author, free text (`claude-finplan`, `owner`).
- `DASH plan show [--scope X] [--kind K]` prints the plans, with each one's
  `est_min_sum`; `DASH plan clear --scope X [--kind K]` removes it (`--scope
  all` for the all-scope plan; both kinds without `--kind`).
- A session plan that ends (expired, replaced or cleared) leaves a record of
  each entry's state then; `DASH plan outcomes [--scope X]` lists them. A
  "why not now" answer on a planned item records the plan's reason too.
- Exit 2 for a refused value, 1 for an unreadable file or DB.

## prioritize

`prioritize [scope]` ("help me prioritize my FinPlan tasks", "I've got half
an hour, what should I bang out?") reads the ask for a scope, a Now mode and a
time box, then `DASH brief` and the `item` dossiers, and proposes an ordered
plan in chat, a reason and `est_min` per item; on the owner's yes it saves it
with `plan set`. **It never writes to a tracker, and never calls an unsized
item small.** The procedure is
[`references/prioritize.md`](references/prioritize.md); read it before
starting.

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
