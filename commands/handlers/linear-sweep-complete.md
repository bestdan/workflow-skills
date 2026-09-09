# Linear handler — /sweep-for-complete flow

Invoked from `/sweep-for-complete [--apply] [--all] [--project <id|name>]`
when `handler: linear` is configured. Finds issues sitting in a **started**-type
state whose **own** linked PR has merged, and completes exactly those by
calling the `linear-complete.md` phase per verified match — the mechanical
transition itself is not re-specified here; see that file's "Caller contract"
and "Steps".

**Shared reference:** see `linear-common.md` for connection details, the
config schema (`linear.projects`, the Unassigned bucket), and the preflight
pattern.

> **Hard note — this sweep is immune to the bare-id over-close bug that got
> Linear's GitHub integration disabled.** That integration scans PR title/body
> text for issue ids and auto-completes anything it finds referenced with a
> closing magic word — including a **bare** `<TEAM>-NNN` token that was never
> meant to close, which is exactly how an unrelated sibling issue got
> silently closed. This sweep never parses issue ids out of PR text at all.
> It works in the opposite direction: it starts from the issues it already
> holds in a started-type state, resolves **that issue's own** structurally-
> linked PR (an explicit Linear `links` attachment, or a title/branch match
> scoped to that one identifier), and only completes an issue whose own
> linked PR independently verified as merged. Batching several completions in
> one run is fine — each one is independently merge-verified before it is
> touched; there is no shared or inferred link between issues.

## 1. Preflight + resolve scope

1. Run the shared preflight from `linear-common.md` (call
   `<linear-mcp>__list_teams`, match `<linear.team>`, capture the team `id`).
   Same failure messages.
2. **Resolve scope.** `--all` and `--project <id|name>` are mutually exclusive
   (one widens, the other narrows) — if both are passed, stop and ask which
   was meant.
   - **`--project <id|name>`** → narrow to exactly **one** project, skipping
     project-list resolution and the Unassigned pass entirely. Resolve the
     value the same way `linear-common.md` "Resolve claim scope" step 1
     resolves a specific pin: match against the configured `linear.projects`
     scopes first (case-insensitive name, or exact id/UUID); if none matches,
     match against the team's live projects via `<linear-mcp>__list_projects`
     (a live, unconfigured project is a valid one-run pin). No match anywhere
     → stop with "`<value>` is not a project in team `<team>`". Step 2 below
     then runs a **single** query with this project's `id` as `projectId`.
   - **`--all`** → skip project resolution entirely and run a **single
     whole-team query** in step 2 below (no `projectId` filter, no Unassigned
     pass — the whole team already covers everything).
   - **Neither flag (default)** → call the **"Resolve configured projects"**
     helper from `linear-common.md` for the configured `linear.projects`
     scopes, **plus the Unassigned bucket — the sweep/reconcile variant**
     (`linear-common.md` "The Unassigned bucket"): membership is `projectId
     == null` **only**, never "any project outside the configured set." This
     is **narrower** than `/do-tasks`'s claim-path Unassigned bucket by
     design — see that section for why. The pass still runs **one** extra
     whole-team query with `projectId` omitted (so the null-project filter
     has something to filter), subject to the same **50-row truncation
     caveat** (the cap applies before the filter, so a full cap with no
     `projectId: null` survivors means "unassigned coverage may be partial,"
     not "no unassigned work"). A project that exists in Linear but isn't
     listed under `linear.projects` is simply **not swept by default** —
     pass `--project <that project>` or `--all` to reach it. This gap is
     not silent, though: the same whole-team query's discarded survivors
     (non-null `projectId`, not in the configured set) feed the out-of-scope
     warning — step 2 below buckets them by project, step 7 reports them —
     at **zero** extra API cost, since it's the Unassigned pass's own
     result set read from the other side of its filter.

## 2. Find in-flight issues

These are the only issues that can possibly be "merged but not yet
completed" — anything not in a started-type state either hasn't begun or is
already terminal. See `linear-common.md` "In-flight scan" for the read this
section implements (state scope, skinny fields, per-scope resolution) — that
block is the single source of truth; it is not restated here.

**Fast-path/floor gate.** This step runs behind the shared gate — see `linear-common.md` "Fast-path / MCP-floor gate (and the security boundary)" for the mechanism (the script's non-zero exit _is_ the gate; **no** separate `[ -n "$LINEAR_API_KEY" ]` pre-check) and the security boundary. The script here is `linear-scan.py`; this consumer **batches all configured projects into one call** (step 2 below), and the script exits non-zero as a whole on any scope's failure, so its fallback granularity is the **whole configured-project batch** — a failure floors all configured projects together, not one scope. The Unassigned scope is a **separate** pass that always floors on its own (the script has no null-project exclusion mode), as "Fast path" below details.

### Fast path (GraphQL, via `linear-scan.py`)

1. **Resolve scope, unchanged.** Run "1. Preflight + resolve scope" above as
   normal — `--all`, `--project <id|name>`, and the default
   configured-projects-plus-Unassigned scope list all resolve the same way
   regardless of which path executes the query. The script's own prelude
   resolves the team itself, so this **replaces** the floor's
   `list_workflow_states` call below — do not also call it on this path.

2. **Call the script**, passing every resolved concrete project scope's `id`
   as a repeated `--project` (omit entirely for the whole-team scope, i.e.
   `--all` or the no-projects-configured case; **never** serialize the
   `__unassigned__` sentinel as a `--project` value — same guard as
   `linear-claim.md` "Find candidates") and `--state-type started` (the
   state-type set this sweep needs, per `linear-common.md` "In-flight scan"):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-scan.py" --team "<linear.team>" \
     --project "<scope-1-id>" --project "<scope-2-id>" ... \
     --state-type started
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/linear-scan.py`.
   Parse stdout as the `{ meta: { viewer, team, states }, issues: [ { id,
   identifier, title, url, state, attachments, project, scope } ] }` object
   described in the script's header comment — `project` is the issue's **own**
   `{ id, name }` (or `null`), and `scope` is the query that returned it; see
   `linear-common.md` "In-flight scan" for why the two are not
   interchangeable. A parse failure is itself a fallback
   trigger (see above). The **Unassigned bucket's exclusion pass** has no
   equivalent in the script (it has no null-project filter, same limitation
   `linear-claim.md` "Find candidates" step 1 documents for `linear-ready.py`)
   — when the Unassigned bucket applies (1+ projects configured, no `--all`),
   **fall back to the MCP floor** for that scope's pass so unassigned
   in-flight work isn't missed; the configured-project scopes can still run
   fast.

3. **Consume `meta` in place of the equivalent MCP reads.** `meta.states` (an
   array of `{ id, name, type }`) replaces the state-id → type map the floor
   builds via `list_workflow_states` in step 1 of the MCP floor below — cache it the same
   way and resolve the `started`-type ids from it. `meta.viewer` is present in
   the payload for parity with the fast-path pattern `linear-claim.md` uses,
   though this scan performs no assignee/viewer check and so doesn't need it.
   On the fast path, **no** `list_workflow_states` read should run — only the
   script's GraphQL call(s).

4. **Skip the per-issue attachment read.** Each returned issue already carries
   its own PR attachment URL(s) in `attachments` (a plain list of URLs, per
   the script's skinny-fields contract), so **skip "3. Resolve each issue's
   PR" step 1** (the `get_issue` call that reads `attachments` off the live
   issue) for every fast-path issue — feed `issue.attachments` directly into
   that step's GitHub-PR-URL match instead. Steps 3.2–3.3 (the title-search
   and branch-name fallbacks) still apply if no attachment resolves a PR, and
   step 4's merge-check runs unchanged for every issue regardless of which
   path found it — over `gh` on `local-full`, over the GitHub MCP on
   `claude-web`.

### MCP floor (fallback)

Runs whenever the fast path isn't attempted or falls back per the gate above.

1. Call `<linear-mcp>__list_workflow_states` with the team `id` (cache the
   state-id → type map, same cache `linear-claim.md` and `linear-complete.md`
   build). Resolve **every** state id of type `started` — by **type only,
   never display name** (names are user-configurable; see the kanban mapping
   in `linear-common.md`). On a default team that is `In Progress` and
   `In Review`, but a team with renamed or extra started columns is covered
   the same way: any started-type issue can be the case where a PR opened,
   got reviewed, and merged, but nothing moved the Linear issue.
2. Call `<linear-mcp>__list_issues` once per resolved scope from step 1
   above **per started-type state id from step 1 of the MCP floor** — the tool's `state`
   filter takes a **single** value, so a scope with two started states means
   two calls; union the results per scope:
   - `teamId`: resolved team id
   - `projectId`: the scope's `id` (omit for the whole-team scope and for
     `--all`); **never** pass the Unassigned sentinel as a `projectId`. For
     the Unassigned scope, resolve it client-side with the **sweep/reconcile
     predicate** — one whole-team query with `projectId` omitted, then keep
     only issues whose `projectId` is `null` (**not** `linear-claim.md`'s
     wider "null or outside the configured set"). Only the never-pass-the-
     sentinel guard is shared with `linear-claim.md`.
   - `state`: one `started`-type state id from step 1 of the MCP floor per call
   - `includeArchived`: `false`
   - Limit: 50 per scope × state. If a query truncates, note it in the
     report — do not paginate.
3. Union the results across scopes (tag each with its source scope for the
   report; no dedup needed — the `projectId == null` Unassigned pass is
   disjoint from every configured-project scope by construction).
4. **Bucket the out-of-scope warning (default scope only, when 1+ projects
   are configured).** The Unassigned scope's whole-team query in step 2
   already returns every started-type issue on the team, not just the
   `projectId == null` ones kept above — the rest were simply discarded by
   the null-project filter. Before discarding them, group the survivors
   whose `projectId` is **neither** `null` **nor** one of the configured
   scopes' ids by their `project` name; this is the out-of-scope bucket step
   7 reports. This adds **zero** extra `list_issues` calls — it's a second
   read of the same result set the Unassigned pass already fetched. It
   inherits that query's 50-per-state truncation cap, so a full cap can
   under-count (or entirely miss) out-of-scope work — never report an empty
   bucket as "nothing out of scope" when the query truncated. This step
   applies identically regardless of whether the Unassigned pass itself ran
   on the fast path or floored here — the fast path already falls back to
   this MCP floor for the Unassigned scope (see "Fast path" step 2 above),
   so the same whole-team result set is what's available to bucket either
   way.

On the **MCP floor**, the per-issue `get_issue` read that step 3's input needs
(`attachments`, `branchName`, `project`) runs as written below — that read is
MCP, so it stays with the agent either way.

## 3. Resolve each issue's PR

**`linear-pr-resolve.py` owns this step and step 4.** The three-source walk,
the coarse-search post-filter, the per-issue repo resolution, the fail-closed
probe handling and step 4's merge-state precedence are one tested script — not
an agent procedure — because each of them has a failure mode that reads as a
clean result. Build its input, run it, read its rows:

```bash
python3 commands/handlers/assets/linear-pr-resolve.py \
  --config dev_docs/tasks/.task-config.yml \
  --repo "$(gh repo view --json nameWithOwner --jq .nameWithOwner)" \
  < issues.json
```

Outside this repo the path is
`"$CLAUDE_PLUGIN_ROOT/commands/handlers/assets/linear-pr-resolve.py"`.

**Input** — a JSON array, one object per in-flight issue from step 2:

| key           | on the fast path                         | on the MCP floor                                 |
| ------------- | ---------------------------------------- | ------------------------------------------------ |
| `id`          | `linear-scan.py`                         | `list_issues`                                    |
| `identifier`  | `linear-scan.py` (required)              | `list_issues` (required)                         |
| `attachments` | `linear-scan.py`                         | the per-issue `<linear-mcp>__get_issue`          |
| `branchName`  | `linear-scan.py`                         | the same `get_issue` — one call, not two         |
| `project`     | `linear-scan.py` carries the issue's own | the same `get_issue` — read it off that response |

The `get_issue` call also refreshes the issue's state, which step 6 uses; make
it once per issue and feed the same response into all three.

**What the script decides, and why each rule is there.** Read
`linear-pr-resolve.py`'s docstrings for the full statement of each; this is the
contract the report and steps 5–6 depend on.

1. **Three sources, in order, stopping at the first that resolves** — the
   issue's `links` attachment (authoritative: a structural link `/do-tasks` and
   `/deliver-task` write at PR-open time), then `gh pr list --search
   "<IDENTIFIER> in:title"`, then `gh pr list --head "<branchName>"`. A source
   can yield several PRs and all of them are kept: an issue can accumulate a
   stale closed-unmerged PR _and_ a newer merged one, and taking the first hit
   could mask the merged one.

2. **The title search is post-filtered on a whole-token match** —
   `identifier_in_title()`, the identifier bounded by a non-alphanumeric
   character or the string edge on each side. GitHub search tokenizes on
   punctuation, so the query alone also returns a title carrying `PRE` and `73`
   separately.

   > **Do not narrow it to the `[<IDENTIFIER>]` bracket form.** Only the tracker
   > execute path writes brackets; a hand-opened PR routinely puts the id in
   > parentheses or at the end of the title, and hand-opened PRs are exactly the
   > population these fallbacks exist for — anything `/do-tasks` opened already
   > resolved at source 1. Observed in the nightly tidy run of 2026-09-02:
   > `repo:bestdan/finplan PRE-73 in:title` returned open PR #1003, `Scaffold
   > packages/rest-server FastAPI package (PRE-73)`, and the run reported PRE-73
   > as having "genuinely no PR found yet". That title is now a fixture in
   > `scripts/test_linear_pr_resolve.py`.

3. **The repo is resolved per issue, from that issue's own project** —
   `--config`'s `linear.projects[].repo` matched on `project.id`, else
   `--repo`. Source 1 needs none (a `links` attachment carries a full
   `github.com/<owner>/<name>/pull/<n>` url); sources 2 and 3 search **one**
   repo, and a Linear workspace spans repos, so without this every issue whose
   PR lives elsewhere would come back empty and be filed as "no PR".

   Resolve from the issue's `project`, **never** from its `scope`: `--all`
   skips project resolution for the _query_, so `scope` is the team name on
   every issue there and answers nothing. Read `linear.projects` for the
   **mapping** even when it was not used for **scoping**. An issue with no
   project, or in a project with no `repo:`, falls to `--repo`.

   If the `gh repo view` fallback itself fails (the sweep is running outside
   any repo, or `gh` cannot reach the remote), omit `--repo`: every issue that
   reaches sources 2–3 then lands in `unresolved`, not "no PR".

   A project whose work spans several repos still resolves to one repo — name
   the repo whose merged PRs cover most of it, and rely on source 1 for the
   rest.

4. **A failed probe is never "no PR".** `gh pr list` prints an empty result
   when it **fails** (network, auth, rate limit) exactly as it does on a
   genuine no-match, so the script treats a non-zero exit as a recorded error
   and the issue as `unresolved`. That distinction is the whole point of the
   bucket: `/reconcile-tasks` row 4 GC's the "no-PR skipped" bucket, so a
   failure misfiled there could demote a live-PR issue.

**Output** — a JSON array on stdout, one row per input issue, in input order:

```json
{
  "id": "…",
  "identifier": "PRE-73",
  "repo": "bestdan/finplan",
  "prs": [{ "number": 1003, "url": "…", "state": "OPEN", "mergedAt": null }],
  "resolved_via": "title",
  "state": "open",
  "unresolved": false,
  "probe_errors": []
}
```

`resolved_via` is `attachment`, `title`, `branch`, or `null` when nothing
resolved. `state` is step 4's verdict. `probe_errors` carries every failure
text, for the report.

### Steps 2–3 in a `claude-web` environment

The two fallback probes are `gh pr list`, and **that command cannot run in a
cloud routine** — the same environment split
`skills/auto-pilot/references/launch-preflight.md` calls `local-full` vs
`claude-web`. **`linear-pr-resolve.py` therefore cannot run there either**: its
probes are `gh`. In that environment the agent walks the same three sources by
hand over `mcp__github__*`, applying the script's rules — the whole-token title
post-filter above especially — as the specification they are.

> **It is not the credential, and REST is not a way round it.**
>
> - `gh pr list` and `gh pr view` are refused because they are GraphQL, which
>   is not served. No provisioning fixes that.
> - Repo-scoped `gh api` REST was refused in every measured run (the repo was a
>   cloned source, never credential-attached; attaching works, but no run has
>   yet had both an attach and a `gh`) — do not
>   spend the run probing it.
> - **`gh` may not be installed at all** — it comes from a source repo's own
>   `SessionStart` hook, not from the image, so a session sourced from a repo
>   without one has no `gh`. Where it does exist,
>   `gh api user` answers and `gh auth status` reports the token invalid
>   **while exiting 0**. So never gate on `gh` being present, and never gate on
>   its exit code.
>
> So treat `mcp__github__*` as the only working GitHub channel and do not route
> this sweep through `gh`. If a `gh` REST call does start answering, the repo
> was provisioned since — re-measure, do not treat it as a malfunction.
> Measurements, refusal texts and run ids:
> `dev_docs/decisions/2026-09-07-cloud-routine-plugins-and-gh.md`.

The prefix is `mcp__github__`, and the surface comes from the **GitHub App
installed for claude.ai/code** — not a claude.ai connector, so it is absent
from a routine's connector list and there is nothing to attach. Confirmed in
production: the nightly Linear tidy routine lists only Google-Drive, Linear,
Slack, Todoist and visualize under `mcp_connections`, and calls
`mcp__github__*` successfully anyway.

**Load the tools first.** In a routine these are deferred — call `ToolSearch`
with `select:mcp__github__search_pull_requests,mcp__github__list_pull_requests,mcp__github__pull_request_read`
before the first use, or the call fails as an unknown tool.

**If the MCP tools are unavailable, the run resolves nothing here.** The
`gh api` REST the `gh pr list` refusal names is itself refused for any
repo-scoped path, so there is no second channel to fall back to — do not spend
the run probing for one. Every issue that reaches sources 2–3 lands in
`left: unresolved`.

The tools, each attested from a routine run (2026-09-02), not merely inferred
from upstream:

- **Source 2 (title search)** → `search_pull_requests`. Put the repo **in the
  query** as a `repo:<owner>/<name>` qualifier — `"repo:bestdan/finplan
  PRE-808 in:title"` is the attested form — which is what carries `-R` here.
  (`owner`/`repo` parameters also exist; either works.)
- **Source 3 (branch)** → `list_pull_requests`, with `owner`, `repo`,
  `state: "all"`, and `head`. **`head` is not a bare branch name.** It takes
  `<owner>:<branch>` — `"bestdan:dpegan/pre-507-…"` — unlike `gh pr list
  --head`, which takes the branch alone.

  > **Get this wrong and the sweep completes the wrong issues.** A `head` with
  > no colon is not rejected and does not return `[]` — it is **silently
  > dropped**, and the call returns the repo's unfiltered first page. Measured
  > against `GET /repos/{owner}/{repo}/pulls`, which these tools wrap: no
  > `head` → 30 PRs, a bare branch → the same 30, a nonsense value with no
  > colon → the same 30, while `<owner>:<nonexistent>` → 0 and
  > `<owner>:<real-branch>` → 1. So the filter is honored only once it carries
  > a colon.
  >
  > That is a false **positive**, not the false negative a dropped filter
  > sounds like. Those ~30 unrelated PRs flow into step 4, which completes the
  > issue if **any** resolved PR is `MERGED` — and across an arbitrary page,
  > one always is. The failure mode is therefore the one this whole command
  > exists to prevent, and the one `/find-false-closures` has to clean up
  > after. Build the `<owner>:<branch>` value explicitly; never interpolate a
  > branch name alone.

Both accept `fields` to trim the response; omitting `body` drops the largest
per-result payload, and this flow never reads PR body text.

Apply the identical post-filter — source 2's title check still applies, because
an MCP search tokenizes no more precisely than `gh` does. Treat a tool error
the same way the script treats a non-zero probe exit: `left: unresolved`, never
"no-PR skipped". A missing capability is not a confirmed absence of a PR, and
`/reconcile-tasks` row 4 GC's the skipped bucket.

## 4. Check merge state

On the `gh` path this is the **same script run** as step 3 — no second command.
`linear-pr-resolve.py` reads each resolved PR's merge state with `gh pr view
<url> --json number,url,state,mergedAt` and classifies the issue; read the
verdict off each row's `state`.

**Pass the URL, never the number** — the reason the script does, and the reason
the `claude-web` path below has to reconstruct it. A PR number is
repository-local and step 3 can resolve a PR in another repo, so a number would
read the merge state of whatever same-numbered PR exists in the current
checkout, and a false `MERGED` completes an issue whose real PR never merged.
The `-R` on step 3's probes does not carry into `gh pr view`; each `gh`
invocation is independent.

This read runs for **every** resolved PR regardless of which source found it —
including one resolved from a `links` attachment, which proves a PR is linked
but never that it merged. (`number` and `url` come back on each row, so step
6's completion comment has them from the merge-verification read itself.)

**Row `state`, and the bucket each maps to:**

| `state`           | meaning                                                    | outcome                                  |
| ----------------- | ---------------------------------------------------------- | ---------------------------------------- |
| `merged`          | **any** PR of this issue merged, whatever the others say   | step-5 candidate → step 6 completes it   |
| `open`            | none merged, **any** PR open                               | leave untouched, `left: open`            |
| `unresolved`      | a probe failed, or a resolved PR's state could not be read | leave untouched, `left: unresolved`      |
| `closed_unmerged` | **every** resolved PR read cleanly and is closed unmerged  | leave untouched, `left: closed unmerged` |
| `no_pr`           | every probe succeeded and found nothing                    | `skipped: no PR found` — not an error    |

That order is a precedence, checked top to bottom — `classify()` in the script.
Three properties of it are load-bearing:

- `left: open` is `/reconcile-tasks` row 2's concern and `left: closed
  unmerged` is row 3's, which **demotes** the issue back to Backlog. This file
  only classifies and reports; do not add either behavior here.
- An **unread** PR does not fall through to `closed_unmerged`. A missing read
  is not a confirmed closed-unmerged read, and since row 3 demotes only
  `closed_unmerged`, keeping the unread case in `unresolved` is what makes the
  demote path fail-closed.
- For the same reason, a recorded probe error promotes a `no_pr` or
  `closed_unmerged` verdict to `unresolved` — the two verdicts a missed PR
  would make destructive. `merged` and `open` already rest on positive
  evidence, so an error alongside them changes nothing.

**In a `claude-web` environment, where the `gh pr view` read is refused as a
GraphQL query** (and the script cannot run at all), read the same fields with
`mcp__github__pull_request_read` (`method: "get"`). It takes `owner`, `repo`,
and `pullNumber` — the attested call shape is `{method: "get", owner:
"bestdan", repo: "finplan", pullNumber: 1149}` — and **has no URL parameter**,
so parse all three out of the PR URL and pass them together. That is the same
guarantee the URL rule above buys on the `gh` path: the repo travels with the
number. A bare `pullNumber` with an inferred owner/repo is the one form to
avoid.

**What qualifies is per-backend — the two vocabularies do not overlap.** On the
`gh` path (and so inside the script), `state == "MERGED"`, equivalently a
non-null `mergedAt`. On the MCP path, **`merged == true`** — and nothing else,
because the MCP response is shaped by GitHub's REST API, where `state` is only
ever `open` or `closed`. A merged PR reads `state: "closed"`, so applying the
`gh` rule to an MCP response qualifies **nothing** and the sweep silently
completes zero issues. Measured on a merged PR: `gh` reports `state "MERGED"` /
`mergedAt`, while REST and MCP report `state "closed"` / `merged true` /
`merged_at`. Mind the field spelling too — `merged_at`, not `mergedAt`.

Read **`merged`** off the returned pull request. That field is always present —
it is serialized without `omitempty`, so an unmerged PR carries `merged: false`
rather than omitting it. **`merged_at` is not**: it is omitted entirely when the
PR has not merged, so a missing `merged_at` is a _merge state read
successfully_, not an unread. Only a failed or unanswered call is an unread.

**`gh api` REST is not a fallback for this read.** A repo-scoped REST call was
refused in every measured routine, in each of which the repo was a cloned
source that nobody had attached with credentials; a credentialed attach is
untested. So if `mcp__github__pull_request_read` is unavailable the merge state
is unreadable and the issue lands in `left: unresolved`.

Count `left: open`, `left: unresolved`, and `left: closed unmerged` separately
in the report.

## 5. Dry-run (default)

Without `--apply`, print the candidate table and stop — change nothing:

```
<IDENTIFIER> — PR #<n> (merged <date>) → Done
```

followed by the left/skipped lines (open PRs left, closed-unmerged PRs left,
no-PR issues skipped), and an explicit "nothing changed (dry-run)." Mutation
requires the caller to have passed `--apply` — this mirrors `/archive-tasks`'
dry-run-first posture: always show the candidate list before ever touching
anything.

## 6. Apply (`--apply` only)

For each issue whose own PR verified as merged in step 4, invoke the
`linear-complete.md` phase directly (do **not** duplicate its steps here —
read that file for "Preflight," "Resolve the issue," "Resolve the target
`completed`-type state id," "Idempotence check," "`--dry-run` and
confirmation," "Apply," "Comment," and "Report") with:

- the issue's identifier
- `assume_verified: true` — this sweep is the caller asserting it already
  confirmed the merge in step 4 above, so `linear-complete.md` skips its
  per-issue interactive confirmation and applies the transition directly.
  Batching several completions in one run this way is safe: each one was
  independently merge-verified against its **own** PR before this call, never
  inferred from another issue's PR.
- `comment_body: "Closed by merge of PR #<n> (<PR URL>)"` — `<n>` and the URL
  from the `number`/`url` fields step 4's `gh pr view` captured for that
  issue.

Complete **only** the issue whose own linked PR merged — never a sibling or a
co-mentioned issue. `linear-complete.md`'s own idempotence check (its step 4)
already makes a re-run of this sweep safe: an issue already in a
`completed`/`canceled` state on a later sweep simply reports "already
complete" and is not written again, so re-running the sweep after a partial
apply is not destructive.

## 7. Report

Print:

- **Scope** — one line stating exactly what this run covered: `scope:
  configured projects (<names>) + Unassigned (project-less only)` (default),
  `scope: whole team (--all)`, or `scope: project <name> only (--project)`.
- **Counts** — `k completed, m open (left), u unresolved (left), s no-PR
  skipped, c closed-unmerged (left)`.
- **Out-of-scope warning** (default scope only, when 1+ projects are
  configured; omit entirely for `--all`, for `--project`, and for the
  no-projects-configured case, since each of those already covers the whole
  team) — from the bucket built by step 2's MCP floor step 4 (at zero extra
  API cost, off the Unassigned pass's own whole-team query), print one
  line: `⚠ N started-type issue(s) outside configured scope: <project>
  (n), <project> (n) — not swept. Use --all or --project <name> to reach
  them.` This count is a **floor, not a census** — it inherits the
  Unassigned pass's 50-row truncation cap, so note that explicitly whenever
  that cap was hit (e.g. append "(query truncated — actual count may be
  higher)"). Omit the line only when the bucket is empty **and** the query
  did not truncate. When the bucket is empty **but** the query truncated,
  the count line above would degenerate to a bare `0` with no projects to
  name — print this instead: `⚠ out-of-scope coverage incomplete (query
  truncated) — started-type issues outside configured scope may exist. Use
  --all or --project <name> to check.`
- **Per-issue lines** — identifier, the PR resolved (if any) and its merge
  state, and the outcome (`completed`, `left: open PR`, `left: unresolved`,
  `left: closed unmerged`, `skipped: no PR found`, or `already complete` for
  an idempotent no-op).
- On dry-run, the same table with no outcome column, plus "nothing changed
  (dry-run)."
