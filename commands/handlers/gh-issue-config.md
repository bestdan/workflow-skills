# gh-issue handler — /todo-config setup

Configures the `gh-issue` handler, which creates GitHub Issues via `gh issue create` at `/add-todo` time. This file owns the prerequisite check (`gh auth status`) and the optional-fields prompt; the actual create flow lives in `gh-issue.md`.

## Steps

1. **Verify `gh` auth.** Run `gh auth status 2>&1`.
   - If it fails with a TLS/x509/certificate error → likely the sandbox blocking keychain access; tell the user to re-run outside sandbox mode and stop.
   - Otherwise, on any other failure → ask the user to authenticate. `gh auth login` is interactive: have them run it via the session prefix, e.g. `! gh auth login`, then continue.
   - **Do not write the config until `gh auth status` succeeds.** A config that can't deliver is worse than no config.

2. **Prompt for optional settings:**
   - `repo` — default to the current repo. Resolve with `gh repo view --json nameWithOwner --jq .nameWithOwner`. Confirm with the user; let them override if they want issues filed elsewhere.
   - `labels` — list, e.g. `[follow-up]`. Plain text prompt (comma-separated); skip if blank.
   - `assignees` — list of GitHub usernames. Plain text prompt (comma-separated); skip if blank.

3. **Return the config block** to `/todo-config`:

   ```yaml
   handler: gh-issue
   gh-issue:
     repo: owner/name
     labels: [follow-up]
     assignees: []
   ```

   Omit any optional key the user didn't set.
