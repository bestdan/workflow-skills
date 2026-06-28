# gh-issue handler — /task-config setup

Configures the `gh-issue` handler, which creates GitHub Issues via `gh issue create` at `/add-task` time. This file owns the prerequisite check (`gh auth status`) and the optional-fields prompt; the actual create flow lives in `gh-issue.md`.

## Steps

1. **Verify `gh` auth.** Run `gh auth status 2>&1`.
   - If it fails with a TLS/x509/certificate error → likely the sandbox blocking keychain access; tell the user to re-run outside sandbox mode and stop.
   - Otherwise, on any other failure → ask the user to authenticate. `gh auth login` is interactive: have them run it via the session prefix, e.g. `! gh auth login`, then continue.
   - **Do not write the config until `gh auth status` succeeds.** A config that can't deliver is worse than no config.

2. **Prompt for optional settings:**
   - `repo` — default to the current repo. Resolve with `gh repo view --json nameWithOwner --jq .nameWithOwner`. Confirm with the user; let them override if they want issues filed elsewhere.
   - `labels` — list, e.g. `[follow-up]`. Plain text prompt (comma-separated); skip if blank.
   - `assignees` — list of GitHub usernames. Plain text prompt (comma-separated); skip if blank.

3. **Return the config block** to `/task-config`:

   ```yaml
   handler: gh-issue
   gh-issue:
     repo: owner/name
     labels: [follow-up]
     assignees: []
   # archive_after: 30   # optional, top-level — default /archive-tasks age threshold (days)
   ```

   Omit any optional key the user didn't set.

## Archiving (`/archive-tasks`)

gh-issue needs **no archive-specific config key**. GitHub has no active-issue cap and no true issue archive, so `/archive-tasks` is **hygiene only** here: it adds an `archived` label to issues that have been **closed** longer than the threshold (creating the label once if missing), purely so they can be filtered out of `gh issue list` / `/list-tasks` views. The shared top-level **`archive_after`** (days) sets the default age threshold when `--older-than` is omitted. See `commands/handlers/gh-issue-archive.md`.
