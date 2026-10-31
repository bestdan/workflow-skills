---
created: 2026-10-30
status: proposed
---

# The task dashboard as a data source an agent can query

The dashboard already holds, in one local SQLite file, most of what an agent
needs to answer "help me prioritize my X tasks": tracker items, the PRs on
them with CI and review state, the sessions and worktrees working on them, the
workstreams they belong to, and the owner's own pins, snoozes and "why not
now" answers. An agent can't use most of it today, because the only read is
`view <tab> --json`, which returns a whole tab shaped for a page. This
proposes a query-shaped read: scopes, a compact item list, a per-item dossier,
and a one-call prioritization brief. It also proposes a place for the agent to
write its answer back so the page shows it. The dashboard still never calls a
model; the agent is always outside it.

"X" is any scope the mirror can name: a repo, a Jira project or epic, a GitHub
milestone, a Linear project, a label. FinPlan is the worked example below
only because it exposes the most gaps.

## What is true today

Measured on the mini's fleet instance, 2026-10-30.

**There is an agent interface, and it is page-shaped.**
`dashboard.py view <name> --json` prints a tab's lanes as the page shows them,
with no server needed (`agent_view.py`). `overlay` makes a card button's write
and `refresh` re-reads one GitHub item. That covers "what's on Stale?" and
`clean`. It doesn't cover a question about a slice of the work, for three
reasons:

- **No scope.** `view` filters by include group and Now mode only. To find
  one repo's work, an agent reads every tab and filters by `ref`.
- **The tabs are too big to read.** `view --json` output sizes:

  | View   | Bytes   | Roughly  |
  | ------ | ------- | -------- |
  | now    | 19,384  | 5k tok   |
  | prs    | 93,794  | 23k tok  |
  | needs  | 269,397 | 67k tok  |
  | stale  | 293,776 | 73k tok  |
  | issues | 438,460 | 110k tok |

  Now fits because it is capped at a list length. Everything else costs a
  large share of a context window before the agent has asked anything.
- **No per-item join.** The mirror knows which PR closes an issue, which
  session runs on that PR's branch, which worktree holds it and which
  workstream it belongs to (`workstream_members`, `needs.py`'s session-to-PR
  match). A `view` item carries only what its card shows, so an agent can't
  ask "everything you know about PRE-938".

**Some trackers are only half mirrored.** Linear is a grouping: the
Workstreams step reads Linear _projects_, and a PR carrying a Linear key joins
its project, but Linear _issues_ never become items. For FinPlan that is most
of the work. The mirror holds four Linear projects named for FinPlan, 49 open
`bestdan/finplan` PRs and 14 worktrees, and two GitHub issues. The backlog an
owner would prioritize lives in Linear team PRE and isn't there. repo-pr task
files aren't mirrored either. The shared-ranker design
([`2026-10-27-shared-now-ranker.md`](2026-10-27-shared-now-ranker.md)) names
both as future sources behind its seam.

**The tables are not a contract.** They are private to the sync and change
with each migration (`db.py`). Reading them directly works, but it breaks
silently on the next schema change. On this machine, the `sqlite3` CLI with
`-readonly` failed to open the WAL-mode DB (error 14) where Python's
`mode=ro` URI succeeded, so even a raw read has a trap in it.

## What "help me prioritize" needs

Walking the request through, the agent needs five things, in order:

1. **Resolve the scope.** "FinPlan" means the `bestdan/finplan` repo, the
   Linear projects whose names say FinPlan, and the sessions and worktrees
   under that repo. "My Jira tasks for the payments epic" means an epic and its
   children. The agent should be told what the word matched, so it can say so.
2. **A candidate set it can afford to read.** Open items in scope, one compact
   record each.
3. **Context per item that a tracker alone can't give.** Is there already a
   PR, and is it green, approved, co-reviewed? Is a session working on it, or
   waiting on the owner? Is a worktree sitting on it with unpushed commits?
   What does it block, and what blocks it? Has the owner snoozed it, and why?
   This is the dashboard's real advantage over asking Linear or Jira directly:
   the join across tracker, GitHub and the fleet is already done, locally and
   with no API calls.
4. **Freshness.** When each source last synced, so the agent can say "Linear
   is 3h stale" or `refresh` an item before acting on it.
5. **Somewhere to put the answer.** A ranking that lives only in the chat is
   gone by the next session, and the owner reads the dashboard from a phone.

## How the owner works, and what it implies

What the mirror and the existing designs show about the owner's work, and the
consequence for this design:

- **Lots of work in flight.** 49 open PRs and 14 worktrees on one repo is
  normal here, and the review queue has a cap of 5 that the header gauge
  shows. So prioritizing is mostly about _finishing_: which in-flight work to
  land, unblock or abandon. Picking the next backlog item comes second. The
  brief puts in-flight work first for that reason.
- **Agents do the work and the owner reviews it.** Sessions run on the mini
  and lindev, driven from a phone through Remote Control. The agent that
  prioritizes is usually a Claude session on the machine that holds the DB, so
  a local CLI reaches it. A claude.ai chat or a phone app with no shell is
  the case a CLI can't reach (see Later).
- **Two instances, never merged.** The work laptop's Jira instance and the
  mini's fleet instance share code, never data. The same commands work on
  both; a scope only names what that instance mirrors.
- **The tracker's own ladder is the vocabulary.** gh-issue's
  `status:*` and `prio:*` labels, `auto:human-review-needed`, milestones with
  `approved: yes`, Jira status categories and sprints. A brief should report
  these as they are, not translate them into a new scale.
- **Rankings are argued with, not trusted.** Now shows its score terms, Now
  modes exist because one ranking was wrong for the owner's session, and
  `tune` learns from "why not now". An agent's prioritization should be just
  as inspectable: a reason per item, shown on the card.

## Proposal

Five additions to `dashboard.py`, a skill procedure, and two sources, plus an
optional `sql` escape hatch (see Decisions). Each
read needs no server, reads the DB in `mode=ro`, and prints JSON with a
`schema` version, like `view` does.

### 1. `scopes [query]`: name what X can mean

Lists the scopes this instance can filter by, each with a stable id and a
count of open items:

| Scope kind | Id form                     | From                                      |
| ---------- | --------------------------- | ----------------------------------------- |
| repo       | `repo:bestdan/finplan`      | PR and issue keys                         |
| workstream | `ws:<id>`                   | `workstreams` (milestone, Linear, design) |
| jira       | `jira:PROJ`, `epic:PROJ-12` | issue keys, `parent_key`                  |
| label      | `label:prio:1`              | issue labels                              |
| host       | `host:lindev`               | sessions, worktrees                       |

With a query, it matches ids and titles case-insensitively and prints what
matched. `finplan` matches the repo and four Linear projects. Every other
command takes `--scope`, repeatable; a bare word goes through the same match,
and the output's `scope` field names what it resolved to. The agent reports
that back rather than guessing.

**Names miss most of the work, so a repo scope also reports `related`
workstreams.** On the mini's mirror, `bestdan/finplan` PRs belong to 14
Linear projects, and 10 of them have names that don't mention FinPlan:
Decumulation, Cashflow Modeling, Funding Sources Framework and others. A
match on the bare word `fin` is no better: it also hits a Linear project about
`find-candidates`. So a repo scope lists, under `related`, every workstream
with a member in that repo, with the member count. The agent shows that list
to the owner and adds the ones it confirms as explicit `ws:` ids. They are
never included automatically, because membership changes with each sync, and
a stored plan must keep meaning what the agent chose.

**`all` is reserved.** `--scope all` means no filter on every command, and is
never fuzzy-matched. Without the reservation, `all` would match a design doc
titled "Allow-listed `gh api` writes".

### 2. `items`: one compact record per item

```
dashboard.py items --scope finplan [--kind issue,pr,session,worktree]
    [--state todo,started,done] [--only blocked,waiting,stale]
    [--fields brief|full] [--limit N] [--offset N] [--jsonl]
```

`--state` filters the tracker's status category, the `state` field below.
`--only` filters on facts the dashboard derives, which hold independently of
status: `blocked` (a non-empty or non-zero `blocked_by`), `waiting` (a session on the item
is waiting on the owner, as Needs you decides it) and `stale` (the item is on
the Stale tab by its evidence rule). Keeping them on two flags is what lets an
agent ask for "started and blocked".

Every source's items, in one record shape. The `brief` record is the fields a
prioritizer reads, around 60 to 100 tokens each:

| Field                          | Meaning                                                         |
| ------------------------------ | --------------------------------------------------------------- |
| `source`, `key`, `ref`, `kind` | identity, as the overlay keys it                                |
| `title`, `url`                 |                                                                 |
| `state`                        | the tracker's status, verbatim, plus its category               |
| `prio`, `due`                  | the tracker's priority and due date, verbatim                   |
| `age_d`, `idle_d`              | days since created, and since last updated                      |
| `labels`                       |                                                                 |
| `workstreams`                  | ids of the workstreams it belongs to                            |
| `pr`                           | the linked PR's `{ref, ci, review, draft, co_reviewed}` or null |
| `activity`                     | live session or worktree on it: `{host, state, waiting_since}`  |
| `blocks`, `blocked_by`         | refs where the mirror holds links (Jira), else counts (GitHub)  |
| `overlay`                      | pinned, snoozed until, ignored, note, last why-not reason       |
| `now`                          | its Now tier and score terms, when Now ranks it                 |
| `size`                         | `{bucket, est_min, from}`, or null when unsized (see below)     |

**Size, in minutes.** A time-boxed request ("I've got half an hour") needs a
duration per item, and the mirror has only signals of size. `size` turns those
into a rough estimate and says where it came from:

- `bucket` is Little mode's size bucket
  ([`2026-10-26-now-modes.md`](2026-10-26-now-modes.md#little)): bot PR,
  docs-only PR, `est:1`, small PR (at most 3 files and 50 lines), `est:2`, and
  above those, `est:3`, `est:5`, `est:8`. Jira story points and Linear
  estimates map to the same `est:` ladder.
- `est_min` reads the bucket through a config table, `[size.minutes]`, with
  defaults such as bot PR 5, docs PR 10, small PR 15, `est:1` 20, `est:2` 45.
  These are the owner's numbers to tune, not a measurement. They ship as
  defaults because `--minutes` and the session-plan calibration (§5)
  both read `est_min`. With no numbers, the agent would convert `est:1` to
  minutes inside the model, differently each session and out of sight.
- `from` names the evidence, for example `label est:1` or
  `PR 2 files +14/-3`.

An unsized item has `size: null`. As in Little mode, an unknown size never
counts as small, and the agent says so rather than guessing one.

The signals already exist. On the mini's mirror (2026-10-30), 349 of 558
GitHub issues carry an `est:` label, and every open PR row carries
`{files, additions, deletions, paths}` under `raw_json.size`
(`github_fleet.py`), which Little's small-PR test reads
(`fleet_now.LITTLE_FILES`, `LITTLE_LINES`). Most open issues in the owner's
Linear team carry a 1, 2, 3 or 5-point estimate.

`full` adds the evidence, risks and action that `view` items carry. The
output always states `total` and `returned`, so a truncated list says so.

The fields come from the same code the views use: `candidate.py` builds the
Now terms, `needs.py` the session match, `workstreams.py` the membership.
`items` adds no ranking rule of its own.

### 3. `item <ref>`: the dossier

Everything the mirror holds about one item, joined: the tracker record, every
linked PR with its checks, the sessions on its branch with their first prompt
and last ask (when transcripts are collected), the worktrees and whether they
have unpushed commits, its workstreams, its links both ways (refs for Jira's
`links` table; for GitHub issues the mirror holds only counts of open blockers,
`github_fleet._blocks`), and the overlay's
full history for it, including every why-not answer. This is what an agent
reads before it says "close this" or "this one first".

The session text is the mirror's capped summary and nothing more: a first
prompt of at most 200 characters and a last ask of at most 400
(`snapshot.py`). `view --json` already prints both for every session item, so
the dossier prints them by default too. For more, the agent opens the session
itself; the CLI never grows a full-transcript read. The remote endpoint in
Later is where a default-off switch belongs, because that reader is a model
off the machine.

### 4. `brief`: one call for a prioritization

```
dashboard.py brief [--scope X ...] [--mode ship|little|deep|tidy] [--minutes N]
```

With no `--scope`, the brief covers every scope the instance has, which is
what "what should I do now" needs. A fixed-shape pack, budgeted to about 8k
tokens:

1. **Freshness**: each source's last sync, and each host's last snapshot.
2. **In flight**, first: open PRs by readiness (ready to merge, in the review
   queue, CI failing, draft), sessions waiting on the owner, worktrees with no
   PR. Each item in `brief` form.
3. **Blocked and blocking**: what is stuck, and on what.
4. **Backlog candidates**: open items not in flight, in Now's order with
   their terms. The order is a starting point to argue with, not the answer.
5. **The owner's signals**: pins, active snoozes, notes and recent why-not
   answers in scope.
6. **Gaps**: sources this instance doesn't mirror for the scope ("Linear
   issues: not mirrored"), so the agent knows what it can't see.

Past the budget, each section says how many items it left out and gives the
`items` command that lists them. The brief makes no judgment; it is a
deterministic projection of the mirror, testable like the views are.

**`--mode` uses Now's modes rather than a new vocabulary.** The modes already
answer the questions a time-boxed request asks: Little is "what can I knock
out fast", Deep is "which decision unblocks the most", Tidy is "what is
rotting". With `--mode`, brief section 4 is that mode's collector and order, exactly
as `view now --mode` would build it, but over the scope and not capped at the
page's list length. Only brief section 4 changes; the others stay, because
in-flight work, blockers, the owner's signals and the gaps matter in every
mode.

**`--minutes` budgets the candidates, it doesn't pick them.** The brief keeps
taking candidates in the mode's order until their `est_min` sum reaches about
twice the budget, so the agent has room to choose and swap. It then lists the
unsized candidates separately, since they could fit or not. Packing the box is
the agent's job, because only the agent can weigh "this one is 20 minutes but
it unblocks three PRs". Deep ignores the sum and keeps its WIP of one: an hour
of deep work goes to one item, and the brief gives that item's `item` dossier
(§3) inline, so the agent can open the session with what's needed to
resume it.

### 5. `plan`: where the answer goes

A new overlay table, `plans`, keyed by scope and kind (`standing` or
`session`), so both kinds can exist for one scope at once. The scope is a
resolved scope id, or `all` for a plan across every scope:

```
dashboard.py plan set [--scope <scope-id> | --scope all] [--minutes N] --file plan.json
dashboard.py plan show|clear [--scope …]
```

`plan.json` is an ordered list of `{source, key, action, reason, est_min}`.
`action` is one of `do`, `finish`, `unblock`, `review`, `defer`, `drop`. Each
`reason` is one line. A plan carries its `created_at`, the agent that wrote
it, the mode it was made in, and when it expires. There are two kinds:

- **A standing plan** for a scope or for `all`: "this is the order for
  FinPlan". It shows as stale after `[plan] stale_days` (default 7), and
  stays until it is replaced or cleared.
- **A session plan**, made with `--minutes`: "these three, in the next 30
  minutes". It expires at the end of the day, because by then it is either
  done or wrong. It shows the time box and the sum of its `est_min`.

The page shows the newest unexpired plan as a Plan mode on Now, with the
reason on each card and a ticked state for each item whose PR merged or whose
issue closed since the plan was made. A session plan wins over a standing
plan while it lasts. A plan is overlay, so `export` and `import` back it up
like pins and notes are.

**A tick needs a positive closed signal; leaving the mirror is not one.** The
GitHub issue mirror holds only open issues, and a row also disappears when a
repo is archived, an issue is transferred, or a search returns less. This is
the close-out checklist's rule too (`close_out.py`). The signals are:

- for the owner's PRs, a merged `closed_pr` row;
- for Jira, the done window's `resolution_date`;
- for everything else (GitHub issues and other people's PRs), the
  single-item `refresh`, which reports `merged`, `closed`, `archived` or
  `not_found`.

Ticks are drawn from mirror rows only. The plan's items are refreshed at each
sync while the plan is live and once at expiry, never on page load, so Plan
mode never calls GitHub to draw a page.

**A finished session plan is a calibration record.** When a session plan
expires, the dashboard records which items closed and when, by the signals
above. Comparing that
with their `est_min` is how `[size.minutes]` gets tuned, the same way `tune`
proposes weights: a suggestion with its evidence, applied only when the owner
approves it.

Rejected: writing the order into the existing `order` table and the reasons
into `notes`. `order` is one global hand-ordering, and a notes field the agent
overwrites would lose what the owner wrote there. A plan is a separate thing
the owner can take or leave.

**The owner's verdict feeds `tune`.** When the owner snoozes, drops or
reorders a planned item, that is a labelled disagreement with a stated reason,
which is what `tune` learns from. `whynot` records it with the plan's reason
alongside.

### 6. The procedure: `/task-dashboard prioritize [scope]`

A reference file, `skills/task-dashboard/references/prioritize.md`, that the
skill loads, as `clean` loads `clean.md`:

1. **Read the ask for scope, mode and time.** "My FinPlan tasks" is a scope.
   "Small tasks" or "bang out" is Little, "a hard design" or "think through" is
   Deep, "clean up" is Tidy, and "land" or "ship" is Ship. "Half an hour" is
   `--minutes 30`. With no scope, use `all`. Ask only when the ask names none
   of the three.
2. If a scope was named, `scopes <scope>`, and say what it matched.
3. `brief` with what step 1 found. If a source is stale, say so; offer
   `sync-now` or `refresh` on specific items, and don't refresh by default.
4. `item <ref>` on the items the plan would act on.
5. Propose the plan in chat, with a reason and an `est_min` per item, and the
   total against the time box.
6. On the owner's yes, `plan set`. Snoozes, ignores and pins go through
   `overlay`, one per item, each one approved.
7. For a session plan, offer to start the first item: the card's `action`
   names the command, such as resuming its session or opening its worktree.

#### Two worked asks

**"I've got half an hour, what small tasks should I bang out?"** Mode
Little, `--minutes 30`, scope `all`. The brief puts in-flight work first, and
the in-flight items that are also Little-sized come up as the best fits: a
green, approved bot PR to merge, a docs PR waiting on its review. Then come
`est:1` and `est:2` issues. The agent proposes, say, three items summing to
about 25 minutes, names the unsized ones it left out, and writes a session
plan. That evening the plan shows which of the three closed.

**"I've got an hour, let's finish up a hard design."** Mode Deep,
`--minutes 60`. Deep's candidates are proposed designs, issues at
`status:1_needs_refinement`, and issues that block others, ordered by what
they unblock. "Finish" points at designs already in flight: a design with an
open PR, a session that was working on it, a worktree holding it. So the
agent prefers those over a design no one has started. It picks one, reads
its dossier, and opens with what's needed to resume: the doc, the open review
comments on its PR, the last ask of the session that was writing it, and the
decision that's still open. The plan has one item, which is Deep's WIP of
one.

The procedure never writes to a tracker. Moving a ticket, closing an issue or merging a
PR goes through the tracker's own handler or command, which the card's
`action` already names.

### 7. Linear issues and repo-pr tasks as sources

Without this, "prioritize FinPlan" sees PRs and sessions, but not the
backlog. A Linear issues step writes `source = 'linear'` items, and a
candidate builder puts them behind the shared ranker's seam.

It mirrors every open issue (backlog, unstarted, started) of the teams that
the Workstreams step's projects belong to. `workstreams.py` already derives
that team set, and the `linear` handler's `linear-scan.py` query is
team-scoped in the same way, so neither needs new config. On the owner's key
that is one team, PRE, with about 200 open issues, of which about 20 have no
project. Project scope would drop exactly those unfiled issues, which a
prioritizer most needs to surface. Assignee scope would drop every unassigned
issue, and it answers "what's mine", not "what's in this backlog". If a key ever
sees other people's teams, a `[linear] teams` allowlist is the fix, added
then. repo-pr task files are a local read of
`dev_docs/tasks/` in the configured repos and are cheaper still. Both are
sources that ranking and every view gain, not only the agent, so each one
should be its own PR, after the shared Now ranker (issue #1225,
[design](2026-10-27-shared-now-ranker.md)) lands.

## Decisions

### A CLI and a skill first, not an MCP server

The agent that prioritizes is almost always a Claude Code session on the
machine that holds the DB, and the CLI already exists there with the right
safety properties: read-only by default, no server needed, and the same
validation as the page for each write. A CLI costs no context until it is
called, and Codex can run it too. An MCP server would put every tool's schema
in every session's context and would need its own auth story, so it's deferred
to Later rather than rejected.

### Query commands, not raw SQL

Rejected as the primary interface: give the agent the schema and let it
write SQL. It is the most flexible option, and it pushes every join rule onto
each agent. Those rules (which PR belongs to which ticket, when a session
counts as ended, what puts a PR in the review queue) are where the
dashboard's value is, and they live in Python. An agent writing its own SQL
would re-derive them and get them subtly wrong, and break on the next
migration.

Kept as an escape hatch: `dashboard.py sql "<select>"`, which opens the DB
`mode=ro` with `PRAGMA query_only`, against a small set of documented
`agent_*` views (`agent_items`, `agent_links`) that are the only stable
names. The tables stay private, and that is a security property, not only a
stability one: `meta` holds the page's server token in plain text, so `sql`
refuses any query that names something other than an `agent_*` view.

### The brief is deterministic; the judgment is the agent's

The dashboard's rule is that nothing calls a model at runtime, and every
ranking is a visible sum. `brief` keeps that: it selects and orders by the
same code Now uses, and labels the order as Now's. The agent supplies the
judgment, and the plan records it with a reason per item. That way the owner
can always tell which order came from the weights and which from an agent.

### Scope resolution is fuzzy only at the edge

A bare word goes through `scopes` matching, and the output names the ids it
used. Everything stored (a plan, an include filter) keys on resolved ids or
the literal `all`, so a
renamed Linear project or a new repo that happens to match "fin" never
changes what a saved plan means.

## Later

- **Read access from outside the machine.** A claude.ai chat or the phone
  app can't run the CLI. The fleet server is already on the tailnet through
  Tailscale Serve; a read-only MCP endpoint on it, exposing `scopes`, `items`,
  `item` and `brief` as tools, would reach those clients. It needs a token
  model like ingest's per-machine bearer tokens, and it is the first
  dashboard surface that would hand item text to a remote model, so it gets
  its own design.
- **Both instances in one question.** "Prioritize everything" across the work
  laptop and the mini means two DBs. Out of scope: the instances share no
  data on purpose.
- **Scheduled briefs.** A morning `brief` per active workstream, sent to the
  phone, is one `/schedule` routine once the commands exist.

## Rollout

Each slice is one PR and is useful without the next:

1. `items` and `scopes`, over the sources that exist today, with `size` and
   `[size.minutes]`.
2. `item <ref>`.
3. `brief` with `--mode` and `--minutes`, and `references/prioritize.md`
   without steps 6 and 7 (the plan steps), which slice 4 adds.
4. `plans` table (standing and session plans), `plan` command, Plan mode on
   Now, and the `tune` join.
5. The session-plan calibration of `[size.minutes]`.
6. Linear issues as a source (after the shared Now ranker, issue #1225).
7. repo-pr tasks as a source.
8. `sql` and the `agent_*` views, if an agent turns out to need them.

## Open questions

None blocking. The `[size.minutes]` defaults are guesses by design, and the
owner can overwrite them at any time; calibration proposes changes once
session plans exist.
