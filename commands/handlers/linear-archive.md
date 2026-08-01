# linear handler — /archive-tasks flow

Invoked from `/archive-tasks` when `handler: linear` is configured. Retires
terminal-state Linear issues (`completed`/`canceled`/`duplicate` state types) older than the
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

> **Gotcha: `account is not signed in` means no session, not a forbidden shell.**
> `op read` needs an authorized 1Password session, and that message says one has
> not been established — it is **not** a statement that the agent's tool-spawned
> subshell is disallowed. Once `op signin` has run in **your own** terminal (with
> desktop-app integration that raises the biometric prompt), `op` keeps the
> session in a per-user cache daemon and `op read` works from the agent's subshell
> too. Sessions lapse after roughly 30 minutes of inactivity. **Test once** with the
> non-revealing probe, which honors a configured non-default resolver and never
> prints the key:
>
> ```sh
> python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/_secret_resolve.py" --probe LINEAR_API_KEY
> ```
>
> Exit 0 → use it. Category `no-session` → run `op signin` in your terminal and
> probe again — or fall back to one of these two paths:
>
> - **Interactive:** run the archive step in _your_ terminal via the session's
>   `!` prefix (e.g. `! python3 …`), where `op` is authorized. The agent prepares
>   the script/commands; you run them; the key never enters the transcript.
> - **Headless / scheduled:** set `OP_SERVICE_ACCOUNT_TOKEN` (a 1Password service
>   account) or drop a Linear key into a CI secret. No desktop app, no terminal
>   authorization — this is the cron/GitHub-Action path.
>
> Once a first `op` call from the agent shell has failed with `account is not
> signed in`, do **not** keep retrying it expecting it to eventually authorize —
> it won't; switch to one of the paths above.

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

   `$LINEAR_API_KEY_REF` holds the `linear.api_key_ref` value from the **merged
   config** — `dev_docs/tasks/.task-config.yml` overlaid with the gitignored
   `dev_docs/tasks/.task-config.local.yml` (its canonical home; see
   `linear-config.md` → "Archive key"). The agent already parses that merged
   config, so it reads the value directly (no YAML-scraping one-liner); a
   cron/Action sets `$LINEAR_API_KEY_REF` — or `$LINEAR_API_KEY` outright — in the
   job env. `$LINEAR_API_KEY_RESOLVER` rides alongside it when the operator has
   configured a non-default resolver; it comes from `linear.api_key_resolver` in
   the **gitignored local config only** (`dev_docs/auth_key_access.md` →
   "Provenance"), never from the committed file.

   The script resolves the key itself — prefer letting it, rather than resolving
   in the shell, so the redaction and the 120s bound apply. Where you do need the
   value in a shell (the standalone cron path below), use the configured resolver:

   ```bash
   LINEAR_API_KEY="$(op read "$LINEAR_API_KEY_REF")"   # default resolver; e.g. op://Private/Linear API/credential
   # with an approval-based resolver configured instead:
   # LINEAR_API_KEY="$(opx "$LINEAR_API_KEY_REF")"
   ```

   Per the gotcha above, the default resolver needs an authorized `op` session —
   establish one with `op signin` in your own terminal (it then works from the
   agent's subshell too), or run headless with `OP_SERVICE_ACCOUNT_TOKEN`. If `linear.api_key_ref` is unset, **stop** with: "Linear archiving
   needs a personal API key. Add `linear.api_key_ref` (a 1Password `op://`
   reference) to the gitignored `dev_docs/tasks/.task-config.local.yml` — see
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
    team: { name: { eq: $team } },          # UUID-configured team? use id: { eq: $team } instead
    state: { type: { eq: $type } },         # repeat for "completed", "canceled", "duplicate"
    # project: { id: { eq: $projectId } },  # per §3: loop once per configured project (omit for whole-team) — also declare $projectId: String in the signature above
    completedAt: { lt: $cutoff }            # canceledAt for the canceled + duplicate passes
  }) {
    nodes { id identifier title completedAt }
    pageInfo { hasNextPage endCursor }
  }
}
```

3. **Scope** the same way the rest of the Linear handler scopes — always bind the
   team. `linear.team` may be a **name or a UUID id**: filter on
   `team: { id: { eq: $team } }` when the value is UUID-shaped, else
   `team: { name: { eq: $team } }` — otherwise a UUID-configured team matches
   nothing and the archive silently no-ops. (The shipped script auto-detects this;
   the agent path can resolve the id via the common preflight instead.) For the
   **project** scope, call the **"Resolve configured projects"** step in
   `commands/handlers/linear-common.md` (do **not** read the scalar
   `default_project`). It returns a list of `{ id, name, wip_limit, max_estimate }`:
   - **Whole-team scope** — the list is the single synthetic entry with `id: null`
     (projects absent/empty): omit the `project` filter and sweep the whole team,
     unchanged from today.
   - **One or more configured projects** — each entry has a non-null `id`: **loop
     the query once per `project.id`**, adding `project: { id: { eq: $projectId } }`
     to the filter **and** declaring `$projectId: String` in the query signature
     (the base example above omits it, so enabling the filter without also adding
     the variable fails GraphQL validation). **Union** the candidates across all
     projects (dedupe by issue `id`). This sweeps **every** configured project, not
     just one.

   (`--project X` narrowing — restricting the sweep to a single named project — is
   deferred; don't build it here.)
4. **Paginate.** Linear caps a page (default 50; ask for `first: 100`). Loop on
   `pageInfo.hasNextPage`, passing `endCursor` as the next `after`, until
   exhausted — a single page silently undercounts a backlog at the cap. The
   `issues` query **excludes archived issues by default**, so re-running the
   sweep is idempotent: already-archived items simply don't come back. (No
   `archivedAt` filter needed.) That default is wrong for `--issues`, where the
   caller named the issues — see that section below.
5. **Cutoff & the terminal passes.** `$cutoff` is `now − N days` as an ISO-8601
   string (`DateTimeOrDuration`). Run the query once per terminal type **× per
   configured project** (§3's loop) — **all three types, always**: `completed`
   filtered on `completedAt`, and `canceled` and `duplicate` both filtered on
   `canceledAt`. An issue missing the relevant timestamp is skipped (never archive
   on an unknown date). Collect each match's UUID `id` and `identifier` into the
   unioned candidate set (dedupe by `id`).

   > **Sweep every terminal state, unconditionally.** A state left unswept can never
   > be archived and consumes the workspace cap permanently. That is exactly what
   > happened to `duplicate` while the canceled sweep was opt-in: `duplicate` is its
   > own state type, not a flavour of `canceled`, so it matched neither filter and
   > accumulated invisibly. There is no flag to narrow this — archiving completed
   > work while deliberately retaining canceled work is not a thing anyone wants.

### Named issues instead of a sweep (`--issues <refs>`)

When `/archive-tasks` passed `--issues <refs>`, **skip steps 3–5 entirely** —
there is no cutoff and no per-project loop, because the refs _are_ the candidate
set. Replace the find with a direct lookup, then rejoin the flow at **Archive**
below (the mutation, the report, and dry-run all behave identically):

```graphql
query($team: String!, $numbers: [Float!]) {
  issues(first: 250, includeArchived: true, filter: {
    team: { name: { eq: $team } },   # UUID-configured team? use id: { eq: $team }
    number: { in: $numbers }         # the numeric halves of PRE-12, PRE-13, …
  }) {
    nodes { id identifier title completedAt canceledAt archivedAt state { type } team { id name } }
  }
}
```

Four rules make this safe, and none of them are optional:

- **Terminal state is still required.** The age gate is gone; this one is not.
  Check `state.type` **client-side** against `completed`/`canceled`/`duplicate`
  and **report-and-skip** anything else. Never archive an issue that is still
  open just because someone named it.
- **Stay inside the configured team.** The `number` filter is team-scoped
  server-side, so another team's `OTH-12` simply matches nothing. If a ref is a
  raw issue **UUID** instead, filter on `id: { in: $ids }` — but an `id` is a
  **global** key that cannot bind the team, so compare the returned
  `team.id`/`team.name` yourself and drop mismatches.
- **Ask for archived rows, and report them as done.** `includeArchived: true`
  plus `archivedAt` in the selection, then split the matches three ways: live
  (archive these), already archived (report "already archived", archive
  nothing), and unmatched. Without it a re-run reports work that already
  succeeded as "not found on this team" — the sweep's exclude-archived default
  is what makes _it_ idempotent, and it does not transfer here. The team check
  runs first, so an archived ref on another team is still not found.
- **Report what didn't resolve.** Any ref with no matching node is listed as not
  found. Silence would read as "archived", which is the one wrong impression to
  leave about a destructive op.

The shipped script implements exactly this as `--issues` (see below); prefer it
over hand-rolling the queries.

> **In-session alternative (no key for the query).** If you are already in an
> agent session with the Linear MCP, you _can_ do the read half over the MCP:
> resolve `completed`/`canceled`/`duplicate` state ids with `<linear-mcp>__list_workflow_states`,
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

## Run it without an agent — the shipped script

Because the backstop needs only the API key, it runs as a standalone job with no
agent session — the cleanest way to dodge the `op`-in-agent-shell gotcha
entirely, and the form to schedule on a cron / GitHub Action. The whole flow
above (paginated query → candidate list → per-id `issueArchive` loop) is packaged
as a runnable script:

**`commands/handlers/assets/linear-archive.py`** (Glob `**/handlers/assets/linear-archive.py` if the relative path doesn't resolve).

It **defaults to a dry run** and only mutates with `--apply` — preserve that. It
reads the key from `$LINEAR_API_KEY`, else resolves `$LINEAR_API_KEY_REF` with the
program named by `$LINEAR_API_KEY_RESOLVER` (`op` by default — see
`dev_docs/auth_key_access.md`). Because this command has **no MCP floor**, an
unresolvable key is **fatal**: the script exits non-zero with a reason category
and the archive does not run. This is the exact script validated against a real
workspace (archived 75 issues, 0 failures).

> **Plain-key fallback.** If the 1Password desktop-app integration doesn't
> expose an account to the CLI (`op account list` comes back empty even when
> signed in — a snag seen in practice), skip `op` entirely: open the item in
> the 1Password **GUI**, copy the field value, and export it directly —
> `export LINEAR_API_KEY="$(pbpaste)"` or a literal paste into the shell. This is a
> first-class supported path, not just an aside; both `op item get <uuid>` and
> `op read` require a working CLI integration that may not be present.

```bash
# Dry run (lists candidates, changes nothing):
python3 commands/handlers/assets/linear-archive.py --team PreThink --older-than 10

# Archive them:
python3 commands/handlers/assets/linear-archive.py --team PreThink --older-than 10 --apply

# Scope to a project (all terminal states are swept either way):
python3 commands/handlers/assets/linear-archive.py --team PreThink --older-than 30 \
  --project <uuid> --apply

# Archive named issues regardless of age (identifiers and/or UUIDs):
python3 commands/handlers/assets/linear-archive.py --team PreThink \
  --issues PRE-12,PRE-13 --apply
```

### `--issues` — archive specific issues, no age threshold

The age threshold guards the **bulk sweep**: without a cutoff there is no bounded
candidate list, so a bare run could archive a whole workspace. That reasoning does
not apply once the caller has **named** the issues, and the guard was blocking a
real case — issues closed minutes ago are un-archivable until the cutoff passes,
even when you want exactly those three gone now.

`--issues <refs>` is that mode. Refs are issue identifiers (`PRE-12`,
case-insensitive) or issue UUIDs, comma-separated and/or the flag repeated. It
ignores `--older-than` and `--project` (both are sweep-scoping knobs) and says so
on stdout rather than silently, and it is still dry-run until `--apply`. Refs are
capped at **250 per run** — the lookup fetches one page, so an overflowing list
would come back as "not found" and go unarchived, which reads exactly like a
clean run; it is rejected outright instead. Split the list, or use `--older-than`
if you are archiving that many.

What it does **not** relax is the terminal-state rule: a named issue that is not
`completed`/`canceled`/`duplicate` is **reported and skipped**, never archived —
archiving open work would hide it. Nor does it relax the **team** scope: an
identifier is confined server-side by the team-scoped `number` filter, and a raw
UUID — a global key whose query cannot bind the team — is checked against the
returned `team` client-side. Either way a ref outside `--team` is reported as not
found rather than archived, so another team's `OTH-12` never matches this team's
issue 12.

`--team`/`--older-than` also read `$LINEAR_TEAM`/`$ARCHIVE_AFTER`, so a cron entry
can set those plus `$LINEAR_API_KEY` (or `$OP_SERVICE_ACCOUNT_TOKEN` +
`$LINEAR_API_KEY_REF`) and run with just `--apply`. The agent-driven flow
(sections above) and this script are the same logic; the script is the
no-`op`-gotcha path. Scheduling guidance lives in `commands/archive-tasks.md` →
"Scheduling".
