# gh-issue handler — /reoptimize-tasks flow

Invoked from `/reoptimize-tasks` when `handler: gh-issue` is configured. Audits
an existing GitHub Issues backlog's dependency graph and ordering against the
**native `blocked_by` edges**, then applies the approved fixes as real edges.

**The edge is the graph. The footer is an echo of it.** GitHub Issues has a real
`blocked_by` dependency edge, and it is the only encoding anything in this
handler honours: `/push-plan` §5.5 draws one per plan dependency,
`/list-tasks` and `/do-tasks` read it (`gh-issue-ready.py`), and this flow now
creates and removes it too. The `Blocked by: #<n>` body footer stays alongside as
a human-readable copy — visible where the dependency panel is easy to miss, and
readable by a cloud routine, which has no `gh` and no MCP dependency tool
([`2026-08-24-routine-claim-channel.md`](../../dev_docs/decisions/2026-08-24-routine-claim-channel.md)).

Two rules follow, and they are the whole design:

- **Never read the footer as a dependency.** It is prose someone can type, edit
  or leave behind; it enforces nothing. Every finding below comes from the edge.
- **Never write a footer for a dependency that has no edge.** That is what the
  previous version of this flow did, and it is what made the footer untrustworthy
  in the first place — a reader could not tell a recorded dependency from a
  proposal nobody applied. Write the edge first; the footer echoes it.

The one place the footer is read is the **migration** (§Load step 6): a
`Blocked by: #<n>` line with no edge behind it is a dependency this handler used
to record in the only form it had. It becomes an edge, once, and is thereafter an
echo like any other.

`Related:` has no native counterpart and stays a body footer line. It is a
cross-reference, not a blocking relation, so it is not in tension with the rules
above — but say so in the report rather than letting a reader assume it is an
edge. `duplicateOf` has no counterpart either (Dimension 4).

**Shared reference:** the label vocabulary is
`commands/handlers/assets/labels.yml` — `status:0_untriaged`…`status:4_needs_review`,
`auto:eligible` / `auto:human-review-needed`, `prio:0`–`prio:3`, `est:<n>`. Read it
from there; do not hardcode label names. Every status write goes through
`commands/handlers/assets/gh-issue-state.py`, which validates against that
vocabulary before any network call — raw REST **auto-creates** unknown labels, so
an unvalidated write invents state silently. The confidence-check style judgment
used below mirrors `linear-reoptimize.md`'s Analysis dimensions — read that file
for the fuller rationale on each check; this file documents what differs for
gh-issue.

> **"Carrying a `prio:`" means carrying one `labels.yml` defines** — never merely
> a label whose name starts with `prio:`. A hand-typed `prio:urgent` satisfies the
> prefix and is not a priority; ranking by it would invent an order from a value
> nothing defines. `gh-issue-graph.py` reads the vocabulary, and so must any
> judgment you make on top of its output.

## Load — build the graph

1. **Preflight auth.** Run `gh auth status 2>&1`. On failure, use the same
   handling as `gh-issue.md` step 1 (TLS/x509 → sandbox keychain hint;
   otherwise report the auth failure) and **stop** — do not fall back to
   another handler.

2. **Resolve the repo.** `gh-issue.repo` from `dev_docs/tasks/.task-config.yml`
   if set, else the current repo (`gh repo view --json nameWithOwner --jq
   .nameWithOwner`). Pass it as `--repo <repo>` on every call below.

3. **Resolve scope** (from `/reoptimize-tasks`'s generic `[project|initiative|
   team] [name]` argument, mapped onto gh-issue's own grouping):
   - **project** → a **milestone**. Resolve its title the same way
     `gh-issue-promote.md` §2a does (enumerate open milestones via
     `gh api "repos/<repo>/milestones?state=all"`, match the typed name
     case-insensitively; on no match, push back and re-ask).
   - **initiative** → **not supported** — gh-issue has no grouping above a
     milestone. Stop with: "gh-issue has no initiative-level grouping; scope
     to a milestone (`project <name>`) or the whole repo (`team`) instead."
     Stop whether or not the run applies: continuing repo-wide would answer a
     request to _narrow_ with a _wider_ run, and this flow writes.
   - **team** → every issue in the repo (gh-issue has no team concept; this is
     the whole-repo scope).

4. **Read the native graph.**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-graph.py" \
     --repo "<repo>" [--milestone "<milestone>"] --limit 500 --json
   ```

   Read-only — every call it makes is a GET. It returns the nodes in scope, the
   real `blocked_by` edges between them, and the five findings Dimensions 1–3
   rest on: `cycles`, `stale_edges`, `satisfied_edges`, `inversions`,
   `concurrent` — plus `footer_only` and `edge_only`, which §Load step 6 and
   Dimension 1 use.

   Three properties of that read are load-bearing, and each is silent when
   wrong, so do not reimplement them inline:
   - `blocked_by` is **paginated**. It is read with `--paginate --slurp`; a bare
     read stops at 30 entries, and an edge past that page reads as absent.
   - **Blockers outside the scope are backfilled** as `"in_scope": false` nodes,
     and the backfill is **transitive** — it closes the reachable graph. A
     milestone-scoped run otherwise has no `state` for a cross-milestone blocker,
     and a cycle that leaves the scope and re-enters it (`#1 → #9 → #5 → #1`
     across three milestones) would read as absent. These nodes are **analysis
     inputs only, never mutation targets**: `cycles` and `edges` span the whole
     closure, while `stale_edges`, `satisfied_edges`, `inversions` and
     `concurrent` are already filtered to in-scope dependents, so every finding
     you are handed is one §Apply is allowed to act on.
   - `"truncated": true` means the list came back exactly at `--limit`, so an
     issue outside the window would read as absent. Report it; do not paginate
     further (gh-issue graphs at this scale are rare, matching the no-paginate
     stance the other gh-issue handlers take).

5. **Derive per-node judgment from the returned labels.** Each node carries
   `prio`, `est` and `status` already resolved against the vocabulary, plus
   `state` / `state_reason` and its `body` — so the prose parse the Dimensions
   below run needs no second fetch. Map them the way the rest of the handler does:
   an open issue's `status:` value is its position on the ladder; a closed issue
   with `state_reason: completed` is done, with `not_planned` is canceled.

6. **Migrate the footers (once per backlog).** `footer_only` lists every
   `Blocked by: #<n>` line with no edge behind it — the dependencies this
   handler used to record in the only form it had. Propose each as an edge to
   create. This is the only place a footer is read as a dependency, and after
   the migration a `footer_only` entry means something different: someone typed
   a footer by hand, and it still needs an edge.

   `Blocked by task: <slug>` lines are **not** in `footer_only` and are not
   migrated. A slug names a plan task that never became an issue (a blocker
   `/push-plan --ready-only` held back), so there is nothing to link to.

## Analysis

Run all four dimensions. For each finding, capture the **evidence** (the exact
edge, prose phrase or conflicting label) and the **proposed mutation** so §Apply
can execute it verbatim.

### Dimension 1 — Repair blocking chains

- **Missing edges, from prose.** Parse every issue's body for the signals
  `linear-reoptimize.md` Dimension 1 parses: bare `#<n>` / `owner/repo#<n>`
  mentions, and the dependency phrases (case-insensitive) `unblocks`, `blocked
  on`, `blocked by`, `relies on`, `depends on`, `requires`, `with X in place`,
  `re-scoped per`, `part of … plan`. Exclude the issue's own number so a body
  restating its own id never yields a self-block. For each referenced issue with
  **no native edge already** (check `edges` from step 4, not the footer),
  classify by phrasing strength: strong (`blocked on/by`, `relies on`, `depends
  on`, `requires`, `unblocks`) → propose a **`blocked_by` edge**; weak (`part
  of`, `re-scoped per`, a bare mention) → propose a `Related: #<n>` footer line,
  which is a cross-reference and stays prose.
- **Cycles.** `cycles` from step 4, computed over the real edges. **Report** the
  members; never auto-resolve — breaking a cycle means deciding which dependency
  is wrong, which is a human call, same as Linear. A cycle that appears only in
  footer prose is **not** a cycle: prose deadlocks nothing.
- **Stale edges.** `stale_edges` — the blocker is closed `not_planned`, so the
  edge blocks forever → propose **removing the edge**.
- **Satisfied edges.** `satisfied_edges` — the blocker is closed `completed`. It
  is _satisfied, not a bug_; GitHub already stops counting it. Report it, offer
  optional cleanup (low priority), do **not** auto-remove.
- **Missing echoes.** `edge_only` — a real edge whose body carries no
  `Blocked by:` line. Propose adding the footer line. This is the echo catching
  up with the edge, never the reverse.

### Dimension 2 — Hidden cross-milestone dependencies

From the same prose parse, flag any reference whose target node's `milestone`
differs from the referrer's (or either has none) and that has **no native edge**
→ propose the edge per Dimension 1's phrasing-strength rule. These are invisible
inside a single milestone's board view, and step 4's backfill is what makes the
target's state readable at all. Semantic inference (a shared file or subsystem
implying an unstated order) is in scope too, exactly as `linear-reoptimize.md`
Dimension 2 — propose the edge with the shared evidence quoted, marked
lower-confidence.

### Dimension 3 — Re-order & re-prioritize

- **Topological order.** Fold in the proposed Dimension 1–2 edges (label the
  order **provisional** — it assumes those edges are approved), then
  topologically sort the non-terminal nodes. Within topo constraints, rank by:
  `prio:` first (`prio:0` most urgent through `prio:3`, no `prio:` label last),
  then smaller `est:<n>` first when both sides of a comparison carry the label
  (omit the tie-break otherwise), then age. Print as a recommendation —
  gh-issue has no board-rank field to write back to, so this is advisory only,
  same as Linear.
- **Priority inversions.** `inversions` from step 4, swept over every edge
  rather than a sample: an open blocker less urgent than the open issue it
  blocks, including a blocker carrying **no** `prio:` label, which ranks last.
  Each entry carries `raise_blocker_to` — the dependent's `prio:` value, which
  is the least urgent the blocker may be without inverting. A **closed** blocker
  is never an inversion; it is stale or satisfied, and ranking a finished issue's
  urgency says nothing.
- **Concurrency sanity (report-only).** `concurrent` — a blocker and its
  dependent both at `status:3_started`. They cannot legitimately both be
  mid-build.

### Dimension 4 — Duplicates / overlap (report + optional label)

Pairwise-compare titles and bodies for overlapping scope or a shared code
surface, quoting the overlapping evidence. GitHub issues have no native
`duplicateOf` — propose a `duplicate` label plus a comment naming the suspected
canonical issue. **Never** `gh issue close` from this path; a duplicate is
always a human decision here, same as Linear never auto-merges.

> `duplicate` is outside `labels.yml`'s four namespaces, so it is **not** written
> through `gh-issue-state.py` — that helper owns `status:`/`auto:`/`prio:`/`est:`
> and carries every other label forward untouched. Add it with `gh issue edit`,
> which rejects an undefined label rather than inventing one.

## Apply (gated)

For each **approved** finding:

| Finding                    | Action                                                                                                                                                                                                                                                                             |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Add a dependency           | `gh-issue-deps.py --repo "<repo>" --edge <blocked>:<blocker> --apply`. Create-missing-only, so an approved edge that already exists is a no-op. Repeat `--edge` to batch a run. **Then** add the `Blocked by: #<n>` echo to the body, per the row below.                           |
| Remove a stale dependency  | `gh-issue-deps.py --repo "<repo>" --remove-edge <blocked>:<blocker> --apply`. Remove-existing-only. **Then** drop the matching `Blocked by:` line from the body, so the echo does not outlive the edge.                                                                            |
| Add or drop a footer echo  | Read the current body (`gh issue view <n> --repo "<repo>" --json body --jq .body`), add or remove **only** that `Blocked by:` / `Related:` line (create a `---` footer section per `gh-issue.md` step 2 if none exists), write it back with `gh issue edit <n> --body-file <tmp>`. |
| Fix a priority inversion   | `gh-issue-state.py --repo "<repo>" --issue <blocker> --labels "<the issue's full label set with prio: set to raise_blocker_to>" --apply`. The helper replaces the whole set, so **every** label the issue should keep must appear in the value.                                    |
| Mark a suspected duplicate | `gh issue edit <n> --repo "<repo>" --add-label duplicate` (create it first if missing: `gh label create duplicate --repo "<repo>" 2>/dev/null`) + `gh issue comment` per Dimension 4.                                                                                              |

Every `gh` call above takes `--repo "<repo>"` (the value resolved in §Load step
2) — omitting it silently targets the current directory's repo, which is the
wrong one whenever `gh-issue.repo` is configured.

**Two mechanical notes that fail silently when missed:**

- The dependency POST body and the removal DELETE path both carry the blocker's
  **database id**, not its `#<number>`. `gh-issue-deps.py` resolves it; that is
  why the edge writes go through the helper rather than a hand-rolled `gh api`.
- The local `sandbox-network-guard` hook blocks non-GET `gh api`, so any
  `--apply` run needs the sandbox escape. A blocked write surfaces as a hook
  refusal, not as a failed edge.

**Hard rules (stop if you're about to break one):**

- **Never** close, reopen, or re-milestone an issue from this path — state and
  milestone changes belong to `/do-tasks` / `/promote-tasks` / a human.
- **Never** edit an issue that step 4 returned with `"in_scope": false`. Those
  were backfilled to make a cross-scope edge readable, not to be mutated.
- **Never** rewrite a body wholesale — only the specific footer line being added
  or removed; every other byte must survive unchanged.
- **Never** write a `Blocked by:` footer for a dependency with no edge. The
  footer follows the edge; a footer alone records a proposal as if it were a
  decision, which is the defect this rewrite exists to remove.
- **Never** auto-resolve a cycle or auto-close a duplicate — both are reported
  for human action; the `duplicate` label is the only mutation, and only when
  approved.
- Apply only what the user approved in `/reoptimize-tasks` §5; echo each applied
  mutation back in the final summary (issue number + what changed), and list
  anything skipped (unapproved, terminal, out of scope, or cyclic) with the
  reason.
- **State what a routine can and cannot see** in the final summary: the edges
  are the graph, and a cloud routine cannot read them — it sees only the footer
  echo. Anything relying on unattended dependency awareness through the MCP
  connector is reading a hint.

## Optional deepening — cross-check the source plan

Same as `linear-reoptimize.md`'s optional deepening: when an issue body
references a local plan file (`Local task: dev_docs/tasks/<plan>/…md`) present
in the repo, **Read** it and cross-check the tracker graph against the plan's
`is_blocked_by` edges. A divergence is a Dimension-1 finding. Skip silently if
the files aren't on disk.
