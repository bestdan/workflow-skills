# Linear handler — /reoptimize-tasks flow

Invoked from `/reoptimize-tasks` when `handler: linear` is configured. Audits an
existing Linear backlog's dependency graph and ordering, then applies the
approved fixes. The analysis (§Analysis) is read-only; mutations happen only in
§Apply, only after per-group approval.

**Shared reference:** see `linear-common.md` for connection details, the MCP
namespace note (`<linear-mcp>__` is `mcp__linear__` or `mcp__claude_ai_Linear__`),
the config schema, the kanban mapping, and the **hard rule** against moving
issues to `completed`/`canceled` states. The relation/priority edits here reuse
the exact `save_issue` fields documented in `linear-add.md` step 4.

## Load — build the graph (exhaustive)

**Fast-path/floor gate.** This load runs behind the shared gate — see `linear-common.md` "Fast-path / MCP-floor gate (and the security boundary)" for the mechanism (the `linear-relations.py` non-zero exit _is_ the gate; **no** `[ -n "$LINEAR_API_KEY" ]` pre-check) and the security boundary. The script here is `linear-relations.py`; this consumer loads the whole relation graph in one pass, so its fallback granularity is the **whole load** (any failure floors the entire load, not a per-scope subset).

### Fast path (GraphQL, via `linear-relations.py`)

On the fast path, the script's own prelude resolves the team itself, so this
**replaces** the MCP-floor "Preflight" step below — do not also call
`list_teams` on this path.

1. **Resolve project scopes.** Use the **scope already resolved by
   `reoptimize-tasks.md` §2** (the `project`/`initiative`/`team` from
   `$ARGUMENTS`) — exactly the scope the floor's "Collect the scope's issues"
   loads, so the two paths analyze the same issue set. For a single project,
   pass its real `id` as `--project`; for an initiative, union its projects and
   pass each as `--project`; for the whole-team scope, omit `--project` (the
   whole-team `id: null` scope). Do **not** re-resolve from `linear.projects`
   config here — that would make the fast path analyze the configured projects
   instead of the requested scope, diverging from the floor.
2. **Call the script.**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-relations.py" --team "<linear.team>" \
     --project "<scope-1-id>" --project "<scope-2-id>" ...
   ```

   Omit `--project` entirely for the whole-team scope. If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/linear-relations.py`. Parse stdout as the
   `{ meta: { viewer, team, states }, issues: [...] }` object described in the
   script's header comment; a parse failure is itself a fallback trigger (see
   above). The query is **not** filtered by `state` — terminal (`Done`/
   `Canceled`) issues are included by construction, same as the floor.
3. **Build the graph.** Each `issues[]` entry already carries `description`
   and the derived `blockedBy`/`blocks`/`relatedTo`/`duplicateOf` edge lists
   (see the script's header for the exact `relations`/`inverseRelations`
   derivation). Note `relatedTo` folds in Linear's **`similar`** relation
   alongside `related` — they are indistinguishable downstream, so a proposal
   to convert a weak link into a real `blocks` may be acting on either. Nodes carry `{id, title, project, priority, estimate, updatedAt,
   statusType (from state.type), description, labels, relations}` — `estimate`
   is selected so Dimension 3's "smaller estimate first (quick wins)" ordering
   matches the floor. Edges come from native `blockedBy`. Keep terminal nodes in the graph for
   Dimension 1; **exclude** them from the Dimension 3 ordering output. Because
   this is a **single filtered query per scope** rather than a per-issue
   fan-out, there is no need to confirm with the user before running it, even
   on a whole-team scope — that is the whole point of the fast path.

### MCP floor (fallback)

Runs whenever the fast path isn't attempted or falls back per the gate above.

1. **Preflight.** Resolve the team id via the `linear-common.md` preflight.
2. **Collect the scope's issues.** Call `<linear-mcp>__list_issues` for the
   resolved scope (`teamId`, plus `projectId` when scope is one project; for an
   initiative, union the issues of each of its projects). **Page until
   `hasNextPage` is false** — never analyze a truncated graph.
3. **Fetch every node's relations — including terminal ones.** For **every**
   issue in scope, call `<linear-mcp>__get_issue` with
   `includeRelations: true` to get the full `description` plus native
   `blocks` / `blockedBy` / `relatedTo` / `duplicateOf`. Do this for `Done` and
   `Canceled` issues too — a stale or never-satisfiable edge is only visible from
   the terminal side, and skipping them is the most common blind spot. This is
   one `get_issue` per issue; for a very large scope (a whole team with many
   issues), confirm with the user before fanning out — this loop is still
   expensive on the floor, unlike the fast path's single query.
4. **Build the graph.** Nodes carry `{id, title, projectId, priority, estimate,
   statusType, description, relations}`. Edges come from native `blockedBy`.
   Keep terminal nodes in the graph for Dimension 1; **exclude** them from the
   Dimension 3 ordering output.

## Analysis

Run all four dimensions. For each finding, capture the **evidence** (the exact
prose phrase or `<issue>` mention, or the conflicting relation/priority) and the
**proposed mutation** so §Apply can execute it verbatim.

### Dimension 1 — Repair blocking chains

- **Cycles.** Run `linear-graph-analyze.py` (see Dimension 3's "Run the
  helper" — one call serves both dimensions) and read its `cycles` field:
  each entry is a strongly-connected-component's members, in no particular
  order. **Report** the members; never auto-resolve — a cycle is a human
  decision (mirrors push-plan §4.3).
- **Stale / never-satisfiable links.** A `blockedBy` pointing at a **`Canceled`**
  issue blocks the dependent forever → propose `removeBlockedBy`. A `blockedBy`
  pointing at a **`Done`** issue is _satisfied_, not a bug → report it as
  satisfied and offer optional cleanup (low priority); do **not** auto-remove.
- **Prose → native reconciliation (the core fix).** The dependency-phrase
  table lives once, in `commands/handlers/assets/_body_refs.py` — shared
  with the gh-issue handler, not restated here.

  - **Fast path.** `linear-relations.py` already ran that table over every
    issue's description while building the graph (§Load). Read its top-level
    `proposed` field: each entry is a `{from, target, phrase, direction,
    strength}` reference **not already covered by a native relation** —
    self-mentions and code-span mentions are already excluded. `direction:
    "blocked_by"` → propose `blockedBy` on `from`; `direction: "blocks"` →
    propose `blocks` on `from` (only `unblocks` produces this, already
    reversed correctly — `_body_refs.py`'s tests pin that, so don't re-derive
    it); `direction: "related"`, i.e. `strength: "weak"` → propose
    `relatedTo`. This catches drift like PRE-210's body saying it "unblocks
    PRE-189" while the native relation is only `relatedTo` — propose
    converting it to a real `blocks` edge.
  - **MCP floor.** There is no script entry point on this path (§Load's floor
    has no `gh`/`linear-relations.py` fast-path call to piggyback on), so run
    the same `_body_refs.py` table by hand, issue by issue, exhaustively — not
    a spot-check. It is the identical rules the fast path reads pre-computed;
    read the module's docstring for the phrase → direction/strength mapping
    rather than re-deriving it, and apply its stated exclusions
    (self-mentions, code-span mentions).

  Build the diff for **all** issues on either path; do not stop at the
  load-bearing ones.

### Dimension 2 — Hidden cross-project dependencies

- **Cross-project references.** From the same references (`proposed` on the
  fast path; the hand-walk above on the floor), flag any whose target issue's
  `projectId` **differs** from the referrer's and that has **no native link**
  → propose `blockedBy`/`relatedTo`/`blocks` per Dimension 1's direction, which
  is already resolved. These are invisible inside any single project view and
  are the whole point of the initiative-scoped run.
- **Semantic inference (judgment, lower-confidence).** Read descriptions for a
  shared file / function / subsystem that implies one issue must precede another
  even when neither cites the other (e.g. two issues both rewriting the same
  `manage_state` write surface, or a schema change that invalidates another
  issue's stated assumption). Propose a link **with the shared evidence quoted**
  and mark it lower-confidence so the user vets it. If the collision implies
  unscoped work (e.g. a migration neither issue owns), say so explicitly rather
  than only proposing an edge.

### Dimension 3 — Re-order & re-prioritize

**Run the helper.** Cycle detection, the topological order, and the priority-
inversion sweep are all one deterministic pass over the same graph, so run it
once and read all three fields — do not hand-walk any of them:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-graph-analyze.py" --file <graph.json>
```

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/handlers/assets/linear-graph-analyze.py`. Its input is a `{issues: [...]}`
JSON object — the fast path's `linear-relations.py` output already has this
shape; on the MCP floor, build it from the "Build the graph" nodes, giving
each issue `identifier` (or `id`), `priority`, `estimate`, `state: {type}`,
and native `blockedBy` (by identifier), plus `createdAt` when the node
carries it. **Fold in the proposed Dimension 1–2 edges** before calling it
(the report is printed before approval, so label everything below
**provisional** — it assumes those edges are approved; if approval diverges
in §5, re-run the helper over the edges that survived). Its stdout is one
JSON object: `{cycles, order, inversions}` — parse it, don't re-derive it.

- **Topological order.** Read `order`: a valid execution order over the
  non-terminal nodes, already ranked by urgency (`1`=Urgent…`4`=Low, `0`=None
  sorted **last** — never a formula like `priority ÷ estimate`, which is
  incoherent over that scale), then smaller `estimate` (quick wins), then age.
  A cycle's members are absent from `order` (they're in `cycles` instead);
  anything merely downstream of a cycle is absent from **both** — **report**
  that gap rather than treating a short `order` as an error. **Output the
  order as a recommendation** (a
  printed ordered list): the Linear MCP doesn't expose board rank, so
  re-ordering is advisory.
- **Priority-inversion sweep (systematic, every edge).** Read `inversions`:
  each entry (`blocker`, `dependent`, `blocker_priority`, `dependent_priority`)
  is an edge where the blocker is less urgent than what it blocks — the
  dependent can't start until a less-urgent task finishes. Propose making the
  blocker **at least as urgent as the dependent** (its `priority` ≤ the
  dependent's, treating `0`=None as least urgent, so a `0` blocker is raised
  to a real priority). The helper already swept the full edge set restricted
  to open issues on both ends; nothing further to scan here.
- **Concurrency sanity (report-only).** Flag any chain where multiple issues on
  the _same_ `blockedBy` path are simultaneously `In Progress` — a blocker and its
  dependent can't both legitimately be in flight.

### Dimension 4 — Duplicates / overlap

Pairwise-compare titles and descriptions for overlapping scope or a shared code
surface. Propose `duplicateOf` (merge) for true duplicates or a split for
oversized overlap, **quoting the overlapping evidence**. Never auto-merge — every
duplicate is a gated proposal.

## Apply (gated)

For each **approved** finding, call `<linear-mcp>__save_issue` with `id` set to
the issue being changed. Use these fields (per `save_issue`'s schema — the
relation fields are **append-only**, so adding a link never clobbers existing
ones; paired `remove*` fields undo):

| Finding                             | `save_issue` field on the target issue                                                                                                                                                        |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Add a blocker (dependent ← blocker) | `blockedBy: [<blocker id>]` on the dependent                                                                                                                                                  |
| Convert `relatedTo` → real block    | `blocks: [<dependent id>]` + `removeRelatedTo: [<dependent id>]`, both in one `save_issue` call on the blocker (`relatedTo` is symmetric — removing it from the blocker side clears the pair) |
| Remove a stale/cancelled blocker    | `removeBlockedBy: [<id>]`                                                                                                                                                                     |
| Add a weak relation                 | `relatedTo: [<id>]`                                                                                                                                                                           |
| Fix a priority inversion            | `priority: <0–4>` on the blocker                                                                                                                                                              |
| Mark a duplicate                    | `duplicateOf: <canonical id>`                                                                                                                                                                 |

**Hard rules (stop if you're about to break one):**

- **Never** pass `state` — this command does not move issues across workflow
  states (that's `/promote-tasks` / `/do-tasks`'s job, and the `linear-common.md`
  completion rule forbids machine-driven completion).
- **Never** mutate a `completed`- or `canceled`-type issue.
- **Never** auto-resolve a cycle or auto-merge a duplicate — both are reported
  for human action.
- Apply only what the user approved in `/reoptimize-tasks` §5; echo each applied
  mutation back in the final summary, and list anything skipped (unapproved,
  terminal, or cyclic) with the reason.

## Optional deepening — cross-check the source plan

When an issue body references a local plan file (e.g. `Local task:
dev_docs/tasks/<plan>/…md`, common on push-plan'd issues), and that file is
present in the repo, **Read** it to cross-check the tracker ordering against the
plan's `is_blocked_by` edges. A divergence (a plan edge missing from the tracker,
or vice-versa) is a Dimension-1 finding. This is opt-in depth — skip silently if
the files aren't on disk.
