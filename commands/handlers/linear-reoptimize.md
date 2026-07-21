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

**One mechanism: try the fast path, fall back to the floor.** Same shape as
`linear-claim.md`'s "Find candidates" gate — if `Bash` is available, attempt
the GraphQL fast-path first; on **any** non-zero exit from
`linear-relations.py`, or stdout that doesn't parse as the expected
`{ meta, issues }` object, log one debug line
(`Fast-path unavailable (<reason>) — falling back to MCP floor.`) and run the
**MCP floor** instead. There is no separate `[ -n "$LINEAR_API_KEY" ]`
pre-check gating this — the script itself exits fast and non-zero when no key
is resolvable, so the fallback **is** the gate.

> **This gate is also the security boundary.** A Linear personal API key is a
> full-account bearer token and must **never** be injected into a
> `claude.ai`/Claude Code **cloud** sandbox — see `linear-claim.md`'s "Find
> candidates" security-boundary note, which applies here verbatim. Cloud
> sessions never set `$LINEAR_API_KEY`/`$LINEAR_API_KEY_REF`, so `linear-
> relations.py` exits non-zero before any GraphQL request and the run falls to
> the MCP floor (OAuth-scoped, no raw key) by design.

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

   Omit `--project` entirely for the whole-team scope. Parse stdout as the
   `{ meta: { viewer, team, states }, issues: [...] }` object described in the
   script's header comment; a parse failure is itself a fallback trigger (see
   above). The query is **not** filtered by `state` — terminal (`Done`/
   `Canceled`) issues are included by construction, same as the floor.
3. **Build the graph.** Each `issues[]` entry already carries `description`
   and the derived `blockedBy`/`blocks`/`relatedTo`/`duplicateOf` edge lists
   (see the script's header for the exact `relations`/`inverseRelations`
   derivation). Nodes carry `{id, title, project, priority, estimate, updatedAt,
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

- **Cycles.** Detect any cycle in the `blockedBy` graph. **Report** the members;
  never auto-resolve — a cycle is a human decision (mirrors push-plan §4.3).
- **Stale / never-satisfiable links.** A `blockedBy` pointing at a **`Canceled`**
  issue blocks the dependent forever → propose `removeBlockedBy`. A `blockedBy`
  pointing at a **`Done`** issue is _satisfied_, not a bug → report it as
  satisfied and offer optional cleanup (low priority); do **not** auto-remove.
- **Prose → native reconciliation (the core fix).** For **every** issue, parse
  the description for both signals — exhaustively, not a spot-check:
  - embedded mentions: `<issue id="…" href="…/PRE-NNN/…">` and bare `PRE-NNN`;
  - dependency phrases (match case-insensitively): `unblocks`, `blocked on`,
    `blocked by`, `relies on`, `depends on`, `requires`, `with X in place`,
    `re-scoped per`, `part of … plan`.

  For each referenced issue — **excluding the issue's own id**, so a body that
  restates its own identifier never yields a self-block — **not already covered
  by a native relation**, propose the missing link, classified by phrasing
  strength:
  - strong (`blocked on/by`, `relies on`, `depends on`, `requires`, `unblocks`)
    → **`blockedBy`** (on the dependent) or **`blocks`** (on the blocker);
  - weak (`part of`, `re-scoped per`, a bare mention) → **`relatedTo`**.

  This catches drift like PRE-210's body saying it "unblocks PRE-189" while the
  native relation is only `relatedTo` — propose converting it to a real `blocks`
  edge. Build the diff for **all** issues; do not stop at the load-bearing ones.

### Dimension 2 — Hidden cross-project dependencies

- **Cross-project references.** From the same parse, flag any reference whose
  target issue's `projectId` **differs** from the referrer's and that has **no
  native link** → propose `blockedBy`/`relatedTo` per phrasing strength. These
  are invisible inside any single project view and are the whole point of the
  initiative-scoped run.
- **Semantic inference (judgment, lower-confidence).** Read descriptions for a
  shared file / function / subsystem that implies one issue must precede another
  even when neither cites the other (e.g. two issues both rewriting the same
  `manage_state` write surface, or a schema change that invalidates another
  issue's stated assumption). Propose a link **with the shared evidence quoted**
  and mark it lower-confidence so the user vets it. If the collision implies
  unscoped work (e.g. a migration neither issue owns), say so explicitly rather
  than only proposing an edge.

### Dimension 3 — Re-order & re-prioritize

- **Topological order.** Fold in the proposed Dimension 1–2 edges (the report
  is printed before approval, so label the order **provisional** — it assumes
  those edges are approved; if approval diverges in §5, restate the order over
  the edges that survived), then topologically sort the non-terminal nodes → a
  valid execution order. Within the
  topo constraints, rank by **sequential sort keys** (not a single formula —
  Linear's `priority` is `1`=Urgent…`4`=Low with `0`=None, so arithmetic like
  `priority ÷ estimate` is incoherent): first by urgency (Urgent→Low, with
  `0`=None sorted **last**), then smaller `estimate` first (quick wins), then age
  — the same ranking notion `linear-promote.md` uses. **Output the order as a
  recommendation** (a printed ordered list): the Linear MCP doesn't expose board
  rank, so re-ordering is advisory.
- **Priority-inversion sweep (systematic, every edge).** For **each** `blockedBy`
  edge, compare the blocker's `priority` to the dependent's. A blocker that is
  _less_ urgent (numerically larger non-zero `priority`, or `0`=None) than what it
  blocks is an inversion — the dependent can't start until a less-urgent task
  finishes → propose making the blocker **at least as urgent as the dependent**:
  its numeric `priority` ≤ the dependent's, treating `0`=None as least urgent (so
  a `0` blocker is raised to a real priority). Sweep the full edge set, not a
  sample.
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
