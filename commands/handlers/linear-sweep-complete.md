# Linear handler — /sweep-for-complete flow

Invoked from `/sweep-for-complete [--apply] [--all] [--project <id|name>]`
when `handler: linear` is configured. Finds issues sitting in a **started**-type
state whose **own** linked PR has merged, and completes exactly those by
calling the `linear-complete.md` phase per verified match — the mechanical
transition itself is not re-specified here; see that file's "Caller contract"
and "Steps".

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
2. **Resolve scope.** `--all` and `--project <id|name>` are mutually exclusive
   (one widens, the other narrows) — if both are passed, stop and ask which
   was meant.
   - **`--project <id|name>`** → narrow to exactly **one** project, skipping
     project-list resolution and the Unassigned pass entirely. Resolve the
     value the same way `linear-common.md` "Resolve claim scope" step 1
     resolves a specific pin: match against the configured `linear.projects`
     scopes first (case-insensitive name, or exact id/UUID); if none matches,
     match against the team's live projects via `<linear-mcp>__list_projects`
     (a live, unconfigured project is a valid one-run pin). No match anywhere
     → stop with "`<value>` is not a project in team `<team>`". Step 2 below
     then runs a **single** query with this project's `id` as `projectId`.
   - **`--all`** → skip project resolution entirely and run a **single
     whole-team query** in step 2 below (no `projectId` filter, no Unassigned
     pass — the whole team already covers everything).
   - **Neither flag (default)** → call the **"Resolve configured projects"**
     helper from `linear-common.md` for the configured `linear.projects`
     scopes, **plus the Unassigned bucket — the sweep/reconcile variant**
     (`linear-common.md` "The Unassigned bucket"): membership is `projectId
     == null` **only**, never "any project outside the configured set." This
     is **narrower** than `/do-tasks`'s claim-path Unassigned bucket by
     design — see that section for why. The pass still runs **one** extra
     whole-team query with `projectId` omitted (so the null-project filter
     has something to filter), subject to the same **50-row truncation
     caveat** (the cap applies before the filter, so a full cap with no
     `projectId: null` survivors means "unassigned coverage may be partial,"
     not "no unassigned work"). A project that exists in Linear but isn't
     listed under `linear.projects` is simply **not swept by default** —
     pass `--project <that project>` or `--all` to reach it.

## 2. Find in-flight issues

These are the only issues that can possibly be "merged but not yet
completed" — anything not in a started-type state either hasn't begun or is
already terminal. See `linear-common.md` "In-flight scan" for the read this
section implements (state scope, skinny fields, per-scope resolution) — that
block is the single source of truth; it is not restated here.

**One mechanism: try the fast path, fall back to the floor.** At the top of
this step, if `Bash` is available, attempt the **GraphQL fast-path** first —
`commands/handlers/assets/linear-scan.py`, the same script `linear-common.md`
"In-flight scan" names as one of the read's consumers. On **any** non-zero
exit, or stdout that doesn't parse as the expected `{ meta, issues }` object,
log one debug line (`Fast-path unavailable (<reason>) — falling back to MCP
floor.`) and run the **MCP floor** below instead. There is no separate
`[ -n "$LINEAR_API_KEY" ]` pre-check gating this — `linear-scan.py` itself
exits fast and non-zero when no key is resolvable, so the fallback **is** the
gate (same mechanism as `linear-claim.md` "Find candidates"). A host with no
`Bash` tool falls to the floor by construction.

> **This gate is also the security boundary.** A Linear personal API key
> (what `linear.api_key_ref` points at) is a full-account bearer token —
> anyone holding it can read and write everything the key's owner can in
> Linear. It must **never** be injected into a `claude.ai`/Claude Code
> **cloud** sandbox. Cloud sessions never set `$LINEAR_API_KEY`/
> `$LINEAR_API_KEY_REF`, so even where a cloud host is `Bash`-capable and
> attempts `linear-scan.py`, the script exits non-zero before any GraphQL
> request (no key resolvable) and the run falls to the MCP floor
> (OAuth-scoped, no raw key) by design — the guarantee is that the key is
> never present, not that the script is never invoked. Do not "fix" this by
> wiring the key into cloud config.

### Fast path (GraphQL, via `linear-scan.py`)

1. **Resolve scope, unchanged.** Run "1. Preflight + resolve scope" above as
   normal — `--all`, `--project <id|name>`, and the default
   configured-projects-plus-Unassigned scope list all resolve the same way
   regardless of which path executes the query. The script's own prelude
   resolves the team itself, so this **replaces** the floor's
   `list_workflow_states` call below — do not also call it on this path.

2. **Call the script**, passing every resolved concrete project scope's `id`
   as a repeated `--project` (omit entirely for the whole-team scope, i.e.
   `--all` or the no-projects-configured case; **never** serialize the
   `__unassigned__` sentinel as a `--project` value — same guard as
   `linear-claim.md` "Find candidates") and `--state-type started` (the
   state-type set this sweep needs, per `linear-common.md` "In-flight scan"):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-scan.py" --team "<linear.team>" \
     --project "<scope-1-id>" --project "<scope-2-id>" ... \
     --state-type started
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/linear-scan.py`.
   Parse stdout as the `{ meta: { viewer, team, states }, issues: [ { id,
   identifier, title, url, state, attachments, project } ] }` object described
   in the script's header comment; a parse failure is itself a fallback
   trigger (see above). The **Unassigned bucket's exclusion pass** has no
   equivalent in the script (it has no null-project filter, same limitation
   `linear-claim.md` "Find candidates" step 1 documents for `linear-ready.py`)
   — when the Unassigned bucket applies (1+ projects configured, no `--all`),
   **fall back to the MCP floor** for that scope's pass so unassigned
   in-flight work isn't missed; the configured-project scopes can still run
   fast.

3. **Consume `meta` in place of the equivalent MCP reads.** `meta.states` (an
   array of `{ id, name, type }`) replaces the state-id → type map the floor
   builds via `list_workflow_states` in step 1 of the MCP floor below — cache it the same
   way and resolve the `started`-type ids from it. `meta.viewer` is present in
   the payload for parity with the fast-path pattern `linear-claim.md` uses,
   though this scan performs no assignee/viewer check and so doesn't need it.
   On the fast path, **no** `list_workflow_states` read should run — only the
   script's GraphQL call(s).

4. **Skip the per-issue attachment read.** Each returned issue already carries
   its own PR attachment URL(s) in `attachments` (a plain list of URLs, per
   the script's skinny-fields contract), so **skip "3. Resolve each issue's
   PR" step 1** (the `get_issue` call that reads `attachments` off the live
   issue) for every fast-path issue — feed `issue.attachments` directly into
   that step's GitHub-PR-URL match instead. Steps 3.2–3.3 (the title-search
   and branch-name fallbacks) still apply if no attachment resolves a PR, and
   step 4's `gh pr view` merge-check runs unchanged for every issue regardless
   of which path found it.

### MCP floor (fallback)

Runs whenever the fast path isn't attempted or falls back per the gate above.

1. Call `<linear-mcp>__list_workflow_states` with the team `id` (cache the
   state-id → type map, same cache `linear-claim.md` and `linear-complete.md`
   build). Resolve **every** state id of type `started` — by **type only,
   never display name** (names are user-configurable; see the kanban mapping
   in `linear-common.md`). On a default team that is `In Progress` and
   `In Review`, but a team with renamed or extra started columns is covered
   the same way: any started-type issue can be the case where a PR opened,
   got reviewed, and merged, but nothing moved the Linear issue.
2. Call `<linear-mcp>__list_issues` once per resolved scope from step 1
   above **per started-type state id from step 1 of the MCP floor** — the tool's `state`
   filter takes a **single** value, so a scope with two started states means
   two calls; union the results per scope:
   - `teamId`: resolved team id
   - `projectId`: the scope's `id` (omit for the whole-team scope and for
     `--all`); **never** pass the Unassigned sentinel as a `projectId`. For
     the Unassigned scope, resolve it client-side with the **sweep/reconcile
     predicate** — one whole-team query with `projectId` omitted, then keep
     only issues whose `projectId` is `null` (**not** `linear-claim.md`'s
     wider "null or outside the configured set"). Only the never-pass-the-
     sentinel guard is shared with `linear-claim.md`.
   - `state`: one `started`-type state id from step 1 of the MCP floor per call
   - `includeArchived`: `false`
   - Limit: 50 per scope × state. If a query truncates, note it in the
     report — do not paginate.
3. Union the results across scopes (tag each with its source scope for the
   report; no dedup needed — the `projectId == null` Unassigned pass is
   disjoint from every configured-project scope by construction).

On the **MCP floor**, "3. Resolve each issue's PR" step 1 (the per-issue
`get_issue` attachment read) runs as written below, unchanged.

## 3. Resolve each issue's PR

For each in-flight issue from step 2, resolve its **own** PR in this priority
order — stop at the first **source** that resolves, but keep **every** PR that
source yields. A source can return more than one PR (several `links`
attachments, several bracket-token title hits, several PRs off one branch) —
an issue can legitimately accumulate a stale closed-unmerged PR _and_ a newer
merged one, and picking the first hit could mask the merged one. Step 4
checks **all** of a source's PRs and the issue qualifies if **any** of its own
PRs verified as merged (report that one):

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

For **each** of the issue's resolved PRs (step 3 can yield several), call:

```bash
gh pr view <url-or-number> --json number,url,state,mergedAt
```

(`number` and `url` are captured here so step 6's completion comment has
them from the merge-verification read itself, whichever step-3 fallback
resolved the PR.)

Only `state == "MERGED"` (equivalently, a non-null `mergedAt`) qualifies as a
candidate for step 5.

**Multi-PR precedence.** An issue can carry more than one resolved PR (a
stale one plus a newer one). Classify the whole issue by this precedence,
checked in order:

1. **Any** PR `MERGED` → the issue is a step-5 candidate, regardless of the
   state of its other PRs.
2. Else, **any** PR `OPEN` → leave the issue untouched, bucket `left: open`.
   This is `/reconcile-tasks` row 2's concern — do not add that logic here.
3. Else, **any** resolved PR whose state could **not** be read (the `gh pr
   view` above errored, returned no `state`, or the PR was deleted after step
   3 resolved its URL) → leave the issue untouched, bucket `left: unresolved`.
   The issue does **not** fall through to `left: closed unmerged` on an
   unread PR — a missing read is not a confirmed closed-unmerged read. This
   keeps the classification **fail-closed**: `/reconcile-tasks` row 3 demotes
   only issues in `left: closed unmerged`, so an unreadable PR can never
   trigger a demote.
4. Else (**every** resolved PR is `CLOSED` and unmerged, each read
   successfully) → leave the issue untouched, bucket `left: closed unmerged`.
   `/reconcile-tasks` row 3 reads this exact bucket to demote the issue back
   to Backlog — do not add that logic here either; this file only classifies
   and reports.

Count `left: open`, `left: unresolved`, and `left: closed unmerged`
separately in the report.

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
  from the `number`/`url` fields step 4's `gh pr view` captured for that
  issue.

Complete **only** the issue whose own linked PR merged — never a sibling or a
co-mentioned issue. `linear-complete.md`'s own idempotence check (its step 4)
already makes a re-run of this sweep safe: an issue already in a
`completed`/`canceled` state on a later sweep simply reports "already
complete" and is not written again, so re-running the sweep after a partial
apply is not destructive.

## 7. Report

Print:

- **Scope** — one line stating exactly what this run covered: `scope:
  configured projects (<names>) + Unassigned (project-less only)` (default),
  `scope: whole team (--all)`, or `scope: project <name> only (--project)`.
- **Counts** — `k completed, m open (left), u unresolved (left), s no-PR
  skipped, c closed-unmerged (left)`.
- **Per-issue lines** — identifier, the PR resolved (if any) and its merge
  state, and the outcome (`completed`, `left: open PR`, `left: unresolved`,
  `left: closed unmerged`, `skipped: no PR found`, or `already complete` for
  an idempotent no-op).
- On dry-run, the same table with no outcome column, plus "nothing changed
  (dry-run)."
