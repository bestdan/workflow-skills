# Linear handler — /sweep-for-complete flow

Invoked from `/sweep-for-complete [--apply] [--all]` when `handler: linear` is
configured. Finds issues sitting in a **started**-type state whose **own**
linked PR has merged, and completes exactly those by calling the
`linear-complete.md` phase per verified match — the mechanical transition
itself is not re-specified here; see that file's "Caller contract" and
"Steps".

**Shared reference:** see `linear-common.md` for connection details, the
config schema (`linear.projects`, the Unassigned bucket), and the preflight
pattern.

> **Hard note — this sweep is immune to the bare-id over-close bug that got
> Linear's GitHub integration disabled.** That integration scans PR title/body
> text for issue ids and auto-completes anything it finds referenced with a
> closing magic word — including a **bare** `<TEAM>-NNN` token that was never
> meant to close, which is exactly how an unrelated sibling issue got
> silently closed. This sweep never parses issue ids out of PR text at all.
> It works in the opposite direction: it starts from the issues it already
> holds in a started-type state, resolves **that issue's own** structurally-
> linked PR (an explicit Linear `links` attachment, or a title/branch match
> scoped to that one identifier), and only completes an issue whose own
> linked PR independently verified as merged. Batching several completions in
> one run is fine — each one is independently merge-verified before it is
> touched; there is no shared or inferred link between issues.

## 1. Preflight + resolve scope

1. Run the shared preflight from `linear-common.md` (call
   `<linear-mcp>__list_teams`, match `<linear.team>`, capture the team `id`).
   Same failure messages.
2. **Resolve scope**, mirroring `linear-claim.md` "Find candidates" steps 2–4:
   - **`--all`** → skip project resolution entirely and run a **single
     whole-team query** in step 2 below (no `projectId` filter, no Unassigned
     pass — the whole team already covers everything).
   - **No `--all`** → call the **"Resolve configured projects"** helper from
     `linear-common.md` for the configured `linear.projects` scopes, **plus
     the Unassigned bucket** exactly as `linear-claim.md` "Find candidates"
     composes it — including its **whole-team-query exclusion rule** (the
     Unassigned pass runs one extra whole-team query with `projectId`
     omitted, then keeps only issues whose `projectId` is `null` or not
     among the configured scopes) and its **50-row truncation caveat** (the
     cap applies before the exclusion filter, so a full cap with no
     survivors means "unassigned coverage may be partial," not "no
     unassigned work"). See `linear-common.md` "The Unassigned bucket" for
     the exact sentinel shape.

## 2. Find in-flight issues

These are the only issues that can possibly be "merged but not yet
completed" — anything not in a started-type state either hasn't begun or is
already terminal.

1. Call `<linear-mcp>__list_workflow_states` with the team `id` (cache the
   state-id → type map, same cache `linear-claim.md` and `linear-complete.md`
   build). Resolve the state ids for type `started` whose **name**
   (case-insensitive) is `In Progress` **or** `In Review` — both are
   `started`-type per the kanban mapping in `linear-common.md`, and both are
   candidates: an `In Review` issue is exactly the case where a PR opened,
   got reviewed, and merged, but nothing moved the Linear issue.
2. Call `<linear-mcp>__list_issues` once per resolved scope from step 1
   above:
   - `teamId`: resolved team id
   - `projectId`: the scope's `id` (omit for the whole-team scope and for
     `--all`); **never** pass the Unassigned sentinel as a `projectId` — use
     the same exclusion-pass technique as `linear-claim.md`.
   - `stateId`: the `started`-type state ids from step 2.1 (both `In
     Progress` and `In Review`, where the team has a distinct `In Review`
     state — some teams only have one `started`-type state, in which case
     this is a single id)
   - `includeArchived`: `false`
   - Limit: 50 per scope. If a scope truncates, note it in the report — do
     not paginate.
3. Union the results across scopes (tag each with its source scope for the
   report; no dedup needed — the Unassigned exclusion pass is disjoint by
   construction, same as `linear-claim.md`).

## 3. Resolve each issue's PR

For each in-flight issue from step 2, resolve its **own** PR in this priority
order — stop at the first that resolves:

1. **The issue's `links` attachment** — the explicit attachment `/do-tasks`
   writes in `linear-claim.md` "Move to review on PR open." Call
   `<linear-mcp>__get_issue` with the identifier (this also refreshes state,
   used again in step 4) and read its `attachments`. Pick the attachment
   whose `url` is a GitHub PR URL (matches `github.com/.../pull/<n>`). This
   is the **authoritative** source — it is a structural link written at PR-open
   time, not an inferred one.
2. **Fallback — title search.**

   ```bash
   gh pr list --state all --search "<IDENTIFIER> in:title" --json number,url,title,state
   ```

   GitHub search tokenizes on punctuation, so this is a **coarse** pre-filter
   — the same caveat `linear-claim.md` "Pre-flight" step 3 documents. Before
   accepting a match, require the returned PR's `title` to actually contain
   the literal `[<IDENTIFIER>]` bracket token (the exact form the tracker
   execute path puts in every PR title). Discard any hit that doesn't carry
   that literal token.
3. **Fallback — branch name.** Call `<linear-mcp>__get_issue` for the issue's
   `branchName` if not already fetched, then:

   ```bash
   gh pr list --state all --head "<branchName>" --json number,url,state
   ```

If none of the three resolve a PR, **skip the issue** — there is nothing to
act on. Count it toward the report's "no-PR skipped" bucket; do not treat
this as an error.

## 4. Check merge state

For each issue with a resolved PR, call:

```bash
gh pr view <url-or-number> --json state,mergedAt
```

Only `state == "MERGED"` (equivalently, a non-null `mergedAt`) qualifies as a
candidate for step 5.

- **`OPEN`** → leave the issue untouched. This is `/reconcile-tasks` row 2's
  concern (a future command that reconciles open-PR state) — do not add that
  logic here.
- **`CLOSED` and unmerged** → leave the issue untouched. Whether a
  closed-unmerged PR should demote its issue back to backlog is a deferred
  rule for future work — do not add it here either. Count both cases (open,
  closed-unmerged) separately in the report as "left."

## 5. Dry-run (default)

Without `--apply`, print the candidate table and stop — change nothing:

```
<IDENTIFIER> — PR #<n> (merged <date>) → Done
```

followed by the left/skipped lines (open PRs left, closed-unmerged PRs left,
no-PR issues skipped), and an explicit "nothing changed (dry-run)." Mutation
requires the caller to have passed `--apply` — this mirrors `/archive-tasks`'
dry-run-first posture: always show the candidate list before ever touching
anything.

## 6. Apply (`--apply` only)

For each issue whose own PR verified as merged in step 4, invoke the
`linear-complete.md` phase directly (do **not** duplicate its steps here —
read that file for "Preflight," "Resolve the issue," "Resolve the target
`completed`-type state id," "Idempotence check," "`--dry-run` and
confirmation," "Apply," "Comment," and "Report") with:

- the issue's identifier
- `assume_verified: true` — this sweep is the caller asserting it already
  confirmed the merge in step 4 above, so `linear-complete.md` skips its
  per-issue interactive confirmation and applies the transition directly.
  Batching several completions in one run this way is safe: each one was
  independently merge-verified against its **own** PR before this call, never
  inferred from another issue's PR.
- `comment_body: "Closed by merge of PR #<n> (<PR URL>)"` — `<n>` and the URL
  from step 4's `gh pr view` result for that issue.

Complete **only** the issue whose own linked PR merged — never a sibling or a
co-mentioned issue. `linear-complete.md`'s own idempotence check (its step 4)
already makes a re-run of this sweep safe: an issue already in a
`completed`/`canceled` state on a later sweep simply reports "already
complete" and is not written again, so re-running the sweep after a partial
apply is not destructive.

## 7. Report

Print:

- **Counts** — `k completed, m open (left), s no-PR skipped, c
  closed-unmerged (left)`.
- **Per-issue lines** — identifier, the PR resolved (if any) and its merge
  state, and the outcome (`completed`, `left: open PR`, `left: closed
  unmerged`, `skipped: no PR found`, or `already complete` for an idempotent
  no-op).
- On dry-run, the same table with no outcome column, plus "nothing changed
  (dry-run)."
