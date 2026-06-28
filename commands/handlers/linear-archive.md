# linear handler — /archive-tasks flow

Invoked from `/archive-tasks` when `handler: linear` is configured. Retires
terminal-state Linear issues (`completed`/`canceled` state types) older than the
resolved threshold so the workspace stays under Linear's **free-plan cap of 250
_active_ issues** — archived issues are unlimited and excluded from the cap.

**Shared reference:** see `commands/handlers/linear-common.md` for connection
details, the config schema, the preflight pattern, and the kanban → state-type
mapping this file reuses.

> **The Linear MCP has no archive (or delete) mutation.** It exposes
> `save_issue` (which cannot move an issue to archived) and `delete_*` only for
> comments/attachments/status-updates — there is no `archive_issue`. So the
> retire step **cannot** go through the MCP; the `issueArchive` mutation goes
> through Linear's **GraphQL API directly** with a personal API key. The
> candidate **query** can go either way, but since the mutation already needs the
> key and a non-agent shell (see the gotcha below), the validated path does the
> whole thing — query **and** mutate — in GraphQL with one key. This is the one
> handler whose retire op is not MCP-native.

> **Gotcha (learned in practice): the agent usually can't read the key from its
> own shell.** With 1Password **desktop-app integration**, only your authorized
> terminal apps can unlock `op`. The agent's tool-spawned subshell is not one of
> them, so `op read` / `op item get` there returns `account is not signed in`
> even while you are signed in interactively. Two ways that actually work:
>
> - **Interactive:** run the archive step in _your_ terminal via the session's
>   `!` prefix (e.g. `! python3 …`), where `op` is authorized. The agent prepares
>   the script/commands; you run them; the key never enters the transcript.
> - **Headless / scheduled:** set `OP_SERVICE_ACCOUNT_TOKEN` (a 1Password service
>   account) or drop a Linear key into a CI secret. No desktop app, no terminal
>   authorization — this is the cron/GitHub-Action path.
>
> Do **not** keep retrying `op` inside the agent's own shell expecting it to
> eventually authorize — it won't.

## Primary fix: native team auto-archive (do this first)

Before reaching for the script, point the user at Linear's built-in
auto-archive, which is the zero-maintenance way to stay under the cap:

> **Linear → Settings → Team → Issue statuses & automations → Auto-archive.**
> Set "Automatically archive closed issues after …" to the shortest window the
> team tolerates (the menu's minimum is the floor). Archived issues drop out of
> the 250-issue cap immediately.

State plainly: **native auto-archive is the recommended primary mechanism.** Its
shortest window can still be too long for a workspace closing ~5 issues/day, so
the GraphQL backstop below exists for tighter-than-UI windows and for an
immediate one-shot cleanup when you are already at the cap. If the user only
needs steady-state hygiene, enabling native auto-archive may be all they need —
say so and stop.

## Backstop: the GraphQL `issueArchive` retire step

Use this when the user passes an explicit `--older-than <N>d` tighter than the
native window, or needs to drain a workspace that has already hit the cap.

### Preflight

1. Run the shared preflight from `linear-common.md` (call `<linear-mcp>__list_teams`,
   match `<linear.team>`, capture the team `id`). On failure, stop with the same
   error messages.
2. **Resolve the API key.** The GraphQL call needs a Linear **personal API key**
   (the MCP's OAuth session is not usable for raw GraphQL). Read it from the
   reference configured in `linear.api_key_ref` (see `linear-config.md`), never
   from a literal in the repo. **Use the full `op://<vault>/<item>/<field>`
   reference (or the item's UUID) — not a bare item name.** A bare name is
   ambiguous and may not resolve (`"<name>" isn't an item`); the explicit
   reference is unambiguous:

   ```bash
   LINEAR_API_KEY="$(op read "$LINEAR_API_KEY_REF")"   # e.g. op://Private/Linear API/credential
   # or, by item UUID + field:
   # LINEAR_API_KEY="$(op item get <uuid> --fields label=<field> --reveal)"
   ```

   Per the gotcha above, run this in an `op`-authorized shell (your terminal via
   `!`, or a headless shell with `OP_SERVICE_ACCOUNT_TOKEN`) — not the agent's own
   subshell. If `linear.api_key_ref` is unset, **stop** with: "Linear archiving
   needs a personal API key. Add `linear.api_key_ref` (a 1Password `op://`
   reference) to `dev_docs/tasks/.task-config.yml` — see
   `commands/handlers/linear-config.md` → 'Archive key'." Do not prompt for a
   pasted key and do not write one to the repo.

### Find candidates

**Canonical: one GraphQL query (validated).** Filter on the **state type** (not
display name — names are team-configurable) and the terminal timestamp directly,
so no separate `list_workflow_states` call is needed. This is the same query the
standalone script uses, and it ran cleanly against a real workspace:

```graphql
query($cursor: String, $cutoff: DateTimeOrDuration!, $team: String!, $type: String!) {
  issues(first: 100, after: $cursor, filter: {
    team: { name: { eq: $team } },          # or id: { eq: $teamId }
    state: { type: { eq: $type } },         # "completed" (Done) — repeat for "canceled"
    completedAt: { lt: $cutoff }            # use canceledAt for the canceled pass
  }) {
    nodes { id identifier title completedAt }
    pageInfo { hasNextPage endCursor }
  }
}
```

3. **Scope** the same way the rest of the Linear handler scopes — always bind the
   team (`name`/`id`), and if `linear.default_project` is set (non-empty) add
   `project: { id: { eq: $projectId } }` so only that project is swept; otherwise
   omit it and sweep the whole team.
4. **Paginate.** Linear caps a page (default 50; ask for `first: 100`). Loop on
   `pageInfo.hasNextPage`, passing `endCursor` as the next `after`, until
   exhausted — a single page silently undercounts a backlog at the cap. The
   `issues` query **excludes archived issues by default**, so re-running is
   idempotent: already-archived items simply don't come back. (No `archivedAt`
   filter needed.)
5. **Cutoff & the canceled pass.** `$cutoff` is `now − N days` as an ISO-8601
   string (`DateTimeOrDuration`). Run the query once per terminal type: `completed`
   filtered on `completedAt`, and — only if the user wants canceled work swept too
   — `canceled` filtered on `canceledAt`. An issue missing the relevant timestamp
   is skipped (never archive on an unknown date). Collect each match's UUID `id`
   and `identifier`.

> **In-session alternative (no key for the query).** If you are already in an
> agent session with the Linear MCP, you _can_ do the read half over the MCP:
> resolve `completed`/`canceled` state ids with `<linear-mcp>__list_workflow_states`,
> then call `<linear-mcp>__list_issues` (`teamId`, optional `projectId`,
> `includeArchived: false`, those state ids) and filter by age client-side. But
> the mutation still needs the key in a non-agent shell, so for anything but a
> tiny manual run, prefer the single-key GraphQL path above end-to-end.

### Archive (mutation)

6. **Always print the candidate list first** (identifier + terminal date). If
   `dry-run`, stop here and report "nothing archived (dry-run)".
7. Otherwise call the GraphQL `issueArchive` mutation **once per id** — there is
   **no bulk archive mutation, so loop**. Use `trash: false` (archive, not trash):

   ```bash
   curl -sS https://api.linear.app/graphql \
     -H "Authorization: $LINEAR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"query":"mutation($id:String!){issueArchive(id:$id,trash:false){success}}","variables":{"id":"<ISSUE_ID>"}}'
   ```

   Check each response's `data.issueArchive.success`. On a `false` or an error
   payload for an id, record it and continue the loop — one failure must not abort
   the rest. (The personal API key authenticates as the user; `issueArchive` uses
   the issue **UUID** `id`, not the `identifier` like `PRE-12`.)

### Report

8. Report: the candidate count, how many archived successfully, any ids that
   failed (with the error), and a one-line "now under the 250 cap" note if you can
   compute the remaining active count cheaply (otherwise omit — do not add tool
   calls just for the count). In dry-run, report the candidates and that nothing
   was changed.

## Run it without an agent — reference script

Because the backstop needs only the API key, it runs as a standalone job with no
agent session — the cleanest way to dodge the `op`-in-agent-shell gotcha
entirely. Drop this in a cron / GitHub Action (key from a CI secret or
`OP_SERVICE_ACCOUNT_TOKEN`), or run it from your own terminal. It **defaults to a
dry run** and only mutates with `--apply` — keep that safety. Validated against a
real workspace (archived 75 issues, 0 failures):

```python
#!/usr/bin/env python3
"""Archive <TEAM> Linear issues that are Done and older than N days.
Dry run by default; pass --apply to archive. Key from $LINEAR_API_KEY or 1Password."""

import json, os, subprocess, sys, urllib.request
from datetime import datetime, timedelta, timezone

TEAM, STATE_TYPE, DAYS = "PreThink", "completed", 10  # "canceled" for the cancel pass
APPLY = "--apply" in sys.argv
API = "https://api.linear.app/graphql"


def key():
    k = os.environ.get("LINEAR_API_KEY")
    if k:
        return k
    # op MUST run in an authorized shell (your terminal) or with OP_SERVICE_ACCOUNT_TOKEN.
    return subprocess.run(
        ["op", "read", os.environ["LINEAR_API_KEY_REF"]], capture_output=True, text=True
    ).stdout.strip()


def gql(k, q, v=None):
    req = urllib.request.Request(
        API,
        json.dumps({"query": q, "variables": v or {}}).encode(),
        {"Authorization": k, "Content-Type": "application/json"},
    )  # personal key, no "Bearer"
    d = json.loads(urllib.request.urlopen(req).read())
    if "errors" in d:
        sys.exit(json.dumps(d["errors"], indent=2))
    return d["data"]


K = key()
cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime(
    "%Y-%m-%dT%H:%M:%S.000Z"
)
Q = """query($c:String,$cut:DateTimeOrDuration!,$t:String!,$ty:String!){
  issues(first:100,after:$c,filter:{team:{name:{eq:$t}},state:{type:{eq:$ty}},completedAt:{lt:$cut}}){
    nodes{id identifier completedAt} pageInfo{hasNextPage endCursor}}}"""
items, cur = [], None
while True:
    p = gql(K, Q, {"c": cur, "cut": cutoff, "t": TEAM, "ty": STATE_TYPE})["issues"]
    items += p["nodes"]
    if not p["pageInfo"]["hasNextPage"]:
        break
    cur = p["pageInfo"]["endCursor"]
print(f"{len(items)} candidate(s) older than {cutoff}")
for i in sorted(items, key=lambda x: x["completedAt"]):
    print(f"  {i['identifier']:<10} {i['completedAt'][:10]}")
if not APPLY:
    sys.exit(f"DRY RUN — re-run with --apply to archive these {len(items)}.")
M = "mutation($id:String!){issueArchive(id:$id,trash:false){success}}"
ok = sum(gql(K, M, {"id": i["id"]})["issueArchive"]["success"] for i in items)
print(
    f"Archived {ok}/{len(items)}. Archived issues no longer count toward the 250 cap."
)
```

Scope by `project` too where needed, and add the `canceled`/`canceledAt` pass if
you want cancellations swept. Scheduling guidance for the agent-driven path lives
in `commands/archive-tasks.md` → "Scheduling".
