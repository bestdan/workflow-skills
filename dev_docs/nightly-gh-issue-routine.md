# Nightly gh-issue routine

A once-a-night pass over this repo's GitHub-Issues board, run as a scheduled
cloud agent: repair the label invariants, triage what arrived untriaged, then
take **one** ready issue as far as an open PR sitting at `status:4_needs_review`.
Nothing merges and nothing closes unattended.

> **Blocked as of 2026-09-21, and not on anything this file can fix.** A cloud
> routine's proxy refuses **GitHub GraphQL entirely** (`HTTP 403`), and the
> handler's shipped assets reach GitHub through `gh` porcelain — `gh issue list`,
> `gh pr list`, `gh pr view`, `gh pr edit`, `gh pr create`, `gh pr ready` — every
> one of which gh implements over GraphQL. Only `gh-issue-state.py` is on `gh api`
> (REST), which works. So candidate-finding and PR creation both fail, and no step
> here is independently runnable. Measured in
> [`dev_docs/research/2026-09-21-nightly-gh-issue-routine-preflight.md`](research/2026-09-21-nightly-gh-issue-routine-preflight.md).
> Until the handler grows a REST path, **run this job locally**, where GraphQL is
> served. The steps below are correct wherever that holds.

**This runbook invokes plugin verbs. It does not describe what they do.** That is
the design, borrowed from `bestdan/dotfiles`' `agents/routines/nightly-linear-tidy.md`
after three consecutive runs of a prose-shaped predecessor executed three
materially different algorithms: the steps that named a script never drifted, and
the step written as a clause of English drifted every night. If a step here looks
underspecified, that is a bug in this file — fix the file, do not improvise the
step.

The Linear-side sibling is finplan's `dev_docs/automation/nightly_linear_job.md`.
This is the same job against the `gh-issue` handler, and the differences below are
all consequences of the handler and the runner, not of taste.

## The pipeline (order is load-bearing)

| # | Step                    | Verb                                                  | Posture                       |
| - | ----------------------- | ----------------------------------------------------- | ----------------------------- |
| 1 | Repair label invariants | `/workflow-skills:reconcile-tasks --all --apply`      | **apply**                     |
| 2 | Triage untriaged issues | `/workflow-skills:promote-tasks`                      | **apply** (it is the default) |
| 3 | Deliver one ready issue | `/workflow-skills:do-tasks --local --non-interactive` | **apply**, one issue          |

**Why this order.** Step 1 is what makes step 3's candidate query trustworthy: an
issue carrying two `status:` rungs, or none, ranks unpredictably, and `/do-tasks`
picks from exactly that ranking. Step 2 then feeds step 3 — an issue that arrived
`status:0_untriaged` yesterday is only a candidate tonight if promotion has scored
it to `status:2_ready` first. Running 3 before 1 or 2 means delivering against a
board that has not been made honest yet.

**`--all` on step 1 is required here, not optional.** This repo's
`dev_docs/tasks/.task-config.yml` sets `gh-issue.labels: []` on purpose, and the
reconciler's default scope is the configured-label scope; the config's own comment
block explains why `--all` is the correct scope for this repo.

**`--apply` is required on step 1.** `/reconcile-tasks` defaults to a dry run, so a
bare invocation reports candidates and changes nothing. `/promote-tasks` is the
opposite — apply is its default and `dry-run` is the flag — so step 2 takes no flag.

## Where it runs — the measured environment

Everything in this section was measured inside a cloud routine on the dates given.
It is a dated snapshot and is **allowed to go stale**. The operative rule, which
this repo's records have had to relearn twice, is in
[`commands/handlers/claim-lock.md`](../commands/handlers/claim-lock.md):
**do not re-derive routine behaviour from documentation — probe it.**

- **The plugin is installed by the environment's setup script**, before the agent
  starts. A mid-session `claude plugin install` does not register with the running
  agent, so there is no recovery inside a run — hence the hard assertion in step 0.
  Measured 2026-09-05:
  [`dev_docs/research/2026-09-05-cloud-session-plugin-and-proxy.md`](research/2026-09-05-cloud-session-plugin-and-proxy.md).
- **`$CLAUDE_PLUGIN_ROOT` is empty** in a Bash call even with the plugin installed,
  so `python3 "$CLAUDE_PLUGIN_ROOT/…"` collapses to an invalid path. Never write
  that. Reach assets through the checkout at `/home/user/workflow-skills`.
- **Sources clone to `/home/user/<repo>` and the agent starts in `/home/user`**,
  which is not a repository. Every verb resolves its handler from
  `rev-parse --show-toplevel`, so each invocation must run with the cwd **inside**
  `/home/user/workflow-skills`.
- **`$TMPDIR` is empty and `/tmp/claude` does not exist.** Create your own scratch
  directory under `/tmp`.

### GitHub: the MCP works, and so does `gh` REST — conditionally

This is the one place the gh-issue handler and the cloud runner are in tension, and
step 0 exists to keep a run from papering over it.

> **`gh` is present only because the environment's setup script installs it**, and
> it writes a receipt at `/usr/local/share/gh-setup-receipt.txt` when it does.
> Measured 2026-09-21 on `env_01KURKZo3LcfRKBaEZWcbsrk`:
> [`dev_docs/research/2026-09-21-nightly-gh-issue-routine-preflight.md`](research/2026-09-21-nightly-gh-issue-routine-preflight.md).
> Attaching `bestdan/dotfiles` as a source does **not** install it — that was
> measured and is not the mechanism, whatever an older record implies.

- **Repo-scoped `gh` REST serves a repo attached as a source.**
  `gh api repos/bestdan/workflow-skills` and `.../labels` both returned correct
  data, exit 0 (2026-09-21). The routine's `sources` list is therefore an **access
  grant**, not just a checkout — do not trim it.
- **GraphQL is not served.** `gh pr list` and `gh pr view` are GraphQL queries and
  were refused `HTTP 403` (2026-09-07); no credential fixes that. Prefer
  `gh api repos/{owner}/{repo}/…` anywhere the choice exists.
- **Never gate on `gh auth status`.** Measured 2026-09-21: it reported
  `The token in GH_TOKEN is invalid.` and **exited 0**, seconds before two REST
  calls succeeded. The proxy replaces the credential. It fails in the direction of
  a false negative, so a preflight built on it would call a working environment
  broken. Probe the call you actually need — which is what assertion 4 does.
- **Every label write in this handler goes through `gh`.**
  `commands/handlers/assets/gh-issue-state.py` shells out to the CLI, by design —
  the enum guarantee is the CLI's, and a raw REST write silently creates an unknown
  label instead of rejecting it
  ([`dev_docs/gh_issue_task_loop.md`](gh_issue_task_loop.md) §2). So all three steps
  depend on `gh` being present **and** able to reach the API.
- **The proxy's write block is path-scoped, not blanket.** A routine `PATCH`ed an
  issue's labels over plain `curl` and got `HTTP 200` (2026-09-17), while
  `POST`/`DELETE` on `/git/refs` returned
  `403 Write access to this GitHub API path is not permitted through this proxy`
  — word for word, three weeks apart. Read every claim about this channel as scoped
  to the endpoint it was measured on.
- **Consequently the claim lock is the comment-token election, not the ref lock.**
  A routine can _acquire_ a ref (`mcp__github__create_branch` is create-only and
  rejects a duplicate) but **cannot release one** — the connector exposes no
  delete-ref tool and `push --delete` 403s. `claim-lock.md` § "Cloud routines"
  owns this rule; do not restate or re-derive it, and treat a leftover claim token
  from a losing routine as expected rather than as a live claim.
- **Both MCP surfaces arrive deferred** and must be loaded with `ToolSearch` before
  first use. `No such tool available` before that load means "not loaded yet", never
  "the service is down".

## Steps

### 0. Preflight — assert, then stop or proceed

Every check is an assertion with a stated failure action. A failed assertion takes
**that** action and reports which assertion fired. It never degrades into doing the
step by hand: a silent setup failure that turns into a plausible-looking improvised
run is the exact defect this runbook exists to prevent.

1. **The plugin is registered.** `claude plugin list` must name `workflow-skills`
   on at least one `enabled` line. If none appears, the environment's setup script
   failed: **stop, run nothing, and report it.** Do not fall back to reading the
   handler files and following them by hand.
2. **The cwd is the checkout.** Move into `/home/user/workflow-skills` and confirm
   `rev-parse --show-toplevel` agrees. Every step below runs from there.
3. **The GitHub MCP answers.** Load it — `ToolSearch select:mcp__github__get_me` —
   and call `get_me`; it must return `bestdan`. If it does not, **stop and report**.
4. **`gh` can read this repo over REST.** Run
   `gh api repos/bestdan/workflow-skills/labels?per_page=1` and record the verbatim
   result. This is the assertion the whole run turns on, because every label write
   goes through the CLI. Use **`gh api`**, not `gh issue list` — the latter is
   GraphQL-backed, GraphQL is refused here, and gating on it would stop a healthy
   run. Do not substitute `gh auth status`; it reports an invalid token and exits 0
   while REST works.
   - **It works** → run steps 1, 2 and 3.
   - **`gh` is missing, or the call is refused** → run **nothing**. Report the
     verbatim error and stop. When `gh` is missing, also report whether
     `/usr/local/share/gh-setup-receipt.txt` exists: present-but-no-`gh` indicts
     the install, absent indicts the setup script, and the two have opposite
     fixes. Do not substitute raw `curl`, do not hand-write
     labels over the MCP, and do not "just do step 3 without the label moves" —
     a delivery whose state transitions silently no-op leaves an issue claimed,
     in the wrong rung, with a PR nobody is watching. Record the result in
     `dev_docs/research/` as a dated measurement so the next run inherits it.

Do not probe `gh` anywhere else in the run, and do not use it directly — the verbs
call it themselves.

### 1. Repair the label invariants

```
/workflow-skills:reconcile-tasks --all --apply
```

Its rule table is four rows auditing the invariants, and it lives in
`commands/handlers/gh-issue-reconcile.md`. Two of the four rows are flag-only and
write nothing at any flag combination; that is the command's business, not this
file's. Report what it changed.

### 2. Triage what arrived untriaged

```
/workflow-skills:promote-tasks
```

Scores `status:0_untriaged` issues against the confidence check and moves each to
`status:2_ready` or `status:1_needs_refinement`, backfilling `prio:`/`est:` as it
goes. Apply is its default; do not pass `dry-run`. Report the counts by outcome.

### 3. Deliver exactly one ready issue

```
/workflow-skills:do-tasks --local --non-interactive
```

`--local` caps the run at the single highest-ranked dependency-ready issue and runs
it in this session; `--non-interactive` is the declaration that no human is present,
which makes every prompt site inside the verb take its documented default rather
than block the run. `commands/handlers/gh-issue-claim.md` owns the candidate query,
the feasibility judgment, the claim, the work branch, the PR, the move to
`status:4_needs_review`, and the bail mechanics. Do not restate any of it here and
do not select a candidate by hand.

The lifecycle ends at `status:4_needs_review` with an **open PR**. Merging is a human
act, and on this handler the merge is what completes the issue (via the `Closes #<n>`
keyword) — which is why `/sweep-for-complete` is unsupported here and refuses by
design. See [`dev_docs/gh_issue_task_loop.md`](gh_issue_task_loop.md) §4.

If the verb bails, let it run its own bail path — releasing the claim, restoring the
rung, and commenting the reason are all steps it owns. Report the bail verbatim.

### 4. Report

One short report, whatever happened:

```
PREFLIGHT: ok | STOPPED at assertion <n> — <verbatim error>
RECONCILE: <n> issues repaired — <ids>
PROMOTE:   <n> ready, <n> needs_refinement
DELIVER:   COMPLETED #<n> -> PR #<m>
           | NO_ELIGIBLE_ISSUES
           | BAILED #<n> — <reason>
```

## Guardrails

- **One issue per run.** Never attempt a second, however small the first turned out
  to be.
- **Never merge and never close.** The routine's output is an open PR at
  `status:4_needs_review`; a human merges it, and the merge closes the issue.
- **Label-gated.** Only `auto:eligible` issues are candidates. `auto:human-review-needed`
  is a live instruction to this scheduler, and it means no.
- **No commits to `main`.** It is protected, and every qualifying merge auto-bumps
  the version.
- **A stopped run is a successful run.** Reporting a failed assertion is the job.
  Improvising past one is not.

## Changing the schedule

The trigger's prompt carries the path to this file, and that prompt is stored
outside the repo where no `rg` sweep and no test will reach it — so **moving or
renaming this file breaks the routine silently.** `dotfiles`' `nightly-review.md`
fired weekly against a deleted path for over a month. After any move, check the
routines page by hand: <https://claude.ai/code/routines>.
