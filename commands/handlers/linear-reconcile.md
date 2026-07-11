# Linear handler — /reconcile-tasks flow

Invoked from `/reconcile-tasks [--apply] [--all]` when `handler: linear` is
configured. This is the **bounded reconciler**: it corrects issues sitting in
the wrong state against a fixed, enumerated rule table, not open-ended
judgment about what "looks off." v1 enforces **exactly** these two rows and
nothing else:

| # | Detected drift                                                | Correction  |
| - | ------------------------------------------------------------- | ----------- |
| 1 | linked PR **merged**, issue still in a **started**-type state | → Done      |
| 2 | **open** PR, issue in a **non-started** column (Backlog/Todo) | → In Review |

Row 1 is scoped to **started**-type issues because it delegates wholesale to
`linear-sweep-complete.md`, whose candidate query covers exactly those. A
merged PR on a **non-started** issue falls between the rows — v1 reports it
as an anomaly (step 3.3) rather than acting.

> **Bounded-rule-set doctrine.** This table is deliberately closed, not a
> starting point. Two more rows are known, deferred follow-ups — **do not add
> them here**, and do not add any other speculative rule:
>
> - A **closed-unmerged PR demoting its issue back to Backlog** — related to
>   PRE-407.
> - **Orphaned-claim GC** (a stale `auto-claimed` label/assignee with no live
>   claim behind it) — related to PRE-408.
>
> Both are tracked in Backlog. Both rows 1 and 2 above only ever **promote or
> complete** an issue — never demote it — so a mistaken read under this v1
> rule set can leave an issue ahead of where it should be, but it can never
> retire live work that isn't actually done. Adding a demoting rule changes
> that safety property; it is out of scope here.

**Shared reference:** see `linear-common.md` for connection details, the
config schema (`linear.projects`, the Unassigned bucket), and the kanban
state-type mapping (rows here read `backlog`/`unstarted`/`started` state
**types**, never display names).

## 1. Preflight + scope

Run the shared preflight from `linear-common.md` (call
`<linear-mcp>__list_teams`, match `<linear.team>`, capture the team `id`). Same
failure messages.

Resolve scope exactly as `linear-sweep-complete.md` "Preflight + resolve
scope" step 2 does — do not duplicate the mechanics here, read that file:

- **`--all`** → a single whole-team query, no project resolution.
- **No `--all`** → the configured `linear.projects` scopes (via
  `linear-common.md` "Resolve configured projects") **plus** the Unassigned
  bucket, composed exactly as `linear-claim.md` "Find candidates" does,
  including its whole-team-query exclusion rule and its 50-row truncation
  caveat.

Both rows below run against this same resolved scope set.

## 2. Row 1 — merged → Done

Invoke the **`linear-sweep-complete.md`** flow in full (its own "Find
in-flight issues" through "Report" steps), passing `--apply` and `--all`
through unchanged. That file is the **single source of truth** for the
completion rule — do not re-specify PR resolution, merge verification, or the
`linear-complete.md` apply call here. Fold its per-issue candidate lines and
counts into this command's combined report under the `→ Done` heading.

**Row 1 needs no separate wiring in this file.** Any GraphQL fast-path
`linear-sweep-complete.md` uses for its own in-flight scan is entirely that
file's concern — row 1 inherits it automatically by delegating wholesale, the
same way it inherits everything else about the completion rule. Do not
duplicate a fast-path gate here for row 1.

## 3. Row 2 — open PR, wrong column → In Review

The read this row needs — every `backlog`/`unstarted`-type issue plus its PR
attachment — is defined once in `linear-common.md` "In-flight scan"; this
section only wires that read behind a try-script-then-floor gate. Do not
restate the read's field list or state-scope rule here.

**One mechanism: try the fast path, fall back to the floor.** For each scope
resolved in "1. Preflight + scope" above, attempt the **GraphQL fast-path**
first via `linear-scan.py`. On **any** non-zero exit, or stdout that doesn't
parse as the expected JSON object, log one debug line (`Fast-path unavailable
(<reason>) — falling back to MCP floor.`) and run the **MCP floor** (steps 1–2
below) for that scope instead. There is no separate mechanism and no
independent pre-check gating this — `linear-scan.py` itself exits fast and
non-zero when no key is resolvable, so the fallback **is** the gate, same as
`linear-claim.md` "Find candidates."

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
> wiring the key into cloud config. See `linear-claim.md` "Find candidates"
> for the full account-key setup (`linear.api_key_ref`, the launching-terminal
> `export`, the headless `$OP_SERVICE_ACCOUNT_TOKEN` path) — it is identical
> here.

### Fast path (GraphQL, via `linear-scan.py`)

1. **Call the script once per resolved scope**, passing each scope's real
   project `id` as `--project` (omit for the whole-team scope). **Never**
   pass the synthetic `"__unassigned__"` sentinel as `--project` —
   `linear-scan.py` has no Unassigned-bucket exclusion mode, same as
   `linear-ready.py`; if the resolved scope set includes the Unassigned
   bucket, fall back to the MCP floor for that scope.

   ```bash
   python3 commands/handlers/assets/linear-scan.py --team "<linear.team>" \
     --project "<scope-id>" --state-type backlog --state-type unstarted
   ```

   Parse stdout as the `{ meta: { viewer, team, states }, issues: [...] }`
   object described in the script's header comment; a parse failure is
   itself a fallback trigger (see above).

2. **Consume `meta.states` in place of `list_workflow_states`.**
   `meta.states` (an array of `{ id, name, type }`) replaces the state-id →
   type map step 1 below builds via `list_workflow_states` — cache it the
   same way; step 4's target-state resolution reads this cache on either
   path.

3. **Skip the per-issue attachment read.** Each returned issue already
   carries its `attachments` URL list (the "In-flight scan" skinny fields),
   so resolving its PR needs none of step 2 below's `get_issue` →
   title-search → `branchName` fallback chain — take **every** `attachments`
   entry that matches a GitHub PR URL (`github.com/.../pull/<n>`), **not just
   the first**. This mirrors `linear-sweep-complete.md` step 3's multi-PR
   rule: an issue can accumulate a stale closed-unmerged PR **and** a newer
   open one, so picking only the first hit could mask the open PR that makes
   it a row-2 candidate. An issue with **no** matching attachment is **not a
   row-2 candidate** on this path; skip it silently. Note this is a **narrower
   skip** than the floor's step 2 below: the fast path resolves only
   attachment-linked PRs, so an issue whose open PR is discoverable **solely**
   by `[<IDENTIFIER>]` title-match or `branchName` (no Linear attachment) is
   picked up only when its scope falls back to the MCP floor — an accepted
   trade-off (the tracked claim/open flow always writes the PR attachment, so
   this is an edge case), not fixed by expanding `linear-scan.py`.

4. **Continue at step 3 below** with the resolved issue + PR list — the PR
   open/merged check, target-state resolution, and hard guard are identical
   on both paths.

### MCP floor (fallback)

Runs whenever the fast path isn't attempted or falls back per the gate above,
per scope.

1. **Query non-started issues.** Call `<linear-mcp>__list_workflow_states`
   with the team `id` (reuse the cache if row 2 shares a session with row 1).
   Resolve **every** state id of type `backlog` and every state id of type
   `unstarted` — **by type only, never display name** (names are
   user-configurable; this doctrine is load-bearing — see `linear-common.md`
   "Kanban mapping"). For each resolved scope from step 1, call
   `<linear-mcp>__list_issues` **once per scope per state id** — the MCP
   `state` filter is single-valued, so a team with two `backlog`-type states
   and one `unstarted`-type state means three calls per scope; union the
   results per scope, same pattern as `linear-sweep-complete.md` step 2.2.
   **Never** pass the synthetic `"__unassigned__"` sentinel as a `projectId`
   — the Unassigned scope is covered by the client-side exclusion pass
   (`projectId` omitted, keep only issues outside the configured projects),
   exactly as `linear-claim.md` "Find candidates" does.

2. **Resolve each issue's PR(s).** Use the **same priority order** as
   `linear-sweep-complete.md` step 3 (`links` attachment → `[<IDENTIFIER>]`
   title match → `branchName`) — reference that step, do not duplicate it —
   **including its multi-PR rule**: keep **every** PR the resolving source
   yields, not just the first, so an issue's full PR set reaches "Both paths"
   step 3 below (an issue can carry a stale closed PR **and** a newer open
   one). No PR resolves at all → **not a row-2 candidate**; skip silently (a
   backlog issue with no PR is just normal backlog, not drift).

### Both paths

3. **Check the PR's state.** Both paths resolve the issue's **own** PR(s) —
   one or more (an issue can accumulate several attachments). Check **each**
   resolved PR:

   ```bash
   gh pr view <url-or-number> --json number,url,state
   ```

   Act if **any** of the issue's PRs is `state == "OPEN"` (the same "check
   **all** of a source's PRs" rule as `linear-sweep-complete.md` step 4).
   The bullets below cover an issue whose PRs are **all** non-open:

   - A **merged** PR on a non-started issue is **row 1's** business, not
     row 2's — `linear-sweep-complete.md`'s own scope only covers
     started-type issues, so this is an edge case neither rule corrects in
     v1. **Do not act on it here.** Call it out as an anomaly line in the
     report instead: `<IDENTIFIER> — PR #<n> merged but issue sits in a
     non-started column — not corrected in v1, see row 1/row 2 scope gap`.
   - A **closed, unmerged** PR on a non-started issue is out of scope
     entirely (that's the deferred demote rule, related to PRE-407) — leave
     it untouched, no report line.

4. **Resolve the target state.** Prefer the `started`-type state named
   (case-insensitive) `In Review`; if none exists, use the team's default
   `started`-type state (typically `In Progress`). This is a **name-based
   preference** among already-`started`-type candidates — the same pattern
   `linear-claim.md` "Move to review on PR open" step 1 uses — not a
   name-based filter of which issues qualify (the query in step 1 above stays
   type-only).

   > **Hard guard.** Never touch an issue already in a `started`-type state
   > (it is already correct where it is — row 2 only applies to
   > `backlog`/`unstarted` issues) and never set a `completed`/`canceled`
   > state from this row.

## 4. Dry-run (default)

Without `--apply`, print the combined table grouped by rule and stop — change
nothing:

```
→ Done
<IDENTIFIER> — PR #<n> (merged <date>) → Done

→ In Review
<IDENTIFIER> — PR #<n> (open) → In Review
```

followed by the left/skipped lines from row 1 (open PRs left, closed-unmerged
PRs left, no-PR issues skipped), row 2's skipped/anomaly lines, and an
explicit "nothing changed (dry-run)."

## 5. Apply (`--apply` only)

- **Row 1** — via `linear-sweep-complete.md`'s own apply path (step 6, which
  calls the `linear-complete.md` phase with `assume_verified: true` per
  verified match). Do not re-implement it here.
- **Row 2** — for each row-2 candidate, **one** `<linear-mcp>__save_issue`
  call setting `id` to the issue's UUID and `state` to the resolved
  In-Review/started state id from step 3.4 above (do not touch `labels` or
  `assignee`), followed by **one** `<linear-mcp>__save_comment` call (with
  `issueId` = the issue's UUID and `body` = the text below — same parameter
  names as `linear-claim.md` and `linear-complete.md`):

  ```
  Moved to In Review by /reconcile-tasks — open PR #<n> (<url>) found while
  the issue sat in <old state name>.
  ```

## 6. Report

Print, grouped by rule:

- **Counts** — `k completed (row 1), j moved to In Review (row 2)`, plus row
  1's own left/skipped counts and row 2's skipped/anomaly count.
- **Per-issue lines** — identifier, the PR resolved and its state, the rule
  that fired, and the outcome (or the planned transition on dry-run).
- **Anomaly lines** — the row 1/row 2 scope-gap case from step 3.3 above,
  called out explicitly rather than silently dropped.
- **Explicitly note what v1 does not enforce** — the closed-unmerged →
  Backlog demote and orphaned-claim GC rules are **not** applied by this
  command; they are related to PRE-407 and related to PRE-408 respectively,
  both still in Backlog. Never write a bare `<TEAM>-NNN` token in this report
  or anywhere else in this file — see `linear-claim.md` "PR body magic words"
  for why a bare id auto-closes on merge; every mention here is wrapped in a
  non-closing phrase (`related to <id>`) for the same reason, even though
  this is a report and not a PR body — the same habit is what keeps the two
  from ever drifting apart.
- On dry-run, the same table with no outcome column, plus "nothing changed
  (dry-run)."
