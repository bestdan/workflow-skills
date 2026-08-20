# linear handler — /task-config setup

Configures the `linear` handler, which creates Linear issues via the official Linear MCP server (connected from `https://mcp.linear.app/mcp`) at `/add-task` time. This file owns the Linear MCP preflight and the team/projects/default_priority prompts (including migrating a pre-existing scalar `default_project`); the actual create flow lives in `linear-add.md`.

Linear's OAuth flow handles auth — no token to paste, and agents installed in the workspace don't consume seats.

> **Linear MCP tool namespace.** The same Linear MCP can be installed two ways, which produces two different tool prefixes:
>
> - Installed via `claude.ai` settings → tools are `mcp__claude_ai_Linear__list_teams`, etc.
> - Installed via `claude mcp add --transport http linear https://mcp.linear.app/mcp` (what `mcp-setup-offer.md` instructs) → tools are `mcp__linear__list_teams`, etc. (the prefix is `mcp__<server-name>__`, and the server is registered as `linear`).
>
> Use whichever prefix is loaded in the current session. The tool names after the prefix (`list_teams`, `list_projects`, `list_workflow_states`, `create_issue`, `save_issue`, `get_issue`, `save_comment`, `list_issue_labels`, `create_issue_label`, `get_user`, `list_issues`) are identical across both installs. The rest of this file omits the prefix and writes tool names as `<linear-mcp>__list_teams`, etc.

## Steps

1. **Linear MCP preflight.** Call `<linear-mcp>__list_teams` (no args) to discover accessible teams.
   - If the tool isn't available at all (the `<linear-mcp>__*` namespace isn't loaded), the Linear MCP isn't connected yet. **Dispatch to `mcp-setup-offer.md`** with:
     - `server`: `linear`
     - `add-command`: `! claude mcp add --transport http linear https://mcp.linear.app/mcp`
     - `handler`: `linear`

     The setup offer stops the run; the user restarts Claude Code and re-invokes `/task-config linear` from a fresh session.
   - If the tool is available but returns no teams, **stop** with: "Linear MCP is connected but no teams are accessible. Authenticate via `/mcp` (pick `linear`), then re-run `/task-config linear`." Do not write the config.

2. **Resolve `team`:**
   - Exactly one team → use it; tell the user which one you picked.
   - Multiple teams → ask via `AskUserQuestion` (header: "Linear team", one option per team labeled `<name>` — do NOT label as `<KEY> — <name>` because `list_teams` does not return the team key, so you literally cannot render `<KEY>`. If two teams collide on name, disambiguate with `<name> (<short-id>)`. Cap at 3 so 3 teams + "Other" fits the 4-option max). "Other" lets the user type a team name (not key), which you then re-validate against the list. Never prompt the user to type blind.

   **Write the team's `name` into the config — NOT its `key`.** Linear's MCP `list_teams` tool returns each team's `name` and `id` but NOT its `key` (e.g. `PRE`), so a key value will not match downstream and `/add-task`/`/list-tasks`/`/do-tasks` will all stop at preflight with "Configured Linear team `PRE` is not in your accessible teams." The `name` (e.g. `PreThink`) is human-readable and matches what `list_teams` returns. The `id` (UUID) also works but is unreadable in a committed config file.

3. **Resolve `projects` — one or more, validated against real projects (no free-text).** The handler scopes to a **list** of projects (`linear.projects`), which replaces the old scalar `default_project`. See `linear-common.md` "Config block" + "Resolve configured projects" for the full schema and the inheritance rules (per-project `wip_limit`/`max_estimate` fall back to the global defaults). An unvalidated id silently breaks `/add-task`, so resolve every entry against `list_projects` — never accept a typed id blind.

   **3a. Migrate an existing scalar `default_project` first.** If the current config (shown by `/task-config` step 1) carries a scalar `linear.default_project`, it predates the list. Resolve its name via `<linear-mcp>__list_projects` (match the id) and **seed** the projects list with it as a single `{ id, name }` entry (the name is already in hand from that lookup, so write it for a readable config; no overrides — it inherits the globals). Tell the user once: "Migrating pinned project `<name>` to the new `projects:` list." The written config will contain **only** `projects:` — the scalar `default_project` key is **dropped** (hard cut: handlers read only `projects:` going forward). The user can then add or remove entries in 3b.

   **3b. Build the projects list (loop).** Call `<linear-mcp>__list_projects` (`teamId` from step 2, `includeArchived: false`) once, and keep a working **set of chosen projects** (seeded by 3a's migrated entry, if any). Each iteration, present a small **action menu** via `AskUserQuestion` (header: "Configure projects") whose options depend on the current set — always **≤ 4 counting the automatic "Other"**:
   - **Set empty** → options: **"Add a project"** and **"None — prompt me per-task"** (the recommended default if unsure). No "Done" here — an empty set means either add one or choose None.
   - **Set non-empty** → options: **"Add a project"**, **"Remove a project"**, and **"Done — that's all"**.

   Then act on the choice:
   - **Add a project** → a follow-up `AskUserQuestion` listing up to **3** not-yet-chosen projects (most-recently-updated); the automatic **"Other"** (4th slot) lets the user type a project name, looked up via `list_projects` and matched case-insensitively — on no match push back ("`<TYPED>` is not a project in team `<team>`") and re-ask. For the chosen project, **only if the user volunteers one**, capture a per-project `wip_limit` and/or `max_estimate` override — don't prompt by default; most entries inherit the global defaults. Record `{ id, name?, wip_limit?, max_estimate? }` (write the `name` you already have from `list_projects`). Loop.
   - **Remove a project** → a follow-up `AskUserQuestion` listing the currently-chosen projects — cap at **3** (most-recently-added) so it stays within the 4-option max; the automatic **"Other"** (4th slot) lets the user type the name of any chosen project not shown, matched case-insensitively against the set. Drop the picked one from the set. Loop (once the set is empty again, the menu re-offers "None").
   - **Done — that's all** (offered only when ≥ 1 project is chosen) → finish with the chosen list.
   - **None — prompt me per-task** (offered only when the set is empty) → write **no** `projects` key at all (whole-team scope — today's no-pin behavior). Do **not** write `projects: []`.

   **3c. Optional `global_wip_limit`.** Only when **2 or more** projects are configured, optionally ask for `linear.global_wip_limit` — an absolute ceiling on total in-flight across **all** configured projects (on top of the per-project caps). Skip the prompt for 0–1 projects (a global cap is meaningless there). Unset by default (no global ceiling).

   **3d. Optional `unassigned_wip_limit`.** Only when **1 or more** projects are configured, optionally ask for `linear.unassigned_wip_limit` — the WIP cap for the synthetic **Unassigned** bucket that `/do-tasks` uses for issues with no project or in a project not in this list. Default = the top-level `wip_limit`; `0` = never ranked-claim unassigned work. Skip the prompt when **0** projects are configured (the whole-team scope already spans everything). Unset by default (inherits `wip_limit`); emit it only when the user sets a non-default value.

4. **Optional `default_priority`** — skip the prompt unless the user volunteers a preference. Linear priorities are 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low. Default `3` is applied by the handler if omitted.

5. **Return the config block** to `/task-config` (the new `projects` shape — never a scalar `default_project`):

   ```yaml
   handler: linear
   linear:
     team: PreThink # team NAME (or UUID id) — never the team key like "PRE"
     default_priority: 3
     projects:
       - id: ebbc284b-0000-0000-0000-000000000000 # required id/UUID; add wip_limit/max_estimate to override the globals
     # global_wip_limit: 6       # include only when 2+ projects are configured
     # unassigned_wip_limit: 3   # include only when 1+ projects are configured and set non-default
   ```

   Shape rules: write `projects:` as a list of `{ id, name?, wip_limit?, max_estimate? }`; include `global_wip_limit` (under `linear:`) only when 2+ projects are set; include `unassigned_wip_limit` (under `linear:`) only when 1+ projects are set **and** the user chose a non-default value; add a top-level `wip_limit` only if the user chose a non-default global (it defaults to `3`). When the user picked "None — prompt me per-task", **omit `projects` entirely** (whole-team scope). Omit any optional key the user didn't set, and **never** emit a scalar `default_project`. (Full schema: `linear-common.md` "Config block".)

> Labels are not supported in the v1 `linear` handler. The Linear MCP's `create_issue` takes label UUIDs, not names, and resolving names → ids requires an extra tool call (and an `allowed-tools` update). Skip the prompt; if a user asks for labels, the handler can be extended later.

## Archive key (`linear.api_key_ref`, `linear.api_key_resolver`, `linear.api_key`)

`linear.api_key_ref` is **optional**. Its primary consumer is `/archive-tasks`; when set, the same key **also** powers the read-only GraphQL fast paths — `/do-tasks`' find-candidates, plus `/sweep-for-complete`, `/reconcile-tasks`, and `/reoptimize-tasks`. That works because the handler bridges the configured ref into `$LINEAR_API_KEY_REF` before invoking the scripts (`linear-common.md` → "Key resolution"); the scripts themselves read the environment only and parse no config. It is a **full 1Password `op://<vault>/<item>/<field>` reference** (e.g. `op://Private/Linear API/credential`) to a Linear **personal API key** — use the explicit vault/item/field form (or the item UUID), **not a bare item name**, which is ambiguous and may not resolve.

`linear.api_key_resolver` names **which allow-listed program** turns that reference into the key: `op` (the default, `op read <ref>`) or `opx`. It is an **identifier, never a command line** — a value like `opx op://…` in `api_key_ref` is a `malformed-ref` error, not a resolver. Because it names a program to run, it is machine-scoped: honored only from `.task-config.local.yml` or `$LINEAR_API_KEY_RESOLVER`, refused from the committed config. The full contract — both ladders, the allow-list, reference grammar, failure categories — is `dev_docs/auth_key_access.md`.

Why a raw key and not the MCP: the Linear MCP exposes **no archive mutation** (only `save_issue`, which can't archive, and `delete_*` for comments/attachments). So `/archive-tasks`'s backstop calls Linear's GraphQL `issueArchive` mutation directly, and that needs a personal API key the MCP's OAuth session can't provide. The recommended shape is a secret manager plus a reference — the archive flow resolves the ref at runtime with the configured resolver, `op read "<ref>"` by default (see `commands/handlers/linear-archive.md` and `dev_docs/auth_key_access.md`).

> **If you'd rather not run a secret manager: `linear.api_key`.** A raw key is accepted in the **gitignored `.task-config.local.yml`** only — never in the committed `.task-config.yml`, where it is refused outright, exactly like `api_key_resolver`. It takes precedence over `api_key_ref` (it is rung 0 of the pointer ladder; nothing needs resolving) and the handler bridges it into `$LINEAR_API_KEY`. Understand the trade: `.task-config.local.yml` is ignored robustly — `.gitignore` ignores `dev_docs/tasks/*` wholesale and negates only the committed config, so this is not one forgotten ignore line away from a leak — but the token is then plaintext **inside the repo tree**, readable by every agent session, editor index, and backup that touches this directory — and because the handler bridges it into the command it runs, it also lands in the **session transcript**. If that's an acceptable trade for you, it is a supported configuration and no flow will second-guess it. If you want a plaintext key but not one sitting in the repo tree, export `$LINEAR_API_KEY` from your shell profile instead — same rung, no file in the checkout.

> **Keep `api_key_ref` out of the committed config.** Put it — and any other secret-bearing field — in the **gitignored `.task-config.local.yml`**, not the shared, tracked `.task-config.yml`. Even though the value is an `op://` reference and not the raw key, a personal `op://` pointer is per-clone and still sensitive: committing it advertises which vault/item holds a full-account bearer token and churns the shared config across machines. The `.local` file is merged over the committed config at read time (see `commands/task-config.md` → "Local override"), so the key resolves the same way. If any flow — e.g. `/archive-tasks` — ever writes `api_key_ref` into the tracked `.task-config.yml`, move it to `.task-config.local.yml` before committing, or supply it out-of-band via `$LINEAR_API_KEY_REF` (an `op://` ref) or `$LINEAR_API_KEY` (the raw key) instead.

> **Where the resolve actually works.** The default resolver, `op read`, needs an **authorized 1Password session**, and `account is not signed in` means exactly that — no session yet — **not** that the agent's subshell is unauthorized. Run `op signin` in _your own_ terminal (with desktop-app integration this raises the biometric prompt); `op` then holds the session in a per-user cache daemon, and `op read` works from the agent's tool-spawned subshell too. Sessions lapse after roughly 30 minutes of inactivity, after which the fast paths floor until the next `op signin`. **Unattended** runs have no session at all and still need `OP_SERVICE_ACCOUNT_TOKEN` / a CI secret — which is why the archive backstop is happiest as the standalone script in `linear-archive.md` → "Run it without an agent", scheduled on a cron.
>
> An **approval-based** resolver behaves differently on both counts, so don't carry the `op` mental model over: `opx` raises a native dialog per read and invalidates the `op` session afterwards, so a subsequent plain `op read` in the same shell failing is expected rather than a lapsed session — and it **cannot serve an unattended run at all** (no UI to approve, so it fails closed). A cron that needs a key must set `api_key_resolver: op` with `$OP_SERVICE_ACCOUNT_TOKEN`, or inject `$LINEAR_API_KEY` directly.
>
> To check whether a key resolves without revealing it, use the shared probe rather than a bare `op read` — it honors the configured resolver and writes nothing to stdout:
>
> ```sh
> python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/_secret_resolve.py" --probe LINEAR_API_KEY
> ```

This key is **not** required for `/add-task`, `/list-tasks`, `/promote-tasks`, or `/do-tasks` — but the same `api_key_ref` now **also** powers the read-only GraphQL fast paths (see `linear-common.md` → "Key resolution" for how it reaches them, and `linear-claim.md` "Find candidates"), if set. It remains entirely **optional** there too: `/do-tasks` falls back to the MCP floor when it's unset. If the team relies solely on Linear's **native team auto-archive** (the recommended primary mechanism) and doesn't want the fast-path either, the key is unnecessary; leave it unset. Don't prompt for it during `/task-config` setup — it's a full-account bearer token, so don't nudge; mention it only if the user asks about archiving, hits the 250-issue cap, or asks about speeding up `/do-tasks`.
