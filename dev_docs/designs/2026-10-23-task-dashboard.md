---
created: 2026-10-23
---

# task-dashboard — a local, read-only mirror for prioritizing and coordinating

Proposal for a locally hosted dashboard over the user's work, mostly Jira. A
background job syncs Jira (through `acli`) and GitHub (through `gh`) into a
local SQLite file, and a stdlib web server renders views from that file. Pages
never wait on a network call, so the dashboard is fast. It never writes to Jira
or GitHub. Changes still go through `/add-task`, `/complete-task` and the claim
flows.

Status: design only. Nothing below is built. The **Verify before building**
section lists the claims about `acli` that are inferred and not yet tested.

## Decisions taken

| Question         | Decision                                                                                                         |
| ---------------- | ---------------------------------------------------------------------------------------------------------------- |
| What gets synced | Issues assigned to me, my team's active sprint(s), issues I reported or watch, and issues one link-hop from mine |
| Writes to Jira   | None. A **local overlay** (pins, manual order, snooze, private notes) lives in SQLite only                       |
| Sources in v1    | Jira and GitHub PRs (mine, and reviews requested of me), behind a source-adapter seam                            |
| Lifecycle        | Always-on `launchd` agent: syncs on an interval and serves continuously                                          |
| Manual sync      | A **Sync now** button in the UI header runs a sync immediately, outside the interval                             |
| Host             | The work laptop only, localhost only. Porting to the Mac mini (with a tailnet link) is a later step              |
| Jira access      | `acli`, not the Atlassian MCP — a background job has no Claude session to reach an MCP through                   |

The Jira access row means the plugin will read Jira two ways: the existing
`commands/handlers/jira*.md` flows use the MCP, and the dashboard uses `acli`.
That is acceptable for v1. If `acli` proves solid, moving the `/list-tasks`
jira path onto the local mirror is a follow-up worth considering.

## Architecture

```
launchd agent ─┬─ sync loop ── source adapters ── acli / gh ──▶ SQLite (mirror tables)
               │                                                     │
               └─ HTTP server (127.0.0.1) ◀── reads ─────────────────┤
                       │                                             │
                       └── overlay writes (pins, notes) ──▶ SQLite (overlay tables)
```

It is **one process**: the HTTP server, plus a sync thread. A
`threading.Event` wakes the sync thread, and a lock ensures only one sync runs
at a time. Manual sync relies on both. One process also means one `launchd`
plist to manage. The DB runs in WAL mode, so page reads see the last committed
snapshot while a sync is writing.

- **Code:** `scripts/task-dashboard/`, stdlib-only Python like
  `scripts/local-review/server.py`. No framework and no `pip install`, so
  `launchd` can run it with the system `python3` or `uv run`.
- **State:** `~/.local/state/task-dashboard/dashboard.db`, outside every
  checkout and plugin directory, so a plugin update never wipes the overlay.
- **Skill:** `/task-dashboard` with subcommands `install` (write and load the
  plist), `status`, `sync-now`, `open` (print the URL), `export` (write the
  overlay tables to a JSON file), `import` (restore that file) and
  `uninstall`.

### Mirror vs. overlay

The DB keeps the two strictly apart:

- **Mirror tables** (`issues`, `links`, `sprints`, `prs`, `sync_runs`) belong
  to the sync. Any row can be overwritten or deleted on the next run. Nothing
  else writes them.
- **Overlay tables** (`pins`, `order`, `snoozes`, `notes`) belong to the user
  and are keyed by issue key or PR URL. A sync never touches them. An overlay
  row whose issue has left the mirror is kept, so a note survives an issue
  dropping out of scope and coming back. The UI hides it.

The overlay is the only thing on this machine that cannot be rebuilt.
Back it up with `/task-dashboard export` and restore it with `import`, rather
than by copying the DB.

## Sync

### Jira through `acli`

One JQL query per scope. Results are unioned by key:

| Scope            | JQL                                                                    |
| ---------------- | ---------------------------------------------------------------------- |
| Assigned         | `assignee = currentUser()`                                             |
| Sprint           | `sprint in (<active sprint ids of the configured board_ids>)`          |
| Reported/watched | `(reporter = currentUser() OR watcher = currentUser())`                |
| Linked           | `key in (<blocker/blocked keys from the issues above>)`, a second pass |

The sprint ids come from each configured board's active sprints, looked up at
the start of each run. `sprint in openSprints()` would return every team's open
sprints in a shared project, not just mine. If `acli` can't list a board's
sprints (see **Verify before building**), drop `board_ids` and accept a
project-wide sprint scope.

Each scope is wrapped in parentheses, and `AND (statusCategory != Done OR
resolved >= -14d)` is appended outside them. That way recently finished work
still shows and old work doesn't pile up. The parentheses matter: JQL binds
`AND` tighter than `OR`, so without them an appended clause filters only the
last `OR` branch. The same applies to the incremental clause below.

**Incremental.** After the first full pull, each run appends
`AND updated >= "-<N>m"`, where N is the number of minutes since the last
successful sync _started_, plus 5. The overlap covers index lag, and upserts
make the duplicates harmless. The relative form is deliberate: Jira reads an
absolute date in JQL in the Jira user's profile timezone. A cursor formatted in
UTC or in the laptop's timezone can be off by hours, and a 5-minute overlap
cannot cover that.

**Reconcile.** An incremental query cannot see an issue that _left_ scope, for
example one reassigned away. It also misses an issue that _entered_ scope
without its `updated` changing. Adding me as a watcher, for example, doesn't
bump `updated`. On a slower interval (hourly), run each scope's query for
**keys only**. Fetch in full every key that is missing from the mirror, then
drop the mirror rows that no scope returned.

**Fields.** Summary, status and `statusCategory`, assignee, reporter,
priority, due date, sprint (name and end date), parent/epic, labels, the
`Flagged` field, `issuelinks[]`, `created`, `updated`, and the timestamp of the
last status change. That last one gives time-in-status, and it has to come from
the changelog's last status transition. `statuscategorychangedate` is not a
substitute: it records the last change of _category_. In Progress and In
Review are both `indeterminate`, so it can't measure time in the current
status, which the Stuck term and the Stale view need. How cheaply the changelog
can be read is an open item below.

**Failure.** Each run fetches everything first, then writes its mirror changes
in a single SQLite transaction. A failed run therefore leaves the mirror as it
was. The run's `sync_runs` row is written separately, so the failure is still
recorded. The UI header always shows how old the last successful sync is, and
it turns red past three intervals. The dashboard never presents stale data as
current.

### Manual sync

A **Sync now** button in the UI header, and `/task-dashboard sync-now` from a
session, both run an incremental sync immediately instead of waiting for the
next interval.

- The button `POST`s to `/sync`, guarded by the same token as overlay writes.
  The server wakes the sync loop rather than syncing inside the request, so
  there is only ever one sync path and one writer to the mirror tables.
- **One sync at a time.** If a sync is already running, the request joins it
  instead of starting a second one. A press during a run therefore can't race
  the scheduled sync or double the `acli` calls.
- The header shows _Syncing…_ while the run is in flight, then reloads the page
  when `sync_runs` records it finished. A failed manual sync shows its error in
  the header and leaves the mirror unchanged, the same as a scheduled failure.
- A manual sync resets the interval timer, so a scheduled run doesn't fire
  seconds after a manual one.
- **Shift-click runs a full reconcile** instead of an incremental sync. That
  covers "I just reassigned something and it's still showing". A reconcile
  requested while an incremental sync is running is queued to run right after
  it. Joining the incremental run would leave the reassigned issue on screen. A
  reconcile requested while a reconcile is running joins that one.

### GitHub through `gh`

- `gh search prs --author @me --state open`
- `gh search prs --review-requested @me --state open`
- For each PR: review decision, CI rollup, draft state, last update.

A PR is joined to a Jira issue by finding an issue key in the PR's branch name
or title, with the regex `[A-Z][A-Z0-9]+-\d+`, restricted to configured
project keys. Unmatched PRs are still shown in their own lane.

### Adapter seam

Each source implements `fetch(since) -> rows` and `scope_keys() -> set`. The
sync loop, the incremental cursor and the reconcile step are shared. Linear or
the repo-pr task files could plug in later without changing the server.

## Views

All of them read SQLite only.

1. **Now.** One ranked list: pinned items first, then items by score. Each card
   shows its score broken into terms, so the ranking can be argued with.
2. **Waiting on me.** Issues of mine that block someone else, and PRs where my
   review is requested.
3. **I'm waiting on.** My issues with an unresolved inward "is blocked by"
   link, and my PRs waiting on review or CI.
4. **Board.** The sprint as a kanban built with the shared
   `commands/handlers/assets/kanban-classify.py`, so its sections match
   `/list-tasks`.
5. **Stale.** In progress, or in review, for longer than a threshold with no
   update.
6. **Epics.** Rollup per parent: done/total and blocked count.

**Blocked** is detected from four signals. The first is the `blocked` label,
which `/list-tasks` already uses. The other three are the signals
`commands/handlers/jira.md` ("Blocked signals beyond the label") documents as
missing: `Flagged`, a status listed in `jira.blocked_statuses`, and an
unresolved inward `Blocks` link. The link
signal can't be expressed in JQL, but it is a cheap SQL join once
`issuelinks[]` is local.

## Prioritization score

The score is a deterministic sum of named terms. The weights live in config.
There is no LLM in the loop, because the ranking has to be stable between
refreshes and explainable on the card.

| Term              | Signal                                                       |
| ----------------- | ------------------------------------------------------------ |
| Priority          | Jira priority mapped to urgent/high/medium/low               |
| Deadline pressure | Days to due date, or to sprint end when there is no due date |
| Unblocks others   | Count of open issues this one blocks (outward `Blocks`)      |
| Stuck             | Time in current status beyond a per-status threshold         |
| Review waiting    | A PR of mine is approved or green, so the next step is mine  |
| Blocked           | Large negative term: blocked work sinks, but stays visible   |

The overlay comes on top: a pin forces an item to the top, a manual order
overrides the score within the pinned set, and a snooze hides the item until a
date.

An LLM-written "what should I do next" summary would fit as a later, opt-in
button that reads the same DB. It is not part of v1.

## Hosting and access

- The server binds to `127.0.0.1` only.
- **Every request must carry a `Host` header of exactly `127.0.0.1:<port>` or
  `localhost:<port>`**, and is refused otherwise. Binding to loopback alone
  does not stop DNS rebinding. Through it, any website open in the browser
  could point its own hostname at `127.0.0.1` and read pages full of work Jira
  data.
- Overlay writes and `/sync` are `POST`s. Each must carry the per-install token
  embedded in the page and an `Origin` header matching the allowed hosts. The
  token stops other origins from forging writes, and it holds only because the
  `Host` check stops them from reading the page.
- **v1 runs on the work laptop, localhost only.** That is where `acli`, the
  Jira login and the work GitHub account live, and the laptop has no
  Tailscale, so there is no phone link in v1.
- **A later port to the Mac mini** would add a tailnet link through
  `ts-share.sh <port>`. Nothing in v1 should rule that out: keep the port
  configurable and keep every machine-specific path in config.

## Config

The daemon reads only `~/.config/task-dashboard/config.yml`. The dashboard is
per-user, not per-repo, and a `launchd` agent has no repo working directory to
resolve a repo-relative path from. When `/task-dashboard install` runs inside a
repo, it may seed the overlapping `jira` keys (`site`, `blocked_statuses`) from
that repo's `dev_docs/tasks/.task-config.yml`. After that, the two files are
independent.

```yaml
jira:
  site: mycompany.atlassian.net
  projects: [PLAT, OPS]
  board_ids: [123] # sprint scope: these boards' active sprints
  blocked_statuses: [Blocked, On Hold]
github:
  enabled: true
sync:
  interval_minutes: 5
  reconcile_minutes: 60
  done_window_days: 14
server:
  port: 8765
score:
  weights: {
    priority: 3,
    deadline: 2,
    unblocks: 2,
    stuck: 1,
    review: 2,
    blocked: -10,
  }
```

## The launchd plist and the versioned plugin path

The plugin's install directory carries its version, so a plist that hard-codes
`…/workflow-skills/<version>/scripts/task-dashboard/…` would break on every
update. Point the plist at a small stable launcher instead, placed under
`~/.local/bin/`. The launcher resolves the current `installPath` from
`~/.claude/plugins/installed_plugins.json` at start, the same way the teardown
scripts are located. `/task-dashboard install` writes both the launcher and the
plist. `/doctor` checks that the plist is loaded and that the last sync is
recent.

## Verify before building

These are inferred, not tested. `acli` is not installed on the machine where
this was written.

1. `acli jira workitem search --jql … --json` returns `issuelinks`, `Flagged`,
   the sprint field, and parent. If the `--fields` flag can't request custom
   fields, the plan B is one `acli jira workitem view <KEY> --json` per changed
   key. That is fine for incremental runs and slow for the first one.
2. Pagination: whether `--paginate` (or an equivalent) returns every page in
   one call.
3. Time-in-status: how cheaply the changelog's last status transition can be
   read, per issue or in bulk. `statuscategorychangedate` gives only
   time-in-category (see **Fields**).
4. Auth under `launchd`: `acli jira auth login` stores a credential. It must be
   readable by a non-interactive agent with no TTY. If it lives in a login
   keychain that `launchd` agents can read, this works. If it needs a prompt,
   the daemon can't sync unattended.
5. `watcher = currentUser()` is valid JQL on the target site. Some sites
   restrict watcher search.
6. `acli` can list a board's active sprints. The sprint scope depends on it.

Items 1 and 4 can kill the design, so they are the first spike: a script on the
work laptop that runs the scope queries and dumps the JSON. For item 4, the
same script must also run once from a throwaway `launchd` agent, because an
interactive run has a TTY and a login session and so can't test unattended
auth. The same run answers item 5.

## Out of scope for v1

- Any write to Jira or GitHub.
- Notifications.
- Multi-user use, or any binding other than localhost.
- Linear and repo-pr sources. The adapter seam leaves room for them.
