# gh-issue handler — /promote-tasks flow

Invoked from `/promote-tasks` when `handler: gh-issue` is configured. Scores the repo's **open, un-scored** issues against the same confidence check the file path uses, then applies the kanban transition through the schema writer: HIGH → `status:2_ready` + `auto:eligible` (the gh analogue of moving to `Todo`); LOW → `status:1_needs_refinement` + `auto:human-review-needed`, plus a comment naming the failed check.

**Shared reference:** the label vocabulary is `commands/handlers/assets/labels.yml` and the sections it drives are the `## List` table in `commands/handlers/gh-issue.md` (which mirrors `linear-common.md`); the field-mapped confidence gate is `commands/handlers/linear-promote.md` step 6, read here against the GitHub issue rather than Linear fields. `labels.yml` is the vocabulary: re-read it before composing a query, and never use a name that is not in it — no invented `task:*` labels. The literal spellings written into this file are that vocabulary as it stands, not a second source of truth; a rename there fails loudly rather than silently, because `gh-issue-state.py` validates every name against the file before any network call.

Scoring writes **both** rungs because they answer different questions: `status:` is where the work is, `auto:` is whether automation may take it. A HIGH issue is ready _and_ released to automation; a LOW issue needs a human _and_ is withheld from it. The old single `auto-eligible` label conflated the two, so there was no way to say "ready, but a human takes this one".

> **Hard rule: this path only ever touches open issues that are un-scored — `status:0_untriaged`, or carrying no `status:` label at all (a pre-migration issue).** Any other `status:` rung is the gh analogue of "past the `new` column": the issue has been scored and is out of the promoter's lane, exactly as the file path never touches tasks past `status: new`, and as `linear-promote.md` never touches a non-`backlog` issue. Closed issues are `done` and are never scored. If you are about to write to an already-scored or closed issue, you have a bug — stop.
>
> **`backfill-only` is not an exception to that rule — it is a different write.** The rule guards the _transition_: which rung an issue sits on, and whether automation may take it. `backfill-only` (step 7) never touches either. It writes `prio:`/`est:` and nothing else, at any rung, and it moves nothing. The two coexist because scoring is one-way and one-time while a missing estimate is a gap that outlives it: an issue scored before this flow learned to backfill has no `prio:`/`est:` and no other way to get one.

## Steps

> **`backfill-only` short-circuits this flow.** If `$ARGUMENTS` contains `backfill-only`, run step 1 (auth) and step 2 (repo), then go straight to **step 7** and stop. Step 7's helper requires `--repo`, so resolve it even when step 2 would have omitted the flag: `<repo>` is `gh-issue.repo` from `.task-config.yml` if set, else `gh repo view --json nameWithOwner --jq .nameWithOwner`. Steps 2a–6 are the scoring flow, and `backfill-only` does not score: no candidate query, no confidence check, no transition, no report of promotions that did not happen.

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
gh issue list --state open --search '-label:"status:1_needs_refinement" -label:"status:2_ready" -label:"status:3_started" -label:"status:4_needs_review" -label:blocked' --limit 500 --json number,title,body,labels [--milestone "<scope-milestone>"] [--repo <repo>]
```

- `--state open` only — closed issues are `done` and are never scored.
- The `--search` filter selects the **un-scored** issues by excluding every `status:` rung past `0_untriaged`. Stating it as an exclusion rather than `label:"status:0_untriaged"` is deliberate: a pre-migration issue carries no `status:` label at all and must still be a candidate, and an inclusion filter would drop it. This mirrors how `linear-promote.md` reads candidates only from the `backlog` state, and keeps the 500-item window from being consumed by already-scored issues. Quote each label value — the names contain a colon, which is also the search syntax's own separator. The filter also excludes `blocked`-labeled issues, holding them in the backlog rather than promoting them (mirroring the file path's "hold blocked cards" rule — see `commands/promote-tasks.md`). That is only the **label** half of the hold: an open native dependency is the other, and no search term can express it, so step 3b reads it per candidate. (When `--search` is used, label filters must live in the search string, not a separate `--label` flag.)
- `--milestone "<scope-milestone>"` only when step 2a resolved a milestone scope; omit it on an `all`/no-milestone run.
- Limit 500 — a soft cap, not a hard page size (`gh issue list --limit` fetches through as many API pages as needed under the hood, so this is effectively exhaustive for any repo backlog short of the cap; mirrors the ceiling `gh-issue-reoptimize.md` uses for the same reason). If exactly 500 issues are returned, the query may be truncated: surface that as its own prominent report line in step 6 (not a footnote) rather than paginating further — see step 6.

Set aside (do **not** score) — as a backstop to the query filter — any issue whose returned `labels` carry a `status:` rung other than `0_untriaged` and that still slips through (e.g. label-index lag, or a quoting failure in the search string): the promoter, like the file path, only acts on issues that have not yet been scored (the gh analogue of `status: new`). Keep these in a separate `skipped` list so step 6 can report them; they receive no write. Likewise, any `blocked`-labeled issue that slips through the `--search` filter is **held** with reason `blocked label` (no write), reported in step 6. Report and exit if no un-scored candidates remain.

### 3a. Filter parent rollups

Set aside any candidate that is a **parent rollup** — an issue broken into sub-issues that now serves only as a shell. Promoting a parent rollup would move an empty shell to `status:2_ready` + `auto:eligible`, where `/do-tasks` would try to claim it (the gh analogue of `linear-promote.md` step 5).

`gh issue list` does not expose sub-issue counts as a filterable field, so the set comes from a bulk GraphQL query. Do **not** write that query inline here — run the helper:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-rollups.py" --repo "<repo>"
```

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/gh-issue-rollups.py` (the same fallback step 5 uses for `gh-issue-state.py`). `<repo>` is `gh-issue.repo` from `.task-config.yml` if set (step 2), else `gh repo view --json nameWithOwner --jq .nameWithOwner`.

The helper paginates to exhaustion, validates every page's shape, and fails closed on any doubt. It is a file rather than a fenced block here for a reason: seven defects were found in the block this replaces, every one of them a wrong or empty result that read as a clean run, and none of them reachable by the gate. Its docstring names each one; `scripts/test_gh_issue_rollups.py` pins them.

**Read its stdout — that is the contract.** Steps run as separate tool calls with no shared shell state, so nothing survives the invocation except what it printed:

- `ROLLUP_OK=1` on the first line, then the parent issue numbers, one per line, sorted and unique. **`ROLLUP_OK=1` with no numbers means the repo genuinely has no rollups** — a real, successful, empty answer.
- `ROLLUP_OK=0` then `ROLLUP_REASON=<reason>`, with a non-zero exit, on any failure. The reason is the text to quote in the fallback below.

Any candidate whose number appears in the list is a parent rollup — add it to the `skipped` list with reason `parent rollup` and exclude it from scoring.

**Fallback:** on `ROLLUP_OK=0`, skip parent detection and continue the run with all remaining candidates, but **lead** the step-6 report with `parent rollup detection skipped (<reason>)`, quoting the `ROLLUP_REASON` value — not as a trailing footnote. A run that could not check for rollups may promote one, so the reader has to see that before the promotion list, not after it. The most common reason is `subIssues field unavailable`: GitHub's sub-issues feature is active only on some repos and orgs.

### 3b. Hold dependency-blocked candidates

An issue with an open native dependency is **held**, exactly like a `blocked`-labeled one. What counts as an open dependency is defined once, in `commands/handlers/gh-issue.md` → `## List` → "Blocked has two independent sources"; this step applies that definition and does not restate it. The `blocked` label is not a substitute: a repo whose dependencies live only in the graph carries no such label, so the step-3 search term holds nothing there.

If steps 3 and 3a left no candidates, skip this step: report and exit, as step 3 does. Never make the call with no `--issue` flag — the helper would fall back to querying the repo's whole `status:2_ready` board, and those issues would reach step 4 as `ready`.

Otherwise ask about **exactly** the candidates still in play after steps 3 and 3a, by number:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-ready.py" --repo "<repo>" \
  --issue <n1> --issue <n2> ... --json
```

This is the same helper `/list-tasks`, `/do-tasks` and step 7a read the graph through, so every gh-issue surface answers "is it blocked?" identically. Two things about the call are deliberate:

- **No `--max-estimate`.** Promotion has no size gate (step 4), so the helper reads no labels and reports no `oversized` bucket; it makes one paginated `blocked_by` read per candidate and nothing else.
- **Bare numbers.** In candidate-scoped mode the helper never checks the `status:` rung, so an un-scored candidate gets a verdict like any other. Its `ready` bucket here means only "no open blocker", not `status:2_ready`.

Every candidate in the result's `blocked` array is **held**: it is not scored, and it receives **no write at all** — no transition, no `prio:`/`est:` backfill, no comment — per the held-card exception in `commands/handlers/task-fill.md` → "When it runs". It stays un-scored, so the next run re-checks it and scores it once every blocker closes. Keep its `open_blockers` for the step-6 report. Candidates in `ready` go on to step 4.

**A failed read stops the run before any write.** The helper exits non-zero on any `gh api` failure. Scoring without the answer would promote exactly the issues this step exists to hold, so report the error and stop; do not fall back to scoring every candidate. `dry-run` runs this step too, since it only reads.

### 4. Score each candidate

For each candidate, run the **confidence check** from `skills/task/SKILL.md` — the **same judgment-based gate the file path uses** (`commands/promote-tasks.md` step 2), read against the GitHub issue per the field mapping `linear-promote.md` step 6 defines. GitHub issues carry no native `priority` or `estimate` field, so both live as the `prio:`/`est:` labels from `commands/handlers/assets/labels.yml`.

**Backfill `prio:` and `est:` first,** before scoring. The rules are defined once in `commands/handlers/task-fill.md` — the static `medium` default, the model-produced Fibonacci estimate, the over-ceiling rule, reason precedence, and the fact that a backfilled value is trusted downstream. Read it and apply it; do not re-derive any of it here.

Its **`gh-issue` adapter** is this path's half, and two things in it are specific to GitHub:

- **`prio:0` is highest here**, the inverse of Linear's priority `0` (which means _none_). Encode from the symbolic value through the adapter table's `gh-issue` row, never from Linear's integer — that inversion is the one mistake this encoding invites.
- **This ladder runs to `13`**, so this is the one handler that **records** an over-ceiling estimate rather than leaving the field unset. Write the honest number up to `13`; above it the field stays unset, as on every other handler. The scope-fit check below still scores LOW either way.

The adapter row carries the rest — the exact encoding, the ladder and where provenance lands — and this file deliberately does not repeat it. The one thing worth saying twice is the write: both fields ride out on step 5's existing full-set PATCH, an argument change to the write the transition already makes, not a second write or a second round trip. `dry-run` reports the intended backfills and writes nothing.

**Do not spell the two labels out from the table — ask for them.** The encoding has one implementation, and a hand-composed `prio:` is exactly where the inversion above lands:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-backfill.py" \
  encode --priority <urgent|high|medium|low|none> [--estimate <n>]
```

It prints the comma-separated label names (`prio:2,est:3`) to splice into step 5's `--labels` value, an empty line when neither field encodes to one, and a `# …` note on stderr when a value was deliberately not encoded — a `none` priority, or an estimate past the ladder's top. It refuses `urgent` with a non-zero exit, which is `task-fill.md`'s "never auto-set `urgent`" made mechanical. Same `$CLAUDE_PLUGIN_ROOT` fallback as the other assets: Glob `**/handlers/assets/gh-issue-backfill.py`.

If a relative path doesn't resolve, find it with **Glob** (`**/commands/handlers/task-fill.md`) and Read it.

The backfilled `est:` is **recorded for claim-time selection**, not read by any gate in this step. `/do-tasks` is what consumes it (`commands/handlers/gh-issue-claim.md` → "Find candidates", via `gh-issue-ready.py --max-estimate`), which is why filling it here still matters: an issue with no `est:` label is invisible to that bound, so the promoter is the only place the number reliably gets written. Scoring below judges the card's specification, never its size.

**HIGH (→ promote)** requires ALL of:

- `title` present and non-empty.
- `body` contains acceptance-style content — a `## Acceptance Criteria` section (or an equivalent concrete, checkable outcome). A bare title or an "investigate X" body fails with `body missing acceptance criteria`.
- `body` has no unresolved `## Open Questions` / `## TBD` content (an empty heading is fine) → otherwise `unresolved open questions`.
- **There is no `est:` gate here.** `max_estimate` is a **claim-time** bound on this handler, exactly as on `linear` — see `commands/handlers/task-fill.md` → "Where the estimate gate lives", which both handlers cite and which owns the rule. This step still **records** the estimate (the backfill above); it just does not judge the card on it.

  > **This moved, and the old placement is the bug.** Until v2.59 the gate ran here and scored an oversized card LOW, which conflated two different verdicts: "this card is not well enough specified for automation" and "this card is too big for one unattended session". The first needs a human to clear it, and a demoted card can only be retrieved by one; the second is a routing decision the loop makes every time it selects, and the card should stay `status:2_ready` and visible while it waits. Measured on `bestdan/dotfiles` 2026-09-20: five correctly-specified cards (complete acceptance criteria, no open questions) were demoted to `needs_refinement` on nothing but `max_estimate` defaulting to `3` against an exclusive bound — three of them sized exactly `3`. Re-promoting them was impossible without a config PR, because the promoter only ever scores un-scored issues. See bestdan/workflow-skills#746.

- **Scope fits one PR (~size 5), judgment not keywords.** This is the same judgment that produced the backfilled `est:` above — weigh the body's breadth against ~300 lines / ~5 files (see **Task size** in `skills/task/SKILL.md`). If the scope clearly exceeds size `5`, the honest `est:8`/`est:13` is recorded and the issue scores LOW with reason `scope exceeds size 5 — split into sub-issues`. The `break-down-task` skill (`skills/break-down-task/SKILL.md`) performs that split.

**The size-`5` ceiling is not `max_estimate`, and removing the latter does not soften the former.** The scope check above still scores LOW on a card that cannot fit one PR, at any `max_estimate`. The two were easy to conflate while both lived here, which is what made the old gate look load-bearing: `max_estimate` is a tunable routing bound the loop applies at selection, while size `5` is the fixed ceiling above which a card needs splitting rather than scheduling. A card sized `8` fails the scope check and is demoted, as before; a card sized `3` or `5` is now the loop's problem, not the promoter's.

**LOW** if any HIGH condition fails. Record the first failed check as the reason (e.g. `body missing acceptance criteria`, `unresolved open questions`).

As on the file path, the scope gate is **model judgment, not a deterministic rule** — acceptable because `/promote-tasks` is not a blocking CI gate: a misjudged issue lands labeled `auto:human-review-needed` for a human to confirm, never silently lost.

### 4b. Re-read the dependency graph before writing

Step 3b's answer ages across step 4's model-paced scoring. In an unattended nightly run (`dev_docs/nightly-gh-issue-routine.md`) that window is unwatched; `/do-tasks` re-checks `blocked_by` at claim, so a miss there is not fatal, but a promoted-then-blocked issue sits wrongly in the ready lane until then. In four attended runs the candidate read to first write took 33–165 s (#868). The cost is one more paginated `blocked_by` read per scored issue, doubling the run's graph traffic — accepted.

Re-read, in one batched call over every candidate step 4 scored (HIGH and LOW alike — a LOW write is still a write): same helper, same flags as step 3b, and the same "never call it with no `--issue`" rule (skip this step if step 4 scored nothing). Runs in `dry-run` too, since it only reads.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-ready.py" --repo "<repo>" \
  --issue <n1> --issue <n2> ... --json
```

Any scored candidate in the result's `blocked` array is **held**, exactly as step 3b holds one: no transition, no backfill, no comment. It stays un-scored, so the next run re-checks and re-scores it. Keep its `open_blockers` for the step-6 report.

**A failed re-read holds every scored candidate and writes nothing.** Scoring is already spent, but writing on 3b's stale answer would promote exactly the issues this step exists to hold; holding costs only a re-score next run. Report the scores anyway, in each held issue's reason (step 6 gives the form), so the spent judgment stays visible, and lead the step-6 report with the quoted helper error — before the scope line, like the rollup-fallback line. Do not fall back to step 3b's answer.

### 5. Apply

If `$ARGUMENTS` contains `dry-run`, print the proposed transitions (per the report shape below) and exit **without** any write and **without** any `gh issue comment`.

Every transition goes through `commands/handlers/assets/gh-issue-state.py`, never `gh issue edit --add-label` / `--remove-label`. That helper validates the requested names against `labels.yml` and then issues **one** full-set PATCH; the incremental `gh issue edit` form is not atomic (measured: 8 HTTP requests), so a crash mid-way strands an issue carrying two `status:` rungs or none. The helper's own docstring carries the measurements.

The consequence for this flow is that a transition is a **read-modify-write on the managed axes**. The PATCH replaces the issue's entire label set. The helper carries forward every label _outside_ the four managed namespaces by itself — `follow-up`, `blocked`, `bug`, anything a human added — but it carries forward nothing _inside_ them, and `--labels status:2_ready` on its own is refused by its validator for want of an `auto:` rung. So read the issue's current managed labels first and pass the complete resulting set:

```bash
gh issue view <n> --json labels --jq '[.labels[].name]' [--repo <repo>]
```

If that read shows `blocked`, the issue was labelled after step 3 selected it: **hold** it with reason `blocked label` and write nothing — no transition, no backfill, no comment — exactly as step 3b holds a dependency-blocked one. The dependency graph itself is step 4b's read, batched over the whole scored set before this loop starts; this per-issue label read is not a second graph read.

Keep that issue's `prio:` and `est:` labels — **or, where step 4 backfilled one, the names `encode` printed** — drop its `status:`/`auto:` rungs, and append the new pair. An issue with `prio:1,est:3` promoted HIGH is written as `status:2_ready,auto:eligible,prio:1,est:3`; omitting `prio:1,est:3` from the `--labels` value would delete them. An issue that carried neither and was backfilled to medium/2 is written as `status:2_ready,auto:eligible,prio:2,est:2`. This is why the backfill costs no extra write: the `--labels` value is being composed anyway, and `prio:`/`est:` are two more entries in it.

A backfilled label carries no marker distinguishing it from a human's, by design — `task-fill.md`'s "trusted downstream" rule. The provenance is the issue comment below, not the label.

**Batch writes — never fire them all in parallel.** Each candidate costs a `gh issue view` read, a `gh-issue-state.py` PATCH, and — for LOW, or for a HIGH that was backfilled — a `gh issue comment`. Firing those for every scored candidate at once can saturate the GitHub API transport, causing cascading timeouts and partial-state corruption: some candidates end up relabeled while their siblings silently fail mid-batch. Apply writes serially, or in small concurrent groups — 2–5 candidates at a time is a judgment call, not a measured limit, and what matters is that the fan-out is bounded (the incident behind this rule fired ~36 writes in a single turn). Wait for each group to finish before starting the next.

Then, for each scored candidate (`<managed>` is the set just assembled):

- **HIGH** — `status:2_ready` + `auto:eligible`, plus the issue's `prio:`/`est:` (existing or backfilled):

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-state.py" \
    --repo "<repo>" --issue <n> \
    --labels status:2_ready,auto:eligible[,<prio:…>][,<est:…>] --apply
  ```

  If anything was backfilled, also comment what was auto-set, so a HIGH issue's guessed numbers are as correctable as a LOW one's:

  ```bash
  gh issue comment <n> --body "/promote-tasks: promoter auto-set <the labels backfilled>" [--repo <repo>]
  ```

  `<the labels backfilled>` names only what step 4 actually wrote — `prio:2, est:3` when both, `est:3` alone when the issue already carried a `prio:`. Claiming a field that was already a human's is worse than saying nothing: it sends them to correct a value they set themselves.

- **LOW** — `status:1_needs_refinement` + `auto:human-review-needed`, plus the issue's `prio:`/`est:` (existing or backfilled):

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-state.py" \
    --repo "<repo>" --issue <n> \
    --labels status:1_needs_refinement,auto:human-review-needed[,<prio:…>][,<est:…>] --apply
  gh issue comment <n> --body "/promote-tasks: <failed-check> (promoter auto-set <the labels backfilled>)" [--repo <repo>]
  ```

  The comment names the failed check so the human can fix it quickly — the gh analogue of the file path's `# promoter:` frontmatter comment and `linear-promote.md`'s LOW comment — and names the auto-set fields in the same comment rather than a second one, exactly as the Linear path does. `<the labels backfilled>` is the same actual-fields-only rule as the HIGH path above; drop the parenthetical entirely when nothing was backfilled.

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/gh-issue-state.py`. `--repo` is required by the helper, so resolve the current repo with `gh repo view --json nameWithOwner --jq .nameWithOwner` when `gh-issue.repo` is unset; drop `--apply` to see the resulting set without writing it. No `gh label create` step is needed here — the raw PATCH creates a missing label rather than rejecting it, and `/task-config` provisions the vocabulary with its intended colors (`commands/handlers/gh-issue-config.md` step 3).

**Never close an issue here**, and never reach for `--done`/`--reopen`: promotion only moves an open issue between rungs.

### 6. Report

Print the same summary shape as the file path (`commands/promote-tasks.md` step 4), keyed by issue number and annotating any issue that got a backfill. Lead with the resolved scope from step 2a (`scope: milestone <title>` / `scope: whole backlog (all)` / `scope: whole backlog (no milestones)`), and — if step 3's query hit the 500-issue cap — a prominent warning line **before** the summary (not a trailing footnote). The `Promoted N of M candidates` line directly below carries the true count: under a cap hit `M` **is** the cap, so the warning states the truncation and the summary states the number. Neither repeats the other:

```
parent rollup detection skipped (subIssues field unavailable)
scope: milestone v2.0
⚠ candidate query hit the 500-issue cap — some open issues may not have been scored this run.
Promoted 5 of 9 candidates:
  ready (3):
    - #142  Fix broken import  (backfilled: est)
    - #145  Bump eslint config  (backfilled: prio, est)
    - #148  Remove stale alias
  needs_refinement (2):
    - #151  Restructure auth module  (scope exceeds size 5 — split into sub-issues)  (backfilled: est 8)
    - #152  Rewrite the config loader  (body missing acceptance criteria)
  held (2, blocked):
    - #111  (blocked by #112, #115)
    - #113  (blocked label)
  skipped (2):
    - #109  (already scored)
    - #110  (parent rollup)
backfilled (3):
  - #142  (est)
  - #145  (prio, est)
  - #151  (est 8)
```

On a step 4b re-read failure, the first line is instead `dependency re-read failed: <quoted helper error>`, and every scored candidate appears under `held (N, blocked)` with reason `dependency re-read failed (scored HIGH)` or `dependency re-read failed (scored LOW: <failed check>)`.

Skipped issues are reported with their reason — `already scored` or `parent rollup`. Held issues are reported under `held (N, blocked)`, each naming what held it: `blocked by #<n>, …` listing the open blockers step 3b or 4b returned, `blocked label`, or `dependency re-read failed (scored …)`. Like skipped issues, held ones count toward `M` without being promoted, as on the file path. The 500-cap warning (if it applied) leads the report per above, not a trailing footnote.

**If step 3a's fallback fired, `parent rollup detection skipped (<reason>)` is the report's first line**, above the scope line — as shown above, quoting the `ROLLUP_REASON` the helper printed. It leads rather than trails because such a run may have promoted a rollup, and a reader who stops before the last line must still see that. **A step 4b re-read failure leads ahead of that**, quoting the helper's error, since it means every scored candidate in the run was held.

### 7. `backfill-only` — fill `prio:`/`est:` without a transition

Reached only from the short-circuit above. This writes the two optional labels on issues that are **missing** one, at any `status:` rung, and writes nothing else. It is not a re-score: it never moves an issue between rungs in either direction, never touches `auto:`, and never overwrites a value a human set. Demotion stays a human's call.

The judgment is still `commands/handlers/task-fill.md`'s and is unchanged here — the static `medium` default, the model-produced estimate, the over-ceiling rule, the held-card exception. What changes is only that there is no transition for the write to ride on, so the whole write belongs to `gh-issue-backfill.py`.

### 7a. Scan

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-backfill.py" \
  scan --repo "<repo>" [--milestone "<scope-milestone>"] --json
```

Returns `candidates` (open, missing `prio:` or `est:`, not held — each with its `title`, `body`, current `labels`, and which fields are `missing`), `held` (each with the reason that held it), and `complete` (the numbers already carrying both, which get no write). The hold check is the native one: the `blocked` label **or** any open entry in the issue's `blocked_by` graph, per `commands/handlers/gh-issue.md`'s definition of an open dependency — it reuses `gh-issue-ready.py`'s paginating implementation rather than a third copy. Report and stop if `candidates` is empty.

`--milestone` is optional here and there is no detection step: a backfill sweep is normally the whole backlog, and narrowing it is the caller's explicit choice.

**`truncated: true` in the result means the query sat at the `--limit` cap and later open issues may not have been scanned.** Surface that as a prominent line **leading** the step-7d report, not a footnote — the same treatment step 6 gives its own 500-cap hit, and for the same reason: a run that could not see the whole backlog must not be read as having swept it.

### 7b. Estimate each candidate

For each candidate, produce the symbolic priority and the Fibonacci estimate per `task-fill.md` — the same scope-fit reasoning step 4 runs, which is what produces the number. Do not encode them; the script does that. Write the plan to a file:

```json
[
  { "number": 142, "priority": "medium", "estimate": 3 },
  { "number": 145, "estimate": 8 }
]
```

- `priority` is the **symbolic word**, never an integer. Omit it to take the static `medium` default; `"none"` means no `prio:` label. `"urgent"` is refused — escalation is a human's call.
- `estimate` is an honest integer on the ladder. Over the ladder's top it is left unset and reported, per the over-ceiling rule; off the ladder (`7`) it is refused rather than rounded.
- An issue missing only one of the two needs only that one judged; a value supplied for a field the issue already carries is ignored, not written.

### 7c. Apply

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-backfill.py" \
  apply --repo "<repo>" --plan <plan.json> --apply [--provenance-issue <n>]
```

Drop `--apply` for a dry run — it re-reads every issue, reports exactly what it would write, and sends nothing (including no comment). `dry-run` in `$ARGUMENTS` means exactly this.

Each issue is re-read at write time, because the plan was composed between the scan and the write and an issue can acquire a label, a blocker or a closure in that window. Then, per issue: a held one is reported and skipped, one already carrying both is skipped, one missing a `status:`/`auto:` rung is skipped (a pre-migration issue is the scoring flow's lane, not this one), and the rest get one full-set PATCH whose `status:`/`auto:` labels are byte-identical to the ones just read — asserted before the write, not after.

**Provenance.** Per-issue provenance is the issue's own `labeled` timeline event: it names the actor and the time, it sits where a human correcting the value already is, and it notifies no one. The batch's one record is the report the script prints. Pass `--provenance-issue <n>` to also post it as a **single** comment on one issue — the issue that motivated the sweep, when there is one. Do not post one comment per issue: that is right for a promote run touching a handful of issues and is notification noise at thirty.

### 7d. Report

Print what the script printed. It is already the batch's provenance record, so restating it in a different shape would make two records that can disagree:

```
Backfilled 12 issue(s):
  #142  prio:2, est:3
  #151  prio:1  (estimate 21 over ladder top 13 — unset)

Held (2, no write):
  #693  blocked by #694
  #710  blocked label

Skipped (1):
  #109  already carries prio: and est:
```
