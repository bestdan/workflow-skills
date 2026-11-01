# prioritize `[scope]`: the prioritization procedure

Read by the task-dashboard skill's **prioritize** section. `DASH` is the
`dashboard.py` call that skill defines. Run every `DASH` read below
unsandboxed: inside the sandbox SQLite cannot open the WAL file beside the DB.

The dashboard ranks by visible sums and never calls a model; the judgment here
is yours, and every item you propose carries its reason. The procedure never
writes to a tracker: merging, closing or moving an item goes through the
command its `action` names, and only on the owner's yes.

## 1. Read the ask for scope, mode and time

- **Scope**: "my FinPlan tasks" is a scope word. With none, use `all`.
- **Mode**: "small tasks", "bang out", "knock out" is `little`; "a hard
  design", "think through", "decide" is `deep`; "clean up", "what's rotting"
  is `tidy`; "land", "ship", "merge", "what's waiting on me" is `ship`. With
  none, `ship`.
- **Time**: "half an hour" is `--minutes 30`; "an hour" is `--minutes 60`.

Ask only when the ask names none of the three.

## 2. Resolve the scope

If a scope was named, `DASH scopes <word>` and say what it matched. For a repo
it also lists `related` workstreams; show them, and add only the ones the
owner confirms as `--scope ws:<id>`.

## 3. Read the brief

```bash
DASH brief --scope <id> [--scope <id> ...] --mode <mode> [--minutes N]
```

Its sections, in order:

- `freshness`: if a source `warn`s or is hours old, say so before
  ranking anything. Offer `DASH sync-now` or `DASH refresh <owner/repo#N>` on
  the items you would act on; don't refresh by default.
- `in_flight`, first, because most of the work here is finishing: open PRs
  by readiness (`ready_to_merge`, `review_queue`, `ci_failing`, `draft`,
  `other`), sessions `waiting` on the owner, and `worktrees` with no PR.
- `blocked`: what is stuck, with `blocked_by`, and what is `blocking` others.
  For a GitHub issue these are counts, not refs; say so rather than naming
  blockers.
- `candidates`: the mode's ranked items, in Now's order with each one's
  placing as `now`. The order is a starting point to argue with, not the
  answer. An item already listed under `in_flight` appears only as a stub
  marked `in_flight: true`.
- `signals`: the owner's pins, snoozes, notes and recent "why not now"
  answers. Respect them: a snoozed item stays out unless the owner says
  otherwise.
- `gaps`: the sources this instance does not mirror. Name them, so the owner
  knows what the ranking cannot see.

A list cut by the token budget says how many it left out (`omitted`) and the
command that lists them (`more`). Run that only when the plan needs them.

**With `--minutes`**, `candidates.items` holds the sized candidates, in order,
whose `est_min` sum stays within twice the box (`minutes.cap`), so you have
room to choose and swap. `candidates.unsized` lists the rest: a PR too big for
Little's buckets (`large_pr`), a PR writing a research, design or plan doc
(`deep_doc`: deep work, which Deep mode ranks), an `est:3` issue or larger,
or no size at all.
They have no minutes by design (`[size.minutes]` has no default for them), so
**never call an unsized item small**; name the ones you left out. Packing the
box is your call, weighing what each item unblocks.

**Deep** keeps a WIP of one and ignores the sum: `candidates.top` is the first
candidate's whole `item` dossier, so you can open with what's needed to
resume it. Prefer a design already in flight (an open PR, a session that was
writing it, a worktree holding it) over one nobody has started.

## 4. Read the dossier of each item you would act on

`DASH item <source:key>` (or `--sections prs,sessions` for one question)
before saying "close this", "merge this" or "this one first".

## 5. Propose the plan in chat

An ordered list, each line naming the item, what to do (`do`, `finish`,
`unblock`, `review`, `defer` or `drop`), a one-line reason, and its `est_min`
(or "unsized"). With a time box, give the total against it. Say which
candidates you left out and why, and which gaps limit the answer.

## 6. On the owner's yes, keep the plan

Save the plan they agreed to, as agreed, with `DASH plan set` (SKILL.md's
`plan` section has the file's shape): the scope from step 1, `--by` naming
you, and the items in order, each with its reason, `action` and `est_min`. A
time box makes it a session plan (`--minutes N`), which lapses at the next
04:00; without one it is standing. Now's Plan mode then shows it, ticking
items as they close.

Any snooze, ignore or pin the plan implies (a `defer` or `drop` line) goes
through `DASH overlay`, one item at a time, each one the owner approved.
Never batch them, and never set a plan the owner did not say yes to.

## 7. Offer to start the first item

For a session plan, offer to start its first item. The `action` in its
`item` dossier names how: resume its session, open its worktree, or review
its PR. Wait for the owner to say go.
