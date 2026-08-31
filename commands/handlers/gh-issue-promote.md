# gh-issue handler — /promote-tasks flow

Invoked from `/promote-tasks` when `handler: gh-issue` is configured. Scores the repo's **open, un-scored** issues against the same confidence check the file path uses, then applies the kanban transition through the schema writer: HIGH → `status:2_ready` + `auto:eligible` (the gh analogue of moving to `Todo`); LOW → `status:1_needs_refinement` + `auto:human-review-needed`, plus a comment naming the failed check.

**Shared reference:** the label vocabulary is `commands/handlers/assets/labels.yml` and the sections it drives are the `## List` table in `commands/handlers/gh-issue.md` (which mirrors `linear-common.md`); the field-mapped confidence gate is `commands/handlers/linear-promote.md` step 6, read here against the GitHub issue rather than Linear fields. Read label names from `labels.yml` — do **not** invent `task:*` labels or hardcode a spelling.

Scoring writes **both** rungs because they answer different questions: `status:` is where the work is, `auto:` is whether automation may take it. A HIGH issue is ready _and_ released to automation; a LOW issue needs a human _and_ is withheld from it. The old single `auto-eligible` label conflated the two, so there was no way to say "ready, but a human takes this one".

> **Hard rule: this path only ever touches open issues that are un-scored — `status:0_untriaged`, or carrying no `status:` label at all (a pre-migration issue).** Any other `status:` rung is the gh analogue of "past the `new` column": the issue has been scored and is out of the promoter's lane, exactly as the file path never touches tasks past `status: new`, and as `linear-promote.md` never touches a non-`backlog` issue. Closed issues are `done` and are never scored. If you are about to write to an already-scored or closed issue, you have a bug — stop.

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
gh issue list --state open --search '-label:"status:1_needs_refinement" -label:"status:2_ready" -label:"status:3_started" -label:"status:4_needs_review" -label:blocked' --limit 50 --json number,title,body,labels [--milestone "<scope-milestone>"] [--repo <repo>]
```

- `--state open` only — closed issues are `done` and are never scored.
- The `--search` filter selects the **un-scored** issues by excluding every `status:` rung past `0_untriaged`. Stating it as an exclusion rather than `label:"status:0_untriaged"` is deliberate: a pre-migration issue carries no `status:` label at all and must still be a candidate, and an inclusion filter would drop it. This mirrors how `linear-promote.md` reads candidates only from the `backlog` state, and keeps the 50-item window from being consumed by already-scored issues. Quote each label value — the names contain a colon, which is also the search syntax's own separator. The filter also excludes `blocked`-labeled issues, holding them in the backlog rather than promoting them (mirroring the file path's "hold blocked cards" rule — see `commands/promote-tasks.md`). (When `--search` is used, label filters must live in the search string, not a separate `--label` flag.)
- `--milestone "<scope-milestone>"` only when step 2a resolved a milestone scope; omit it on an `all`/no-milestone run.
- Limit 50. If exactly 50 issues are returned the page may be truncated — note possible truncation in the report; do not paginate.

Set aside (do **not** score) — as a backstop to the query filter — any issue whose returned `labels` carry a `status:` rung other than `0_untriaged` and that still slips through (e.g. label-index lag, or a quoting failure in the search string): the promoter, like the file path, only acts on issues that have not yet been scored (the gh analogue of `status: new`). Keep these in a separate `skipped` list so step 6 can report them; they receive no write. Likewise, any `blocked`-labeled issue that slips through the `--search` filter is set aside with reason `blocked` (no write), reported in step 6. Report and exit if no un-scored candidates remain.

### 3a. Filter parent rollups via GraphQL

Set aside any candidate that is a **parent rollup** — an issue broken into sub-issues that now serves only as a shell. Promoting a parent rollup would move an empty shell to `status:2_ready` + `auto:eligible`, where `/do-tasks` would try to claim it (the gh analogue of `linear-promote.md` step 5).

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

For each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against the GitHub issue per the field mapping `linear-promote.md` step 6 defines. GitHub issues carry no native `priority` or `estimate` field, so both live as the optional `prio:`/`est:` labels from `commands/handlers/assets/labels.yml`. Neither is required for a HIGH score, and this flow never backfills either — unlike `linear-promote.md` step 6, which writes a backfilled `estimate` into a native field. Scoring only reads `est:`, as the gate below.

**HIGH (→ promote)** requires ALL of:

- `title` present and non-empty.
- `body` contains acceptance-style content — a `## Acceptance Criteria` section (or an equivalent concrete, checkable outcome). A bare title or an "investigate X" body fails with `body missing acceptance criteria`.
- `body` has no unresolved `## Open Questions` / `## TBD` content (an empty heading is fine) → otherwise `unresolved open questions`.
- **The `est:` gate, when the issue carries an `est:` label.** Read `gh-issue.max_estimate` from the merged `dev_docs/tasks/.task-config.yml` (default `3` when unset — same key, scale and default as `linear.max_estimate`; see `commands/handlers/gh-issue.md`'s config block). The bound is **exclusive**, matching Linear: `linear-ready.py` gates on `estimate >= max_estimate`, so `est:5` fails against `max_estimate: 5`. An `est:<n>` at or above the bound scores LOW with reason `estimate <n> >= max_estimate <m>` — the same reason string Linear emits, so a board reads identically across trackers. This is a deterministic check on a recorded number, so it runs before the judgment call below and wins the reason slot.

- **Scope fits one PR (~size 5), judgment not keywords.** For an issue carrying **no** `est:` label, weigh the body's breadth against ~300 lines / ~5 files (see **Task size** in `skills/task/SKILL.md`). Do **not** invent an estimate and do not write one: an absent `est:` means nobody has sized the issue, and a guessed number would be indistinguishable downstream from a human's. If the scope clearly exceeds size `5`, score LOW with reason `scope exceeds size 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split. An issue that **has** an `est:` label has already passed the deterministic gate above and needs no second scope judgment.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `body missing acceptance criteria`, `unresolved open questions`).

As on the file path, the scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands labeled `human-approval-requested` for a human to confirm, never silently lost.

### 5. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions (per the report shape below) and exit **without** any write and **without** any `gh issue comment`.

Every transition goes through `commands/handlers/assets/gh-issue-state.py`, never `gh issue edit --add-label` / `--remove-label`. That helper validates the requested names against `labels.yml` and then issues **one** full-set PATCH; the incremental `gh issue edit` form is not atomic (measured: 8 HTTP requests), so a crash mid-way strands an issue carrying two `status:` rungs or none. The helper's own docstring carries the measurements.

The consequence for this flow is that a transition is a **read-modify-write on the managed axes**. The PATCH replaces the issue's entire label set. The helper carries forward every label _outside_ the four managed namespaces by itself — `follow-up`, `blocked`, `bug`, anything a human added — but it carries forward nothing _inside_ them, and `--labels status:2_ready` on its own is refused by its validator for want of an `auto:` rung. So read the issue's current managed labels first and pass the complete resulting set:

```bash
gh issue view <n> --json labels --jq '[.labels[].name]' [--repo <repo>]
```

Keep that issue's `prio:` and `est:` labels verbatim, drop its `status:`/`auto:` rungs, and append the new pair. An issue with `prio:1,est:3` promoted HIGH is written as `status:2_ready,auto:eligible,prio:1,est:3` — omitting `prio:1,est:3` from the `--labels` value would delete them.

Then, for each scored candidate (`<managed>` is the set just assembled):

- **HIGH** — `status:2_ready` + `auto:eligible`, plus the issue's existing `prio:`/`est:`:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-state.py" \
    --repo "<repo>" --issue <n> \
    --labels status:2_ready,auto:eligible[,<prio:…>][,<est:…>] --apply
  ```

- **LOW** — `status:1_needs_refinement` + `auto:human-review-needed`, plus the issue's existing `prio:`/`est:`:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-state.py" \
    --repo "<repo>" --issue <n> \
    --labels status:1_needs_refinement,auto:human-review-needed[,<prio:…>][,<est:…>] --apply
  gh issue comment <n> --body "/promote-tasks: <failed-check>" [--repo <repo>]
  ```

  The comment names the failed check so the human can fix it quickly — the gh analogue of the file path's `# promoter:` frontmatter comment and `linear-promote.md`'s LOW comment.

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/gh-issue-state.py`. `--repo` is required by the helper, so resolve the current repo with `gh repo view --json nameWithOwner --jq .nameWithOwner` when `gh-issue.repo` is unset; drop `--apply` to see the resulting set without writing it. No `gh label create` step is needed here — the raw PATCH creates a missing label rather than rejecting it, and `/task-config` provisions the vocabulary with its intended colors (`commands/handlers/gh-issue-config.md` step 3).

**Never close an issue here**, and never reach for `--done`/`--reopen`: promotion only moves an open issue between rungs.

### 6. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by issue number. Lead with the resolved scope from step 2a (`scope: milestone <title>` / `scope: whole backlog (all)` / `scope: whole backlog (no milestones)`) so it's clear what the run covered:

```
scope: milestone v2.0
Promoted 5 of 8 candidates:
  ready (3):
    - #142  Fix broken import
    - #145  Bump eslint config
    - #148  Remove stale alias
  needs_refinement (2):
    - #151  Restructure auth module  (scope exceeds size 5 — split into sub-issues)
    - #152  Rewrite the config loader  (estimate 8 >= max_estimate 3)
  skipped (3):
    - #109  (already scored)
    - #110  (parent rollup)
    - #111  (blocked)
```

Skipped issues are reported with their reason — `already scored`, `parent rollup`, or `blocked`. Append the truncation note from step 3 if it applied. If parent rollup detection was skipped due to the `subIssues` field being unavailable, append that note here too.
