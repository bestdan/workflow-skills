# Audit: deterministic-code opportunities across the skills/commands prose

**Date:** 2026-07-10
**Status:** Findings only — no code changed. Each recommendation below is sized
to become its own task/PR.
**Scope:** All prose under `skills/`, `commands/` (including
`commands/handlers/`), and `agents/`.

## Why this audit

Nearly everything this plugin ships is markdown prose that an agent reads and
re-executes by reasoning, not code that runs the same way every time. Prose is
the right tool for genuine judgment calls (routing, scope assessment, NL
inference over ambiguous input) but the wrong tool for computation an agent
has to re-derive identically on every invocation — every re-derivation is
both a context cost and a chance to apply the same logic slightly differently
from the last run.

## Surface audit

Prose volume, plugin-wide:

| Source | Lines |
|---|---|
| `skills/*/SKILL.md` | ~2,221 |
| `skills/*/` reference/support `.md` | ~1,319 |
| `commands/*.md` (slash commands) | ~2,394 |
| `commands/handlers/*.md` | ~3,495 |
| `agents/fact-reviewer.md` | 116 |
| **Total prose** | **~9,545** |

Existing deterministic code, for comparison:

| Source | Lines |
|---|---|
| `scripts/*.py`, `*.sh` (incl. tests) | ~1,565 |
| `skills/analysis-pipeline/example/*.py` (a worked example, not shipped tooling) | ~203 |
| `commands/handlers/assets/linear-archive.py` | 206 |
| **Total code** | **~1,974** |

Roughly a 5:1 prose-to-code ratio. Biggest single prose files, by size alone:
`commands/push-plan.md` (560), `commands/do-tasks.md` (482),
`skills/auto-pilot/` (SKILL.md 421 + 4 reference docs ≈ 1,140 total),
`commands/handlers/repo-pr-execute.md` (368), `skills/task/SKILL.md` (316),
`skills/co-review/SKILL.md` (303 + reviewer docs), `commands/handlers/jira-claim.md`
(259), `commands/doctor.md` (241), `commands/handlers/linear-archive.md` (216),
`commands/deliver-task.md` (215).

**Existing precedent, already in the repo (inconsistently applied):**
- `scripts/probe-coders.sh` — explicitly built to "replace the ad-hoc `command
  -v` / config-grep probes the select-coder skill would otherwise re-derive
  each time."
- `scripts/claude-usage.sh` — `skills/auto-pilot/references/run-budget.md`
  argues directly for hitting the structured usage API from a script instead
  of having the agent scrape CLI usage text ("unversioned UI that drifts
  silently across releases").
- `commands/handlers/assets/linear-archive.py` — the Linear GraphQL
  `issueArchive` mutation as a standalone dry-run-by-default script, while the
  Jira/gh-issue/repo-pr archive handlers implement structurally similar
  "find terminal-state items older than N days, dedupe, retire" flows
  entirely in prose.

## Structural constraint that shapes every recommendation below

Any flow that runs through an **MCP tool** (`mcp__linear__*`,
`mcp__atlassian__*`) cannot move into a script — MCP tools are only invocable
by the agent, not from bash. A script is only possible where there's a raw
credential path (Linear GraphQL + personal API key, as `linear-archive.py`
proves) or a CLI (`gh`, `git`). This resolves several plausible-looking
candidates immediately: the Linear claim lock, all Jira flows, and the
preflight/kanban plumbing in `linear-common.md`/`jira*.md` are prose no
matter how mechanical they look, because their actual work happens through
MCP calls the agent alone can make.

The repo's existing scripts (`probe-coders.sh`, `claude-usage.sh`,
`await-pr-review.sh`, `preflight-freshness.sh`) share a mold worth copying for
every recommendation below: an explicit "replaces the ad-hoc X the agent
would re-derive" header, structured/parseable stdout, fail-closed behavior,
and a paired test script wired into `scripts/check.sh`.

## Findings

### 1. `scripts/task-scan.py` — file-path task scan/rank/readiness (top pick)

**Verdict: ship it.**

The single most-duplicated, most-frequently-executed deterministic procedure
in the plugin is *scan `dev_docs/tasks/**/*.md` → parse frontmatter → classify
→ compute readiness → rank*. It's restated, with local variations, in five
places: `commands/handlers/repo-pr-execute.md` §1–2 (scan, epic-skip,
multi-blocker readiness, 4-key ranking), `commands/list-tasks.md` §2–3 (same
scan plus expired flags, dependency-blocked annotation, epic-rollup tally),
`commands/promote-tasks.md` §1 and the deterministic 7/8 of the HIGH gate in
§2, `commands/handlers/repo-pr-archive.md` §2, and `commands/doctor.md`
checks 4–5 — with the canonical rules specified once more in
`skills/task/SKILL.md` (Ranking, Field reference, Epics/Membership/Rollup).

The ranking rule (priority tier → `impact/size` descending → missing-impact
sorts last-in-tier-never-dropped → oldest `created`) and multi-blocker
readiness ("every blocker's file absent or `done`") are precise and easy to
fumble at runtime — today every `/list-tasks` or `/do-tasks` invocation makes
the agent read N full task files and re-derive all of this by hand.

**Shape:** `scripts/task-scan.py` (Python/uv/pyyaml, matching `validate.py`'s
dependency profile), argument = task dir, optional `--prs <json>` for
tracker-issue merge. Emits one JSON doc: cards grouped by status with
computed `rank`, `dependency_ready` + unresolved blockers, `expired`, epic
rollups, archive candidates, and per-`new`-card deterministic promote-gate
results (fields present, size ∈ {1,2,3,5}, AC bullet present, Open
Questions/TBD content, `human_approval_requested`). The agent's remaining job
is exactly the judgment left over: promote's "scope fits size 5" gate
(explicitly NL judgment, not keywords — keep as prose), rendering, and
dispatch decisions.

Estimated ~250–300 lines + test script; collapses ~100–150 duplicated prose
lines and removes re-derivation from the plugin's hottest command path.
Caveat: only serves the `repo-pr` handler (the default) — Linear/gh-issue/jira
scan/rank runs over MCP/`gh` responses in-session and isn't covered by this.

### 2. Topological sort in `push-plan` / `linear-reoptimize`

**Verdict: ship a script**, `scripts/plan-graph.py`.

**Where:** `commands/push-plan.md` §4.3 (Linear), §5.3 (gh-issue), §5b.3
(jira) — these are *not* three full re-explanations (§5.3/§5b.3 are ~8-line
deltas noting "same algorithm, only the id-shape regex differs"), so the
prose duplication itself is modest. The real cost is runtime: on every push
the agent hand-parses N task files' frontmatter, classifies each
`is_blocked_by` entry against a regex, hand-executes Kahn's algorithm,
detects cycles, and distinguishes "typo'd slug" from "external id" — in its
head, over a graph that can run 15–30 nodes. A missed edge silently produces
a wrong creation order; a missed cycle produces a partial push the prose
explicitly forbids. `commands/handlers/linear-reoptimize.md` Dimension 3
(topo sort + priority-inversion sweep + concurrency sanity check) does the
same class of graph arithmetic over already-fetched data.

**Shape:** input = plan directory or JSON on stdin
(`[{slug, is_blocked_by, tracker_id, status}]`); `--id-shape
linear|gh|jira` selects the three regexes already spelled out verbatim in the
prose; optional `--rewrite <slug>=<id>` for the kept-dependent rewrite step.
Output: ordered slugs, edges used, entries classified
`in-plan | tracker-id | unknown-slug (warn)`, cycles (fail), seeded
slug→tracker_id map. A `--audit` mode reuses the same graph for
`linear-reoptimize`'s inversion sweep and concurrency check. ~120–150 lines.
Deletes/shrinks ~30–40 prose lines; the larger win is determinism on a
push that's explicitly forbidden from landing partially.

Dimensions 1–2 of `linear-reoptimize` (prose→native reconciliation, semantic
inference from descriptions) and Dimension 4 (duplicate detection) are
genuine NL judgment — leave those as prose.

### 3. Extend `validate.py` for doctor + fix a real path bug

**Verdict: partial** — extend the script (or fold into #1), and fix a
defect found along the way.

**Where:** `commands/doctor.md` check 1 (59–68), check 4 (103–133), check 5
expired-tasks (136–138); `scripts/validate.py` task-file section (143–222).

Doctor already delegates check 4.1 to `validate.py`. But check 4.2 (missing
required fields, per `skills/task/SKILL.md` Field reference), `expires`
semantics (never checked by `validate.py` at all), and check 5's expired-task
computation (`expires < today && status non-terminal`) are deterministic
frontmatter arithmetic re-derived per card. Doctor's own fallback clause —
"if `uv` is unavailable, read the frontmatter shape rules from
`scripts/validate.py` and apply them yourself" — is an instruction to
hand-simulate a script, the purest form of the anti-pattern this audit is
about.

**Latent bug found in passing:** doctor invokes
`uv run "$(git rev-parse --show-toplevel)/scripts/validate.py"`, but in a
**consumer repo** (the plugin's actual deployment target) there is no
`scripts/validate.py` at the repo root, and `validate.py`'s
`ROOT = Path(__file__).resolve().parent.parent` means even run from the
plugin install dir it would validate the *plugin's own*
`dev_docs/tasks/`, not the user's. Check 4.1 only works today inside the
workflow-skills repo itself. Fix: parameterize the task dir as an argument,
and have doctor resolve the script via the plugin root, not
`git rev-parse --show-toplevel`.

Checks 2 (MCP reachability), 3 (shared migration procedure reference), and 6
(config advice) are correctly prose as-is.

### 4. `scripts/claim-scan.sh` — WIP dedupe / claim scanning

**Verdict: partial** — script the orchestrator-side query+match+dedupe; the
remote-dispatch copy must stay prose.

**Where:** `commands/handlers/repo-pr-execute.md` Claim protocol step 1
(49–81), WIP count step 4.2 (179–197), the embedded remote prompt's copy of
the same (251–255); `commands/do-tasks.md` (183–195); `commands/doctor.md`
stale-claim check (147–152); `skills/task/SKILL.md` Race conditions.

The whole-line `Claims-task: <slug>` matching rule — including the
documented `task_1` vs `task_13` substring bug the repo has already been
burned by once — is restated in at least four places; every restatement is a
chance to reintroduce that exact bug. A `scripts/claim-scan.sh` (bash,
matching `await-pr-review.sh`'s style) running the three `gh pr list --label
task-{claim,loop,blocked} --limit 100` queries, extracting slugs by
whole-line marker or `headRefName == task/<slug>`, and deduping against
`in_progress` files would serve the WIP count, the local pre-claim check,
`--local` mode, and doctor's stale-claim check from one tested
implementation.

**Limiting factor:** the highest-risk consumer is the remote dispatch prompt
— and the remote VM explicitly does not have the plugin installed
(`repo-pr-execute.md` line 234; `skills/task/SKILL.md` "Remote session
notes"), so it cannot call plugin scripts. That copy stays inline prose
(it already embeds the exact `grep -Fxq` semantics, the next best thing).
So this script only hardens the orchestrator-side paths — worth doing, but
medium impact rather than high.

### 5. Auto-pilot run-budget / run-state

**Verdict: mostly leave as prose — but one designed component is missing
code the design already requires.**

**Where:** `skills/auto-pilot/references/run-budget.md`, `run-state.md`,
`SKILL.md`.

`run-budget.md` is ~70% rationale and policy ("why exit rather than sleep",
"two pause kinds", caveats stated rather than hidden), not per-run
computation — and its one genuinely mechanical read (the usage query,
percent-consumed semantics) is *already scripted* in `claude-usage.sh`,
exactly per the repo's own precedent. `run-state.md` is a format spec plus a
G1–G7 crash-reconciliation table; reconciliation requires observing git/
tracker/PR reality through tool calls and judging non-matching states →
`parked` — legitimately prose. Don't convert either.

**The exception:** `run-budget.md` (lines 49–57, 109–131) describes an
**outer supervisor** that, with no model call, inspects exit codes,
classifies rate-limit errors, computes `resume_at`, and backs off
exponentially (30m→1h→2h, cap ~6h) with a consecutive-pause stop condition.
That is, by its own definition, code — an agent cannot supervise its own
rate limiting. No script in `scripts/` implements it yet;
`launch-runtime.md` currently describes writing "a self-contained launch
script" ad hoc per run. `SKILL.md` line 141 already concedes this: "(This
and the auth/binary probes are good candidates to extract into a small
pre-flight helper script.)" Treat `scripts/auto-pilot-supervisor.sh` (relaunch
wrapper + backoff + pause-marker write) as a required build when auto-pilot
v1 lands, not an optional optimization. Small add-on: give `claude-usage.sh`
a `--near-cap <pct>` mode (exit 0/1 on threshold) so even the run loop's
comparison is tested code.

### 6. API-call prose / the archive family

**Verdict: mostly don't script — `linear-archive.py`'s pattern doesn't
generalize.**

- `jira-archive.md` (75 lines): every operation goes through the Atlassian
  MCP. A script would need a separate Jira API token the handler deliberately
  avoids by using MCP OAuth, and the per-issue transition-resolution step is
  response-dependent. **Don't script.**
- `gh-issue-archive.md` (67 lines): already ~half verbatim `gh` commands; the
  logic is one date filter and one label add. A script saves ~15 lines at
  the cost of a new maintenance surface, on a low-frequency hygiene path
  (GitHub has no issue cap). **Don't script.**
- `repo-pr-archive.md`: candidate selection (frontmatter `status: done` +
  three-way date fallback) is fiddly deterministic logic — fold it into
  `task-scan.py --archive-candidates --older-than N` (#1) rather than a
  standalone script.
- `linear-archive.md` (216 lines): the script already exists; the markdown
  is mostly credential-handling operational knowledge (the 1Password
  `op`-in-agent-shell gotcha) — correctly prose. Could trim ~40 lines of
  GraphQL blocks duplicated from the `.py` file, pointing at the script as
  the single source instead. Cosmetic.
- `gh-issue-promote.md` §3a`: the GraphQL query is already embedded verbatim
  as a runnable block — effectively code-in-prose already. Scripting adds
  nothing.
- `linear-common.md`, `linear-config.md`, `task-config.md`: MCP calls plus
  interactive `AskUserQuestion` wizards. Inherently prose.
- `agents/fact-reviewer.md`: judgment audit; its mechanical bits (git-diff
  reproducibility check, `jq -S` normalization) are already exact commands.
  The link-resolution sweep could be scripted, but "counts as broken" is NL
  judgment on response bodies. Don't script.
- `skills/select-coder/`: probing is already scripted (`probe-coders.sh`);
  the matrix/routing is judgment over a curated table. Prose doing exactly
  what prose is for.

## Prioritized recommendations

1. **`scripts/task-scan.py`** — highest frequency (every `/list-tasks`,
   `/do-tasks`, `/promote-tasks`, `/archive-tasks`, `/doctor` on the default
   `repo-pr` handler) × removes the most re-derived arithmetic. Medium
   effort; naturally absorbs #3 and #6's `repo-pr-archive` logic if built as
   one family.
2. **Extend `validate.py`** (or fold into #1) for doctor's field/expiry
   checks, parameterize the task dir, and fix the consumer-repo path bug.
   Small effort, fixes a real defect.
3. **`scripts/plan-graph.py`** — topo order, cycle detection, id-shape edge
   classification, typo warnings for `/push-plan`; `--audit` mode for
   `/reoptimize-tasks`. Low frequency but high stakes per run (a bad sort
   partially pushes a plan); small effort.
4. **`scripts/claim-scan.sh`** — claim/WIP query + whole-line slug matching
   in one place, serving the pre-claim check, WIP count, and doctor's
   stale-claim check. Small effort, medium impact (remote-VM copy can't use
   it).
5. **Auto-pilot supervisor script** — not a prose-to-code conversion but a
   component the design already mandates as code. Build when auto-pilot v1
   lands; until then `run-budget.md`/`run-state.md` stay prose.

**Explicitly recommended to stay as prose:** everything MCP-bound (Linear
claim lock/election, all Jira flows, Linear/Jira archive mutations beyond
the existing GraphQL script), the config wizards (`task-config`,
`*-config.md`), select-coder/assess-task routing judgment, promote's scope
gate, `linear-reoptimize` Dimensions 1/2/4, the crash-reconciliation table,
`fact-reviewer`, and `gh-issue-promote`'s already-verbatim GraphQL block.

## Methodology

Line-count audit done directly against the repo (`wc -l` over
`skills/*/SKILL.md`, `skills/*/**/*.md`, `commands/*.md`,
`commands/handlers/*.md`, `agents/*.md`, `scripts/*`). Hotspots were selected
by grepping for computation-shaped language (`topological`, `dedupe`,
`calculate`, `parse the json`, etc.) across all prose files, then read in
full. Findings above were produced by a Fable sub-agent given this surface
audit plus full read access to the repo, tasked with verifying each hotspot
against the actual files and identifying its own additional candidates —
including pushing back on any hotspot that turned out not to be a good fit
for scripting.
