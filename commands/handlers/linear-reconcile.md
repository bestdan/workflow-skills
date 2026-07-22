# Linear handler — /reconcile-tasks flow

Invoked from `/reconcile-tasks [--apply] [--all] [--project <id|name>]` when
`handler: linear` is configured. This is the **bounded reconciler**: it corrects issues sitting in
the wrong state against a fixed, enumerated rule table, not open-ended
judgment about what "looks off." v1 enforces **exactly** these four rows and
nothing else:

| # | Detected drift                                                               | Correction         |
| - | ---------------------------------------------------------------------------- | ------------------ |
| 1 | linked PR **merged**, issue still in a **started**-type state                | → Done             |
| 2 | **open** PR, issue in a **non-started** column (Backlog/Todo)                | → In Review        |
| 3 | linked PR **closed, unmerged**, issue still in a **started**-type state      | → Backlog (demote) |
| 4 | `auto-claimed` + **started**-type, no resolvable PR/branch, idle > threshold | → Backlog (demote) |

Row 1 is scoped to **started**-type issues because it delegates wholesale to
`linear-sweep-complete.md`, whose candidate query covers exactly those. A
merged PR on a **non-started** issue falls between the rows — v1 reports it
as an anomaly (step 3.3) rather than acting. Row 3 shares that same
started-type scan (see "4. Row 3" below) — it does not run a second query.
Row 4 reuses that scan's `no-PR skipped` bucket (see "5. Row 4" below).

> **Bounded-rule-set doctrine.** This table is deliberately closed, not a
> starting point. Do not add a fifth row here, and do not add any other
> speculative rule.
>
> Rows 1 and 2 only ever **promote or complete** an issue; rows 3 and 4 are
> v1's **demotes** — they carry more blast radius than a promote/complete
> (a false-positive read pulls live work back to Backlog), which is exactly
> why they stayed deferred follow-ups until the promote/complete rules (1-2)
> were proven in practice. Row 3 ships with the same narrow guard the
> deferred description specified: act only on a PR that is **definitively**
> closed-unmerged, never on an unresolvable PR state, and never set a
> `completed`/`canceled` state from it. Row 4 ships with its own narrow
> guard: act only when **both** no-PR/no-branch **and** the age threshold
> hold, never on ambiguity. Do not widen either row.

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
`--all` and `--project <id|name>` are mutually exclusive; `--project` narrows
to exactly one project (configured or live/unconfigured) with no Unassigned
pass; `--all` is a single whole-team query with no project resolution;
neither flag (default) uses the configured `linear.projects` scopes **plus**
the Unassigned bucket — the **sweep/reconcile variant** from
`linear-common.md` "The Unassigned bucket" (`projectId == null` only, not
"any project outside the configured set" — narrower than `/do-tasks`'s claim
variant by design, since this reconciler is destructive-adjacent).

All four rows below run against this same resolved scope set.

## 2. Row 1 — merged → Done

Invoke the **`linear-sweep-complete.md`** flow in full (its own "Find
in-flight issues" through "Report" steps), passing `--apply`, `--all`, and
`--project` through unchanged. That file is the **single source of truth** for the
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
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-scan.py" --team "<linear.team>" \
     --project "<scope-id>" --state-type backlog --state-type unstarted
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/linear-scan.py`. Parse stdout as the `{ meta: { viewer, team, states }, issues: [...] }`
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
   — the Unassigned scope is resolved client-side: run one whole-team query
   with `projectId` omitted, then keep only issues whose `projectId` is `null`
   (the **sweep/reconcile variant** of `linear-common.md` "The Unassigned
   bucket", **not** `linear-claim.md`'s wider "null or outside the configured
   set"). Only the never-pass-the-sentinel guard is shared with
   `linear-claim.md`; the membership predicate here is narrower.

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
     entirely — row 3 below only demotes issues that are already in a
     **started**-type state, so a closed-unmerged PR on a backlog/unstarted
     issue isn't that row's territory either. Leave it untouched, no report
     line.

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

## 4. Row 3 — closed-unmerged PR → Backlog demote

This row reuses the **same started-type scan and PR resolution** row 1
already runs via `linear-sweep-complete.md` above — it is not a second
query. That file's "Check merge state" step classifies every in-flight issue
it resolves into exactly one bucket per its precedence rule (a `MERGED` PR
wins regardless of any other PR on the issue; failing that, any `OPEN` PR
wins — the issue is still legitimately in flight; only when **every**
resolved PR is `CLOSED` with `mergedAt == null` does the issue land in
**`left: closed unmerged`**). Row 3 acts on exactly that bucket:

1. **Take row 1's `left: closed unmerged` set as the candidate list** — no
   extra `gh pr view` or `get_issue` call. Each candidate already carries its
   identifier, its resolved PR(s) (`number`/`url`, from the merge-state read),
   and its current state name (captured by the `get_issue` call in
   `linear-sweep-complete.md` "Resolve each issue's PR" step 1).
2. **Guard — act only on a definitive closed-unmerged read.** An issue with
   no resolvable PR URL lands in row 1's "no-PR skipped" bucket, and an issue
   whose PR URL resolved but whose state could not be read (deleted PR, `gh`
   failure, etc.) lands in `left: unresolved` (`linear-sweep-complete.md` "Check
   merge state" precedence step 3) — **neither** feeds `left: closed
   unmerged`, so a demote never fires on an unread or unresolvable PR. That
   fail-closed split is enforced by the shared scan, not re-checked here: row 3
   consumes only `left: closed unmerged`, so there is no separate ambiguous
   case to filter. If a future change to the shared scan ever makes this
   classification uncertain, skip the issue rather than guess.

   > **Hard guard.** Row 1's scan already scopes this bucket to **started**-type
   > issues — never demote a `backlog`/`unstarted` issue (a closed-unmerged PR
   > there is out of scope entirely, see "Row 2" step 3 above) and never set a
   > `completed`/`canceled` state from this row.

## 5. Row 4 — orphaned claim → Backlog demote

Detects an issue left `started` + `auto-claimed` (+ assignee) by a session
that died after the claim write (`linear-claim.md` "Claim the issue" step 6)
but before ever pushing a branch or opening a PR — invisible to `/do-tasks`
(its "Find candidates" gates out anything already `started`+`auto-claimed`)
and invisible to rows 1–3 (they all require a resolvable PR to act). Left
alone it is claimed forever.

1. **Take row 1's `no-PR skipped` bucket as the candidate pool** — the
   started-type issues `linear-sweep-complete.md` step 3 already found no
   `links` attachment, no `[<IDENTIFIER>]` PR, and no PR on `branchName` for.
   No extra scan query — same reuse pattern as row 3's `left: closed
   unmerged` bucket.

2. **Narrow to `auto-claimed`.** The no-PR-skipped bucket carries only the
   "In-flight scan" skinny fields (`id identifier title url state`), never
   labels, so read each candidate's labels — reuse the `<linear-mcp>__get_issue`
   call `linear-sweep-complete.md` "Resolve each issue's PR" step 1 already
   made for it (it fetches `attachments`, which comes back empty for a no-PR
   candidate, on the **same** response as `labels` — no second call). Drop
   any candidate without the `auto-claimed` label; it isn't a live claim to
   begin with.

3. **Guard — no remote branch.** For each remaining candidate, resolve
   `branchName` (already fetched in step 2's `get_issue` call) and check:

   ```bash
   git ls-remote --heads origin "<branchName>"
   ```

   A **non-empty** result means a branch exists — the claim may be
   legitimately mid-work with nothing pushed to review yet is still possible
   only when the branch itself is also absent, so a pushed branch (even with
   no PR) is **not** a row-4 candidate; leave it untouched, no report line
   (it's ordinary in-flight work, not drift). Only an **empty** result
   (no branch) continues to the age guard.

4. **Guard — age threshold.** Resolve `linear.orphan_claim_hours` (see
   `linear-common.md` config block; default `24`, a sensible middle of the
   12–24h range). Compute **last activity** as the more recent of the
   candidate's `updatedAt` (from step 2's `get_issue`) and, if a
   `do-tasks-claim:` comment exists on it (`<linear-mcp>__list_comments`,
   filtered to bodies containing that marker — same filter `linear-claim.md`
   "Claim the issue" step 8 uses), that comment's `createdAt`. A candidate
   qualifies only when **now − last activity > orphan_claim_hours**. This is
   the guard against GC-ing a claim that is legitimately mid-work but
   pre-PR — a fresh claim with no branch yet is normal, not orphaned.

   > **Hard guard.** Skip on any ambiguity — an unreadable `updatedAt`, a
   > `list_comments` failure, or an age within the threshold all mean
   > **do not demote**. Row 4 never sets a `completed`/`canceled` state and
   > never touches an issue already outside a `started`-type state (the
   > no-PR-skipped bucket is scoped to started-type by construction, same as
   > row 1).

## 6. Dry-run (default)

Without `--apply`, print the combined table grouped by rule and stop — change
nothing:

```
→ Done
<IDENTIFIER> — PR #<n> (merged <date>) → Done

→ In Review
<IDENTIFIER> — PR #<n> (open) → In Review

→ Backlog (closed-unmerged)
<IDENTIFIER> — PRs #<n1>, #<n2>, … (all closed, unmerged) → Backlog

→ Backlog (orphaned claim)
<IDENTIFIER> — auto-claimed, no PR/branch, idle <Nh> (> <orphan_claim_hours>h) → Backlog
```

Unlike rows 1 and 2 — where the single acting PR (the merged one, the open
one) is unambiguous — a row-3 candidate qualifies only because **every**
resolved PR is closed unmerged (§4), so list them **all** on the line rather
than an arbitrary one. A row-4 line reports the idle duration against the
configured threshold, not a PR (there is none).

followed by the left/skipped lines from row 1 (open PRs left, no-PR issues
skipped — closed-unmerged PRs now feed row 3 and orphaned claims now feed row
4, instead of being left), row 2's skipped/anomaly lines, and an explicit
"nothing changed (dry-run)."

## 7. Apply (`--apply` only)

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

- **Row 3** — for each row-3 candidate, mirror `linear-claim.md` "Bail"'s
  release mechanics (same shape, different caller).

  **First, re-verify the PR state (TOCTOU guard).** Row 3's candidate list is
  the `left: closed unmerged` bucket from row 1's scan, which may have been
  read many issues earlier in a long run. A demote is the one
  irreversible-adjacent transition in this command, so before mutating,
  re-run `gh pr view <url> --json number,url,state,mergedAt` on **each** of the
  issue's resolved PRs and re-apply the merge-state precedence. **Skip the
  issue** (no mutation) and report it as `left: revalidated (<new state>)` if
  **any** resolved PR now reads `OPEN` or `MERGED`, or if **any** read fails —
  only a set that is still, definitively, every-PR-closed-unmerged proceeds.
  This is a deliberate extra `gh` call beyond §4's "reuse row 1's read" rule,
  scoped to apply-time for the demote row alone, so a PR merged or reopened
  between the scan and the mutation can never pull freshly-completed or
  live work back to Backlog. Then, for a candidate that survives:
  1. Resolve the `human-approval-requested` label id (`<linear-mcp>__list_issue_labels`
     with `teamId`; create it if absent, same pattern as `auto-claimed` in
     `linear-claim.md` "Claim the issue" step 1).
  2. Resolve the team's default `backlog`-type state id from the cached
     state map (prefer the one named `Backlog`).
  3. **Read the issue's current labels** — **one** `<linear-mcp>__get_issue`
     call for the candidate's UUID, to capture its live label id set. This is
     the one place row 3 spends a read beyond the shared scan: §4 step 1's
     "no extra call" rule governs **candidate selection**, not apply. The
     `save_issue` below **replaces** the label set, and the existing labels
     are not in hand at this point — `linear-scan.py`'s skinny fields carry
     no `labels`, and the fast path skips the per-issue `get_issue`
     (`linear-sweep-complete.md` §2) — so without this read the demote would
     clobber every label except `human-approval-requested`. (`linear-claim.md`
     Bail needs no such read only because the claiming session already
     fetched labels in its read-before-write guard; row 3 never did.)
  4. **One** `<linear-mcp>__save_issue` call: `id` = the issue's UUID,
     `state` = the backlog state id, `labels` = the labels from step 3
     minus `auto-claimed` plus `human-approval-requested` (the call replaces
     the label set — include the existing ones), `assignee` = `null`.
  5. **One** `<linear-mcp>__save_comment` call (`issueId` = the issue's UUID)
     noting the demote. Row 3 candidates have **every** resolved PR closed
     unmerged (§4) — there is no single acting PR, so list them **all**:

     ```
     Moved back to Backlog by /reconcile-tasks — all resolved PRs (#<n1>
     <url1>, #<n2> <url2>, …) were closed without merging while the issue sat
     in <old state name>. Flagged for human review.
     ```

  Unlike `linear-claim.md`'s Bail, row 3 does **not** delete any
  `do-tasks-claim:` comment — that cleanup is row 4's job, not row 3's; an
  issue reaching row 3 has a resolvable PR (that's how it got there), so it
  isn't row 4's territory anyway, but the two rows stay independent by
  design. This row only corrects state/labels/assignee.

- **Row 4** — for each row-4 candidate, the same release shape as
  `linear-claim.md` "Bail" and row 3 above, minus the PR-state re-verify
  (there is no PR to re-check — the re-verify that matters here is the
  no-branch guard, already re-confirmed live in step 5.3 above since row 4's
  candidate list is built fresh each run, not carried over from an earlier
  scan):
  1. Resolve the `human-approval-requested` label id (same pattern as row 3
     step 1).
  2. Resolve the team's default `backlog`-type state id from the cached
     state map (prefer the one named `Backlog`).
  3. **One** `<linear-mcp>__save_issue` call: `id` = the issue's UUID,
     `state` = the backlog state id, `labels` = the labels already read in
     step 5.2 above minus `auto-claimed` plus `human-approval-requested`
     (the call replaces the label set — include the existing ones),
     `assignee` = `null`.
  4. **Delete the stale `do-tasks-claim:` comment** if step 5.4 found one
     (`<linear-mcp>__delete_comment`) — this is the one cleanup step row 3
     explicitly does not do; row 4 exists specifically to GC this orphan.
  5. **One** `<linear-mcp>__save_comment` call (`issueId` = the issue's UUID)
     noting the GC:

     ```
     Moved back to Backlog by /reconcile-tasks — claimed but no PR or branch
     appeared within <orphan_claim_hours>h. Flagged for human review.
     ```

## 8. Report

Print, grouped by rule:

- **Scope** — one line stating exactly what this run covered, same wording as `linear-sweep-complete.md` "Report": `scope: configured projects (<names>) + Unassigned (project-less only)` (default), `scope: whole team (--all)`, or `scope: project <name> only (--project)`.
- **Counts** — `k completed (row 1), j moved to In Review (row 2), d demoted (row 3), o GC'd (row 4)`,
  plus row 1's own remaining left/skipped counts (`left: open`, `left:
  unresolved`, `left: closed unmerged` that were **not** demoted, no-PR
  skipped that were **not** GC'd by row 4), row 2's skipped/anomaly count,
  row 3's `r revalidated-skip` count (candidates dropped by the TOCTOU
  re-verify above), and row 4's own skip counts (dropped for a live branch,
  dropped for being within the age threshold).
- **Per-issue lines** — identifier, the PR resolved and its state, the rule
  that fired, and the outcome (or the planned transition on dry-run). For
  **row 3**, list **every** resolved PR (all are closed unmerged — there is no
  single acting PR to name, unlike rows 1/2). For **row 4**, there is no PR to
  name — report the idle duration against `orphan_claim_hours` instead.
- **Anomaly lines** — the row 1/row 2 scope-gap case from step 3.3 above,
  called out explicitly rather than silently dropped.
- Never write a bare `<TEAM>-NNN` token in this report or anywhere else in
  this file — see `linear-claim.md` "PR body magic words" for why a bare id
  auto-closes on merge; every mention here is wrapped in a non-closing phrase
  (`related to <id>`) for the same reason, even though this is a report and
  not a PR body — the same habit is what keeps the two from ever drifting
  apart.
- On dry-run, the same table with no outcome column, plus "nothing changed
  (dry-run)."
