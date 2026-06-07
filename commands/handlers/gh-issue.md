# Handler: gh-issue

Creates a GitHub Issue via the `gh` CLI. Runs in the **foreground in the current session** — one API call, no git plumbing, no remote/subagent/local cascade.

Config block in `dev_docs/tasks/.task-config.yml`:

```yaml
handler: gh-issue
gh-issue:
  repo: owner/name # optional; defaults to the current repo
  labels: [follow-up] # optional; each is created if missing
  assignees: [] # optional GitHub usernames
```

## Steps

1. **Preflight auth.** Run `gh auth status 2>&1`. If it fails:
   - If the error mentions TLS/x509/certificate, it is likely the sandbox blocking keychain access — tell the user to re-run outside sandbox mode.
   - Otherwise report the auth failure.
   - Either way, **stop**. Do not silently fall back to `repo-pr` — the destination was chosen deliberately.

2. **Build the issue body.** Take the drafted task's `body` and append a source footer (omit lines whose value is empty):

   ```
   ---
   Source branch: <source_branch>
   Source PR: #<source_pr>
   Blocked by task: <is_blocked_by>
   ```

   Write it to a temp file to avoid shell-quoting problems with multi-line markdown.

3. **Ensure labels exist.** For each label in `gh-issue.labels`:

   ```bash
   gh label create "<label>" --repo "<repo>" 2>/dev/null   # no-op if it already exists
   ```

   (Omit `--repo` when no `gh-issue.repo` is configured, matching step 4 — labels must be created in the same repo the issue lands in.)

4. **Create the issue.** Map the drafted task: `--title` ← `title`, body ← step 2, `--label`/`--assignee` ← config, `--repo` ← `gh-issue.repo` if set.

   ```bash
   gh issue create \
     --repo "<repo>" \
     --title "<title>" \
     --label "<label>" --label "<label2>" \
     --assignee "<assignee>" --assignee "<assignee2>" \
     --body-file "<path-to-body>"
   ```

   (Omit `--repo` to use the current repo; omit `--label`/`--assignee` flags that have no configured values.)

5. **Return the URL.** `gh issue create` prints the new issue URL to stdout — capture it and return it as this handler's artifact URL for `/add-task` step 8.

This handler does **not** create any `dev_docs/tasks/*.md` file, branch, or PR.

## List

Invoked from `/list-tasks` when `handler: gh-issue` is configured. Read-only — one `gh issue list` query, no edits, no claims. Renders the repo's issues as the same vertical-section kanban the file-based path uses, so `$ARGUMENTS` and the layout match `commands/list-tasks.md` step 4.

> **Coverage note.** `gh-issue` is a create-only handler — there is no gh-issue promote/claim/execute flow that sets status labels — so in practice most issues land in `new` (open) or `done` (closed). The status-label mapping below is honored when those labels happen to be present (e.g. set by hand or a board automation), but a plain gh-issue setup renders a two-column `new`/`done` board, which is the expected v1 behavior.

1. **Preflight auth.** Run `gh auth status 2>&1`. If it fails, use the same handling as the create flow's step 1 (TLS/x509 → sandbox keychain hint; otherwise report the auth failure) and **stop** — do not fall back to another handler.

2. **Query issues.** List the configured repo's issues, honoring `gh-issue.repo` and `gh-issue.labels`:

   ```bash
   gh issue list \
     --repo "<repo>" \
     --label "<label>" --label "<label2>" \
     --state all \
     --limit 50 \
     --json number,title,labels,assignees,state,createdAt
   ```

   - Omit `--repo` when no `gh-issue.repo` is configured (uses the current repo), matching the create flow.
   - Pass one `--label` flag per entry in `gh-issue.labels` (AND filter) so the board shows only the issues this loop files. Omit `--label` entirely when no labels are configured.
   - `--state all` is required so closed issues populate the `done` section (`gh issue list` defaults to open only).

3. **Group into kanban sections.** Classify each issue by `state` plus label presence. Reuse the same label vocabulary as the Linear mapping in `linear-common.md` so a board behaves consistently across trackers:

   | Section            | Match rule                                                                                                                       |
   | ------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
   | `new`              | `open`, none of the status labels below present                                                                                  |
   | `needs_refinement` | `open`, has `human-approval-requested`                                                                                           |
   | `ready`            | `open`, has `auto-eligible`                                                                                                      |
   | `in_progress`      | `open`, has `auto-claimed` (or `in-progress`), no `blocked`                                                                      |
   | `blocked`          | `open`, has `blocked`                                                                                                            |
   | `needs_review`     | `open`, has `needs-review` (best-effort — `gh issue list` does not return linked PRs, so do not call extra tools to detect them) |
   | `done`             | `closed` — limit to the 10 most recent by `createdAt`                                                                            |

   If an issue matches more than one rule, prefer the more actionable signal in this order: `blocked` > `needs_review` > `in_progress` > `ready` > `needs_refinement`.

4. **Render** as stacked vertical sections in the fixed order `new → needs_refinement → ready → in_progress → blocked → needs_review → done`, using the same `## <section> (N)` header, single-line bullet, and `---` separator layout as `commands/list-tasks.md` step 4 (don't re-specify it). Card line:

   ```
   - [high] #142 Fix broken import — assignee dan
   ```

   Field mapping (vs. the `repo-pr` card line, which uses slug + frontmatter):

   | Field       | Source                                                                                                         |
   | ----------- | -------------------------------------------------------------------------------------------------------------- |
   | Priority    | A `priority:<urgent\|high\|medium\|low>` label if present; otherwise render `[—]` and sort it last in the tier |
   | Identifier  | `#<number>`                                                                                                    |
   | Title       | issue `title`                                                                                                  |
   | Assignee    | first `assignees[].login` (omit `— assignee …` when unassigned)                                                |
   | Annotations | `human-approval-requested`, `blocked`, `needs-review` — bare label name when the matching label is present     |

   Sort within each section by priority (`urgent > high > medium > low`, none last), then `createdAt` (oldest first).

5. **Summary line.** Same shape as the file-based path:

   ```
   8 issues (1 new, 1 needs_refinement, 2 ready, 1 in_progress, 0 blocked, 2 needs_review, 1 done)
   ```

6. **Filter argument.** If `$ARGUMENTS` is a section name (`new|needs_refinement|ready|in_progress|blocked|needs_review|done|all`), render only that section. Default: every non-empty section.

7. **Empty board.** If the query returns no issues, report `No tasks found in <repo>.` (or `… in this repo.` when `gh-issue.repo` is unset) rather than erroring.
