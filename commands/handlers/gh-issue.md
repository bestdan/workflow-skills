# Handler: gh-issue

Creates a GitHub Issue via the `gh` CLI. Runs in the **foreground in the current session** — one API call, no git plumbing, no remote/subagent/local cascade.

Config block in `dev_docs/tasks/.task-config.yml`:

```yaml
handler: gh-issue
gh-issue:
  repo: owner/name # optional; defaults to the current repo
  labels: [follow-up] # optional; each is created if missing
  assignees: [] # optional GitHub usernames
  remote_batch: true # optional; true/absent → /do-tasks --all dispatches one remote
  # session per issue (each self-checks for `gh`). false → --all degrades to a single
  # foreground claim. The deterministic opt-out for a host whose VMs have no `gh` —
  # same role as `linear.remote_batch`. See commands/do-tasks.md §4 "gh-issue batch".
  max_estimate: 3 # optional — upper bound /promote-tasks gates an issue's `est:` label against.
  # Same key name, same Fibonacci scale and same default (3) as `linear.max_estimate`
  # (see commands/handlers/linear-common.md "Config block"). gh-issue has no per-project
  # list, so there is no per-scope override — this one value covers the repo.
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

   > When the caller supplies `is_blocked_by` as **issue references** rather than
   > task slugs — e.g. `/push-plan` translates blockers to `#142`, `#143` after
   > creating them in dependency order — render this line as
   > `Blocked by: #142, #143` instead of `Blocked by task: …`, so the footer is a
   > real cross-issue link. It is a **human-readable echo** of the native
   > `blocked_by` edge `/push-plan` §5.5 draws, never a substitute for it —
   > `/list-tasks` and `/do-tasks` read the native edge, not the footer, and so
   > does `/reoptimize-tasks` (`gh-issue-reoptimize.md`), which creates and
   > removes edges and reconciles this footer against them.

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

   Then stamp the new issue's initial state. Take `<n>` from the trailing path segment of the URL `gh issue create` printed, and `<repo>` from `gh-issue.repo` — the writer's `--repo` is required, so when the key is unset resolve it with `gh repo view --json nameWithOwner --jq .nameWithOwner` rather than omitting the flag:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-state.py" \
     --repo "<repo>" --issue <n> \
     --labels status:0_untriaged,auto:human-review-needed --apply
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/gh-issue-state.py`. The write replaces the whole label set but carries forward everything outside the four managed namespaces, so the configured `gh-issue.labels` (`follow-up` and friends) survive it — see `commands/handlers/assets/labels.yml` for the vocabulary and its invariants.

   **The pair is the point.** `status:` says where the work is; `auto:` says whether automation may take it. They are independent axes, and a fresh issue is neither scored nor automation's to touch, so it gets a rung on each. That is what keeps an unscored issue _visibly_ unscored: it renders in `new`, it is exactly what `/promote-tasks` selects, and it appears in no automation queue. One conflated label could not say both, so it would have to default the issue into one of those queues by omission.

5. **Return the URL.** `gh issue create` prints the new issue URL to stdout — capture it and return it as this handler's artifact URL for `/add-task` step 8.

This handler does **not** create any `dev_docs/tasks/*.md` file, branch, or PR.

## List

Invoked from `/list-tasks` when `handler: gh-issue` is configured. Read-only — one `gh issue list` query, no edits, no claims. Renders the repo's issues as the same vertical-section kanban the file-based path uses, so `$ARGUMENTS` and the layout match `commands/list-tasks.md` step 4.

> **Coverage note.** `gh-issue` supports capture (`/add-task`), list (this section), **promote** (`/promote-tasks` → `commands/handlers/gh-issue-promote.md`, which scores an issue to `status:2_ready` + `auto:eligible` or `status:1_needs_refinement` + `auto:human-review-needed`), and single **do** (`/do-tasks` → `commands/handlers/gh-issue-claim.md`, which claims an issue — an atomic `<branch_prefix>task-<n>` ref-creation lock plus the `@me` + `status:3_started` board marker — and swaps to the needs-review rung on PR open). So issues now move `new → needs_refinement`/`ready → in_progress → needs_review` through these commands, and reach `done` when the PR merges via `Closes #<n>` — with `/complete-task` → `commands/handlers/gh-issue-complete.md` as the explicit fallback when that auto-close doesn't fire. Batch `/do-tasks --all` is supported too — one dispatched remote session per dependency-ready issue, bounded by the WIP limit (`commands/do-tasks.md` §4 "gh-issue batch").

**The vocabulary lives in `commands/handlers/assets/labels.yml`** — four namespaces (`status:`, `auto:`, `prio:`, `est:`) and the invariants that govern them. Read the names from there; never hardcode one. Two of those invariants decide how this section reads a board: an **open** issue carries exactly one `status:` and exactly one `auto:` rung, and a **closed** issue carries neither while keeping its `prio:`/`est:`. `status:` and `auto:` answer different questions — where the work is, versus whether automation may take it — so listing reads `status:` for the section and ignores `auto:` entirely.

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

3. **Group into kanban sections.** Classify each issue by `state` plus its `status:` rung. The sections mirror the Linear mapping in `linear-common.md` so a board behaves consistently across trackers:

   | Section            | Match rule                                                                                                                                |
   | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
   | `new`              | `open`, has `status:0_untriaged` — **or** no `status:` label at all (an issue filed outside the loop, or before the repo was provisioned) |
   | `needs_refinement` | `open`, has `status:1_needs_refinement`                                                                                                   |
   | `ready`            | `open`, has `status:2_ready` and no open dependency (see **Blocked** below)                                                               |
   | `in_progress`      | `open`, has `status:3_started`                                                                                                            |
   | `blocked`          | `open`, has the `blocked` label — **or** has `status:2_ready` with an open `blocked_by` dependency (see **Blocked** below)                |
   | `needs_review`     | `open`, has `status:4_needs_review` — best-effort: `gh issue list` does not return linked PRs, so call no extra tool to detect them       |
   | `done`             | `closed` — select the 10 most recent by `createdAt`, then sort per step 4                                                                 |

   If an issue matches more than one rule, prefer the more actionable signal in this order: `blocked` > `needs_review` > `in_progress` > `ready` > `needs_refinement`.

   **Blocked has two independent sources; report both in that one section.**
   - **The `blocked` label** is a manual override, and stays the only way to mark an issue blocked by something outside GitHub. It sits outside the four managed namespaces, so `gh-issue-state.py` carries it forward through every state write instead of dropping it — a human's override survives a promote or a claim.
   - **An open dependency** comes from GitHub's native dependency graph, not from a label. Read it per issue at `repos/<repo>/issues/<n>/dependencies/blocked_by`; the issue is dependency-blocked when any entry is still `open`.

     **Check dependencies for `status:2_ready` issues only** — one `gh api` GET each, and readiness is the only claim an open blocker contradicts. No other section asserts anything about dependencies, so spending a call on all 50 buys nothing. `commands/handlers/assets/gh-issue-ready.py` already runs exactly this pass and splits the ready issues into ready and blocked; run it rather than re-deriving the loop:

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-ready.py" \
       --repo "<repo>" --json [--label "<label>" --label "<label2>"]
     ```

     Pass one `--label` per entry in `gh-issue.labels`, the same filters step 2 used. Without them the helper draws its own 50-issue window over the **whole** repo, so an in-scope issue can fall outside it and get no verdict at all — and a missing verdict is indistinguishable from a ready one, which the intersection below cannot detect.

     If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/gh-issue-ready.py`. `--repo` is required, so resolve the current repo with `gh repo view --json nameWithOwner --jq .nameWithOwner` when `gh-issue.repo` is unset. **Intersect its output with the issue numbers step 2 returned**, then move each of those it reports as blocked into the `blocked` section, annotated with the open blockers it names. The helper queries the whole repo, while step 2 may be narrowed by `gh-issue.labels` (which defaults to `[follow-up]`), so applying its verdicts unfiltered would put a card on the board that this board never listed.

4. **Render** as stacked vertical sections in the fixed order `new → needs_refinement → ready → in_progress → blocked → needs_review → done`, using the same `## <section> (N)` header, single-line bullet, and `---` separator layout as `commands/list-tasks.md` step 4 (don't re-specify it). Card line:

   ```
   - [p1] #142 Fix broken import (est 3) — assignee dan
   ```

   Field mapping (vs. the `repo-pr` card line, which uses slug + frontmatter):

   | Field       | Source                                                                                                                                                       |
   | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
   | Priority    | the `prio:<0-3>` label if present, rendered `[p<n>]`; otherwise `[—]`, sorted last in the tier                                                               |
   | Identifier  | `#<number>`                                                                                                                                                  |
   | Title       | issue `title`                                                                                                                                                |
   | Estimate    | the `est:<n>` label if present, rendered `(est <n>)` after the title; omit the parenthetical when the issue carries none                                     |
   | Assignee    | first `assignees[].login` (omit `— assignee …` when unassigned)                                                                                              |
   | Annotations | `blocked` when the label is present, and `waiting on #<n>[, #<m>]` for a dependency-blocked issue — comma-separated and appended after the assignee with `—` |

   Sort within each section by priority (`prio:0` first through `prio:3`, none last), then `createdAt` (oldest first).

5. **Summary line.** Same shape as the file-based path:

   ```
   8 issues (1 new, 1 needs_refinement, 2 ready, 1 in_progress, 0 blocked, 2 needs_review, 1 done)
   ```

6. **Filter argument.** If `$ARGUMENTS` is a section name (`new|needs_refinement|ready|in_progress|blocked|needs_review|done|all`), render only that section. Default: every non-empty section.

7. **Empty board.** If the query returns no issues, report `No tasks found in <repo>.` (or `… in this repo.` when `gh-issue.repo` is unset) rather than erroring.
