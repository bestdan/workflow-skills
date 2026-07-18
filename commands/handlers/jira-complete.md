# jira handler — /complete-task flow

Invoked from `/complete-task <identifier> [--dry-run]` when `handler: jira` is
configured. This is a single **mechanical** phase: given one already-identified
issue, transition it to a `Done`-category status. It does **no PR discovery and
no merge verification** — finding which issues are done and confirming their PR
actually merged is the job of `/sweep-for-complete` (a future jira sweep), which
would call this file's phase directly with the caller contract defined in
"Caller contract" below. If you find yourself adding logic here to search for
merged PRs or open PRs by branch, stop — that belongs one layer up.

**Shared reference:** the Atlassian MCP preflight is `commands/handlers/jira.md`
step 1; `commands/handlers/linear-complete.md` is the structural template this
file mirrors, adapted from Linear's workflow-state `type` to Jira's
`statusCategory`.

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
- **`assume_verified`** (bool, default `false`) — when **true**, the caller (a
  future `/sweep-for-complete` jira path) is asserting it already confirmed the
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

1. **Preflight.** Run the Atlassian MCP preflight exactly as
   `commands/handlers/jira.md` step 1: call
   `<atlassian-mcp>__getAccessibleAtlassianResources` (no args) and confirm a
   resource whose `url` matches `https://<jira.site>`. On either failure,
   **stop** with the same messages ("Jira handler needs the Atlassian MCP.
   Install/connect it in Claude Code settings, then re-run." / "Configured Jira
   site `<site>` is not in your accessible Atlassian resources."). Do not fall
   back to another handler.

2. **Resolve the issue.** Call `<atlassian-mcp>__getJiraIssue` with `cloudId:
   <jira.site>`, `issueIdOrKey: <identifier>`, `fields: ["summary", "status"]`.
   Capture its current `status.name` and `status.statusCategory.key`
   (`new` / `indeterminate` / `done` — the only "category" this flow reads). If
   the identifier doesn't resolve, stop and report "no issue found for
   `<identifier>`".

3. **Idempotence check.** If the issue's current `statusCategory.key` (from
   step 2) is already `done`, **stop here** — do not write:

   `"<IDENTIFIER>" is already complete (<current status name>) — no change made.`

   Jira's `done` category covers both a true "Done" and a "Won't Do"-style
   resolution; either way the issue already sits in a terminal status, so this
   flow reports it and stops rather than trying to distinguish or re-fire a
   transition.

4. **Resolve the target transition.** Call
   `<atlassian-mcp>__getTransitionsForJiraIssue` (`cloudId`, `issueIdOrKey:
   <identifier>`). From the returned `transitions[]`, keep only entries whose
   target status is in the `done` category (`to.statusCategory.key ==
   "done"`). **Resolve by status category, never by display name** — a board
   may rename its terminal status to anything.
   - **None** → stop and report: "`<IDENTIFIER>`: no Done-category transition
     available from `<current status name>` — resolve manually in Jira."
   - **One** → use it.
   - **Several** → some workflows expose more than one terminal transition
     (e.g. `Done` and `Won't Do`). Prefer one whose `to.name` matches
     `done`/`complete`/`resolved` (case-insensitive); if only a `Won't
     Do`-style transition exists (matches `won't do`, `wont do`, `cancel`,
     `reject`, `obsolete`), **do not fire it** — stop and report the available
     transition names so a human can pick, the same way `jira-claim.md`
     declines to guess among ambiguous in-progress transitions.

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

6. **Apply.** One `<atlassian-mcp>__transitionJiraIssue` call:
   - `cloudId`: `<jira.site>`
   - `issueIdOrKey`: `<identifier>`
   - `transition`: `{ id: "<transition-id from step 4>" }`

   Do not touch `assignee` or `labels` here — completion only changes status.

7. **Comment (optional, caller-supplied).** Call
   `<atlassian-mcp>__addCommentToJiraIssue` with `cloudId: <jira.site>`,
   `issueIdOrKey: <identifier>`, `commentBody: <comment_body>` from the caller
   contract, defaulting to `Completed via /complete-task` when the parameter
   was omitted. Skip this step only when the caller passed the explicit
   no-comment signal — `comment_body: ""` (empty string), per the caller
   contract — e.g. a sweep that batches its own summary comment elsewhere
   instead.

8. **Report.** Identifier, old status name → new status name, and whether a
   comment was posted (and its body, if short). On dry-run or idempotent
   no-write, report that instead — never claim a transition that didn't
   happen.
