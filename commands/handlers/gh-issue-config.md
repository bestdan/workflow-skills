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

3. **Provision the repo's label vocabulary.** The handler will store status, priority and estimate as labels, and GitHub label namespaces are **per-repo**. This is a prerequisite for the schema-aware writes. `/add-task` creates each configured label itself (`gh-issue.md` step 3) and then stamps the new issue `status:0_untriaged` + `auto:human-review-needed` through the writer (`gh-issue.md` step 4), so an unprovisioned repo does **not** fail that write — the schema-aware writer goes through a raw `gh api` PATCH, which auto-creates a missing label in the default grey rather than rejecting it. Provisioning is therefore what gives the vocabulary its intended colors, instead of the labels appearing one grey one at a time. It colors what it **creates**: a label already auto-created in grey keeps that color, because the sync compares names and never edits an existing label. It also protects a move: `gh issue transfer` silently drops labels the target repo lacks, which for this schema means losing an issue's entire state. Run the sync against the `repo` resolved in step 2:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-label-sync.py" \
     --repo <gh-issue.repo> --apply
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/gh-label-sync.py`. The vocabulary lives in the sibling `labels.yml` and is the single source for these names — never hardcode a label name. The script is idempotent, so re-running `/task-config` creates nothing. It **reports** labels it does not recognise and never deletes them; a repo may carry unrelated labels of its own. Drop `--apply` to see what it would create without touching the repo.

4. **Return the config block** to `/task-config`:

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

gh-issue needs **no archive-specific config key**. GitHub has no active-issue cap and no true issue archive, so `/archive-tasks` is **hygiene only** here: it adds an `archived` label to issues that have been **closed** longer than the threshold (creating the label once if missing), purely so they can be filtered out of `gh issue list` / `/list-tasks` views. It scopes the sweep to your configured **`gh-issue.labels`** so it only touches loop-filed issues — and **refuses to run when no labels are configured** (without a task-loop marker it can't tell loop issues from unrelated closed issues, and relabeling those is pure downside on a tracker with no cap). The shared top-level **`archive_after`** (days) sets the default age threshold when `--older-than` is omitted. See `commands/handlers/gh-issue-archive.md`.
