---
description: Handler procedure read by its dispatching command; not run directly.
disable-model-invocation: true
user-invocable: false
---

# jira handler — /garden-backlog tracker I/O

Invoked from `/garden-backlog` when `handler: jira` is configured. This file
implements the four operations of the handler contract in
`${CLAUDE_PLUGIN_ROOT}/commands/garden-backlog.md` §2 — **List**, **Read**,
**Write**, **Comment** — and nothing else: the judgment, the record, the
report and the approval are the core's.

**Shared reference:** the backend and preflight are `jira.md` step 1; the
config keys are `jira-config.md` ("Promote keys", "Blocked statuses",
"Estimate and priority encoding"); transition-id resolution and the write
bound come from `jira-promote.md` steps 3a and 5. On the `twg` backend, make
each MCP call below with the command `jira-twg.md` maps it to.

**The deterministic parts are a script.**
`${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py` builds the
JQL, pages the search, turns pages into the core's rows, turns approved writes
into an edit payload, and renders the provenance comment. Its docstring says
why each rule is shaped the way it is; don't re-derive any of it here. It exits
`0` on success, `2` on bad input (it names what), and `4` when a twg call
failed. If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/handlers/assets/jira-garden.py`.

> **Temp files.** `<dir>` is what
> `"${CLAUDE_PLUGIN_ROOT}/scripts/work-tmp.sh" --sub garden-backlog` prints,
> plus a run id (`<project>-<UTC yyyymmddTHHMMSSZ>`) so two runs on one branch
> do not share pages. Write that path out literally in each call: steps run as
> separate tool calls with no shared shell state. Every input file for the
> script below is written with the **Write** tool, never a heredoc: ticket text
> is arbitrary.

## 1. Preflight and config

1. **Scope gate, first — dry run only.** Run List step 1's `jql` call before
   anything else. Exit `2` stops the run with the message it prints: that is
   how a project key or JQL scope without `--wide` stops, before any tracker
   read. Skip it at `apply`: apply reaches each ticket through Read
   (`jql --key`), which takes no scope, so a report from a `--wide` run applies
   without the flag.
2. **Backend.** Resolve it per `jira.md` step 1 and stop with its messages on
   failure. **Prefer `twg`.** A backlog of hundreds of tickets, with
   descriptions and comments, is paged by `fetch` straight into files on
   `twg`; on the MCP every page passes through the conversation twice — once
   as the tool result, once written to a file.
3. **Keys.** From `dev_docs/tasks/.task-config.yml` read `jira.site`,
   `jira.project`, `jira.issue_type`, `jira.ready_status`,
   `jira.refinement_status`, `jira.blocked_statuses`, `jira.estimate_field` and
   `jira.priority_map`. This flow adds no key of its own.
   - `ready_status` / `refinement_status` unset, and the owner can be asked →
     **stop and ask**, exactly as `jira-promote.md` step 3a does: resolve the
     reachable statuses from one candidate's transitions, ask via
     `AskUserQuestion`, and offer to persist the answer. Offer the status
     unready tickets already sit in first for `refinement_status`: unlike
     promote, the garden's query does not filter on status, so that is a valid
     target here. Never guess a status name. `rows` refuses a configured target
     outside the To Do status category, since moving a ticket there would
     start it.
   - **Nobody can be asked** — a subagent, a `claude -p` run, or a session
     without `AskUserQuestion` → never prompt; take both keys from config.
     `ready_status` unset → **stop** with: "`jira.ready_status` is unset and
     no one can be asked; set it in `dev_docs/tasks/.task-config.yml`."
     `refinement_status` unset → leave `--refinement-status` off the dry run's
     `rows` call; `rows` infers it or stops with a message naming the key
     (step 2.3).
   - `estimate_field` or `priority_map` unset → that field's proposals stay
     report-only; `fields` (step 4) reports them rather than writing.
4. **Project default priority.** Call
   `<atlassian-mcp>__getJiraIssueTypeMetaWithFields` (`cloudId: <jira.site>`,
   `projectIdOrKey: <project>`, the `issueTypeId` of `jira.issue_type`,
   `requiredFieldsOnly: false`) and take the `priority` field's
   `defaultValue.name` (e.g. `P3-Minor`). twg's `field create-metadata` leaves
   system fields out (measured, twg 1.4.4), so this one read is always the
   MCP's. If the MCP is not loaded, ask the owner which priority is the
   default, offering the `priority_map` values. "Human-set" priority depends
   on it, so `rows` refuses to run without one.
5. **Project issue types** — at apply only, and only when an approved group
   is `kind`. Call `<atlassian-mcp>__getJiraProjectIssueTypesMetadata`
   (`cloudId: <jira.site>`, `projectIdOrKey: <project>`) and Write the
   response verbatim to `<dir>/issue-types.json`; `fields` (step 4) resolves a
   kind write's target id from it. If the MCP is not loaded, skip this read:
   every kind write then lands in `report_only` rather than being guessed.

## 2. List `(scope, limit)`

1. **Build the queries:**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py" jql --scope "<scope>" [--wide]
   ```

   It prints `{candidates, priority_changed}`. `<scope>` is an epic key (its
   children, via `parent`); with `--wide`, also a project key or a JQL
   fragment without `ORDER BY` (a label scope is a JQL fragment, such as
   `labels = x`). Pass `--wide` exactly when the core was given it. Both queries take open, non-epic tickets outside an active
   sprint, in every status category but Done — `On Hold/Blocked` and
   `Ready for Development` included — ordered by key so pages are stable.
   `priority_changed` is the same set narrowed by `priority CHANGED`: one query
   returning keys, never a per-ticket changelog read.

2. **Page each query to the end.** Candidate fields:
   `summary,description,status,issuetype,priority,parent,created,components,updated,labels,comment,issuelinks`
   plus `<estimate_field>` when set. The `priority_changed` query needs only
   `priority`.
   - **twg:** run unsandboxed, once per query, into its own directory:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py" fetch --site <jira.site> --jql '<query>' --fields <fields> --out-dir <dir>/candidates [--limit <N>]
     ```

     It prints `{pages, count, limit_cut}`. Pass `--limit` on the candidate
     query only.
   - **MCP:** call `<atlassian-mcp>__searchJiraIssuesUsingJql` with
     `maxResults: 100` and the fields as a list, Write each response verbatim
     to `<dir>/candidates/page-<NNN>.json`, then read it back with
     `jira-garden.py page <file>`, which prints `{count, next}`. Pass `next`
     as `nextPageToken` for the next call; stop when it is `null`, or once the
     running count reaches `--limit`. Stop with an error if `next` repeats.

3. **Build the rows:**

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py" rows --pages <candidate pages> --changed-pages <priority_changed pages> --default-priority "<name>" --ready-status "<ready_status>" [--refinement-status "<refinement_status>"] [--blocked-status "<name>" ...] [--estimate-field <id>] [--priority-map-json '<map as JSON>'] --issue-type "<jira.issue_type>" --site <jira.site> [--limit <N>] [--limit-cut]
   ```

   Pass `--limit-cut` when `fetch` (or the MCP loop) stopped at the limit.
   Without `--refinement-status`, `rows` uses the one To Do status the
   candidates hold besides `ready_status` and the blocked statuses (not
   necessarily the workflow's initial status), and exits `2` naming `jira.refinement_status` when
   there is not exactly one. It prints `{count, limit_cut, refinement_status,
   refinement_status_source, rows}`; when the source is `inferred`, put
   the name in the report's scope line, and pass it as `--refinement-status`
   to every later `rows` (Read) and `fields` call. Each row is the core's List shape plus
   `status_name`, `issuetype`, `labels` and `priority` for the report and for
   step 4. The rules behind the symbolic fields, all in the script:
   - `status` — `ready_status` → `ready`, `refinement_status` →
     `refinement`, a `blocked_statuses` name or any In Progress category →
     `started`, any other To Do status → `initial`, anything else → `other`.
   - `gardened_at` — the newest comment that starts with `garden-backlog:`.
   - `updated_at` — `updated`, except that the provenance comment's own bump
     is excluded: a ticket the garden touched last reads as unchanged since.
   - `size_human_set` — the estimate is set and the newest provenance comment
     that recorded a size write did not write this value.
   - `importance_human_set` — the priority differs from the default or is in
     the `priority_changed` set, and the newest provenance comment that
     recorded a priority write did not write this value.
   - `importance` — `priority_map` read backwards over the five importance
     levels only; `null` when the name maps to none.
   - `kind` — `spike` when the issue type is `Spike`, otherwise `fix`.
   - `kind_human_set` — the type is not `jira.issue_type` (the creation
     type), or a provenance comment recorded a kind write and the type no
     longer matches it. The script's docstring names the case this misses.
   - `linked_prs` — the ticket's own-PR trail per **Validity rung 1** below.
   - `blocked_by` — the keys behind the ticket's "is blocked by" links whose
     status is not in the Done category, or null when the page has no
     `issuelinks` field.
   - `created` and `components` — as Jira holds them, for **Validity:
     supersession candidates**.

4. **Resolve rung 1** — **Validity rung 1** below, which adds `own_pr` to
   every row. The core's `prejudge` reads that output, not `rows`'s.

## 3. Read `(key)`

The same three calls as List, with `jql --key <KEY>` in place of `--scope` and
no limit: one code path, one row shape, and no epic gate, since one ticket is
never a wide scope. A ticket the query no longer returns — moved into an
active sprint, done, or turned into an epic since the dry run — is reported as
changed since the dry run and skipped.

## 4. Write `(key, writes)`

Apply one ticket's approved writes. Read it fresh first (step 3).

1. **Payload.** Write the approved `proposed_writes` to `<dir>/<KEY>-writes.json`
   and the fresh row's `labels` to `<dir>/<KEY>-labels.json`, then:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py" fields --writes <dir>/<KEY>-writes.json --labels <dir>/<KEY>-labels.json --ready-status "<ready_status>" --refinement-status "<refinement_status>" [--estimate-field <id>] [--priority-map-json '<map as JSON>'] --issue-type "<jira.issue_type>" [--issue-types <dir>/issue-types.json]
   ```

   It prints `{fields, issuetype, transition_to, report_only}`: the estimate into
   `estimate_field`, the importance level as the priority name
   `priority_map` gives it, `garden:close-candidate` / `garden:needs-human`
   merged into the labels (an edit replaces the whole label set, which is why
   the labels come from the fresh read), the status name to move to, and a
   kind write as the target issue type's id (`Spike` for `spike`,
   `jira.issue_type` for `fix`). A project without that type, subtask types
   aside, gives a `report_only` line and no `issuetype`. `report_only` lines
   go to the apply summary, not the tracker.
2. **Edit**, when `fields` is non-empty: one `<atlassian-mcp>__editJiraIssue`
   (`cloudId`, `issueIdOrKey: <KEY>`, `fields: <fields>`).
3. **Transition**, when `transition_to` is set and differs from the ticket's
   `status_name`: `<atlassian-mcp>__getTransitionsForJiraIssue`, written to a
   file and resolved with
   `python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-resolve-transition.py" --exact "<transition_to>" < <file>`
   (add `--twg` on twg), then `<atlassian-mcp>__transitionJiraIssue` with the
   printed id. On exit 2 (`NONE` / `AMBIGUOUS`) skip the transition and report
   the transitions the ticket offers, as `jira-promote.md` step 5 does. A
   `started` or `other` ticket never reaches here: the core targets it
   `unchanged`.
4. **Issue type**, when `issuetype` is set: one more
   `<atlassian-mcp>__editJiraIssue` with `fields: {"issuetype": <issuetype>}`,
   on its own so that a refusal leaves the other writes standing. Jira refuses
   a type change between workflows or field configurations, which only its
   Move screen can do; report that refusal as this ticket's failed kind write.
5. Return what landed and what failed, per call.

**Bounded writes.** Tickets go serially, or in concurrent groups of at most
5, each group finished before the next; the incident behind the bound is in
`jira-promote.md` step 5. Every write is its own tool call: auto mode denies
a loop-shaped batch however short the list.

## 5. Comment `(key, body)`

After the ticket's writes, and always as its last write: the comment's own
bump is what step 2 excludes from `updated_at`, which works only if nothing
follows it. Write the comment spec —
`{date, changes: [{field, old, new, reason}], labels, human_queue}`, `field`
one of `size`, `importance` (old and new as priority names), `kind` (old and
new as issue-type names), `status` — to
`<dir>/<KEY>-comment.json`, then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py" comment --spec <dir>/<KEY>-comment.json > <dir>/<KEY>-comment.md
```

Post that body with `<atlassian-mcp>__addCommentToJiraIssue`
(`contentFormat: markdown`), or twg's `comment create --body-format markdown`.
Exactly one comment per ticket per apply. Never edit its first line: the
early exit finds the comment by its `garden-backlog:` prefix.

## Validity rung 1: the ticket's own PR

`skills/task/references/validity.md` rung 1 for Jira is
`jira-sweep-complete.md` §3–4. A row's `linked_prs` is the
`jira-pr-sources.py` row for the ticket, built from the plugin's own
`PR opened:` and claim comments in the page's `comment` field. It is the
resolver's **input**, empty on any ticket the plugin did not deliver, and is
never read as a verdict. Resolve every row at once, before the judges run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py" own-prs --rows <rows file> --repo <owner/name> [--repo <owner/name> ...]
```

Pass every repo the scope's tickets name (validity.md §1; for an epic, the
union over its children). Each row gains `own_pr`: `linear-pr-resolve.py`'s
`state`, `prs`, `resolved_via` and `probe_errors`, strongest verdict across
the repos. Its title probe is what finds a merged PR whose title carries the
key on a ticket with no trail. Never use the ticket's remote links or its
development panel: both list PRs that only cite the key. A `source_errors`
entry or a failed probe lands the row in `unresolved`, never in "no PR".

## Validity: supersession candidates

`validity.md` rung 2's supersession lead, for one ticket. Build the query from
its row, adding each of the ticket's distinctive terms as a `--term`, or none
when it has few and the epic's stated purpose is the guide:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-garden.py" superseded --key <KEY> --created "<created>" [--parent <parent>] [--component "<name>" ...] [--term "<term>" ...]
```

It prints `{jql, since, reason}`: Done work in the ticket's epic or
components, resolved from the day before it was created (a day early, since JQL reads the
date in the searching user's timezone). `jql` null means the
ticket has no area to search (`reason` says so), which is no lead, not "no
fix". Otherwise page it with **List** step 2's `fetch` (or the MCP loop) into
`<dir>/superseded-<KEY>`, fields `summary,description,resolutiondate,parent,components,issuetype`,
`--limit 25`, and read each candidate against the ticket's flow.

## Hard rules

Never close a ticket or transition it to a Done-category status. Never
transition into a started status. Never write a field whose config key is
unset, and never change an issue type except through an approved `kind`
write. Never overwrite a human-set value — the core proposes no such write,
and `fields` is not where that is decided.
