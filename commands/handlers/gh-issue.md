# Handler: gh-issue

Creates a GitHub Issue via the `gh` CLI. Runs in the **foreground in the current session** — one API call, no git plumbing, no remote/subagent/local cascade.

Config block in `dev_docs/todos/.todo-config.yml`:

```yaml
handler: gh-issue
gh-issue:
  repo: owner/name        # optional; defaults to the current repo
  labels: [follow-up]     # optional; each is created if missing
  assignees: []           # optional GitHub usernames
```

## Steps

1. **Preflight auth.** Run `gh auth status 2>&1`. If it fails:
   - If the error mentions TLS/x509/certificate, it is likely the sandbox blocking keychain access — tell the user to re-run outside sandbox mode.
   - Otherwise report the auth failure.
   - Either way, **stop**. Do not silently fall back to `repo-pr` — the destination was chosen deliberately.

2. **Build the issue body.** Take the drafted todo's `body` and append a source footer (omit lines whose value is empty):

   ```
   ---
   Source branch: <source_branch>
   Source PR: #<source_pr>
   ```

   Write it to a temp file to avoid shell-quoting problems with multi-line markdown.

3. **Ensure labels exist.** For each label in `gh-issue.labels`:

   ```bash
   gh label create "<label>" --repo "<repo>" 2>/dev/null   # no-op if it already exists
   ```

   (Omit `--repo` when no `gh-issue.repo` is configured, matching step 4 — labels must be created in the same repo the issue lands in.)

4. **Create the issue.** Map the drafted todo: `--title` ← `title`, body ← step 2, `--label`/`--assignee` ← config, `--repo` ← `gh-issue.repo` if set.

   ```bash
   gh issue create \
     --repo "<repo>" \
     --title "<title>" \
     --label "<label>" --label "<label2>" \
     --assignee "<assignee>" --assignee "<assignee2>" \
     --body-file "<path-to-body>"
   ```

   (Omit `--repo` to use the current repo; omit `--label`/`--assignee` flags that have no configured values.)

5. **Return the URL.** `gh issue create` prints the new issue URL to stdout — capture it and return it as this handler's artifact URL for `/add-todo` step 8.

This handler does **not** create any `dev_docs/todos/*.md` file, branch, or PR.
