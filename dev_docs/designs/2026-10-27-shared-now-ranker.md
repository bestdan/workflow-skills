---
created: 2026-10-27
status: proposed
tracks: https://github.com/bestdan/workflow-skills/issues/1225
---

# One Now ranker for every task-dashboard instance

Now ranks a different set of things on each instance, by a different score,
and which one you get depends on whether the instance is a "fleet". On the
work laptop, Now lists Jira tickets only, so a PR that carries no ticket key
reaches no ranking at all. This proposes one ranker that every instance runs
over every source it has, with the same tiers and the same weights. Being a
fleet then means one thing only: the instance collects from more than one
machine. Sources plug into the ranker through a seam, so a Linear or repo-pr
source can join later without another ranker.

## What is true today

**Two rankers, chosen by the fleet switch.** `agent_view._now` and the server
both branch on `fleet_views.enabled(cfg)`:

- **Jira Now** (`views.now_items`) ranks the owner's open Jira tickets by
  `score.score`'s six terms (priority, deadline, unblocks, stuck, review,
  blocked) under `[score.weights]`. PRs reach it only through the `review`
  term, which raises a ticket when a PR linked to it by key is approved or
  green.
- **Fleet Now** (`fleet_now.py`) ranks sessions, PRs, issues and close-outs
  into Ship's fixed tiers (asked, close out, ready to merge, review queue,
  started, the rest), ordered within a tier by `score.fleet_score` under
  `[score.fleet]`. Jira tickets aren't in it. With Jira on as well, they get
  a separate lane below the list, ranked by the Jira score.

**What that costs on the work laptop, measured 2026-10-27.** The `prs` table
held 65 PRs authored by the owner and 27 asking for the owner's review. Only
18 and 6 of them carry a ticket key the mirror recognises. The other 68 reach
no ranking. They show only on Waiting on me and I'm waiting on, which are
unranked lists.

**"Fleet" is two switches merged into one.** `fleet_views.enabled` is true
when either holds:

- `github.fleet` is on (`sync.fleet_enabled`). That turns on
  `github_fleet.py`'s read: every open issue of the configured owners, the
  review queue, PRs others opened on the owners' repos, design docs, and
  recently closed PRs. This is a decision about how much to read from GitHub.
  It has nothing to do with machines.
- `ingest.machines` is non-empty. That is collecting from other hosts, the
  only part that is about more than one machine.

**The wider GitHub read assumes a personal account.** It mirrors up to
`MAX_OPEN_ISSUES` (900) open issues per owner, and every PR someone else opens
on the owners' repos. That is the right scope for the owner's own repos. For
the `Betterment` org it would mirror other teams' backlogs and every
colleague's PR.

**Linear is a grouping, not a source.** With `[linear] enabled = true`, the
Workstreams tab reads Linear _projects_, and a PR or issue carrying a Linear
key joins its project (`workstreams.py`). Linear _issues_ never become items,
so no ranking sees them.

**Now modes already assume a shared ranker.** The Now modes design
([`2026-10-26-now-modes.md`](2026-10-26-now-modes.md), #1218) says the modes
"apply to every instance (work or personal)", but on a Jira instance Now
doesn't run the code the modes change. This design is what that claim needs.

## Proposal

**One Now on every instance.** Every instance builds its Now the same way:

1. **Sources** each supply candidates (the seam below).
2. **Linked candidates fold** into the item they belong to.
3. **The ranker** puts each item in a tier and orders within the tier by its
   score, under one set of weights.
4. **The overlay** (pins, snoozes, ignores, the include filter, "why not
   now") applies as it does today.

Ship's tiers are unchanged apart from a final "blocked" tier (see Decisions),
and the Now modes design keeps them as its
default mode. The work laptop's Now becomes one list: tickets, unlinked PRs,
review requests and (once a local collector runs) its own sessions, in one
order. The separate Jira lane goes away.

**The source seam.** A source turns its rows into candidates. A candidate
carries:

| Field          | Meaning                                                                                        |
| -------------- | ---------------------------------------------------------------------------------------------- |
| `(source,key)` | the overlay key, unchanged                                                                     |
| `kind`         | for the include filter (`include.KIND_GROUPS`)                                                 |
| `category`     | one of `score.FLEET_CATEGORIES`; this puts it in a tier                                        |
| `terms`        | values for the score terms the source can supply. A term the source has no data for is omitted |
| `blocked`      | whether the item is blocked (see Decisions); the ranker reads it to apply the blocked rule     |
| `features`     | what `tune` and "why not now" record, as today                                                 |
| `links`        | keys of items it folds into (see Decisions)                                                    |
| `why`          | its evidence line                                                                              |

The sync-side seam
([`../task-dashboard.md`](../task-dashboard.md#sync-steps), "Sync steps")
stays. This is the ranking-side half that seam never had.

Each source supplies what its data supports:

| Source          | Categories it puts items in                                                          | Terms it supplies                   |
| --------------- | ------------------------------------------------------------------------------------ | ----------------------------------- |
| Jira tickets    | `started` (in-progress category), `ready` (to-do, in sprint)                         | priority, deadline, unblocks, stuck |
| GitHub PRs      | `quick_win`, `own_approved`, `review_queue`, `others_pr`, `own_ci_failing`, `own_pr` | age                                 |
| GitHub issues   | `started`, `ready`, `human_issue`, by the gh-issue ladder                            | priority (`prio:`), age             |
| Sessions        | `asked`, `close_out`                                                                 | age                                 |
| Workstreams     | `close_out` (a complete milestone)                                                   | age                                 |
| Linear, repo-pr | future: none today                                                                   |                                     |

The plugin's tracker handlers are the list of likely sources: `jira` and
`gh-issue` are covered by the Jira and GitHub-issue rows; `linear` (issues,
not projects) and `repo-pr` (task files under `dev_docs/tasks/`) aren't. This
design builds neither. It only makes sure each could be one more row in that
table.

**Being a fleet means collecting from many machines.** `fleet_views.enabled`
narrows to "this instance ingests from other hosts". Collecting sessions,
worktrees and handoffs works the same way with one host as with three, so a
single machine can run its collector against itself and get sessions on Now.
Whether the work laptop does that is its own config choice, not something
being a fleet decides.

## Decisions

### Ranking doesn't depend on being a fleet

Every instance runs the same ranker. The rejected alternative is today's:
choose the ranker by instance type. That made the work laptop's Now blind to
68 PRs, and it means every ranking change (modes, `tune`, new categories) has
to land twice or reaches only one instance. The cost is that a Jira ticket and
a GitHub PR now compete in one order, so their weights have to be
comparable. Tiers settle that: items are first sorted by tier, and the
weights only break ties within a tier, so a weight that is off between
sources can't move an item out of its tier.

### A ticket's tier comes from its status, raised by a linked PR

A Jira ticket in the in-progress category is `started`, and a to-do ticket in
the active sprint is `ready`. `ready` has no named tier, so such a ticket lands
in "the rest", as a `ready` GitHub issue does today. A ticket whose linked PR would sit in a higher
tier (approved and green: ready to merge; in the review queue) takes that
tier. The ticket is the work, and the PR is how far along it is. Rejected:
keeping Jira's `review` term as an additive bump. Inside tiers that bump is
too small to put a ticket whose PR is ready to merge above a ticket that has
only started.

### Linked items fold into the item they belong to

A PR whose title or branch carries a mirrored ticket key folds into that
ticket. A PR that closes a GitHub issue (`closingIssuesReferences`) folds into
that issue. The folded item doesn't get a card of its own. Its evidence shows
on the parent card, and it can raise the parent's tier (above). Overlay
actions on the card act on the parent's key. Rejected: one card for each. Then
a ticket and its PR sit next to each other with the same next step, and
snoozing one leaves the other on the list.

### Blocked items get a last tier of their own

A blocked item goes to a new final tier, "blocked", after "the rest",
whatever its category, and stays visible there. It needs its own tier because
"the rest" holds unblocked items too, and within a tier the category weight
still counts: a blocked `quick_win` (10) would otherwise sort above an
unblocked `ready` (1.5). Zeroing the category term instead isn't enough,
because `prio` and `age` can still lift a blocked item by up to 1.3, past an
unblocked `own_pr` (1). The card's why line then reads `ship: blocked`. Blocked
means any of `score.is_blocked`'s Jira signals
(the `blocked` label, Flagged, a status in `jira.blocked_statuses`, an
unresolved inward Blocks link), or, for a GitHub issue, the `blocked` label or
an open native `blocked_by`. That replaces Jira's −10 `blocked` weight, which
inside tiers would only reorder within one tier. Rejected: hiding blocked
items. The dashboard design's rule that blocked work "sinks, but stays
visible" still holds.

### Being a fleet and GitHub scope are separate settings

`github.fleet` splits in two. Whether the instance ingests from other hosts
stays with `ingest.machines`. How much it reads from GitHub becomes
`github.scope`:

- `owner`: today's wider read, every open issue and others' PRs on the
  configured owners' repos. Right for repos the owner owns.
- `me`: only items that involve the owner: PRs authored by or asking for the
  owner's review, issues assigned to or opened by the owner. Right for an org
  like `Betterment`.

The default is `me`. That keeps today's safe direction: `github.fleet` is off
by default, so an unset key never mirrors a whole org. A config that still sets
`github.fleet`, true or false, is refused at load, as the renamed score tables
are. The error names `github.scope = "owner"` for the wider read and
`ingest.machines` for collecting from other hosts.

The review-queue rules (co-review stamp, approved milestone) apply in both
scopes. A PR that misses one waits in `own_pr` with the rule it misses, as it
does today. Rejected: turning the wider read on as is for every instance. On
the work org it would mirror thousands of issues that aren't the owner's to
do.

### The config is renamed, with no aliases

`[score.fleet]` becomes `[score.categories]`, and `[score.weights]` becomes
`[score.terms]`, holding priority, deadline, unblocks, stuck and age.
`[score.fleet].prio` merges into `[score.terms].priority`: a GitHub issue's
`prio:` label and a Jira ticket's priority feed the same term, each mapped onto
one level scale (`prio:0` as Jira's highest). A config
that still names an old table is refused, and the error names its
replacement, the same way `dashboard.py url` reports a bad key today. With two
installs, both the owner's, a one-time edit costs less than keeping an alias
path. Jira's `review` and `blocked` weights have no replacement, because tiers
now do what they did.

### `tune` and "why not now" work on every instance

They read candidates from the shared ranker, so a Jira-only instance gets them
too. `now_shown` and `why_not` rows are already keyed by `(source, key)`, so
nothing about them changes.

## Not decided here

- **The Waiting on me and I'm waiting on tabs.** On a fleet instance, the Pull
  requests and Issues tabs replace them. Whether every instance should get
  those tabs is a separate question. This design changes only Now.
- **A local collector on the work laptop.** The design allows one. Whether to
  install it, and whether the session hook works there, is the owner's call.
- **Building any new source**, Linear issues and repo-pr task files included.
- **The starting weights for Jira's terms** in `[score.terms]`. They order
  only within a tier, and `tune` exists to correct them.
- **The Now modes work.** That stays #1218. This design is its prerequisite on
  Jira instances, and changes none of its rules.

## Graduation

- Decision records:
  - one ranker for every instance, and what being a fleet means;
  - linked items fold, and a linked PR raises its parent's tier;
  - `github.scope` replaces the GitHub half of `github.fleet`.
- Conventions: `fleet_now.py`'s module docstring (renamed if the module is) for
  the ranking rules and the source seam; `skills/task-dashboard/SKILL.md` for
  the config change; the two 2026-10-23 designs' pointers to this one go when
  those designs are deleted.

## Build slices

Each becomes a child issue of #1225, size 5 or less, once this design is
agreed:

1. **Add `github.scope`.** Add the setting and its `me` searches, refuse
   `github.fleet`, and re-express `sync.fleet_enabled` as `scope = "owner"`.
   That function has about 20 callers, and every one keeps its meaning.
   `fleet_views.enabled` is left as it is, so no instance changes which Now it
   shows. Blocks 3.
2. **The source seam.** A candidate type, with `fleet_now`'s collectors moved
   behind it unchanged. Blocks 3.
3. **Jira tickets as a source**, with the tier mapping and the blocked rule,
   and the separate Jira lane removed. The GitHub-issue blocked signal lands
   here too: the `blocked` label is already mirrored, and an open native
   `blocked_by` needs a new field in `github_fleet.py`'s issue query.
4. **Fold linked items**, PR to ticket and PR to issue, with tier raising.
   Blocked by 3.
5. **Rename the config tables**, and turn on `tune` and "why not now" for every
   instance. Blocked by 3.
6. **Narrow being a fleet to ingest.** `fleet_views.enabled` becomes "ingests
   from other hosts". This waits until slice 3 has removed the Jira/fleet Now
   branch, so no intermediate state shows an instance the wrong Now. The
   slice says what each of the function's other callers does afterwards: tab
   hiding, Now as the home page, the retired-route redirect, the stale lanes
   and Health's why-not section. While the Waiting and PR/Issues tab question
   is open (Not decided here), the tab and home-route callers stay on today's
   test, `scope = "owner"` or any ingest machine. Blocked by 3.
