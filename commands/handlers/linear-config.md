# linear handler — /todo-config setup

Configures the `linear` handler, which creates Linear issues via the official Linear MCP server (connected from `https://mcp.linear.app/mcp`) at `/add-todo` time. This file owns the Linear MCP preflight and the team/default_project/default_priority prompts; the actual create flow lives in `linear.md`.

Linear's OAuth flow handles auth — no token to paste, and agents installed in the workspace don't consume seats.

> **Linear MCP tool namespace.** The same Linear MCP can be installed two ways, which produces two different tool prefixes:
> - Installed via `claude.ai` settings → tools are `<linear-mcp>__list_teams`, etc.
> - Installed via `claude mcp add --transport http linear https://mcp.linear.app/mcp` (what `mcp-setup-offer.md` instructs) → tools are `mcp__linear__list_teams`, etc. (the prefix is `mcp__<server-name>__`, and the server is registered as `linear`).
>
> Use whichever prefix is loaded in the current session. The tool names after the prefix (`list_teams`, `list_projects`, `list_workflow_states`, `create_issue`, `save_issue`, `get_issue`, `save_comment`, `list_issue_labels`, `create_issue_label`, `get_user`, `list_issues`) are identical across both installs. The rest of this file omits the prefix and writes tool names as `<linear-mcp>__list_teams`, etc.

## Steps

1. **Linear MCP preflight.** Call `<linear-mcp>__list_teams` (no args) to discover accessible teams.
   - If the tool isn't available at all (the `<linear-mcp>__*` namespace isn't loaded), the Linear MCP isn't connected yet. **Dispatch to `mcp-setup-offer.md`** with:
     - `server`: `linear`
     - `add-command`: `! claude mcp add --transport http linear https://mcp.linear.app/mcp`
     - `handler`: `linear`

     The setup offer stops the run; the user restarts Claude Code and re-invokes `/todo-config linear` from a fresh session.
   - If the tool is available but returns no teams, **stop** with: "Linear MCP is connected but no teams are accessible. Authenticate via `/mcp` (pick `linear`), then re-run `/todo-config linear`." Do not write the config.

2. **Resolve `team`:**
   - Exactly one team → use it; tell the user which one you picked.
   - Multiple teams → ask via `AskUserQuestion` (header: "Linear team", one option per team labeled `<name>` — do NOT label as `<KEY> — <name>` because `list_teams` does not return the team key, so you literally cannot render `<KEY>`. If two teams collide on name, disambiguate with `<name> (<short-id>)`. Cap at 3 so 3 teams + "Other" fits the 4-option max). "Other" lets the user type a team name (not key), which you then re-validate against the list. Never prompt the user to type blind.

   **Write the team's `name` into the config — NOT its `key`.** Linear's MCP `list_teams` tool returns each team's `name` and `id` but NOT its `key` (e.g. `PRE`), so a key value will not match downstream and `/add-todo`/`/list-todos`/`/claim-todo` will all stop at preflight with "Configured Linear team `PRE` is not in your accessible teams." The `name` (e.g. `PreThink`) is human-readable and matches what `list_teams` returns. The `id` (UUID) also works but is unreadable in a committed config file.

3. **Resolve `default_project` against real projects — do not accept free-text.** This field is optional but, when set, must be a valid project in the chosen team (otherwise it silently breaks `/add-todo`, which uses it to skip the per-todo project prompt).

   Call `<linear-mcp>__list_projects` with:
   - `teamId`: id of the resolved team from step 2
   - `includeArchived`: `false`

   Present the result via `AskUserQuestion` (header: "Default project"):
   - One option per project, labeled `<name>` (cap at 2 project options so 2 projects + "None" + "Other" fits the 4-option max; show the 2 most recently updated).
   - A `"None — prompt me per-todo"` option (this is the recommended default if the user is unsure).
   - "Other" lets the user type a project name; if they pick "Other", look it up by calling `list_projects` again and matching the typed name (case-insensitive). If no match, push back ("`<TYPED>` is not a project in team `<team>`") and re-ask. Do not write the config with an unvalidated value.

   If the user picks "None — prompt me per-todo", omit `default_project` from the written config. Otherwise write the project's id as-is.

4. **Optional `default_priority`** — skip the prompt unless the user volunteers a preference. Linear priorities are 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low. Default `3` is applied by the handler if omitted.

5. **Return the config block** to `/todo-config`:

   ```yaml
   handler: linear
   linear:
     team: PreThink           # team NAME (or UUID id) — never the team key like "PRE"
     default_project: null
     default_priority: 3
   ```

   Omit any optional key the user didn't set.

> Labels are not supported in the v1 `linear` handler. The Linear MCP's `create_issue` takes label UUIDs, not names, and resolving names → ids requires an extra tool call (and an `allowed-tools` update). Skip the prompt; if a user asks for labels, the handler can be extended later.
