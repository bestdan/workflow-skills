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

### Run the script

The retire step **is** `linear-archive.py` — do not re-derive its queries by
hand. See "Run it without an agent — the shipped script" below for the full
invocation and flag reference; this subsection is only the mapping from the
resolved config onto those flags.

- Resolved age threshold (Preflight above, plus `--older-than` on
  `/archive-tasks` if given) → `--older-than <N>`.
- Call `commands/handlers/linear-common.md` → "Resolve configured projects" for
  the project list. Whole-team scope (the synthetic `id: null` entry) → omit
  `--project`. One or more configured projects → pass `--project <id>` once per
  entry with a non-null `id`.
- Refs passed to `/archive-tasks --issues` → `--issues <refs>` (identifiers
  and/or UUIDs, comma-separated and/or repeated).
- `linear.team` → `--team`.

Run once without `--apply` first (the script's default) and show the candidate
list. If the caller asked for a dry run, stop there. Otherwise re-run with
`--apply`.

Report what the script prints: candidate count, archived count, any failed ids
with their error, and "nothing archived (dry-run)" for a dry run.

> Every terminal state is swept unconditionally — `duplicate` is its own state
> type, not a flavour of `canceled`, and a state left out never gets archived.
> If you are already in an agent session with the Linear MCP, you can do the
> **read** half over the MCP instead (`list_workflow_states` +
> `list_issues`), but the mutation still needs the key in a non-agent shell —
> for anything but a tiny manual run, prefer the script end-to-end.

## Run it without an agent — the shipped script

Because the backstop needs only the API key, it runs as a standalone job with no
agent session — the cleanest way to dodge the `op`-in-agent-shell gotcha
entirely, and the form to schedule on a cron / GitHub Action. The retire flow
(paginated query → candidate list → per-id `issueArchive` loop, or the
`--issues` lookup) is packaged as a runnable script:

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

# Scope to several configured projects — repeat --project once per id
# (linear-common.md → "Resolve configured projects" list); the sweep loops per project and unions
# the results, deduped by issue id. Omitting --project entirely still sweeps
# the whole team.
python3 commands/handlers/assets/linear-archive.py --team PreThink --older-than 30 \
  --project <uuid-1> --project <uuid-2> --apply

# Archive named issues regardless of age (identifiers and/or UUIDs):
python3 commands/handlers/assets/linear-archive.py --team PreThink \
  --issues PRE-12,PRE-13 --apply
```

The script's scope can now match the agent-driven flow's ("Run the script"), but only when
the caller passes every configured project's `id` as its own `--project` —
with 1+ projects configured under `linear.projects`, that is what a cron/Action
entry must do to sweep the same scope the agent flow does. Omitting `--project`
does **not** pick this up automatically: the script still reads no config, so
with no flags at all it sweeps the **whole team**, exactly as it always has,
even when `linear.projects` is configured. The whole-team default is unchanged
either way — it is the caller's job to resolve the configured project list and
pass one `--project <uuid>` per entry when narrower archiving is wanted.

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
