---
description: Handler procedure read by its dispatching command; not run directly.
disable-model-invocation: true
user-invocable: false
---

# jira handler — /sweep-for-complete flow

Invoked from `/sweep-for-complete [--apply] [--all] [--project <KEY>]` when
`handler: jira` is configured. Finds issues in the `indeterminate` (In
Progress) status category whose **own** linked PR has merged, and completes
exactly those by calling the `jira-complete.md` phase per verified match. The
transition itself is not re-specified here; see that file's "Caller contract"
and "Steps".

This flow exists for boards where Jira's GitHub integration and smart commits
are not closing issues on merge. Where they are, it finds nothing to do.

**Shared reference:** the backend rule (`twg`, `acli` or the Atlassian MCP) is
`jira-config.md` → "Backend"; `linear-sweep-complete.md` is the structural
template, and its steps 3–4 (PR resolution and merge state) run here unchanged.

> **This sweep never reads issue keys out of PR text.** It starts from the
> issues Jira holds in progress and asks, for each one, whether **its own**
> PR merged. "Its own" means a PR this plugin linked to the issue when it opened
> it (the `PR opened: <url>` comment `/do-tasks` and `/deliver-task` post), or one
> whose title carries the key as a whole token, or one opened from the branch
> the claim comment names. A PR that a comment merely cites is not linked, and
> `jira-pr-sources.py` ignores it.

## 1. Preflight + resolve scope

1. **Resolve the backend** per `jira-config.md` → "Backend", once for the
   whole run, and stop with its messages on failure. Every later step uses the
   backend chosen here, and step 6 passes it to `jira-complete.md`.
2. **Resolve scope.** `--all` and `--project <KEY>` are mutually exclusive; if
   both are passed, stop and ask which was meant.
   - **Neither (default)** → `project = "<jira.project>" AND assignee = currentUser()`.
     Only your own issues: in a shared project, completing a teammate's issue
     is a write on their work that they did not ask for.
   - **`--all`** → `project = "<jira.project>"`, every assignee.
   - **`--project <KEY>`** → `project = "<KEY>" AND assignee = currentUser()`.
     Any project you can browse; it does not have to be the configured one.

   Append `AND statusCategory = "In Progress" ORDER BY updated DESC` to
   whichever scope applies. Filter on the category, never on status names: a
   board's `In Review` and `In Progress` are both `indeterminate`, and both
   can hold an issue whose PR merged with nothing moving it.

## 2. Find in-flight issues

Cap the search at 50. If it returns 50, say in the report that the result was
truncated, and do not paginate.

**acli backend.** Run unsandboxed (sandboxed, acli reports a network block as
`failed to parse JQL`):

```bash
mkdir -p /tmp/claude && acli jira workitem search --jql '<scope JQL>' --fields key,status,summary --limit 50 --json > /tmp/claude/jira-sweep-<run>-issues.json && \
  python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-pr-sources.py" --acli \
  < /tmp/claude/jira-sweep-<run>-issues.json > /tmp/claude/jira-sweep-<run>-rows.json
```

If acli exits non-zero, stop and report its output: the script would
otherwise read the empty file and report invalid JSON instead.

`<run>` names the scope: the project key plus `mine` or `all` (`IP-mine`,
`IP-all`). A file is written in one tool call and read in a later one, so two
sweeps running at once in different sessions must not share it, or one could
complete issues from the other's scope. Two sweeps of the same scope can still
collide, and that fails closed: a file cut short mid-write does not parse and
stops the run, and rows the other run wrote belong to the same scope.

`--acli` makes the script fetch each issue's comments itself
(`acli jira workitem comment list`). acli's search does not return comments.

**twg backend.** Run both calls unsandboxed. First the search:

```bash
mkdir -p /tmp/claude && twg --mode agent --site <jira.site> jira workitem query --jql '<scope JQL>' --fields summary,status --limit 50 -o json --output-summary none > /tmp/claude/jira-sweep-<run>-search.json
```

Stop on a non-zero exit. Read the keys out of its `data.issues[]`; when there
are none, the sweep has nothing to do. Then read every key's comments in one
call, as a command of its own so its exit status is the one you read:

```bash
twg --mode agent --site <jira.site> jira workitem get <KEY> <KEY>... --fields summary,status --comments -o json --output-summary none > /tmp/claude/jira-sweep-<run>-issues.json
```

`get` exits 3 when some keys could not be read and still prints the rest. On
0 or 3, turn the response into rows; `--twg` turns each failed key into a
source error, so that issue lands in `left: unresolved`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-pr-sources.py" --twg \
  < /tmp/claude/jira-sweep-<run>-issues.json > /tmp/claude/jira-sweep-<run>-rows.json
```

Any other exit means the read failed as a whole: stop and report twg's
stderr, and do not run the script. The `<run>` naming below applies to these
files too.

**MCP backend.** Call `<atlassian-mcp>__searchJiraIssuesUsingJql` (`cloudId:
<jira.site>`, `jql: <scope JQL>`, `fields: ["summary", "status"]`,
`maxResults: 50`). Then, per issue, `<atlassian-mcp>__getJiraIssue`
(`cloudId: <jira.site>`, `issueIdOrKey: <key>`, `fields: ["comment"]`). Run
`python3 -c 'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)' /tmp/claude/jira-sweep-<run>-issues.json` (the Write tool refuses to overwrite a file it has not read, and an earlier
run may have left one; `rm` outside the working directory needs approval, so
this uses the already-granted `python3`), then
write the array of `{ key, id, fields: { comment } }` objects to that path
with the **Write** tool (never a shell heredoc: comment text is arbitrary) and
run the same script **without** `--acli`. Leave out the `fields` of any
issue whose `getJiraIssue` failed. The script records that as a source error,
and the issue lands in `left: unresolved`.

Keep the search result. The report and step 6 need each issue's current
status name.

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/handlers/assets/jira-pr-sources.py`. What the script accepts as a link, and
why, is in its docstring.

## 3–4. Resolve each issue's PR and check its merge state

Run `linear-pr-resolve.py` on those rows. Its three sources map directly onto
Jira: the `PR opened:` url is source 1, a whole-token title match on the issue
key is source 2, and the claim comment's branch is source 3:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-pr-resolve.py" \
  --repo "$(gh repo view --json nameWithOwner --jq .nameWithOwner)" \
  < /tmp/claude/jira-sweep-<run>-rows.json
```

Do not pass `--config`. Jira has no project-to-repo mapping, so both
fallbacks search the repo the sweep runs in. A PR this plugin opened resolves
from its own url in any repo. A hand-opened PR in a different repo resolves
only when the sweep runs from that repo's checkout.

Read each row's `state` exactly as `linear-sweep-complete.md` step 4 defines
it: `merged` goes to step 5. `open`, `unresolved` and `closed_unmerged` are
left alone, and `no_pr` is skipped. That file explains why a failed probe is
never "no PR". In a `claude-web` environment the script cannot run, so follow
that file's "Steps 2–3 in a `claude-web` environment" and its step-4 MCP read,
using these rows as input.

## 5. Dry-run (default)

Without `--apply`, print one line per candidate and stop:

```
<KEY> — PR #<n> (merged <date>) → Done category
```

The target status itself is resolved only under `--apply`, by
`jira-complete.md` step 4, which may still stop an issue with `NONE` or
`AMBIGUOUS` and write nothing to it. Then print the left and skipped lines,
and `nothing changed (dry-run).`

## 6. Apply (`--apply` only)

For each `merged` row, invoke the `jira-complete.md` phase with:

- the issue key
- `backend`: the backend step 1 resolved
- `assume_verified: true`. This sweep has already confirmed the issue's own PR
  merged, so `jira-complete.md` skips its confirmation prompt.
- `comment_body: "Closed by merge of PR #<n> (<PR URL>)"`, taking `<n>` and
  the URL from the row's merged PR

Complete only the issue whose own PR merged. `jira-complete.md` step 3 makes a
re-run safe: an issue that is already Done reports `already complete` and is
not written again. A transition that is refused, or a target status that does
not resolve, is reported by `jira-complete.md` for that issue alone. The rest
of the batch continues.

## 7. Report

- **Scope** — `scope: <project>, mine (default)`, `scope: <project>, all
  assignees (--all)`, or `scope: <KEY>, mine (--project)`, then the backend
  (`via twg` / `via acli` / `via Atlassian MCP`).
- **Counts** — `k completed, m open (left), u unresolved (left), s no-PR
  skipped, c closed-unmerged (left)`.
- **Per-issue lines** — key, current status name, the PR resolved (if any) and
  its merge state, and the outcome: `completed`, `left: open PR`, `left:
  unresolved` (with the first probe error), `left: closed unmerged`, `skipped:
  no PR found`, `already complete`, or the `jira-complete.md` stop message.
- `(search truncated at 50 — more in-flight issues may exist)` when step 2 hit
  the cap.
