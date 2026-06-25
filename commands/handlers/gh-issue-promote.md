# gh-issue handler — /promote-tasks flow

Invoked from `/promote-tasks` when `handler: gh-issue` is configured. Scores the repo's **open, un-scored** issues against the same confidence check the file path uses, then applies the kanban transitions via labels: HIGH → add `auto-eligible` (the gh analogue of moving to `Todo`); LOW → add `human-approval-requested` and leave a comment naming the failed check.

**Shared reference:** the status-label vocabulary is defined in the `## List` section of `commands/handlers/gh-issue.md` (and mirrors `linear-common.md`); the field-mapped confidence gate is `commands/handlers/linear-promote.md` step 6, read here against the GitHub issue rather than Linear fields. Reuse those labels — do **not** invent `task:*` labels.

> **Hard rule: this path only ever touches open issues that carry neither `auto-eligible` nor `human-approval-requested`.** Those two labels are the gh analogue of "past the `new` column" — an issue that has one has already been scored and is out of the promoter's lane, exactly as the file path never touches tasks past `status: new`, and as `linear-promote.md` never touches a non-`backlog` issue. Closed issues are `done` and are never scored. If you are about to `gh issue edit` an already-labeled or closed issue, you have a bug — stop.

## Steps

### 1. Preflight auth

Run `gh auth status 2>&1`. If it fails, use the same handling as the gh-issue create flow (`commands/handlers/gh-issue.md` step 1): TLS/x509/certificate → likely the sandbox blocking keychain access, tell the user to re-run outside sandbox mode; otherwise report the auth failure. Either way **stop** — do not fall back to another handler.

### 2. Resolve the repo

If `gh-issue.repo` is set in `dev_docs/tasks/.task-config.yml`, pass it as `--repo <repo>` on every `gh` call below. Otherwise omit `--repo` to act on the current repo, matching the create and list flows.

### 2a. Resolve the milestone scope

By default this flow scores a **single** milestone's issues, not the whole repo backlog (the gh analogue of `linear-promote.md` step 4 scoping to one project). The gh-issue handler has no pin key — scope is always detected, or explicitly widened with `all`. Resolve one milestone title (`<scope-milestone>`) or none, in this order:

1. **`all` override.** If `$ARGUMENTS` contains `all`, set **no** `<scope-milestone>` — score the whole repo backlog. Note `scope: whole backlog (all)` in the step-6 report and skip the rest of this step.
2. **Detect.** Else enumerate open milestones (`<repo>` is `gh-issue.repo` if set, else `gh repo view --json nameWithOwner --jq .nameWithOwner`):

   ```bash
   gh api "repos/<repo>/milestones?state=open" --jq '.[] | {number, title, updated_at}'
   ```

   - **Exactly one** → use its `title` as `<scope-milestone>`. Note `scope: milestone <title>` in the report.
   - **Multiple** → ask via `AskUserQuestion` (header `Milestone`) which one to score: offer the most recently updated milestones by `title` (cap at 3, so 3 + the escape option fits the 4-option max) plus an explicit **`All — whole backlog`** option. If the user picks `All — whole backlog`, set **no** `<scope-milestone>` (as in the `all` override). Otherwise use the chosen milestone's `title`.
   - **Zero** → set **no** `<scope-milestone>`; fall back to the whole repo backlog. Note `scope: whole backlog (no milestones)` in the report.

When `<scope-milestone>` is set, step 3 adds `--milestone "<scope-milestone>"` to the `gh issue list` query; when unset, no milestone filter is added.

### 3. Query candidates

```bash
gh issue list --state open --search "-label:auto-eligible -label:human-approval-requested" --limit 50 --json number,title,body,labels [--milestone "<scope-milestone>"] [--repo <repo>]
```

- `--state open` only — closed issues are `done` and are never scored.
- The `--search` filter excludes issues that already carry `auto-eligible` or `human-approval-requested` **at query time** — mirroring how `linear-promote.md` reads candidates only from the `backlog` state — so the 50-item window isn't consumed by already-scored issues. (When `--search` is used, label filters must live in the search string, not a separate `--label` flag.)
- `--milestone "<scope-milestone>"` only when step 2a resolved a milestone scope; omit it on an `all`/no-milestone run.
- Limit 50. If exactly 50 issues are returned the page may be truncated — note possible truncation in the report; do not paginate.

Set aside (do **not** score) — as a backstop to the query filter — any already-`auto-eligible`/`human-approval-requested` issue that still slips through (e.g. label-index lag): the promoter, like the file path, only acts on issues that have not yet been scored (the gh analogue of `status: new`). Keep these in a separate `skipped` list so step 6 can report them; they receive no `gh issue edit`. Report and exit if no un-scored candidates remain.

### 3a. Filter parent rollups via GraphQL

Set aside any candidate that is a **parent rollup** — an issue broken into sub-issues that now serves only as a shell. Promoting a parent rollup would move an empty shell to `auto-eligible` where `/do-tasks` would try to claim it (the gh analogue of `linear-promote.md` step 5).

GitHub's `gh issue list` does not expose sub-issue counts as a filterable field, but `gh api graphql` can retrieve them in bulk for all open issues in one call:

```bash
REPO="<repo>"  # gh-issue.repo from .task-config.yml if set (step 2a); else resolve cwd
if [ -z "$REPO" ]; then
  REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
fi
OWNER=${REPO%%/*}
REPONAME=${REPO##*/}
gh api graphql -f query='
  query($owner: String!, $repo: String!) {
    repository(owner: $owner, name: $repo) {
      issues(states: [OPEN], first: 100) {
        nodes { number subIssues(first: 0) { totalCount } }
      }
    }
  }' -F owner="$OWNER" -F repo="$REPONAME"
```

Build a parent-number set from every issue in the response where `subIssues.totalCount > 0`. Any candidate whose number appears in that set is a parent rollup — add it to the `skipped` list with reason `parent rollup` and exclude it from scoring.

**Fallback:** if the GraphQL call errors with an unknown-field error on `subIssues` (the field is available only on repos/orgs where GitHub's sub-issues feature is active), skip parent detection and note `parent rollup detection skipped (subIssues field unavailable)` in the step-6 report. The promote run still continues with all remaining candidates.

### 4. Score each candidate

For each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against the GitHub issue per the field mapping `linear-promote.md` step 6 defines. GitHub issues carry no native `priority` or `estimate`/`size` field, so those map to optional labels or fold into the scope judgment, as noted below.

**HIGH (→ promote)** requires ALL of:

- `title` present and non-empty.
- **Not** labeled `priority:urgent` (the gh analogue of Linear `priority` urgent, which fails). A _missing_ priority label does **not** fail — unlike Linear's required `priority`, GitHub priority is optional metadata expressed by an opt-in `priority:<urgent|high|medium|low>` label.
- `body` contains acceptance-style content — a `## Acceptance Criteria` section (or an equivalent concrete, checkable outcome). A bare title or an "investigate X" body fails with `body missing acceptance criteria`.
- `body` has no unresolved `## Open Questions` / `## TBD` content (an empty heading is fine) → otherwise `unresolved open questions`.
- **Scope fits one PR (~size 5), judgment not keywords.** Weigh the body's breadth against ~300 lines / ~5 files (see **Task size** in `skills/task/SKILL.md`); a `size:<n>` label, if the repo uses one, is a hint. Because gh issues have no estimate field, the file path's "`size` is one of 1/2/3/5" check folds into this judgment. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `body missing acceptance criteria`, `priority is urgent`, `unresolved open questions`).

As on the file path, the scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands labeled `human-approval-requested` for a human to confirm, never silently lost.

### 5. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions (per the report shape below) and exit **without** any `gh issue edit`/`gh issue comment`.

Otherwise, for each scored candidate:

- **HIGH:**

  ```bash
  gh issue edit <n> --add-label auto-eligible [--repo <repo>]
  ```

- **LOW:**

  ```bash
  gh issue edit <n> --add-label human-approval-requested [--repo <repo>]
  gh issue comment <n> --body "/promote-tasks: <failed-check>" [--repo <repo>]
  ```

  The comment names the failed check so the human can fix it quickly — the gh analogue of the file path's `# promoter:` frontmatter comment and `linear-promote.md`'s LOW comment.

Create the label first if `gh issue edit` errors on a missing label (`gh label create "<label>" [--repo <repo>] 2>/dev/null`), mirroring how the create flow ensures labels exist. Never close an issue and never remove an existing label here — promotion only adds a status label.

### 6. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by issue number. Lead with the resolved scope from step 2a (`scope: milestone <title>` / `scope: whole backlog (all)` / `scope: whole backlog (no milestones)`) so it's clear what the run covered:

```
scope: milestone v2.0
Promoted 4 of 6 candidates:
  ready (3):
    - #142  Fix broken import
    - #145  Bump eslint config
    - #148  Remove stale alias
  needs_refinement (1):
    - #151  Restructure auth module  (scope exceeds size 5 — split into sub-issues)
  skipped (2):
    - #109  (already scored)
    - #110  (parent rollup)
```

Skipped issues are reported with their reason — `already scored` or `parent rollup`. Append the truncation note from step 3 if it applied. If parent rollup detection was skipped due to the `subIssues` field being unavailable, append that note here too.
