# Index: prose logic that could move to scripts (2026-09-09)

A snapshot. Line numbers are as of `a152b18` (v2.28.1) for this repo and
`e236073` of `bestdan/dotfiles` for `dotfiles/agents`. Nothing here is maintained; a finding
that ships should link its PR from this file and the row can then rot.

**Scope.** Every runtime prompt file in this repo (`commands/`,
`commands/handlers/`, `skills/`, `agents/` — ~19k lines) plus the user's global
agent directions in `dotfiles/agents/` (`AGENTS.md`, the routines, the
skills, `papercuts-triage.md` — ~2k lines).

**Method.** Seven parallel extractors, one per file group, each reading every
assigned file in full against one rubric (hand-walked algorithms; fenced
blocks with logic; decision tables; API choreography; rules that could be
hooks or lints; prose re-deriving a shipped script; the same procedure
restated across handlers). The absence claims that rank a finding — "no
script computes this" — were re-checked here with `rg` against `scripts/` and
`commands/handlers/assets/` before ranking.

**Prior art.** [`deterministic-code-opportunity.md`](deterministic-code-opportunity.md)
(2026-07-10, graduated 2026-07-21) found five extractions; all five shipped.
This index is the follow-on survey. Its one standing constraint still holds:
MCP calls (`mcp__linear__*`, `mcp__atlassian__*`) run only from the agent,
so a script can own the **decision** over fetched JSON but never the fetch or
the write. Most findings below are shaped that way on purpose.

## What the survey found

- **Coverage is good where the previous audit went.** Scan, rank, readiness,
  graph load, claim lock (gh-issue), archive selection, doctor checks, the
  auto-pilot supervisor: all script-backed, and the prose says so.
- **The largest unbacked concentrations are Linear-side.** `linear-reoptimize`
  hand-walks cycle detection, topological sort, a priority-inversion sweep and
  a regex scan of every issue body; `linear-sweep-complete` hand-walks a
  three-source PR-discovery choreography with a dated production defect;
  `do-tasks` hand-walks the Linear WIP arithmetic its gh-issue sibling gets
  from `gh-issue-claim.py wip --json`.
- **Three procedures are restated across handlers** and would each collapse
  to one tested function: Jira transition-id resolution (5 copies), the
  kanban section table (3 copies), the Linear priority sort (4+ copies).
- **Auto-pilot has one real arithmetic gap.** The `usage_delta` reserve
  bookkeeping is specified in two reference files and called from four
  lifecycle boundaries in `deliver-task`, and no script computes it.
- **The dotfiles side is mostly hook-enforced already.** The gaps are
  `papercuts-triage.md`'s dedup search and the nightly routines' doc-drift
  checks, which fit the `*.test.sh` convention that directory already uses.

## Ranked index

Rank weighs: an explicit prior defect in the prose, confidence that the
logic is deterministic, number of call sites, and size. `S` < 50 lines,
`M` < 200, `L` above. "Verified" means the absence of a script was checked
with `rg` in this pass, not only asserted by the extractor.

### Tier 1 — dated defect or explicit warning, high confidence

| #  | Finding                                                                                                                                                                                | Where                                                                                                                                           | Shape                                                                                                                                                                                                                                        | Size | Evidence                                                                                                                                                                                |
| -- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1  | Linear PR-discovery choreography (attachment → title search → branch), merge-state precedence                                                                                          | `commands/handlers/linear-sweep-complete.md:187-353`, `:410-433`; whole-token identifier match duplicated at `linear-claim.md:113`              | New `linear-pr-resolve.py`: issue id + attachments + project → `{prs, resolved_via, unresolved}`; `classify(prs)` → merged/open/unresolved/closed. Share `merged_prs()` with `linear-false-closures.py`.                                     | L    | Prose quotes the 2026-09-02 nightly run: title search returned open PR #1003 and the run reported PRE-73 as "genuinely no PR found yet". Verified: no script covers it.                 |
| 2  | Jira transition-id resolution, two modes: category filter → exclude → prefer-name → disambiguate (claim, complete); exact `to.name` match, skip when absent (promote, create, archive) | `jira-claim.md:213-230`, `jira-complete.md:82-111`; `jira-promote.md:117-125`, `jira.md:79-86`, `jira-archive.md:64-69`                         | `jira-resolve-transition.py`: transitions JSON on stdin + either `--category`/`--prefer`/`--exclude` or `--exact <status>` → id or `AMBIGUOUS`/`NONE` with names. Five call sites, one implementation.                                       | S    | `jira-complete.md`: "Filtering first is what stops a board whose only terminal transition is Canceled from being quietly completed." Verified: no `jira-*.py` asset exists.             |
| 3  | Dependency-phrase body parsing with direction table                                                                                                                                    | `gh-issue-reoptimize.md:136-145`, `:173-182`; `linear-reoptimize.md:96-121`                                                                     | Extend `gh-issue-graph.py` and `linear-relations.py` to emit `body_references: [{target, phrase, direction, strength}]` from the fixed phrase table; the agent keeps only the shared-subsystem inference (`gh-issue-reoptimize.md:178-182`). | M    | "a mistake here writes a real dependency backwards and nothing catches it." Verified: neither script parses bodies.                                                                     |
| 4  | Linear graph analysis: cycles, topological order + tie-break, priority-inversion sweep                                                                                                 | `linear-reoptimize.md:90-91`, `:133-149`; gh-issue topo sort at `gh-issue-reoptimize.md:184-193`                                                | `linear-graph-analyze.py` over `linear-relations.py` JSON: `find_cycles`, `topo_order`, `find_priority_inversions`; `gh-issue-graph.py --sort` (it already has `find_cycles`).                                                               | S–M  | Prose warns against `priority ÷ estimate`. Verified: `linear-relations.py` has only fetch/derive functions.                                                                             |
| 5  | Auto-pilot `usage_delta` reserve bookkeeping and gate                                                                                                                                  | `skills/auto-pilot/references/run-budget.md:64-133`, `run-state.md:135-158`; called at four boundaries in `skills/deliver-task/SKILL.md:60-101` | `spawn-orchestrator.sh reserve-gate --run-md <p> --percent <p> --reset-epoch <e>`: atomic read/update of baseline/deltas, prints effective reserve and go/no-go.                                                                             | M    | Epoch-match rule, cap at 20, fail-closed on failed read — all specified twice. Verified: `rg usage_delta scripts/` finds nothing.                                                       |
| 6  | Claim-lock create-only ref acquire (201/422/other dispatch), hand-walked outside gh-issue                                                                                              | `commands/handlers/claim-lock.md:35-93`, `jira-claim.md`                                                                                        | Already code for gh-issue: `gh-issue-claim.py acquire` (exit 0 won / 3 lost / 4 other). Generalise that entry point to take repo/branch/base so the shared prose and Jira call the same asset; no second implementation.                     | S    | "two racers both cut `<branch>` from the same tip … the loser's push reports `Everything up-to-date` and exits 0" — measured against a real remote.                                     |
| 7  | Auto-pilot scout: task backend ∈ environment fingerprint                                                                                                                               | `skills/auto-pilot/references/launch-preflight.md:167-184`; CAO gate duplicated at `:83-88` and `resume.md:105-110`                             | `preflight.sh --scout-run-md <p>` → `SCOUT VERDICT: go` or `BLOCKS LAUNCH:` lines, mirroring `PREFLIGHT VERDICT`.                                                                                                                            | S    | Named failure: a `codex` task in a `claude-web` run with no `codex` binary. Verified: no scout/capability logic in `preflight.sh`.                                                      |
| 8  | Papercuts id-based dedup search (PRs, issues, comments, Linear residue)                                                                                                                | `dotfiles/agents/papercuts-triage.md:241-337`, `:389-435`                                                                                       | `papercut-dedup-check.py` in the papercuts plugin: pc_ ids + repos → structured match/no-match with citation; Linear-residue branch as the final stage.                                                                                      | M    | "the 2026-08-16 flush filed 130 issues containing ~31 duplicate pairs"; full-UUID search returned zero against an issue that contained the id; fuzzy `list_issues` never returns empty. |
| 9  | Nightly-tidy merged-PR file builder (page, join, field-map, null-coerce)                                                                                                               | `dotfiles/agents/routines/nightly-linear-tidy.md:261-321`; same field map at `commands/handlers/linear-false-closures.md:100-133`               | `linear-false-closures.py --from-mcp-json <search> <list>`: join on number, rename, coerce nulls, die on a missing join.                                                                                                                     | M    | Stop-on-`updated_at`-not-`merged_at` defect quoted at length; asset dies on any null title/body.                                                                                        |
| 10 | co-review comment anchor-check before the atomic review POST                                                                                                                           | `skills/co-review/SKILL.md:383-388`                                                                                                             | `scripts/diff-anchor-check.py`: diff + `[{path,line,body}]` → anchored / folded-to-body split.                                                                                                                                               | S    | "a single bad line rejects the whole review and posts nothing."                                                                                                                         |
| 11 | Research-spike tutorial `$WORK` root guard                                                                                                                                             | `skills/research-spike-tutorial/SKILL.md:57-65`, `:422-429`                                                                                     | `scripts/tutorial-root-guard.sh <path>`: non-empty, absolute, outside `git rev-parse --show-toplevel`, else exit 1.                                                                                                                          | S    | "It has already happened once, during this skill's own development." Guards an `rm -rf`.                                                                                                |
| 12 | fact-reviewer reproducibility re-run (clean check, re-run, `jq -S` diff, guaranteed restore)                                                                                           | `agents/fact-reviewer.md:47-55`                                                                                                                 | `scripts/analysis-pipeline/check-reproducibility.sh <dir>` → pass/fail/n-a + diff.                                                                                                                                                           | S    | "the re-run would clobber uncommitted edits and the diff would report false drift."                                                                                                     |

### Tier 2 — deterministic, duplicated or hot path, no dated incident

| #  | Finding                                                                                                    | Where                                                                                                                                         | Shape                                                                                                                                                                     | Size |
| -- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 13 | Linear per-project + global WIP slack arithmetic and greedy walk                                           | `commands/do-tasks.md:308-425`, `:572-601`; Jira clamp at `jira-claim.md:123-125`                                                             | `linear-wip.py` mirroring `gh-issue-claim.py wip --json`: per-scope `{limit, in_flight, slack}` + `global_slack`; optional ranked-list input → accepted set.              | M    |
| 14 | Crash-reconciliation table (G1/G3/G4/G5) walk on resume                                                    | `skills/auto-pilot/references/resume.md:122-148`, `run-state.md:486-506`                                                                      | `spawn-orchestrator.sh reconcile --dir <run>`: observe git → tracker → run files per non-terminal task, print matched row + action. Only G2/G6/G7 are in `doctor`.        | M–L  |
| 15 | Restack re-verification (verify re-run, file-set intersection, co-review-stale)                            | `skills/auto-pilot/references/run-state.md:292-321`                                                                                           | `spawn-orchestrator.sh restack` gains the three steps; the doc already says "the re-verification is not (yet)" enforced.                                                  | M    |
| 16 | Kanban section classification table                                                                        | `commands/handlers/linear-list.md:28-42`, `gh-issue.md:117-129`, `jira.md:115-127`                                                            | `kanban-classify.py`: tracker-neutral `{category, labels[]}` + field map → grouped, sorted sections. Confirm the three tables agree first; they are close, not identical. | M    |
| 17 | Linear gate + rank restated for the MCP floor                                                              | `commands/handlers/linear-common.md:157-172`; restated in `linear-claim.md`, `linear-list.md:50`, `linear-promote.md`, `linear-reoptimize.md` | Split `gate()`/`rank_key()` out of `linear-ready.py` into an importable module and add a floor-side entry point that takes `list_issues` JSON.                            | M    |
| 18 | Linear reconcile rows 2–4: classification, idle-hours arithmetic, TOCTOU re-read                           | `commands/handlers/linear-reconcile.md:167-274`, `:306-413`; age math at `:250-267`                                                           | `linear-reconcile.py` over `linear-scan.py` output + fresh reads → classified, re-verified action list; MCP writes stay prose.                                            | L    |
| 19 | `is_blocked_by` slug→id rewrite and id-shape regexes                                                       | `commands/push-plan.md:206-212`, `:238-246`, `:410-421`, `:581-609`; `commands/add-task.md:72-77`                                             | `plan-graph.py` already classifies each entry; emit the rewritten `is_blocked_by` per task so four hand-walks become one read.                                            | M    |
| 20 | Comment-token claim election (window filter, state-backed filter, min id)                                  | `commands/handlers/claim-lock.md:167-241`, `linear-claim.md:127-171`                                                                          | `elect_claim_winner(comments, t_unclaimed, state_backed)` as a shared pure function; the comment fetch stays MCP/`gh`.                                                    | S    |
| 21 | Linear promote quota precheck + shared 500-candidate budget                                                | `commands/handlers/linear-promote.md:79-89`, `:42-46`                                                                                         | Small helper: `active_count`, quota, ranked HIGH list → `{promote, held}`.                                                                                                | S    |
| 22 | Epic rollup merge of file members with `task-loop` PRs                                                     | `commands/list-tasks.md:66-78`                                                                                                                | `task-scan.py --prs <json>` already exists; extend it to emit the combined `epics` rollup with the stated precedence.                                                     | M    |
| 23 | `gh-issue-archive` age threshold computed by hand                                                          | `commands/handlers/gh-issue-archive.md:33-74`                                                                                                 | Compute the cutoff once and pass `--search "closed:<<date>"`, as `jira-archive.md` pushes its filter into JQL.                                                            | S    |
| 24 | local-review readiness poll, round-wait loop, comment-posting table                                        | `skills/local-review/SKILL.md:79-95`, `:198-218`, `:271-311`                                                                                  | `scripts/local-review/wait-ready.sh`, `wait-round.sh`, `post-comment.sh`. These are fenced-block loops `validate.py`'s terminator rule currently lets through.            | S ×3 |
| 25 | Papercuts fix-now vs track split and per-run PR cap                                                        | `dotfiles/agents/papercuts-triage.md:197-208`                                                                                                 | Script in the papercuts plugin: clustered JSON → `{fix_now, track, overflow}`.                                                                                            | S    |
| 26 | Nightly-review doc-drift checks: justfile paths, orphan modules, AGENTS.md lists, hooks in `settings.json` | `dotfiles/agents/routines/nightly-review.md:27-36`, `:76-94`, `:117-126`                                                                      | `scripts/justfile-audit.test.sh`, `scripts/settings-audit.test.sh` in dotfiles, the shape `permissions-twins.test.sh` already has.                                        | S–M  |
| 27 | co-review reconciler tree-drift snapshot/compare                                                           | `skills/co-review/SKILL.md:319-320`                                                                                                           | `scripts/tree-drift-guard.sh snapshot\|verify <file>`.                                                                                                                    | S    |
| 28 | orchestrate-coders main-checkout containment recovery                                                      | `skills/orchestrate-coders/SKILL.md:191-204`; observed failure at `backends/cli-coders.md:106-111`                                            | `scripts/recover-packet-files.sh <worktree> <file>...`: scoped diff, apply, move, restore.                                                                                | S    |
| 29 | Legacy `dev_docs/todos` migration                                                                          | `skills/task/SKILL.md:373-382`                                                                                                                | `scripts/migrate-legacy-tasks.sh`, called from the on-contact preflight and `/doctor --fix`.                                                                              | S    |
| 30 | Handler resolution: two-file YAML overlay then switch                                                      | `skills/deliver-task/SKILL.md:102-124`; restated in `skills/task`, `skills/break-down-task`, `commands/task-config.md`                        | `scripts/resolve-handler.sh` printing the merged config and the `handler:` value.                                                                                         | S    |
| 31 | fact-reviewer fetch-once URL dedup and orphan-number set difference                                        | `agents/fact-reviewer.md:29-39`, `:57-60`                                                                                                     | `scripts/analysis-pipeline/fetch-sources.sh`, `orphan-numbers.py`.                                                                                                        | S ×2 |

### Tier 3 — small, low frequency, or already steered away from

| #  | Finding                                                                 | Where                                                                                                       | Note                                                                                                                                                                                           |
| -- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 32 | Agent-driven GraphQL pagination duplicating `linear-archive.py`         | `commands/handlers/linear-archive.md:114-230`                                                               | No new code. The file's own §"Run it without an agent" already prefers the script; cut the prose path to a pointer.                                                                            |
| 33 | `linear.projects` config-shape checks in `/doctor` 1b                   | `commands/doctor.md:92-114`                                                                                 | Extend `validate.py` with a config validator.                                                                                                                                                  |
| 34 | `min_task_budget` formula                                               | `skills/auto-pilot/references/run-budget.md:414-453`                                                        | `spawn-orchestrator.sh` reads the field but never computes it (verified). Small helper.                                                                                                        |
| 35 | Key-resolution bridging ladder                                          | `commands/handlers/linear-common.md:178-219`                                                                | A wrapper that resolves the key and `exec`s the asset itself. Not `--probe`: its contract is no stdout ever (`dev_docs/auth_key_access.md`), and the bridged command line can carry a raw key. |
| 36 | Slugify + collision suffix; next task number; next `Q<n>`               | `commands/add-task.md:32-48`; `skills/plan-with-docs/SKILL.md:56`; `skills/research-spike/SKILL.md:116-118` | One-liners each. Bundle only if a helper exists for a neighbouring reason.                                                                                                                     |
| 37 | research-spike decision promote (cut block, flip state, paste)          | `skills/research-spike/SKILL.md:243-252`                                                                    | **Do not extract.** `scripts/research-spike.py:104-110` and `:1925-1931` reserve promotion to the organizer by design. Recorded so the next survey does not re-propose it.                     |
| 38 | Reviewer input assembly repeated in five reviewer files                 | `skills/co-review/reviewers/*.md`                                                                           | **Do not collapse.** `SKILL.md` keeps the literal command in each file so the exact-match permission rule can approve it byte-for-byte. Recorded so the next survey does not re-propose it.    |
| 39 | Nightly-tidy set operations (duplicate project ids; uncovered projects) | `dotfiles/agents/routines/nightly-linear-tidy.md:171-177`, `:409-413`                                       | Fold into a preflight step if one is built for #9.                                                                                                                                             |

### Dotfiles rules: hook coverage

The "never/always" rules in `dotfiles/agents/AGENTS.md`, checked against each
hook's own header:

| Rule                                                     | Enforced by                                                                            | Gap                                                                                                         |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Never env-prefix `git`                                   | `guard_env_prefix_git.py`                                                              | none                                                                                                        |
| `gh api` method flag before the path                     | `sandbox-network-guard.sh` blocks every non-GET `gh api` call, whatever the flag order | flag order itself is unenforced, and moot on the guarded surface                                            |
| PR body authoring rules                                  | `guard_pr_body.py`                                                                     | enforces the word ceiling and banned phrases; does not scan `--body` for backticks                          |
| `git status` before `git add`                            | `guard_noop_git_add.py`                                                                | its header names the gap: flag forms, multi-segment commands, shell expansions. Candidate: extend it.       |
| Tear down with `dli git worktree-remove`, never `rm -rf` | `worktree-remove.sh` on `ExitWorktree`                                                 | only the session's own worktree, never unattended; a manual `rm -rf` or bare `git branch -d` is not blocked |
| Merge status from PR state, not ancestry                 | correct inside `dli/git.just`                                                          | nothing stops a hand-typed `git merge-base --is-ancestor`                                                   |
| No `cd "$(git rev-parse --show-toplevel)"`               | —                                                                                      | candidate: a PreToolUse guard on `cd "$(…)" &&`                                                             |
| No `find -exec`                                          | `permissions-no-known-writers.test.sh` (allowlist entries only)                        | not checked against agent-issued commands                                                                   |

**Migration constraint, measured 2026-09-09.** Plugin hooks and user hooks
**union**; a plugin hook does not override a user hook with the same event.
Moving a hook such as worktree teardown from `dotfiles/agents/` into a plugin
must delete the user-side entry in the same change, or the mid-migration
state runs the hook twice.

## Already filed

Related opportunities already tracked in this repo's issues. They are not
restated as rows; only #461 bears on one (row 1).

- [#520](https://github.com/bestdan/workflow-skills/issues/520) — deterministic PR size and complexity report for co-review step 6.
- [#404](https://github.com/bestdan/workflow-skills/issues/404) — a named cleanup command so tidy-up stops being re-inferred from prose.
- [#475](https://github.com/bestdan/workflow-skills/issues/475) — `coreview-rule-drift.py` never checks the shared rules.
- [#461](https://github.com/bestdan/workflow-skills/issues/461) — `find-false-closures` supersession gap (bears on #1's ownership rules).
- PRE-619 — auto-pilot backoff numbers in `run-budget.md` that the shipped supervisor does not implement.

## Files with no candidates

Routing wrappers (`commands/auto-pilot.md`, `deliver-task.md`, `select-coder.md`,
`orchestrate-coders.md`, `refresh-coder-comparison.md`, `tutor.md`,
`assess-task.md`, `archive-tasks.md`, `reconcile-tasks.md`,
`sweep-for-complete.md`, `reoptimize-tasks.md`, `complete-task.md`,
`find-false-closures.md`); fully delegated handlers (`gh-issue-claim.md`,
`gh-issue-reconcile.md`, `gh-issue-complete.md`, `gh-issue-promote.md`,
`repo-pr-*.md`, `linear-complete.md`, `linear-false-closures.md` bar #9); config
and policy files (`*-config.md`, `attendedness.md`, `mcp-setup-offer.md`);
judgment-only skills (`select-coder`, `tutor`, `assess-task`,
`analysis-conventions`, `review-facts`, `co-review-reconciler`, the
`research-spike` references, `adapters.md`, `launch-runtime.md`); and on the
dotfiles side `RTK.md`, `authoring_pull_requests.md`, `writing_about_code.md`,
`solo-cli-setup.md`, and every skill under `dotfiles/agents/skills/`.

## Suggested first slice

One PR per finding, in this order: #2 (five call sites, S, a stated defect),
#10 and #11 (S; #11 guards an `rm -rf`, #10 stops a whole review from being
rejected for one bad line), #4 (S–M, closes the largest unbacked algorithm
cluster), then #1 (L, a dated production incident) and #5 (M, the one
arithmetic gap with no script behind it). #16 and #17 are the cross-handler
collapses and are worth doing together once the three tables are diffed.
