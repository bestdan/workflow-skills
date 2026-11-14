---
description: Handler procedure read by its dispatching command; not run directly.
disable-model-invocation: true
user-invocable: false
---

# Linear handler — /complete-task flow

Invoked from `/complete-task <identifier>` when `handler: linear` is configured.
This is a single **mechanical** phase: given one already-identified issue,
transition it to the team's `completed`-type workflow state. It does **no PR
discovery and no merge verification** — finding which issues are done and
confirming their PR actually merged is the job of `/sweep-for-complete` (a
future command), which calls this file's phase directly with the caller
contract defined in "Caller contract" below. If you find yourself adding logic
here to search for merged PRs or open PRs by branch, stop — that belongs one
layer up.

**Shared reference:** see `linear-common.md` for connection details, the
preflight pattern, and the kanban → state-type mapping this file reads against.

> **Relationship to the tracker execute path.** `linear-claim.md` and
> `do-tasks.md` carry a hard rule that the tracker path **never** moves an issue
> to a `completed`/`canceled` state — merge (via Linear's GitHub integration)
> was meant to be the only completion signal. That rule is unchanged; this file
> does not touch `/do-tasks`. `/complete-task` exists **because** that
> auto-close integration can be off or unreachable, so it is the explicit,
> human/sweep-invoked replacement for the transition Linear was supposed to make
> automatically. It is the one place in this plugin that is allowed to move an
> issue to a `completed`-type state outside of Linear's own integration.

## Caller contract

This phase takes, in addition to the identifier:

- **`dry_run`** (bool) — print the plan and stop; no write.
- **`assume_verified`** (bool, default `false`) — when **true**, the caller
  (`/sweep-for-complete`) is asserting it already confirmed the completing PR
  merged, so this phase **skips the interactive confirmation** in step 5 and
  applies the transition directly. A bare manual `/complete-task <id>` never
  sets this — it always confirms (or, in `--dry-run`, just prints the plan).
  This is the one flag that distinguishes "a human typed this identifier" from
  "the sweep already verified this identifier" — do not let a manual invocation
  set it.
- **`comment_body`** (string, optional) — the completion note to post. The
  sweep passes something like `Closed by merge of PR #<n>`; a bare manual
  invocation with no note supplied (the parameter **omitted**) defaults to
  `Completed via /complete-task`. To post **no comment at all**, the caller
  passes an **empty string** (`comment_body: ""`) — that is the explicit
  no-comment signal step 7 checks for; omitted and empty are deliberately
  distinct.

## Fast path (GraphQL, via `linear-complete.py`)

Try this first, behind the shared gate in `linear-common.md` "Fast-path /
MCP-floor gate" — including its "Key resolution" bridge on the same command.
The script runs steps 1–4, 6 and 7 below over the API with the personal key,
so it also works in a session with no Linear MCP loaded:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-complete.py" \
  --team "<linear.team>" --issue "<IDENTIFIER>" [--comment "<comment_body>"] [--dry-run]
```

Pass `--comment` with the caller's `comment_body` — omit it for the default
note, and pass `--comment ""` for the explicit no-comment signal. Step 5 still
applies: with `dry_run: true`, or for a manual run that must confirm, run it
with `--dry-run` first, print the planned line from its `from`/`to`, confirm,
and only then run it again without the flag. `assume_verified: true` runs it
once, without `--dry-run`.

Read the one JSON object it prints. `result` is `completed`, `planned`
(dry run), or one of step 4's two no-write reports: `already-complete` and
`canceled`. Report those exactly as step 4 and step 8 say. `comment` is
`posted`, `skipped`, `failed` or `unknown`, and on the last two the state
**has** moved. On `failed`, Linear refused the comment: post the same body
with `<linear-mcp>__save_comment` when that tool is loaded, and otherwise
report the completion with `comment_error`. On `unknown`, the request may have
landed, so do not repost. Report the completion with `comment_error` and say
the comment may be missing. Never re-run the script to retry a comment: the
re-run finds the issue already complete and posts nothing.

**A non-zero exit means no move was confirmed.** It does not prove nothing
happened, since a request can apply while its response is lost. When the move
call fails, the script re-reads the issue and counts it as moved only when it
sits in the exact state the move targeted.
Falling to the MCP steps below stays safe because step 4 refuses an issue that
is already terminal. One case loses the comment: the move applied, but both
its response and the re-read failed. Step 4 then finds the issue complete and
stops, and the script never posted its comment. So when step 4 reports an
issue already complete right after a script failure, say the completion
comment may be missing. Log the gate's one debug line and run them. When no
Linear MCP is loaded either, stop and report the script's stderr reason. There
is no third route.

## Steps (MCP floor)

1. **Preflight.** Run the shared preflight from `linear-common.md` (call
   `<linear-mcp>__list_teams`, match `<linear.team>`, capture the team `id`). On
   failure, stop with the same error messages.

2. **Resolve the issue.** Call `<linear-mcp>__get_issue` with the given
   identifier. Capture its UUID `id` and current `state` (`id`, `name`, and the
   state's `type` — the workflow-state type enum, the only "type" this flow
   reads). If the identifier doesn't resolve, stop and report "no issue found
   for `<identifier>`".

3. **Resolve the target `completed`-type state id.** Call
   `<linear-mcp>__list_workflow_states` with the team `id` from step 1. Pick the
   team's **default** `completed`-type state. **Resolve by state `type`, never
   by display name** — a team may rename its `Done` column to anything, so
   never match on the string `"Done"`. If more than one `completed`-type state
   exists, prefer the one marked default by the API; otherwise take the first.

4. **Idempotence check.** If the issue's current state `type` (from step 2) is
   already `completed` or `canceled`, **stop here** — do not write. The two
   terminal types get **distinct** reports (a canceled issue is not complete,
   and this flow never silently resurrects one):
   - `completed` → "`<IDENTIFIER>` is already complete (`<current state
     name>`) — no change made."
   - `canceled` → "`<IDENTIFIER>` is canceled (`<current state name>`) — not
     changing it. Reopen it first if you really mean to complete it."

5. **`--dry-run` and confirmation.**
   - **`dry_run: true`** → print the planned transition and **stop, no write**:

     ```
     <IDENTIFIER>: <current state name> → <completed state name>
     ```

   - **Otherwise, interactive + `assume_verified: false`** (the default manual
     path) → print the same planned-transition line, then confirm via
     `AskUserQuestion` before proceeding to step 6. A "no" answer stops here
     with no write.
   - **`assume_verified: true`** (the sweep's call) → skip the confirmation
     entirely and proceed straight to step 6. This is what lets
     `/sweep-for-complete` complete a batch of already-merge-verified issues
     without a prompt per issue.

6. **Apply.** One `<linear-mcp>__save_issue` call:
   - `id`: the issue's UUID `id` from step 2
   - `state`: the `completed`-type state id from step 3 (the field is named
     `state`, not `stateId`; it accepts an id)

   Do not touch `labels` or `assignee` here — completion only changes state.

7. **Comment (optional, caller-supplied).** Call `<linear-mcp>__save_comment`
   with `issueId` = the issue's UUID `id` and `body` = `comment_body` from the
   caller contract, defaulting to `Completed via /complete-task` when the
   parameter was omitted. Skip this step only when the caller passed the
   explicit no-comment signal — `comment_body: ""` (empty string), per the
   caller contract — e.g. a sweep that batches its own summary comment
   elsewhere instead.

8. **Report.** Identifier, old state name → new state name, and whether a
   comment was posted (and its body, if short). On dry-run or idempotent
   no-write, report that instead — never claim a transition that didn't happen.
