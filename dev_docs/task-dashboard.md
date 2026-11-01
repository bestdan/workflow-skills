# Task dashboard

A local, read-only mirror of the owner's work, ranked. A background sync
copies Jira (through `acli`), GitHub (through `gh`) and, on a fleet instance,
machine snapshots and Claude session state into one SQLite file. A stdlib HTTP
server renders every page from that file, so a page never waits on the
network. The dashboard never writes to Jira, GitHub or a session: each card
links to where the action happens and names the command that does it.

This doc says how the dashboard is built and how to run it. The detail lives in
the code: each module under [`scripts/task-dashboard/`](../scripts/task-dashboard/)
opens with a docstring that is the spec for its part, and this doc links to
those rather than restating them. The operator's commands are the
[`/task-dashboard` skill](../skills/task-dashboard/SKILL.md).

It runs as one of two kinds of instance from the same code:

- **A Jira instance** on a work laptop: Jira tickets, plus the owner's PRs and
  review requests in the configured GitHub orgs. It is bound to localhost.
- **A fleet instance** on an always-on machine: agent work across several of
  the owner's machines, read from a phone over the tailnet. See
  [Fleet instance](#fleet-instance).

An instance is a fleet instance when `github.scope = "owner"` and GitHub is
enabled (`sync.fleet_enabled()`). Jira can be on or off in either kind
(`jira.enabled`).

## Architecture

```
launchd agent ─┬─ Syncer thread ── sync steps ── acli / gh / claude -p ──▶ SQLite mirror
               │                                                              │
               └─ HTTP server (127.0.0.1) ◀── reads ──────────────────────────┤
                    │   ├─ POST /overlay/*, /sync, /refresh (page token)      │
                    │   └─ POST /ingest (per-machine token) ──▶ snapshots ───┘
                    └── overlay writes ──▶ SQLite overlay
```

- **One process, stdlib only**, under `uv run --no-project --python ">=3.11"`
  (3.11 for `tomllib`). [`dashboard.py`](../scripts/task-dashboard/dashboard.py)
  is the CLI and carries a 3.9-parseable version guard, because macOS's
  `python3` is 3.9. [`server.py`](../scripts/task-dashboard/server.py) holds the
  HTTP server and the `Syncer` thread. The interval and **Sync now** both wake
  the thread, so there is one sync path and one writer to the mirror. The one
  exception is a card's Refresh, which rewrites a single item's rows on its own
  request thread ([`refresh.py`](../scripts/task-dashboard/refresh.py)).
- **Mirror vs. overlay.** [`db.py`](../scripts/task-dashboard/db.py) keeps the
  two apart and holds the migrations. Mirror rows belong to the sync and can be
  rewritten on any run. Overlay rows are the owner's: pins, order, snoozes,
  notes, kind tags, ignores, last visit, per-view include filters, "why not
  now" answers and Now's mode. A sync never touches them, and they are the only
  state that cannot be rebuilt. Every mirror row is a generic
  item keyed `(source, key)` with a `kind`, and the overlay uses the same key,
  so an overlay row attaches to any kind of item. The DB runs in WAL mode, so a
  page reads the last committed state while a sync writes.
- **State** is `~/.local/state/task-dashboard/dashboard.db`, outside every
  checkout and plugin directory, so a plugin update never wipes the overlay.
  Config is `~/.config/task-dashboard/config.toml`, read only, never written by
  the daemon ([`config.py`](../scripts/task-dashboard/config.py)).

### Sync steps

Each scheduled cycle runs these steps in order (`sync.run_all`). Each step
writes its own `sync_runs` row, so one source's failure never touches another
source's rows or outcome:

| Step                    | Kind in `sync_runs`                  | Module                                                                                                                                                             | Runs when                                            |
| ----------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------- |
| Jira and my PRs         | `incremental` / `reconcile` / `full` | [`sync.py`](../scripts/task-dashboard/sync.py), [`jira_acli.py`](../scripts/task-dashboard/jira_acli.py), [`github_gh.py`](../scripts/task-dashboard/github_gh.py) | always (each half when enabled)                      |
| GitHub fleet read       | `github_fleet`                       | [`github_fleet.py`](../scripts/task-dashboard/github_fleet.py)                                                                                                     | fleet instance                                       |
| Workstreams             | `workstreams`                        | [`workstreams.py`](../scripts/task-dashboard/workstreams.py), [`linear_api.py`](../scripts/task-dashboard/linear_api.py)                                           | fleet instance                                       |
| Linear issues           | `linear_issues`                      | [`linear_issues.py`](../scripts/task-dashboard/linear_issues.py), [`linear_api.py`](../scripts/task-dashboard/linear_api.py)                                       | `linear.enabled = true`                              |
| Hook session rows       | `sessions`                           | [`sessions.py`](../scripts/task-dashboard/sessions.py)                                                                                                             | the instance ingests snapshots                       |
| Cloud session list      | `claude_remote`                      | [`claude_remote.py`](../scripts/task-dashboard/claude_remote.py)                                                                                                   | `claude_remote.enabled = true`                       |
| Plan ticks and outcomes | `plans`                              | [`plan_ticks.py`](../scripts/task-dashboard/plan_ticks.py), [`refresh.py`](../scripts/task-dashboard/refresh.py)                                                   | a plan is live, or a session plan expired unrecorded |

`sync.py`'s module docstring has the exact gates. Each source writes only its
own `source` value, so the steps never overwrite each other's rows.

### Views and the ranking seam

Every view reads SQLite only, and is built as plain-text lanes
([`view_model.py`](../scripts/task-dashboard/view_model.py)) before it becomes
HTML. That is what lets `dashboard.py view <name> --json`
([`agent_view.py`](../scripts/task-dashboard/agent_view.py)) hand an agent
exactly the lanes the page shows.

| Tab                                        | Module                                                                                                                                                                       | Instance                            |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| Now (modes Ship, Little, Deep, Tidy, Plan) | [`fleet_now.py`](../scripts/task-dashboard/fleet_now.py), [`close_out.py`](../scripts/task-dashboard/close_out.py), [`plan_mode.py`](../scripts/task-dashboard/plan_mode.py) | every instance; home on a fleet one |
| Needs you                                  | [`needs.py`](../scripts/task-dashboard/needs.py)                                                                                                                             | fleet                               |
| Pull requests, Issues                      | [`fleet_tabs.py`](../scripts/task-dashboard/fleet_tabs.py)                                                                                                                   | fleet                               |
| Workstreams                                | [`workstreams.py`](../scripts/task-dashboard/workstreams.py)                                                                                                                 | fleet                               |
| Stale                                      | [`views.py`](../scripts/task-dashboard/views.py), [`fleet_views.py`](../scripts/task-dashboard/fleet_views.py)                                                               | every instance                      |
| Board, Epics                               | [`views.py`](../scripts/task-dashboard/views.py)                                                                                                                             | Jira on                             |
| Waiting on me, I'm waiting on              | [`views.py`](../scripts/task-dashboard/views.py)                                                                                                                             | Jira on, not fleet                  |
| Health                                     | [`health.py`](../scripts/task-dashboard/health.py), [`whynot.py`](../scripts/task-dashboard/whynot.py)                                                                       | every instance                      |

Now is one ranker on every instance, over every source the instance has. Each
source turns its rows into `Candidate`s
([`candidate.py`](../scripts/task-dashboard/candidate.py)), and
`fleet_now.rank` reads only those. Within a fixed tier the order is a
deterministic sum of config-weighted terms
([`score.py`](../scripts/task-dashboard/score.py)), and each card shows its
terms, so the ranking can be argued with. Nothing calls a model at runtime.
`dashboard.py tune` only suggests weight changes from the "why not now"
answers. The modes are specified in
[`designs/2026-10-26-now-modes.md`](designs/2026-10-26-now-modes.md), and the
shared ranker in
[`designs/2026-10-27-shared-now-ranker.md`](designs/2026-10-27-shared-now-ranker.md).

Shared by every page:

- the include filter, in [`include.py`](../scripts/task-dashboard/include.py);
- the lane tabs, in [`lane_tabs.py`](../scripts/task-dashboard/lane_tabs.py);
- the card controls and overlay writes, in
  [`overlay.py`](../scripts/task-dashboard/overlay.py);
- the review gauge, in [`gauges.py`](../scripts/task-dashboard/gauges.py);
- the freshness indicator, in
  [`freshness.py`](../scripts/task-dashboard/freshness.py). It shows the age
  of the oldest source. Its panel has one row per sync step and per collector
  host, each with its last outcome and next run. A host that has been silent
  long enough is shown as asleep and does not raise a warning.

### Agent-facing commands

None of these needs the server running:

- **`view <name> [--json] [--all] [--mode M]`** prints one tab's lanes as the
  page shows them. `--all` adds the hidden items, each with the reason it was
  hidden.
- **`scopes [query]`** and **`items --scope X`**
  ([`agent_query.py`](../scripts/task-dashboard/agent_query.py)) are the
  query-shaped read from
  [`designs/2026-10-30-dashboard-as-agent-data-source.md`](designs/2026-10-30-dashboard-as-agent-data-source.md):
  what a word can mean as a scope, and one compact record per item in it,
  joined across tracker, GitHub and the fleet. Each field comes from the code
  a view already uses, so `items` adds no ranking rule. They open the DB with
  `db.connect_read_only` (`mode=ro`, `query_only`), which writes only to
  migrate a DB that is behind the current schema, once, as `view` does. An item's `size` reads its
  bucket (Little's PR categories by size alone, else `large_pr`, or an
  `est:N` label, else a Linear estimate as `est:N`) through `[size.minutes]`.
  A `ticket` is a Jira issue or, with `[linear] enabled`, a mirrored Linear
  one, in the same shape; its `mine` is false for an unassigned or someone
  else's issue (Jira: `views.Mirror.is_mine`; Linear: `linear.me` when set,
  else the key's user) and absent for a Linear row synced before the read
  carried its assignee. Scopes: `jira:<PROJECT>` holds Jira tickets only;
  `linear:<TEAM>` a Linear team's tickets. A Linear project has no scope
  kind of its own: it is the `ws:linear:<id>` workstream, whose members, in
  `items` and `scopes` alike, are
  the project's tickets (joined by the row's project id, else its project
  name) plus the PRs and issues naming them. A completed or canceled project
  is no open workstream, so its tickets fall under their team alone.
- **`item <source:key> [--sections S]`** (`agent_query.item_doc`) is one
  item's dossier, built from the same `collect` pass as `items`, so the joins
  are not repeated: `_link_prs` keeps every PR it matched (`_prs`) besides
  the newest, and `_activity` keeps the session and worktree items
  themselves (`_on`). The sections, which `--sections` names in this
  spelling, are `tracker`, `prs`, `sessions`, `worktrees`, `workstreams`,
  `links` and `overlay`. `tracker.detail` is the mirrored row less what the
  record already carries. `links.form` is `refs` (Jira's `links` table in
  both directions, Linear relations, a PR's closing issues and Jira key) or
  `counts` (a GitHub issue, whose mirror row holds only open-blocker counts).
  `source:key` is unique; a bare key or ref is accepted only when it names one
  item, and otherwise the command exits 2 with the matches as JSON on stdout.
- **`brief [--scope X] [--mode M] [--minutes N] [--sections S]`**
  ([`brief.py`](../scripts/task-dashboard/brief.py)) is the one call behind
  "prioritize X": sections `freshness` (`freshness.sources`), `in_flight`
  (open PRs by `brief.PR_READINESS`, waiting sessions, worktrees with no
  PR), `blocked`, `candidates` (the Now mode's collector and order, uncapped
  and scoped), `signals` and `gaps`. Records come from one `collect` pass;
  the candidates' order is the Now mode's own collect and rank. The budget,
  about 8k tokens as printed bytes over 4, and which list gives way first
  (`brief.FILL`) are in the module docstring. Deep inlines the top
  candidate's `item_doc`. The skill's procedure for it is
  [`references/prioritize.md`](../skills/task-dashboard/references/prioritize.md).
- **`overlay <action> --source S --key K`** makes the same write a card button
  makes, and goes through the same validation (`overlay.apply`).
- **`refresh <owner/repo#N>`** re-reads one PR or issue from GitHub in a
  single GraphQL call (so it needs the network) and rewrites its rows the way a sync would. It writes no `sync_runs`
  row, so the header's age does not move.
- **`clean <view>`** is a procedure the skill runs with the owner, in
  [`references/clean.md`](../skills/task-dashboard/references/clean.md).

## Jira: the two-step `acli` fetch

Every Jira read is two steps ([`jira_acli.py`](../scripts/task-dashboard/jira_acli.py)):

1. `acli jira workitem search --paginate` returns the keys.
2. `acli jira workitem view <KEY> --json` runs once per key and returns the
   fields.

`search` accepts only a fixed field allowlist. That allowlist has no dates,
links, parent or custom fields, so `search` cannot supply the fields the views
need. `view` takes one key per call, so the views fan out over a thread pool
(`sync.view_concurrency`, default 8). Calls that fail get one serial retry,
because about 1% fail transiently at that concurrency. The spike that measured
all of this is
[`research/2026-10-23-task-dashboard-acli-spike.md`](research/2026-10-23-task-dashboard-acli-spike.md).
It also found:

- `view` output has to go to a pipe. With stdout sent to `/dev/null`, acli
  exits 1.
- acli cannot read the changelog, so Stuck and Stale measure time in status
  _category_ (`statuscategorychangedate`), not time in status.
- acli has no field-listing command, so the sprint and Flagged custom-field
  ids are configured per site.
- The sprint field lists every sprint the issue has ever been in. The sync
  keeps the active one.
- acli's OAuth login works under `launchd` because the login keychain is
  unlocked once the user has logged in. That is why the server is a gui-domain
  LaunchAgent.

The JQL scopes (assigned, the configured boards' active sprints,
reported-or-watched, and a one-hop linked pass) are built by
`sync.build_scopes`. Each scope is parenthesized before the done-window clause
is appended, because JQL binds `AND` tighter than `OR`.

GitHub PRs are joined to Jira tickets by a key in the title or branch. The
match is case-insensitive, and allowlisted to project keys already in the
mirror. `github_gh.py` uses a GraphQL search rather than `gh search prs`,
because the latter has no branch name.

## Sync modes and the cursor rule

Specified in `sync.py`'s module docstring, and scheduled by `server.Syncer`:

- **`incremental`** appends `updated >= "-<N>m"` to each scope and views only
  the keys that come back. With no successful incremental or full run recorded,
  it runs as `full`.
- **`reconcile`** searches each scope for keys only, views the keys the mirror
  lacks, and drops the Jira rows that no scope returned. A scheduled run is a
  reconcile once `sync.reconcile_minutes` (default 60) have passed since the
  last one.
- **`full`** views every key. It takes about 100s against real Jira, so it runs
  only at install and on demand.

**The cursor rule.** N is the number of minutes since the last successful run
_started_, rounded up, plus 5 (`sync.cursor_minutes`). The cursor has to be
relative. Jira reads an absolute JQL date in the profile's timezone, not UTC,
and `search` cannot return `updated`, so there is no high-water mark to read
back. The 5-minute overlap covers index lag, and upserts make the duplicates
harmless.

Each run fetches everything first, then writes all of its mirror changes in one
transaction, so a failed run leaves the mirror as it was. The `sync_runs` row
is written outside that transaction, so the failure is still recorded.

**Sync now** (the header button, `POST /sync`, or `/task-dashboard sync-now`)
wakes the `Syncer`. A request made while a run is in flight joins it, with one
exception: a reconcile asked for during an incremental run is queued to run
right after it. Every finished run restarts the interval. Shift-click, or
`sync-now --reconcile`, asks for a reconcile.

## Security model

The access rules are in `server.py`'s module docstring, and ingest's in
[`ingest.py`](../scripts/task-dashboard/ingest.py):

- **Loopback only.** The socket binds to `127.0.0.1`.
- **Host.** Every request's `Host` must be exactly `127.0.0.1:<port>`,
  `localhost:<port>`, or an entry of `server.extra_allowed_hosts`. Anything
  else gets a 403. Loopback binding alone does not stop DNS rebinding, and
  without this check any page open in the browser could read the dashboard's
  pages. A refused `Host` is logged to stderr, so the header a proxy actually
  forwards can be copied into config rather than guessed.
- **Origin and token.** Every `POST` (overlay writes, `/sync` and `/refresh`)
  also needs an `Origin` naming an allowed host, plus the per-install token
  that the page embeds. The token is safe only because the Host check stops
  another origin from reading the page.
- **Ingest.** `POST /ingest` exists only when `[ingest.machines]` is set. It
  passes the same Host check, then takes a per-machine bearer token instead of
  the Origin check and the page token. See
  [Fleet instance](#ingest-security).

## Per-site config

`config.py` is the schema. Its `DEFAULTS` hold every default, with a comment on
each key, so a sparse file is valid. A bad key fails at load, naming the dotted
key. The keys with no sane default are the ones that differ by site:

- **`jira.site`** is the Atlassian site.
- **`jira.board_ids`** lists the boards whose active sprints form the sprint
  scope. A board's id is the `boardId` inside the sprint field of an issue
  that is already in the sprint.
- **`jira.fields.sprint`** and **`jira.fields.flagged`** are the custom-field
  ids. Read them off `acli jira workitem view <KEY> --json`: the sprint field is
  the array of objects carrying `boardId` and `endDate`.
- **`jira.me`**, optional, is the owner's Jira display name. When it is unset,
  every in-scope issue counts as the owner's.
- **`jira.enabled = false`** waives all the Jira keys, for an instance with no
  Jira.
- **`github.orgs`** lists the owners whose PRs and issues count as work. The
  `gh` account usually reaches personal repos as well as work orgs, so every
  search is scoped to this list.
- **`github.scope`** is `"me"` by default (only what involves the owner) or
  `"owner"` (the fleet read). The default keeps an unset key from mirroring a
  whole work org.

`/task-dashboard install` seeds `jira.site` and `jira.blocked_statuses` from a
repo's `.task-config.yml` when there is one, and asks for the rest. Claude reads
the YAML and writes the TOML, and after that the two files are independent.
Point `jira.acli_path` at `scripts/task-dashboard/dev/fake-acli` to try the
Jira views with no Jira.

### Linear (Now and Workstreams)

`[linear] enabled = true` does two things once a key resolves. On every
instance, the `linear_issues` step mirrors the open issues (state type
`unstarted` or `started`) assigned to `linear.me`, an email, or with it unset
to the key's own user. Set `me` when the key was made on a different Linear
account from the one the issues are assigned to: otherwise it reads the key's
own account's issues instead, with no error. A `me` that names no Linear user
fails the step and keeps the last good rows. The same step also mirrors the
whole open backlog (state type `backlog`, `unstarted` or `started`, assigned
or not) of the teams the open Linear projects belong to, for `items` and
`scopes`; those rows carry `scope = 'team'` and Now never ranks them. Its run
counts how many rows are mine, unassigned and in the backlog, and how many
Linear requests it made: about one per 50 open issues, plus the projects
read. Now ranks only the `assigned` rows, as it ranks Jira
tickets: started is the `started` tier, unstarted lands in the rest, an open
blocker or the `blocked` label sends one to the blocked tier, and a PR whose
title or branch carries the key folds into its card. `linear_issues.py` and
`fleet_now.py`'s module docstrings have the row and the rules. On a fleet
instance, Linear projects also join the Workstreams tab.
`linear_api.resolve_key` takes the first hit of:

1. `$LINEAR_API_KEY`, the raw key;
2. the key file `~/.config/workflow-skills/linear_api_key`, mode 600;
3. `$LINEAR_API_KEY_REF`, resolved through
   [`_secret_resolve.py`](../commands/handlers/assets/_secret_resolve.py) per
   [`auth_key_access.md`](auth_key_access.md).

With no key, the Linear issues step fails as `auth_failed` with the reason
and keeps its last rows, and Workstreams records `linear: no key` and shows
the other sources. The freshness panel has a row for each.

The key file is the simplest shape for the LaunchAgent, because install writes
no secret into the plist. To use a 1Password reference instead, the agent's
environment needs four things:

- `LINEAR_API_KEY_REF`, an `op://<vault>/<item>/<field>` reference;
- `OP_SA=on`;
- `OP_SA_PROFILE` naming the service-account profile;
- a `PATH` that has the `op` wrapper in `~/.local/bin` first and
  `/opt/homebrew/bin`, where the real `op` lives, after it.

The resolver runs `op read` by name, so `PATH` order decides which `op` runs. A
LaunchAgent has no desktop session to raise a biometric prompt, so a
service-account token is what makes the read unattended. Install writes only
`HOME`, `PATH` and the `TASK_DASHBOARD_*` paths into the plist's
`EnvironmentVariables` (`server_install.agent_env`), so these entries are added
by hand. After editing the plist, run `launchctl bootout` and then
`launchctl bootstrap`, because `kickstart` reuses the loaded definition. A
reinstall rewrites the plist, so add the entries again after one.

## Operating notes

**Install and uninstall.** Both are covered in the
[skill](../skills/task-dashboard/SKILL.md), and
[`server_install.py`](../scripts/task-dashboard/server_install.py) has the
mechanics.

- **What install does.** It writes the gui-domain LaunchAgent
  `com.bestdan.task-dashboard` and the stable launcher
  `~/.local/bin/task-dashboard-launch`. The launcher resolves the plugin's
  current `installPath` at every start, so the plist never names the
  versioned directory, which moves on every plugin update and would break a
  hard-coded path. Install then runs `init-db` and a `full` sync in the
  foreground, which also warms uv's interpreter cache, and only after that
  bootstraps the agent. If any step fails, install undoes what it wrote.
- **After a plugin update**, run
  `launchctl kickstart -k gui/$(id -u)/com.bestdan.task-dashboard`. No
  reinstall is needed.
- **What uninstall removes.** It removes the server's agent, and the launcher
  unless the collector still uses it. It never removes the DB, the config or
  the logs. Run `export` first to keep the overlay.
- **`/doctor`.** Check 10 runs `dashboard.py status --doctor`.

**Reading `sync_runs`.** Every step writes one row per run:

- `kind` is the step (see [Sync steps](#sync-steps)).
- `outcome` is `ok`, `throttled`, `failed`, `auth_failed`, or NULL while the
  run is going.
- `error` and `error_kind` hold the failure.
- `counts` and `failed_keys` are JSON.

`throttled` counts as a success: the fleet read skipped its per-PR reads to
spare the GraphQL budget (`github.min_remaining`). `dashboard.py status` and the
freshness panel are the readers; for the raw rows:

```sh
sqlite3 ~/.local/state/task-dashboard/dashboard.db \
  "SELECT id, kind, started_at, finished_at, outcome, error_kind FROM sync_runs ORDER BY id DESC LIMIT 20"
```

**Expired auth** is its own outcome, `auth_failed`. The header names the fix:
`acli jira auth login` for Jira, or `gh auth login` for GitHub, run in the
owner's own terminal and followed by **Sync now**. The spike did not establish
whether acli's refresh token expires over days.

**Plan C.** If the `view` fan-out degrades (too slow, or the failure rate rises
past what one serial retry covers), the fallback is Jira's REST
`/rest/api/3/search/jql`. It returns any field in one paginated call, but needs
its own API token, because acli's OAuth token is stored for acli alone. Nothing
is built for it. The swap would sit behind `sync.JiraSource`, whose
`active_sprints`, `search` and `view` are the only Jira calls the sync makes.

## Fleet instance

The fleet instance mirrors the owner's agent work across machines: Claude
sessions, PRs, issues, worktrees and handoffs. It runs on an always-on Mac, and
is read from a phone over the tailnet through Tailscale Serve, with the
forwarded `Host` in `server.extra_allowed_hosts`. It shares code with a Jira
instance, never data.

**Non-goals.** The view never answers a prompt or an approval, and never
relays messages between sessions. It also never enforces a cap: the gauges
show the review-queue cap and nothing blocks on it. Like every instance, it
writes nothing to GitHub, Jira or a session.

### The push model

```
laptop ─┐                                       ┌─ phone (tailnet)
linux ──┼─ collector ── snapshot ──▶ aggregator: /ingest ─▶ SQLite ─▶ server
mini ───┘  (per machine)  (POST, bearer token)   + GitHub / cloud-session steps
```

Each machine runs a collector ([`collector.py`](../scripts/task-dashboard/collector.py))
every five minutes. A gui-domain LaunchAgent runs it on macOS, and a
`systemd --user` timer on Linux. `install --collector-only` sets either up
([`collector_install.py`](../scripts/task-dashboard/collector_install.py)). The
collector builds a snapshot of machine-local state, spools it, and POSTs the
spool oldest first to the aggregator.

The machines push rather than the aggregator pulling, for two reasons. A pull
over ssh fails while a laptop sleeps, whereas a laptop that wakes posts its
spool, and each snapshot carries its own `taken_at`. And messages between
sessions on different machines do not arrive reliably. Delivery is in order
because the server refuses a snapshot older than one it already has (409). How
the collector handles each answer, and how it prunes, is in `collector.py`'s
docstring.

### Ingest security

[`ingest.py`](../scripts/task-dashboard/ingest.py) has the full check order.

- **One token per machine.** `dashboard.py ingest-token add <host>` prints a
  token once. The token goes in that machine's `[collector] token`. Only its
  SHA-256 goes in the server's `[ingest.machines]`, so the server's config
  never holds a usable secret.
- **The collector's config has to be private.** `config.load_collector`
  refuses a config file that is not mode 0600. The token goes only in the
  `Authorization` header: never in argv, a unit file or a log line. Redirects
  are refused, so the header cannot follow one to another host.
- **Checks before the body is read.** The server checks the token (401) and
  the declared size (411 or 413) before it reads the body, so an
  unauthenticated caller cannot make it read anything.
- **Checks on the body.** Then come the JSON and the schema, the snapshot's
  `host` against the token's machine (403), a `taken_at` too far ahead of the
  server's clock, and staleness (409).
- **One transaction.** A snapshot is written whole or not at all
  ([`snapshot_adapter.py`](../scripts/task-dashboard/snapshot_adapter.py)).

### The session-state hook

[`scripts/session-state-hook.py`](../scripts/session-state-hook.py) runs on
`UserPromptSubmit`, `Stop` and `Notification`, and is shipped in the plugin's
`hooks/hooks.json`. It appends one allowlisted line per event to
`~/.local/state/fleet/sessions/<session_id>.jsonl`: the time, the event, the
cwd, the branch, the notification type, and on `Stop` one `asked` boolean.

- **Opt-in, off by default.** The plugin is public, and is installed by
  people who never asked to have their sessions logged. So the hook writes
  nothing unless the dashboard config has a literal `[collector]` table with
  `enabled = true`. Any other form reads as off.
- **No prompt text, ever.** The prompt, the notification message and the last
  assistant text are never written. Only the `asked` boolean is derived from
  the last text.
- **Never fails the turn.** Every error is swallowed and the hook always exits 0.

One part of the collector does carry prompt text, and it is narrow. The
snapshot's `transcripts` (schema 5) sends a capped first prompt and last ask
per recent session, for the cards' session context. `snapshot.py`'s
"TRANSCRIPTS" paragraph says what is never read.

### The snapshot schema

The schema is [`snapshot.py`](../scripts/task-dashboard/snapshot.py)'s
docstring, and `snapshot.validate()` is its executable form. It is now at
schema 5:

| Schema | Adds                                                             |
| ------ | ---------------------------------------------------------------- |
| 2      | `scan_errors`, so a failed worktree scan keeps the previous rows |
| 3      | `registry`, Claude Code's live process registry                  |
| 4      | each registry entry's `cwd`                                      |
| 5      | `transcripts`, the session summaries                             |

Ingest accepts schemas 1 to 5. A collector that an older server refuses with
`unsupported schema` resends one schema lower (`snapshot.downgrade`), so update
the server first where you can.

### Session state

[`sessions.py`](../scripts/task-dashboard/sessions.py) folds the hook events
into one row per session. That row is the primary session source, and gives:

- working or waiting;
- `need_input` after a permission prompt, or after a turn that ended in a
  question;
- the exact wait start;
- whether the session is an empty spawn.

The host's registry adds the Remote Control link and the live status. The
host's transcript summary adds the card's first prompt, last ask and branch.

A session is **ended** when its host's latest snapshot carries a registry, the
session is not in it, and no live process runs in its cwd. An ended session
can only be resumed: Needs you lists it under Finished, with the
`claude --resume` command. The full rule is under `ended` in `sessions.py`.

The cloud session list ([`claude_remote.py`](../scripts/task-dashboard/claude_remote.py))
is an optional inventory, off by default. It adds cloud-only sessions with a
coarse status.

**The spike's verdict**
([`research/2026-10-23-claude-remote-session-read.md`](research/2026-10-23-claude-remote-session-read.md)):
a background process can read the session list, but only by starting
`claude -p` and sending the `@internal` `list_cloud_sessions` control request.
That read returns one page of at most 200 sessions, with no cursor and no
post-turn status, and its route can change in any CLI release. So the state on
Needs you comes from the hook. A session on a machine without the plugin is
invisible unless it is a cloud session and the list is on.

### Health

[`health.py`](../scripts/task-dashboard/health.py) is the definition of every
figure: its `FIGURES` table, and the module docstring for how each one is
computed. The `/health` route and `dashboard.py health --json` read the same
`compute()`.

Windowed figures are compared with the window of the same length just before
them. These are:

- approval-shaped prompts per 100;
- the switch rate;
- the share of prompts with three or more sessions active;
- permission prompts per 100 prompts;
- the two waits (owner on agent, and agent on owner), as median and p90;
- issues opened : closed, from counts the fleet sync takes for each 7-, 14-
  and 30-day window ending at the sync (`github_fleet.issue_flow`). It has a
  value only for one of those windows ending near the last sync; any other
  window says "no source yet". The coverage rule is in `health.py`'s
  docstring.

The waits are paired per session from hook events, and read "baseline" until
`BASELINE_DAYS` of hook data exist. Nothing is estimated: a figure with no data
shows no value. The current figures are open PRs awaiting the owner, the review
queue, and designs at `proposed`.

Targets come from `[health.targets]`, either `"observed"` or a comparison such
as `"<= 5"`. The review-queue cap of 5 is shown by the header gauge and is never
enforced. Health takes the queue count from `gauges.review_queue`. The rules
that decide which PRs are in the queue are defined in `github_fleet.py`'s
docstring, and a PR is in the queue only when all four hold:

1. It is open, not a draft and not approved, and it waits on the owner: the
   owner authored it, or it requests the owner's review.
2. Its head commit's CI rollup is `success`. Pending, failed and no checks all
   miss.
3. It is co-reviewed at its current head. That means a comment by the viewer
   carries a `co-review-stamp` naming the PR's `headRefOid`, and the newest
   such stamp wins.
4. Its milestone is approved, when this applies: if an issue the PR closes is
   in a milestone, one of those milestones must have an `approved: yes` line.
   A PR that closes no issue in a milestone skips this rule.

### Fleet views

Each view's rules are its module's docstring:

- **Needs you** ([`needs.py`](../scripts/task-dashboard/needs.py)) collects
  sessions waiting on the owner, PRs in the review queue, PRs from others, and
  `auto:human-review-needed` issues. Plain approvals go to their own lane, and
  a Finished lane lists what ended since the last visit.
- **Stale** ([`fleet_views.py`](../scripts/task-dashboard/fleet_views.py))
  closes by evidence, never by age, in two lanes: Close and Check.
- **Pull requests and Issues**
  ([`fleet_tabs.py`](../scripts/task-dashboard/fleet_tabs.py)) take the place
  of the Jira waiting tabs.
- **Workstreams** ([`workstreams.py`](../scripts/task-dashboard/workstreams.py))
  groups the mirror by theme: GitHub milestones, Linear projects, and designs
  at `proposed`. A design folds into the milestone it describes. The lanes are
  Complete, In progress, Planned, Design and Stalled, and a Complete
  milestone becomes a close-out on Now.

## History

This doc replaces the two design proposals the dashboard was built from, one
for the Jira instance and one for the fleet instance. Both are in git history:
`git log --diff-filter=D --name-only -- dev_docs/designs/` lists them. Where a
design and the code differ, the code and this doc are current.
