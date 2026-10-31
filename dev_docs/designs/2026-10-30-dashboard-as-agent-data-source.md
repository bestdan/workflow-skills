---
created: 2026-10-30
status: proposed
tracks: https://github.com/bestdan/workflow-skills/issues/1281
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

**Updated 2026-10-31.** Slice 1 (`scopes` and `items`) shipped in #1266, and
Linear issues as a Now source shipped in #1264. Both differ from what this
document first proposed. Where a section covers something that shipped (§1,
§2 and §7), it now describes what shipped, and each change carries a dated
note giving what the proposal said and why it moved. The rest is still the
proposal; the note in §5 records the owner's decisions for the plans slice.
#1281 tracks the re-cut rollout (see Rollout).

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
both as future sources behind its seam. (Since then, #1264 mirrors the
owner's assigned Linear issues; §7 has what it covers.)

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
  as inspectable: a reason per item, shown on the card. And the owner's
  changes to it should be a learning loop, not a correction that's lost:
  each one is a labelled disagreement the next plan should learn from (§5).

## Proposal

Five additions to `dashboard.py`, a skill procedure, and two sources, plus an
optional `sql` escape hatch (see Decisions). Each
read needs no server, reads the DB in `mode=ro`, and prints JSON with a
`schema` version, like `view` does.

**Every read lets the agent ask for less.** An agent that knows what it wants
should get only that, because every unneeded field is context spent. So each
read takes flags that narrow its output: `--fields` on `items`, `--sections` on
`item` and `brief`, and `--limit` wherever there is a list. The defaults suit
an agent that doesn't yet know what it needs.

### 1. `scopes [query]`: name what X can mean

Lists the scopes this instance can filter by, each with a stable id and a
count of open items:

| Scope kind | Id form                                 | From                                                  |
| ---------- | --------------------------------------- | ----------------------------------------------------- |
| repo       | `repo:bestdan/finplan`, `repo:dotfiles` | PR, issue and design repos; worktree and session dirs |
| workstream | `ws:<id>`                               | `workstreams` (milestone, Linear, design)             |
| jira       | `jira:PROJ`                             | Jira ticket keys                                      |
| epic       | `epic:PROJ-12`                          | Jira tickets' `parent_key`                            |
| label      | `label:prio:1`                          | issue and ticket labels                               |
| host       | `host:lindev`                           | sessions, worktrees                                   |

So the id prefixes are `repo:`, `ws:`, `jira:`, `epic:`, `label:` and
`host:`. A repo that only a worktree or session names gets a bare id
(`repo:dotfiles`), because a checkout's path names no owner. **`repo:<bare>`
matches by bare name**: typed with no owner, it names every
`repo:<owner>/<bare>` too. A session or worktree in a `dotfiles` checkout is
a member of `repo:bestdan/dotfiles` and of every other owner's `dotfiles`
scope, and counts in each one's `open`. `repo:dotfiles` is listed as a scope
of its own only when no `<owner>/dotfiles` repo is mirrored.

> **2026-10-31, as shipped (#1266).** The proposal folded `epic:` into the
> `jira` row and gave repos only an `owner/name` id. Shipped, `epic` is a
> scope kind of its own, and repos also have the bare-name id and match,
> because worktree and session paths carry only a directory name. A Linear
> team scope is #1283's.

With a query, it matches ids and titles case-insensitively and prints what
matched. `finplan` matches the repo and four Linear projects. Every other
command takes `--scope`, repeatable; a bare word goes through the same match,
and the output's `scope` field names what it resolved to. The agent reports
that back rather than guessing.

The output shape, for `scopes finplan` (ids, titles and counts illustrative):

```json
{
  "schema": 1,
  "query": "finplan",
  "matched": [
    {
      "id": "repo:bestdan/finplan",
      "kind": "repo",
      "title": "bestdan/finplan",
      "open": 51
    },
    {
      "id": "ws:lin-3f2a",
      "kind": "workstream",
      "title": "FinPlan Tax Engine",
      "open": 9
    }
  ],
  "related": [
    {
      "id": "ws:lin-77c1",
      "kind": "workstream",
      "title": "Decumulation",
      "members_in_scope": 6
    }
  ]
}
```

`open` counts open items of every kind. `related` is printed for any query
other than `all`, and is empty unless a repo scope matched, as the next
paragraph explains. With no query, or `all`, `matched` lists every scope and
`related` is absent.

**Names miss most of the work, so a repo scope also reports `related`
workstreams.** On the mini's mirror, `bestdan/finplan` PRs belong to 14
Linear projects, and 10 of them have names that don't mention FinPlan:
Decumulation, Cashflow Modeling, Funding Sources Framework and others. A
match on the bare word `fin` is no better: it also hits a Linear project about
`find-candidates`. So a repo scope lists, under `related`, every workstream
with an open member in that repo, other than those already matched, with the
count of those open members, most first. The agent shows that list to the owner and adds the ones it confirms
as explicit `ws:` ids. They are never included automatically, because
membership changes with each sync, and a stored plan must keep meaning what
the agent chose.

> **2026-10-31, as shipped (#1266).** `related` counts only open members, the
> same way a scope's `open` does, and leaves out a workstream whose members
> in that repo are all done. The proposal counted every member. The proposal
> also had `related` appear only for a repo scope; shipped, any query other
> than `all` prints it, empty when no repo scope matched.

**`all` is reserved.** `--scope all` means no filter on every command, and is
never fuzzy-matched. Without the reservation, `all` would match a design doc
titled "Allow-listed `gh api` writes".

### 2. `items`: one compact record per item

```
dashboard.py items --scope finplan
    [--kind issue,pr,ticket,design,session,worktree]
    [--state todo,started,done] [--only blocked,waiting,stale]
    [--fields brief|full|<name,...>] [--limit N] [--offset N] [--jsonl]
```

`--kind` takes the dashboard's own word for each item: `issue` (a mirrored
GitHub issue), `pr` (an open PR), `ticket` (a Jira issue; Linear tickets join it with #1283), `design` (a design
doc at `status: proposed`), `session` (a live Claude Code session) and
`worktree` (a worktree a machine snapshot reported).

> **2026-10-31, as shipped (#1266).** The proposal listed four kinds:
> `issue`, `pr`, `session` and `worktree`. Shipped, Jira tickets are `ticket`
> and design docs are `design`, the words the rest of the dashboard already
> uses.

`--state` filters the tracker's status category, the `state` field below.
`--only` filters on facts the dashboard derives, which hold independently of
status: `blocked` (blocked as Now judges it: for a GitHub issue the `blocked`
label or an open blocker, for a Jira ticket a label, the flag, a blocked
status or an open blocking link; other kinds are never blocked), `waiting` (a session on the item
is waiting on the owner, as Needs you decides it) and `stale` (the item is on
the Stale tab by its evidence rule). Keeping them on two flags is what lets an
agent ask for "started and blocked".

Every source's items, in one record shape. The `brief` record is the fields a
prioritizer reads:

| Field                          | Meaning                                                                                    |
| ------------------------------ | ------------------------------------------------------------------------------------------ |
| `source`, `key`, `ref`, `kind` | identity, as the overlay keys it                                                           |
| `title`, `url`                 |                                                                                            |
| `state`                        | the tracker's status, verbatim, plus its category                                          |
| `prio`, `due`                  | the tracker's priority and due date, verbatim                                              |
| `age_d`, `idle_d`              | days since created, and since last updated                                                 |
| `labels`                       |                                                                                            |
| `workstreams`                  | ids of the workstreams it belongs to                                                       |
| `pr`                           | the linked PR's `{ref, ci, review, draft, co_reviewed}`; absent when there is none         |
| `activity`                     | a list of the live sessions and worktrees on it: `{kind, host, state, waiting_since}` each |
| `blocks`, `blocked_by`         | refs where the mirror holds links (Jira), else counts (GitHub)                             |
| `overlay`                      | pinned, snoozed until, ignored, note, last why-not reason                                  |
| `now`                          | its Now tier and score terms, when Now ranks it                                            |
| `size`                         | `{bucket, est_min, from}`; absent when unsized (see below)                                 |

**Brief and full records are sparse.** They leave out every non-identity
field that is null or empty, and a `url` that only repeats the `key`, and the
output's header says `"sparse": true`. Only top-level fields are dropped, so
a nested null such as `size.est_min` stays. A named `--fields` list prints
every field it names, null or not, so an unsized item shows `size: null`
there. Measured on the mini's mirror, a brief record averages
about 550 bytes, around 140 tokens. Most of that is the `now` terms and the
labels, and `--fields` is how an agent trims it.

> **2026-10-31, as shipped (#1266).** The proposal made `activity` one
> object, `{host, state, waiting_since}`. Shipped, it is a list, each entry
> carrying its `kind`, because one branch can hold a session and a worktree
> at once. The proposal estimated 60 to 100 tokens a brief record, and did not
> say records were sparse; sparse records still measured about 140.

**Size, in minutes.** A time-boxed request ("I've got half an hour") needs a
duration per item, and the mirror has only signals of size. `size` turns those
into a rough estimate and says where it came from:

- `bucket` is one of `bot_pr`, `docs_pr`, `small_pr`, `large_pr` or `est:N`.
  The first three are Little mode's PR buckets
  ([`2026-10-26-now-modes.md`](2026-10-26-now-modes.md#little)), a small PR
  being at most 3 files and 50 lines; `large_pr` is a sized PR that is none
  of them. `est:N` reads an issue's or ticket's `est:` label. One function,
  `fleet_now.pr_bucket`, sorts PRs for both `items` and Little, so the two
  cannot drift apart.
- `est_min` reads the bucket through a config table, `[size.minutes]`. Five
  buckets have defaults: `bot_pr` 5, `docs_pr` 10, `small_pr` 15, `est:1` 20
  and `est:2` 45. A bucket with no entry, `large_pr` or `est:3` and up by
  default, has `est_min: null`. These are the owner's numbers to tune, not a
  measurement. They ship as defaults because `--minutes` and the session-plan
  calibration (§5) both read `est_min`. With no numbers, the agent would
  convert `est:1` to minutes inside the model, differently each session and
  out of sight.
- `from` names the evidence, for example `label est:1`,
  `PR, 2 files, +14/−3` or `bot PR from dependabot[bot]`.

An unsized item has no `size` (null under a named `--fields` list). A bot PR
is `bot_pr` whatever its size data says. As in Little mode, an unknown size never
counts as small, and the agent says so rather than guessing one.

> **2026-10-31, as shipped (#1266).** The proposal described the buckets in
> words, as one ladder with `est:1` between the docs and small PR buckets and
> `est:2` after small PR. Shipped, the
> buckets have the names above, `large_pr` is added so a big PR is sized
> rather than null, and only the five listed have default minutes. Size reads
> `est:` labels only: Jira story points are not mapped onto the ladder, and
> Linear estimates wait on Linear tickets reaching `items` (#1283).

The signals already exist. On the mini's mirror (2026-10-30), 349 of 558
GitHub issues carry an `est:` label, and every open PR row carries
`{files, additions, deletions, paths}` under `raw_json.size`
(`github_fleet.py`), which Little's small-PR test reads
(`fleet_now.LITTLE_FILES`, `LITTLE_LINES`). Most open issues in the owner's
Linear team carry a 1, 2, 3 or 5-point estimate.

`full` adds `repo`, `branch`, `host`, `path`, `parent`, `created` and
`updated`, and the evidence, risks and action that `view` items carry. A comma
list names exact fields (`--fields key,title,size,pr`), and identity is always
included. The output always states `total` and `returned`, so a truncated list
says so.

The fields come from the same code the views use: `fleet_now.candidates`
builds the Now terms, `needs.py` the session match, `workstreams.py` the
membership. `items` adds no ranking rule of its own; it lists newest
`updated` first.

> **2026-10-31, as shipped (#1266).** The proposal said `full` adds only the
> evidence, risks and action; shipped, it also adds the location and time
> fields listed above. The proposal named no order for `items`; shipped, it is
> newest `updated` first, which is recency, not priority.

### 3. `item <ref>`: the dossier

Everything the mirror holds about one item, joined: the tracker record, every
linked PR with its checks, the sessions on its branch with their first prompt
and last ask (when transcripts are collected), the worktrees and whether they
have unpushed commits, its workstreams, its links both ways (refs for Jira's
`links` table; for GitHub issues the mirror holds only counts of open blockers,
`github_fleet._blocks`), and the overlay's
full history for it, including every why-not answer. This is what an agent
reads before it says "close this" or "this one first".

`--sections tracker,prs,sessions,worktrees,links,overlay` prints only the
parts named, so "is the PR on PRE-938 green?" costs one section, not the
whole dossier.

The session text is the mirror's capped summary and nothing more: a first
prompt of at most 200 characters and a last ask of at most 400
(`snapshot.py`). `view --json` already prints both for every session item, so
the dossier prints them by default too. For more, the agent opens the session
itself; the CLI never grows a full-transcript read. The remote endpoint in
Later is where a default-off switch belongs, because that reader is a model
off the machine.

> **2026-10-31, as shipped (#1284).** The argument is `source:key`, which is
> unique; a bare key or ref is accepted when it names one item, and otherwise
> the command exits 2 listing every match. The sections are
> `tracker,prs,sessions,worktrees,workstreams,links,overlay`: `workstreams`,
> which the prose above names, is a section of its own. `tracker` is the
> `full` record plus `detail`, what the mirrored row holds beyond it. `links`
> says which form it is in, `refs` or, for a GitHub issue, `counts`. The
> output carries `schema`, as `items` does. `brief` takes `--sections` the same
> way, a comma list of section names, not numbers.

### 4. `brief`: one call for a prioritization

```
dashboard.py brief [--scope X ...] [--mode ship|little|deep|tidy] [--minutes N]
    [--sections <name,...>]
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

> **2026-10-31, as shipped (#1285).** The sections are named `freshness`,
> `in_flight`, `blocked`, `candidates`, `signals` and `gaps`, for
> `--sections`. The budget is 8,000 tokens measured as the printed JSON's
> bytes over 4, the measure of the table in "What is true today". Freshness,
> gaps and the Deep dossier always print; the lists fill in two passes, up to
> three items each and then the rest, in a fixed order that cuts the unsized
> candidates first, then draft and other PRs, blocking and blocked items,
> worktrees with no PR, the mode's candidates, the owner's signals, and last
> the PRs and sessions the owner can act on now (ready to merge, in the
> queue, CI failing, waiting). PR readiness is the first that holds of draft, CI
> failing, ready to merge (CI green and approved, or a bot's PR), in the
> review queue, else `other`. `candidates` is the mode's collector and order
> unmodified, so an item the in-flight section already printed appears there
> only as a stub. `--minutes` keeps each sized candidate, in order, that
> holds the sum within twice the box, rather than stopping at the first that
> would not. `est:3` and up have no default minutes on purpose, so they are
> listed as unsized. `gaps` is per instance, not per scope. The "waiting"
> and "stale" that #1285 listed are `in_flight.waiting` and `--mode tidy`.

### 5. `plan`: where the answer goes

A new overlay table, `plans`, keyed by scope and kind (`standing` or
`session`), so both kinds can exist for one scope at once. **A plan belongs
to exactly one scope**: one resolved scope id, or `all` for a plan across
every scope. `plan set` takes a single `--scope`, unlike the reads, where
`--scope` repeats:

```
dashboard.py plan set (--scope <scope-id> | --scope all) [--minutes N]
    --by <author> --file plan.json
dashboard.py plan show|clear [--scope …]
```

`plan.json` is an ordered list of `{source, key, action, reason, est_min}`.
`action` is one of `do`, `finish`, `unblock`, `review`, `defer`, `drop`. Each
`reason` is one line. A plan carries its `created_at`, its author, the mode
it was made in, and when it expires. The author is whatever `--by` says, free
text: a session name, an agent, or the owner. There are two kinds:

- **A standing plan** for a scope or for `all`: "this is the order for
  FinPlan". It shows as stale after `[plan] stale_days` (default 7), and
  stays until it is replaced or cleared.
- **A session plan**, made with `--minutes`: "these three, in the next 30
  minutes". It expires at the next 04:00, host local time, because by then it
  is either done or wrong. A plan set between midnight and 04:00 expires at
  04:00 that morning. It shows the time box and the sum of its `est_min`.

> **2026-10-31, the owner's decisions for #1286.** The proposal let the scope
> be "a resolved scope id", which left open whether one plan could span
> several; it spans exactly one, or `all`. It said a session plan expires "at
> the end of the day"; that is now the next 04:00, host local time. It said a
> plan records "the agent that wrote it" without saying how; that is the
> free-text `--by` flag.

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

> **2026-10-31, as built in #1287.** Plan mode also ticks a GitHub issue that
> a merged `closed_pr` row names, CLOSED, in its `closes`: the evidence
> `close_out.py` already ticks "task closed" from. An entry with no closed
> signal that has left the mirror (and a PR closed without merging) is
> `gone`: it keeps its place in the plan, muted and flagged `gone`, titled by
> its closed PR's title or else its ref, never ticked. Pins lead, and
> snoozes, ignores and the include filter apply, as in every Now mode; with
> no plan live, a pin still shows and the no-plan line moves above the list.
> The whole plan is shown, with no list limit.

Ticks are drawn from mirror rows only. The plan's items are refreshed at each
sync while the plan is live and once at expiry, never on page load, so Plan
mode never calls GitHub to draw a page.

**A finished session plan is a calibration record.** When a session plan
expires, the dashboard records which items closed and when, by the signals
above. Comparing that
with their `est_min` is how `[size.minutes]` gets tuned, the same way `tune`
proposes weights: a suggestion with its evidence, applied only when the owner
approves it.

**Estimating duration is a development area, not a solved input.** Agents are
poor at saying how long a task will take, and a bucket-to-minutes table is
only a starting point. The calibration above is the cheap first step. Two
richer measurements are available from data the mirror already holds, and
each gets its own design once session plans have produced a few weeks of
records to test against:

- **Actual time per item.** Session snapshots carry start and last-activity
  times, and a session matches an item through its branch. Summing active
  session time per item gives a measured duration to set against `est_min`,
  per bucket and per repo.
- **Better predictors than the bucket.** With measured durations, the
  question becomes which signals predict them: PR size, labels, repo, the
  kind of change, whether a design exists. The answer stays a visible
  formula the owner can read, like Now's weights, not a model call.

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

#### Other asks the procedure should recognize

Each maps onto the same commands; none needs a new one.

| Ask                                          | Mode and flags                  | What the agent reads first                                |
| -------------------------------------------- | ------------------------------- | --------------------------------------------------------- |
| "What can I merge right now?"                | Ship                            | brief section 2, PRs ready to merge                       |
| "What's waiting on me?" / "before I log off" | Ship, `items --only waiting`    | sessions waiting on the owner, PRs in the review queue    |
| "What's blocking FinPlan?"                   | Ship, `--scope`                 | brief section 3, then `item` on the top blockers          |
| "Clean up" / "what's rotting?"               | Tidy                            | brief section 4 under Tidy; hands off to `clean`          |
| "Plan my week on X"                          | Ship, `--scope`, no `--minutes` | the whole brief; writes a standing plan                   |
| "Where was I on X?"                          | none, `--scope`                 | brief section 2, then `item` on the newest active session |
| "What's next after this?" (mid-session)      | the session plan's mode         | the live session plan; its next unticked item             |

The procedure never writes to a tracker. Moving a ticket, closing an issue or merging a
PR goes through the tracker's own handler or command, which the card's
`action` already names.

### 7. Linear issues and repo-pr tasks as sources

Without this, "prioritize FinPlan" sees PRs and sessions, but not the
backlog. A Linear issues step writes `source = 'linear'` items, and a
candidate builder puts them behind the shared ranker's seam.

**What shipped (#1264) is assigned-only.** `linear_issues.py` mirrors the
open issues assigned to `linear.me` (else the key's own user) whose state
type is `unstarted` or `started`, as `source = 'linear'`, `kind = 'ticket'`
rows that fill the Jira columns, and Now ranks them. `items` and `scopes` do
not read those rows yet; #1283 adds them, with a Linear team scope.

What the agent read needs is wider, and #1282 widens the mirror to it: every
open issue (backlog, unstarted, started) of the teams that the Workstreams
step's projects belong to, assigned or not. `workstreams.py` already derives
that team set, and the `linear` handler's `linear-scan.py` query is
team-scoped in the same way, so neither needs new config. On the owner's key
that is one team, PRE, with about 200 open issues, of which about 20 have no
project. Project scope would drop exactly those unfiled issues, which a
prioritizer most needs to surface. Assignee scope would drop every unassigned
issue, and it answers "what's mine", not "what's in this backlog". If a key
ever sees other people's teams, a `[linear] teams` allowlist is the fix,
added then. **Now's ranking stays assigned-only**: the wider rows are for the
agent queries and the Workstreams tab, and Now still ranks only the owner's
assigned `unstarted` and `started` issues.

> **2026-10-31, as shipped (#1264).** The proposal had one Linear source
> serving both Now and the agent read. Linear ingestion was built separately
> and shipped as Now's source, scoped to the owner's assigned issues. The
> backlog read (#1282) widens the mirror without changing what Now ranks.

> **2026-10-31, as shipped (#1282).** The backlog read leaves out `triage`:
> it is Linear's intake queue, issues nobody has accepted yet, so not yet
> backlog. The assigned read still runs beside it, so that the owner's issues
> in a team with no open project stay on Now. Rows the assigned read returned
> carry `scope = 'assigned'` and the rest `scope = 'team'`, and Now ranks only
> the first.

repo-pr task files are a local read of `dev_docs/tasks/` in the configured
repos and are cheaper still. That source is #1289, on hold until it is
refined.

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

Fixed commands are also bounded. Each one is a known query with known
indexes, so it can be tested, timed and optimized like the views are. An
agent's ad hoc SQL can scan every table or join without limit, and nothing
catches that until a call is slow.

Kept as an escape hatch: `dashboard.py sql "<select>"`, which opens the DB
`mode=ro` with `PRAGMA query_only`, against a small set of documented
`agent_*` views (`agent_items`, `agent_links`) that are the only stable
names. The tables stay private, and that is a security property, not only a
stability one: `meta` holds the page's server token in plain text, so `sql`
refuses any query that names something other than an `agent_*` view. It also
caps rows returned and run time, for the reason above.

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
- **An agent chat inside the dashboard.** A pane on the page where the owner
  works with an agent over the cards on screen. It keeps the rule that the
  dashboard never calls a model if the pane starts or attaches to a Claude
  session on the host, as Remote Control does, and that session uses these
  same commands. The scope could be large (auth, streaming, a session per
  page), so it gets its own design once the CLI exists.
- **Scheduled briefs.** A morning `brief` per active workstream, sent to the
  phone, is one `/schedule` routine once the commands exist.

## Rollout

#1281 tracks the rollout. Each sub-issue is one PR and is useful without the
next; only real dependencies are linked.

| Slice as proposed                                 | Where it went                                                                                                                                  |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. `items` and `scopes`, `size`, `[size.minutes]` | shipped, #1266                                                                                                                                 |
| 2. `item <ref>`                                   | #1284                                                                                                                                          |
| 3. `brief` and `references/prioritize.md`         | #1285, after #1284                                                                                                                             |
| 4. plans, Plan mode, the `tune` join              | split in three: #1286 (`plans` table and `plan set\|show\|clear`), #1287 (Plan mode on Now), #1288 (ticks, and the plan's reason in `why_not`) |
| 5. session-plan calibration of `[size.minutes]`   | deferred until the plan slices have produced a few weeks of records; not filed                                                                 |
| 6. Linear issues as a source                      | shipped assigned-only as #1264; #1282 mirrors the whole open backlog, #1283 lists Linear tickets in `items` and `scopes`                       |
| 7. repo-pr tasks as a source                      | #1289, on hold until refined                                                                                                                   |
| 8. `sql` and the `agent_*` views                  | not filed: it waits on an agent that needs it                                                                                                  |

> **2026-10-31, re-cut (#1281).** The proposal's eight slices, in order,
> became the issues above once #1264 and #1266 had shipped: slice 4 was over
> size 5 and split, slice 6 was split into the backlog read and the agent
> read, slice 5 was deferred and slice 8 was left unfiled.

## Open questions

None blocking. The `[size.minutes]` defaults are guesses by design, and the
owner can overwrite them at any time; calibration proposes changes once
session plans exist.
