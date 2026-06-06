# MCP setup offer — shared subroutine

Called from `jira-config.md` and `linear-config.md` when the required MCP namespace isn't loaded in the current session. Inputs:

- `server` — display name shown to the user, also the name used by `claude mcp add` (e.g. `linear`, `atlassian`)
- `add-command` — the exact `claude mcp add …` line the user should run to install the server
- `handler` — the `/task-config` subcommand to re-run after the MCP is connected (e.g. `linear`, `jira`). May differ from `server` (jira-config.md passes `server=atlassian, handler=jira`).

## Steps

1. Ask via `AskUserQuestion` (header: "Set up MCP"): "The `<server>` MCP isn't connected yet. Set it up now?"
   - **Yes, set it up** (recommended, first) — proceed to step 2.
   - **Cancel** — stop the config flow. Tell the user: "OK — re-run `/task-config <handler>` once you've connected the `<server>` MCP."

2. Tell the user exactly what to do, in this order. **Do NOT try to run the install command yourself** — it modifies their Claude Code config; they should run it:
   - "Run this in the prompt to install the server: `<add-command>`"
   - "Then restart Claude Code so the new MCP's tools register in this session — `claude mcp add` doesn't hot-reload tools into an existing session."
   - "After restart, run `/mcp` and authenticate `<server>` via OAuth."
   - "Then re-run `/task-config <handler>` from a fresh session."

3. **Stop** the current run after the install instructions. The new tools won't appear in this session, so there's nothing to retry here — let the user restart and re-invoke.

## Caveats

- **AskUserQuestion rule:** every call needs ≥2 options. If a step would only have one (e.g. a single visible project or site), use it directly and tell the user — don't try to ask.
- **Interactive auth caveat:** `gh auth login` is interactive — never run it headless from inside this command or any config handler. Always have the user run it with the `!` session prefix, then continue once they report success.
