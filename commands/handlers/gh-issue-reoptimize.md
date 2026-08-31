# gh-issue handler — /reoptimize-tasks flow (report-only)

Invoked from `/reoptimize-tasks` when `handler: gh-issue` is configured. Audits
an existing GitHub Issues backlog's dependency graph and ordering, then applies
the approved fixes. **This path is a downgrade from the Linear handler
(`linear-reoptimize.md`) by construction: GitHub Issues have no native
dependency edge** (no `blockedBy`/`blocks`/`relatedTo`/`duplicateOf`), so
Dimensions 1–2 can only ever produce a **report** plus a suggested
`Blocked by: #<n>` / `Related: #<n>` body footer line — never a real link.
State this plainly in every report (mirrors how `push-plan.md` §5.3 notes the
`plan:<name>` label fallback as a weaker grouping than a milestone). Priority
and label fixes (Dimension 3's inversions, Dimension 4's suspected duplicates)
apply through ordinary `gh issue edit`, same as any other gh-issue mutation.

> **This file has not migrated, and the names below are pre-migration.** Every
> other gh-issue verb now reads and writes the namespaced vocabulary in
> `commands/handlers/assets/labels.yml` — `status:0_untriaged`…`status:4_needs_review`,
> `auto:eligible` / `auto:human-review-needed`, `prio:0`–`prio:3`, `est:<n>` — through
> `commands/handlers/assets/gh-issue-state.py`. Reoptimize still speaks the old names,
> so on a migrated board its `statusType` derivation below matches nothing and every
> issue reads as `new`. Migrating it is its own change. Until then, do not follow the
> shared reference below to `gh-issue.md` or `gh-issue-promote.md` expecting to find
> these names — they define the namespaced ones now.

**Shared reference:** the label vocabulary (`auto-eligible`, `auto-claimed`,
`human-approval-requested`, `priority:<urgent|high|medium|low>`,
`size:<n>`) was defined in `gh-issue.md` §List and `gh-issue-promote.md` before
those files migrated; reuse it rather than inventing new labels. The confidence-check style judgment used
below mirrors `linear-reoptimize.md`'s Analysis dimensions — read that file for
the fuller rationale on each check; this file only documents what differs for
gh-issue.

## Load — build the graph (exhaustive, from prose only)

1. **Preflight auth.** Run `gh auth status 2>&1`. On failure, use the same
   handling as `gh-issue.md` step 1 (TLS/x509 → sandbox keychain hint;
   otherwise report the auth failure) and **stop** — do not fall back to
   another handler.

2. **Resolve the repo.** `gh-issue.repo` from `dev_docs/tasks/.task-config.yml`
   if set, else the current repo (`gh repo view --json nameWithOwner --jq
   .nameWithOwner`). Pass it as `--repo <repo>` on every call below when set.

3. **Resolve scope** (from `/reoptimize-tasks`'s generic `[project|initiative|
   team] [name]` argument, mapped onto gh-issue's own grouping):
   - **project** → a **milestone**. Resolve its title/number the same way
     `gh-issue-promote.md` §2a does (enumerate open milestones via
     `gh api "repos/<repo>/milestones?state=all"`, match the typed name
     case-insensitively; on no match, push back and re-ask).
   - **initiative** → **not supported** — gh-issue has no grouping above a
     milestone. Stop with: "gh-issue has no initiative-level grouping; scope
     to a milestone (`project <name>`) or the whole repo (`team`) instead."
   - **team** → every issue in the repo (gh-issue has no team concept; this is
     the whole-repo scope).

4. **Fetch every issue in scope, both open and closed** (terminal issues carry
   the stale/satisfied-link signal Dimension 1 needs, exactly as
   `linear-reoptimize.md` keeps `Done`/`Canceled` nodes):

   ```bash
   gh issue list --repo "<repo>" --state all [--milestone "<milestone>"] \
     --limit 500 --json number,title,body,state,stateReason,labels,milestone,createdAt
   ```

   500 is a soft cap, not a hard page size — if the response returns exactly
   500, note **possible truncation** in the report rather than paginating
   further (gh-issue graphs at this scale are rare; this mirrors the
   no-paginate stance the other gh-issue handlers take).

   **At milestone scope, backfill referenced out-of-scope issues.** For any
   `#<n>` referenced in a fetched body but absent from the fetched set, fetch
   it individually (`gh issue view <n> --repo "<repo>" --json
   number,title,body,state,stateReason,labels,milestone,createdAt`) and add it
   as a node. Without this, Dimension 2 has no milestone to compare against and
   Dimension 1's stale/satisfied check has no `state`/`stateReason` for those
   targets. These out-of-scope nodes are **analysis inputs only, never mutation
   targets** — §Apply must not edit an issue outside the resolved scope.

5. **Build the graph.** Nodes carry `{number, title, milestone, state,
   stateReason, body, labels, createdAt}`. Derive:
   - `priority` from a `priority:<urgent|high|medium|low>` label if present,
     else `none` (gh-issue's optional analogue of Linear's required
     `priority` — see `gh-issue-promote.md` step 4).
   - `estimate` from a `size:<n>` label if the repo uses one, else absent —
     Dimension 3's "smaller estimate first" tie-break degrades to priority +
     age alone when no candidate in the chain carries a size label.
   - `statusType`: `open` with no status label is `new`; `open` with
     `auto-eligible` is `ready`; `open` with `auto-claimed` is `in_progress`;
     `open` with a `blocked` label is `blocked`; `closed` with `stateReason:
     COMPLETED` is `done`; `closed` with `stateReason: NOT_PLANNED` is
     `canceled` (the gh-issue analogue of Linear's `Canceled` terminal type —
     see `gh-issue.md` §List's mapping table for the open-side labels this
     reuses).

   **Edges exist only as parsed prose** — there is no native edge to
   reconcile against, unlike Linear where a `blockedBy` may already exist and
   merely be missing or wrong. Every edge below is therefore a **proposed
   addition**, never a "convert existing relation" fix.

## Analysis

Run all four dimensions. For each finding, capture the **evidence** (the exact
prose phrase or `#<n>` mention, or the conflicting label/priority) and the
**proposed mutation** so §Apply can execute it verbatim.

### Dimension 1 — Repair blocking chains (report + suggested footer)

- **Parse every issue's body** for the same signals `linear-reoptimize.md`
  Dimension 1 parses: bare `#<n>` / `owner/repo#<n>` mentions, and dependency
  phrases (case-insensitive) `unblocks`, `blocked on`, `blocked by`, `relies
  on`, `depends on`, `requires`, `with X in place`, `re-scoped per`, `part of
  … plan`. Exclude the issue's own number so a body restating its own id never
  yields a self-block.
- For each referenced issue **not already named in an existing `Blocked by:` /
  `Related:` footer line**, propose adding one, classified by phrasing
  strength: strong (`blocked on/by`, `relies on`, `depends on`, `requires`,
  `unblocks`) → a new `Blocked by: #<n>` line on the dependent; weak (`part
  of`, `re-scoped per`, a bare mention) → a new `Related: #<n>` line.
- **Cycles.** Detect any cycle in the graph built purely from these
  prose-parsed edges (there is no other edge source). **Report** the members;
  never auto-resolve — a human decision, same as Linear.
- **Stale / satisfied footer links.** An existing `Blocked by: #<n>` line
  where `#<n>` is closed with `stateReason: NOT_PLANNED` blocks forever →
  propose removing that line. Where `#<n>` is closed with `stateReason:
  COMPLETED` it is _satisfied, not a bug_ → report it, offer optional cleanup
  (low priority), do **not** auto-remove.

### Dimension 2 — Hidden cross-milestone dependencies

From the same parse, flag any reference whose target issue's `milestone`
differs from the referrer's (or either has none) and that has **no** existing
`Blocked by:`/`Related:` footer line → propose the missing line per phrasing
strength (Dimension 1's rule). These are invisible inside a single milestone's
board view. Semantic inference (shared file/subsystem implying an unstated
order) is in scope too, exactly as `linear-reoptimize.md` Dimension 2 —
propose the link with the shared evidence quoted, marked lower-confidence.

### Dimension 3 — Re-order & re-prioritize (applies via `gh issue edit`)

- **Topological order.** Fold in the proposed Dimension 1–2 edges (label the
  order **provisional** — it assumes those edges are approved), then
  topologically sort the non-terminal (`open`, non-`canceled`) nodes. Within
  topo constraints, rank by: urgency first (`urgent > high > medium > low`,
  `none` last), then smaller `size:<n>` first when both sides of a comparison
  carry the label (omit the tie-break otherwise), then age. Print as a
  recommendation — gh-issue has no board-rank field to write back to, so this
  is advisory only, same as Linear.
- **Priority-inversion sweep.** For each proposed or existing `Blocked by:`
  edge, compare the blocker's `priority` label to the dependent's. A blocker
  less urgent than what it blocks (including a blocker with **no** priority
  label, treated as least-urgent) is an inversion → propose relabeling the
  blocker to at least as urgent as the dependent (`gh issue edit --add-label
  priority:<x>`, removing any existing `priority:*` label first). Sweep every
  edge, not a sample.
- **Concurrency sanity (report-only).** Flag any chain where both a blocker
  and its dependent carry `auto-claimed` (both simultaneously in flight) —
  they can't legitimately both be mid-build.

### Dimension 4 — Duplicates / overlap (report + optional label)

Pairwise-compare titles and bodies for overlapping scope or a shared code
surface, quoting the overlapping evidence. GitHub issues have no native
`duplicateOf` — propose a `duplicate` label plus a comment naming the
suspected canonical issue (`gh issue comment <n> --repo "<repo>" --body
"Possible duplicate of #<canonical> — <evidence>"`). **Never** `gh issue close`
from this path; a
duplicate is always a human decision here, same as Linear never auto-merges.

## Apply (gated)

For each **approved** finding:

| Finding                               | Action                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Add a `Blocked by:` / `Related:` line | Read the current body (`gh issue view <n> --repo "<repo>" --json body --jq .body`), append the new footer line (create a `---` footer section per `gh-issue.md` step 2 if none exists), write it back with `gh issue edit <n> --repo "<repo>" --body-file <tmp>`. Preserve the rest of the body verbatim — only the footer changes. |
| Remove a stale footer line            | Same read/edit-body-file pattern, dropping just that line.                                                                                                                                                                                                                                                                          |
| Fix a priority inversion              | `gh issue edit <n> --repo "<repo>" --add-label priority:<x> --remove-label priority:<old>` (omit `--remove-label` if no prior priority label existed).                                                                                                                                                                              |
| Mark a suspected duplicate            | `gh issue edit <n> --repo "<repo>" --add-label duplicate` (create the label first if missing: `gh label create duplicate --repo "<repo>" 2>/dev/null`) + `gh issue comment` per Dimension 4.                                                                                                                                        |

Every `gh` call above takes `--repo "<repo>"` (the value resolved in §Load step
2) — omitting it silently targets the current directory's repo, which is the
wrong one whenever `gh-issue.repo` is configured.

**Hard rules (stop if you're about to break one):**

- **Never** close, reopen, or re-milestone an issue from this path — state and
  milestone changes belong to `/do-tasks` / `/promote-tasks` / a human, not
  this command.
- **Never** rewrite a body wholesale — only the specific footer line being
  added or removed; every other byte of the body must survive unchanged.
- **Never** auto-resolve a cycle or auto-mark-and-close a duplicate — both are
  reported for human action; the duplicate label is the only mutation, and
  only when approved.
- Apply only what the user approved in `/reoptimize-tasks` §5; echo each
  applied mutation back in the final summary (issue number + what changed),
  and list anything skipped (unapproved, terminal, or cyclic) with the reason.
- **State the report/native-link downgrade explicitly** in the final summary
  — e.g. "Dimensions 1–2 add suggested `Blocked by:`/`Related:` footer lines
  only; GitHub Issues have no native dependency edge to create" — so the user
  never mistakes a footer line for a real link the way a Linear `blockedBy`
  is.

## Optional deepening — cross-check the source plan

Same as `linear-reoptimize.md`'s optional deepening: when an issue body
references a local plan file (`Local task: dev_docs/tasks/<plan>/…md`) present
in the repo, **Read** it and cross-check the tracker ordering against the
plan's `is_blocked_by` edges. A divergence is a Dimension-1 finding. Skip
silently if the files aren't on disk.
