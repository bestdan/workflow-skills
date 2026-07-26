# gh-issue handler — /complete-task flow

Invoked from `/complete-task <identifier>` when `handler: gh-issue` is
configured. This is a single **mechanical** phase: given one already-identified
issue, close it. It does **no PR discovery and no merge verification** — GitHub
already closes the issue natively on merge via `Closes #<n>` in the PR body;
this handler exists only for the case that auto-close didn't fire (no `Closes`
line, or the issue was closed out-of-band and needs to be re-driven). If you
find yourself adding logic here to search for merged PRs or open PRs by
branch, stop — that is not this file's job, and gh-issue has no
`/sweep-for-complete` equivalent that would need it.

**Shared reference:** the status-label vocabulary (`auto-claimed`,
`human-approval-requested`, `blocked`) is defined in the `## List` section of
`commands/handlers/gh-issue.md`; `commands/handlers/linear-complete.md` is the
structural template this file mirrors over the `gh` CLI instead of the Linear
MCP.

**Repo.** If `gh-issue.repo` is set in `dev_docs/tasks/.task-config.yml`, pass
it as `--repo <repo>` on every `gh` call below. Otherwise omit `--repo` to act
on the current repo, matching the create/list/claim/promote flows.

> **Hard rule: this file only closes the one issue it was given.** It never
> searches for other issues, never touches labels or assignees, and never
> re-opens anything. Completion here means exactly one `gh issue close` call
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

2. **Resolve the issue.** Call:

   ```bash
   gh issue view <n> --json number,title,state,stateReason [--repo <repo>]
   ```

   If the identifier doesn't resolve, stop and report "no issue found for
   `<identifier>`".

3. **Idempotence check.** GitHub issues carry a `state` (`OPEN`/`CLOSED`) and,
   when closed, a `stateReason` (`COMPLETED` or `NOT_PLANNED`). If `state` is
   already `CLOSED`, **stop here** — do not write. The two closed reasons get
   **distinct** reports (a not-planned issue is not complete, and this flow
   never silently resurrects one):
   - `stateReason: COMPLETED` (or absent/null on an older closed issue — treat
     as completed) → "`#<n>` is already complete — no change made."
   - `stateReason: NOT_PLANNED` → "`#<n>` is closed as not planned — not
     changing it. Reopen it first (`gh issue reopen <n>`) if you really mean
     to complete it."

4. **`--dry-run` and confirmation.**
   - **`dry_run: true`** → print the planned transition and **stop, no
     write**:

     ```
     #<n>: open → closed (completed)
     ```

   - **Otherwise, interactive + `assume_verified: false`** (the default manual
     path) → print the same planned-transition line, then confirm via
     `AskUserQuestion` before proceeding to step 5. A "no" answer stops here
     with no write.
   - **`assume_verified: true`** → skip the confirmation entirely and proceed
     straight to step 5. No caller sets this today (see "Caller contract"
     above) — kept for parity with `linear-complete.md` so a future gh-issue
     sweep can reuse this phase without a prompt per issue.

5. **Apply.** One `gh issue close` call, honoring the no-comment signal from
   the caller contract:

   ```bash
   gh issue close <n> --reason completed [--comment "<comment_body>"] [--repo <repo>]
   ```

   - `comment_body` omitted → pass `--comment "Completed via /complete-task"`.
   - `comment_body` set to a non-empty string → pass `--comment "<comment_body>"`.
   - `comment_body` set to the explicit no-comment signal (`""`) → omit
     `--comment` entirely; `gh issue close` still closes the issue with no
     comment posted.

   `--reason completed` is what makes this a genuine completion rather than a
   plain close — it is the mechanical analogue of `linear-complete.md` setting
   the `completed`-type state rather than `canceled`.

6. **Report.** Identifier, old state → new state (`open → closed (completed)`),
   and whether a comment was posted (and its body, if short). On dry-run or
   idempotent no-write, report that instead — never claim a transition that
   didn't happen.
