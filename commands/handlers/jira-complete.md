---
description: Handler procedure read by its dispatching command; not run directly.
disable-model-invocation: true
user-invocable: false
---

# jira handler — /complete-task flow

Invoked from `/complete-task <identifier> [--dry-run]` when `handler: jira` is
configured. This is a single **mechanical** phase: given one already-identified
issue, transition it to a `Done`-category status. It does **no PR discovery and
no merge verification** — finding which issues are done and confirming their PR
actually merged is the job of `/sweep-for-complete` (`jira-sweep-complete.md`),
which calls this file's phase directly with the caller contract defined in
"Caller contract" below. If you find yourself adding logic here to search for
merged PRs or open PRs by branch, stop — that belongs one layer up.

**Shared reference:** the Atlassian MCP preflight is `commands/handlers/jira.md`
step 1, and the choice between it and `acli` is `jira-config.md` → "Backend";
`commands/handlers/linear-complete.md` is the structural template this file
mirrors, adapted from Linear's workflow-state `type` to Jira's
`statusCategory`.

Each step below gives an **MCP** and an **acli** form. Use the one for the
resolved backend throughout. Run every `acli` command unsandboxed.

> **MCP namespace.** `<atlassian-mcp>__` is `mcp__claude_ai_Atlassian__` or
> `mcp__atlassian__` depending on the install (see `jira-config.md`) —
> substitute the prefix loaded in your session.

> **Relationship to the tracker execute path.** `jira-claim.md` carries a hard
> rule that the execute path **never** transitions an issue to a `Done`-category
> status — merge (via Jira's GitHub integration or a smart commit) was meant to
> be the only completion signal. That rule is unchanged; this file does not
> touch `/do-tasks`. `/complete-task` exists **because** that auto-close
> integration can be off, unconfigured, or unreachable, so it is the explicit,
> human/sweep-invoked replacement for the transition Jira was supposed to make
> automatically. It is the one place in this plugin that is allowed to move a
> jira issue to a `Done`-category status outside of Jira's own integration.

## Caller contract

This phase takes, in addition to the identifier:

- **`dry_run`** (bool) — print the plan and stop; no write.
- **`backend`** (`acli` / `mcp`, optional) — a backend the caller already
  resolved. When it is set, step 1 skips resolution; when unset, step 1
  resolves it.
- **`assume_verified`** (bool, default `false`) — when **true**, the caller
  (`jira-sweep-complete.md`) is asserting it already confirmed the
  completing PR merged, so this phase **skips the interactive confirmation** in
  step 5 and applies the transition directly. A bare manual `/complete-task`
  never sets this — it always confirms (or, in `--dry-run`, just prints the
  plan). This is the one flag that distinguishes "a human typed this
  identifier" from "the sweep already verified this identifier" — do not let a
  manual invocation set it.
- **`comment_body`** (string, optional) — the completion note to post. A sweep
  would pass something like `Closed by merge of PR #<n>`; a bare manual
  invocation with no note supplied (the parameter **omitted**) defaults to
  `Completed via /complete-task`. To post **no comment at all**, the caller
  passes an **empty string** (`comment_body: ""`) — that is the explicit
  no-comment signal step 7 checks for; omitted and empty are deliberately
  distinct.

## Steps

1. **Preflight.** If the caller passed `backend`, use it and skip to step 2.
   Otherwise resolve it per `jira-config.md` → "Backend", whose MCP branch is
   the preflight in `commands/handlers/jira.md` step 1: call
   `<atlassian-mcp>__getAccessibleAtlassianResources` (no args) and confirm a
   resource whose `url` matches `https://<jira.site>`. On failure, **stop**
   with that section's messages. Do not fall back to another handler.

2. **Resolve the issue.**
   - **MCP:** `<atlassian-mcp>__getJiraIssue` with `cloudId: <jira.site>`,
     `issueIdOrKey: <identifier>`, `fields: ["summary", "status"]`.
   - **acli:** `acli jira workitem view <identifier> --fields summary,status,issuetype --json`.
     It prints the same `fields.status` object. Keep `fields.issuetype.name`
     for step 4.

   Capture its current `status.name` and `status.statusCategory.key`
   (`new` / `indeterminate` / `done` — the only "category" this flow reads). If
   the identifier doesn't resolve, stop and report "no issue found for
   `<identifier>` (it may not exist, or you may lack permission to view it)" —
   Jira returns one indistinguishable error for both cases, so don't claim the
   issue is missing when the real cause may be a project you can't browse.

3. **Idempotence check.** If the issue's current `statusCategory.key` (from
   step 2) is already `done`, **stop here** — do not write:

   `"<IDENTIFIER>" is already complete (<current status name>) — no change made.`

   Jira's `done` category covers both a true "Done" and a "Won't Do"-style
   resolution; either way the issue already sits in a terminal status, so this
   flow reports it and stops rather than trying to distinguish or re-fire a
   transition.

4. **Resolve the target transition** — **by status category, never by display
   name**, since a board may rename its terminal status to anything.

   **MCP:** call `<atlassian-mcp>__getTransitionsForJiraIssue` (`cloudId`,
   `issueIdOrKey: <identifier>`), write the response to a file, and let the
   helper resolve the completing transition:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-resolve-transition.py" \
     --category done --exclude "won.?t do|cancel|reject|obsolete" --prefer 'done|complete|resolved' \
     < <transitions-response.json>
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
   `**/handlers/assets/jira-resolve-transition.py`. Dropping cancellation-style
   candidates **before** counting is what stops a board whose _only_ terminal
   transition is `Canceled` from being quietly completed with the wrong
   resolution. On success the helper prints `<id>\t<to.name>` — use that id in
   step 6.

   **acli:** acli cannot list an issue's transitions; it transitions by target
   status name. So take the candidates from the Done-category statuses that
   issues of the same type in the same project actually use (`<project>` is
   the key before its last `-`, `<type>` is step 2's issue type), and pass the
   helper `--from-search` with the same filters. Narrowing to the type matters
   because Jira assigns workflows per issue type: another type's terminal
   status may be unreachable from this one.

   ```bash
   mkdir -p /tmp/claude && acli jira workitem search --jql 'project = "<project>" AND issuetype = "<type>" AND statusCategory = Done ORDER BY updated DESC' \
     --fields key,status --limit 100 --json > /tmp/claude/jira-done-statuses-<identifier>.json
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-resolve-transition.py" --from-search \
     --category done --exclude "won.?t do|cancel|reject|obsolete" --prefer 'done|complete|resolved' \
     < /tmp/claude/jira-done-statuses-<identifier>.json
   ```

   On success it prints `<status name>\t<status name>`; step 6 uses that name.
   When no `<type>` issue in the project is Done yet there are no candidates,
   and it prints `NONE`; say so in the stop message below ("no `<type>` issue
   in `<project>` is Done yet"). Do not widen the search to other types: their
   statuses are no evidence about this type's workflow. A resolved status may
   still be unreachable from the issue's current status, in which case step
   6's transition is refused. Report the refusal; do not try another status.

   On `NONE`/`AMBIGUOUS` (exit 2), **do not guess** — never fire a transition
   just to satisfy the request, the same way `jira-claim.md` declines to guess
   among ambiguous in-progress transitions. Stop and report: "`<IDENTIFIER>`:
   no Done-category transition resolves to a completion (available:
   `<printed candidate names, or "none" if the helper printed none>`) —
   resolve manually in Jira."

5. **`--dry-run` and confirmation.**
   - **`dry_run: true`** → print the planned transition and **stop, no
     write**:

     ```
     <IDENTIFIER>: <current status name> → <target status name>
     ```

   - **Otherwise, interactive + `assume_verified: false`** (the default manual
     path) → print the same planned-transition line, then confirm via
     `AskUserQuestion` before proceeding to step 6. A "no" answer stops here
     with no write.
   - **`assume_verified: true`** (a verifying sweep's call) → skip the
     confirmation entirely and proceed straight to step 6.

6. **Apply.**
   - **MCP:** one `<atlassian-mcp>__transitionJiraIssue` call — `cloudId:
     <jira.site>`, `issueIdOrKey: <identifier>`, `transition: { id:
     "<transition-id from step 4>" }`.
   - **acli:** `acli jira workitem transition --key <identifier> --status "<status name from step 4>" --yes`.
     Then re-read the issue as in step 2. Unless its `statusCategory.key` is
     now `done`, stop and report `<IDENTIFIER>: transition to <name> was
     refused (<acli's output>) — resolve manually in Jira`, with no comment.
     A non-zero exit is reported the same way.

   Do not touch `assignee` or `labels` here — completion only changes status.

7. **Comment (optional, caller-supplied).** Post `<comment_body>` from the
   caller contract, defaulting to `Completed via /complete-task` when the parameter
   was omitted. Skip this step only when the caller passed the explicit
   no-comment signal — `comment_body: ""` (empty string), per the caller
   contract — e.g. a sweep that batches its own summary comment elsewhere
   instead.
   - **MCP:** `<atlassian-mcp>__addCommentToJiraIssue` with `cloudId:
     <jira.site>`, `issueIdOrKey: <identifier>`, `commentBody: <comment_body>`.
   - **acli:** write the body to `/tmp/claude/jira-comment-<identifier>.txt` (a file, so
     quotes in it need no escaping), then
     `acli jira workitem comment create --key <identifier> --body-file /tmp/claude/jira-comment-<identifier>.txt`.

8. **Report.** Identifier, old status name → new status name, and whether a
   comment was posted (and its body, if short). On dry-run or idempotent
   no-write, report that instead — never claim a transition that didn't
   happen.
