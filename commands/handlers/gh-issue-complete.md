# gh-issue handler — /complete-task flow

Invoked from `/complete-task <identifier>` when `handler: gh-issue` is
configured. This is a single **mechanical** phase: given one already-identified
issue, write its completed state — the close and the rung-free label set in one
PATCH. It does **no PR discovery and no merge verification** — GitHub
already closes the issue natively on merge via `Closes #<n>` in the PR body;
this handler exists only for the case that auto-close didn't fire (no `Closes`
line, or the issue was closed out-of-band and needs to be re-driven). If you
find yourself adding logic here to search for merged PRs or open PRs by
branch, stop — that is not this file's job, and gh-issue has no
`/sweep-for-complete` equivalent that would need it.

**Shared reference:** the label vocabulary and its invariants live in
`commands/handlers/assets/labels.yml`; the sections they drive are the `## List`
table in `commands/handlers/gh-issue.md`.
`commands/handlers/linear-complete.md` is the structural template this file
mirrors over the `gh` CLI instead of the Linear MCP.

**Repo.** If `gh-issue.repo` is set in the **merged** config — the committed
`dev_docs/tasks/.task-config.yml` overlaid with the optional
`.task-config.local.yml` (per `commands/task-config.md` "Local override"),
already resolved by `/complete-task`'s "Resolve the handler" step — pass it as
`--repo <repo>` on every `gh` call below. Otherwise omit `--repo` to act on the
current repo, matching the create/list/claim/promote flows.

> **Hard rule: this file only completes the one issue it was given.** It never
> searches for other issues, never touches an assignee, and never re-opens
> anything. Completion here means exactly one `gh-issue-state.py --done` call
> (or none, on dry-run / idempotent no-op).

## Caller contract

This phase takes, in addition to the identifier:

- **`dry_run`** (bool) — print the plan and stop; no write.
- **`assume_verified`** (bool, default `false`) — reserved for parity with
  `linear-complete.md`'s caller contract (a future gh-issue sweep would set
  this to skip the interactive confirmation). gh-issue has no sweep today, so
  a bare manual `/complete-task <n>` never sets it — it always confirms (or,
  in `--dry-run`, just prints the plan).
- **`comment_body`** (string, optional) — the completion note to post. Omitted
  → defaults to `Completed via /complete-task`. To post **no comment at all**,
  the caller passes an **empty string** (`comment_body: ""`) — that is the
  explicit no-comment signal step 5 checks for; omitted and empty are
  deliberately distinct.

## Steps

1. **Preflight auth.** Run `gh auth status 2>&1`. If it fails, use the same
   handling as `gh-issue.md` step 1: TLS/x509/certificate → likely the sandbox
   blocking keychain access, tell the user to re-run outside sandbox mode;
   otherwise report the auth failure. Either way **stop** — do not fall back
   to another handler.

2. **Resolve the issue.** Normalize the identifier first — strip a leading `#`
   so both `142` and `#142` work — and pass it quoted; an unquoted `#`-leading
   token turns the rest of the shell line into a comment. Then call:

   ```bash
   gh issue view "<n>" --json number,title,state,stateReason,labels [--repo <repo>]
   ```

   `labels` rides along on the same read because step 5 needs the issue's
   current `prio:`/`est:` labels to carry them through the write — see there.

   If `gh` reports the issue does not exist, stop and report "no issue found
   for `<identifier>`". On any **other** non-zero exit — permission, wrong
   repo, network, rate limit — stop and surface `gh`'s own error verbatim; do
   not report it as not-found.

3. **Idempotence check.** GitHub issues carry a `state` (`OPEN`/`CLOSED`) and,
   when closed, a `stateReason` (`COMPLETED`, `NOT_PLANNED`, or `DUPLICATE`).
   If `state` is already `CLOSED`, **stop here** — do not write. The closed
   reasons get **distinct** reports (a not-planned or duplicate issue is not
   complete, and this flow never silently resurrects one):
   - `stateReason: COMPLETED` (or absent/null on an older closed issue — treat
     as completed) → "`#<n>` is already complete — no change made."
   - `stateReason: NOT_PLANNED` → "`#<n>` is closed as not planned — not
     changing it. Reopen it first (`gh issue reopen "<n>"`) if you really mean
     to complete it."
   - `stateReason: DUPLICATE` → "`#<n>` is closed as a duplicate — not
     changing it. Reopen it first (`gh issue reopen "<n>"`) if you really mean
     to complete it."

4. **`--dry-run` and confirmation.**
   - **`dry_run: true`** → print the planned transition and **stop, no
     write**:

     ```
     #<n>: open → closed, rungs removed (keeping prio:1, est:3)
     ```

   - **Otherwise, interactive + `assume_verified: false`** (the default manual
     path) → print the same planned-transition line, then confirm via
     `AskUserQuestion` before proceeding to step 5. A "no" answer stops here
     with no write.
   - **`assume_verified: true`** → skip the confirmation entirely and proceed
     straight to step 5. No caller sets this today (see "Caller contract"
     above) — kept for parity with `linear-complete.md` so a future gh-issue
     sweep can reuse this phase without a prompt per issue.

5. **Apply.** One call — the schema writer with `--done`:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-state.py" \
     --repo "<repo>" --issue "<n>" --labels "<prio:…>,<est:…>" --done --apply
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
   `**/handlers/assets/gh-issue-state.py`. The helper's `--repo` is required,
   so when `gh-issue.repo` is unset resolve the current repo with
   `gh repo view --json nameWithOwner --jq .nameWithOwner` rather than omitting
   the flag.

   **`--labels` is the complete managed set the closed issue should end with**,
   assembled from the labels step 2 read: keep its `prio:` and `est:` labels
   verbatim, and pass **no** `status:` or `auto:` rung. That is the shape
   `--done` asserts — "done" _is_ the absence of a `status:` rung, and `auto:`
   is a live instruction to a scheduler, so leaving one on a closed issue is a
   hazard rather than information, while `prio:`/`est:` are facts about the work
   and stay useful afterwards (`commands/handlers/assets/labels.yml`). The
   validator rejects a rung under `--done` before any network call, so a set
   copied from the promote flow fails loudly instead of closing an issue that
   still advertises itself to automation. An issue carrying neither `prio:` nor
   `est:` passes `--labels ""`. Every label outside the four managed namespaces
   — `follow-up`, `blocked`, anything a human added — is carried forward by the
   helper and must not be listed.

   **There is no separate `gh issue close` on this path.** The close rides in
   the same PATCH as the labels, so no window exists in which the issue is
   closed while still carrying rungs, or rung-free while still open. Adding a
   `gh issue close` would reopen that window and double-write the state.

   **Post the completion comment separately, and only after the write
   succeeds**, honoring the no-comment signal from the caller contract:

   ```bash
   gh issue comment "<n>" --body-file "<path>" [--repo <repo>]
   ```

   - `comment_body` omitted → post `Completed via /complete-task`.
   - `comment_body` set to a non-empty string → post it.
   - `comment_body` set to the explicit no-comment signal (`""`) → post
     nothing; skip the call entirely.

   Write the body to a temp file and pass `--body-file` rather than inlining it
   into `--body` — the same shell-quoting reason as `gh-issue.md` step 2, and
   here `gh issue comment` does take a `--body-file`. The comment follows the
   write because it reports a completion that has already happened; posting it
   first would leave a note claiming a transition that a failed write never
   made.

   > **`state_reason` is not part of this schema.** The old `gh issue close
   > --reason completed` set it explicitly; the writer's PATCH carries `state`
   > and `labels` only, so `state_reason` is left to whatever GitHub records
   > for a close it was not given one for. Step 3 still **reads** `stateReason`
   > to tell an already-completed issue from one closed as not-planned or
   > duplicate, and that read is unaffected — but do not describe this step as
   > setting it.

6. **Report.** Identifier, old state → new state (`open → closed`), the
   `prio:`/`est:` labels carried through,
   and whether a comment was posted (and its body, if short). On dry-run or
   idempotent no-write, report that instead — never claim a transition that
   didn't happen.
