# Audit: deterministic-code opportunities across the skills/commands prose

**Date:** 2026-07-10 · **Updated:** 2026-07-21 (recommendations #1–#5 all delivered — repo-pr extraction workstream PRE-610/611/612/614 merged; Finding #5 `resume_at` row corrected to delivered-differently) · 2026-07-20 (Finding #5 verified delivered; see "Update" below) · 2026-07-17 (reconciled with PR #119)
**Status:** Findings only — no code changed. Each recommendation below is sized
to become its own task/PR.
**Scope:** All prose under `skills/`, `commands/` (including
`commands/handlers/`), and `agents/`.

> **Read the [Update — 2026-07-15](#update--2026-07-15) section first.** Since
> this audit was written, main landed the read-extraction pattern it advocated
> — built as a fast-path/floor design — but on the _Linear_ path this audit had
> written off as
> unscriptable. Several conclusions below (the "Structural constraint," Finding
> #1's Linear caveat, §6's fact-reviewer note) are materially refined by what
> shipped. The original 2026-07-10 text is preserved as-is below the update as
> the historical record.

> **Graduated 2026-07-21 — read [Delivered](#delivered--2026-07-21-plan-graduated-scaffolding-removed) for the durable record.** The
> **Deterministic-script extraction** project (PRE-609..PRE-615) is complete and
> its plan scaffolding retired. This doc is now that project's permanent home;
> the Delivered section below is the consolidated engineering wisdom (final
> script interfaces + the gotchas that are easy to re-break), so it survives
> even if the per-Finding narrative is ever trimmed.

## Delivered — 2026-07-21 (plan graduated, scaffolding removed)

The repo-pr deterministic-script workstream shipped in full. The per-Finding
"delivered" annotations further down cross-reference the merged PRs; this
section is the load-bearing summary a future maintainer needs before touching
any of these scripts.

### Shipped scripts (final interfaces)

| Script                           | PR(s)                                             | Interface                                                                                                                                               | Replaces (re-derived prose)                                                                                                                                                         |
| -------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/task-scan.py`           | PRE-609 #235; `--archive-candidates` PRE-614 #240 | `task-scan.py [task_dir] [--prs <json>]` · `… --archive-candidates --older-than <N>`                                                                    | scan/rank/readiness + per-`new`-card promote gate (repo-pr-execute §1–2, list-tasks §2–3, promote-tasks §1, doctor 4–5) + `/archive-tasks` candidate selection (repo-pr-archive §2) |
| `scripts/plan-graph.py`          | PRE-610 #236                                      | `plan-graph.py <plan_dir> --id-shape linear\|gh\|jira [--rewrite <slug>=<id>]` · or a `[{slug, is_blocked_by, tracker_id, status}]` JSON array on stdin | `/push-plan` §4.3/§5.3/§5b.3 hand-walked topological order + cycle detection                                                                                                        |
| `scripts/validate.py` (extended) | PRE-611 #237                                      | `validate.py [task_dir]`                                                                                                                                | `/doctor` check 4's hand-simulated frontmatter field/expiry checks; also fixed the consumer-repo path bug (see below)                                                               |
| `scripts/claim-scan.sh`          | PRE-612 #238                                      | `claim-scan.sh [--repo <o/n>] [--task-dir <dir>] [--gh <path>] [--limit <n>]`                                                                           | orchestrator-side claim/WIP query + whole-line slug match (repo-pr-execute claim/WIP, do-tasks, doctor stale-claim)                                                                 |
| Finding #5 supervisor (verified) | PRE-613 #239                                      | `spawn-orchestrator.sh` (`classify-exit`/`supervisor-check`/`supervisor-gate`/`supervisor-scan`), `claude-auto-resume.sh`, `claude-usage.sh`            | the outer relaunch/backoff supervisor the finding said was unbuilt; one residual gap → PRE-619                                                                                      |

### Load-bearing decisions & gotchas (easy to re-break)

1. **Consumer-repo path resolution (task 3 / PRE-611).** The task dir is an
   explicit **argument**, never `git rev-parse --show-toplevel`. `validate.py`,
   `task-scan.py`, and `claim-scan.sh` all default to `dev_docs/tasks` **relative
   to cwd** and accept an override, because a consumer repo has no
   `scripts/validate.py` at its root and `ROOT = __file__.parent.parent` would
   validate the _plugin's own_ tasks instead of the consumer's. `/doctor` passes
   the consumer's resolved dir. Do not "simplify" any of these back to a git-root
   lookup — that is the exact defect PRE-611 fixed.

2. **Whole-line claim matching (task 4 / PRE-612).** The `Claims-task: <slug>`
   marker matches a **whole line** (`grep -Fxq`), never a substring — a naive
   substring test lets slug `task_1` falsely match a `Claims-task: task_13`
   line, a bug the repo was burned by once. `claim-scan.sh` is the single
   orchestrator-side home for the rule so it can only be broken (or fixed) once.
   **Exception:** the remote-dispatch prompt in `repo-pr-execute.md` keeps its
   inline prose copy — the remote VM has no plugin installed and cannot call the
   script. Do not wire that block to `claim-scan.sh`.

3. **repo-pr only; Linear was covered separately (the handler boundary).** These
   scripts serve only the **repo-pr** file path (`dev_docs/tasks/**/*.md`). The
   Linear handler's equivalent scan/rank/graph _reads_ were extracted
   independently and earlier into the GraphQL fast-path assets (`linear-scan.py`,
   `linear-ready.py`, `linear-relations.py`, `linear-false-closures.py`) behind a
   fast-path/floor fallback — see the [2026-07-15 Update](#update--2026-07-15).
   jira/gh-issue scan stays MCP/`gh`-response prose. Don't try to unify the two
   paths: the split — file-path work is scriptable, MCP mutations stay prose — is
   the whole thesis.

4. **Fail-closed, tested, single-source — the shared mold.** Every script carries
   an explicit "replaces the ad-hoc X" header, emits structured/parseable stdout,
   fails closed on malformed input (exit non-zero, never silently skip), and has a
   paired test wired into `scripts/check.sh`. Note `plan-graph.py` still prints
   its JSON doc (with a populated `cycles` list) _before_ dying on a cycle, so the
   caller sees exactly which slugs collide.

### Plan completion

All seven tasks (PRE-609..PRE-615) are delivered. The plan's local scaffolding
(`dev_docs/tasks/deterministic_scripts_plan/`) was discarded with its branch
`claude/sleepy-ride-8d4bjx` before graduation, so no residue remains under
`dev_docs/tasks/`. The one residual item — the auto-pilot backoff /
consecutive-pause numbers `run-budget.md` still states in prose that don't match
the shipped code — is tracked independently as
[PRE-619](https://linear.app/prethinkio/issue/PRE-619) and is **not** in this
project's scope. This audit is the graduated permanent home for the workstream.

## Update — 2026-07-15

Five days of work on main (this branch is ~96 commits behind it) shipped the
read-extraction pattern this audit was arguing for, and it lands squarely on
the Linear handler the audit's "Structural constraint" section had ruled out.
Four new commands arrived — `/complete-task`, `/sweep-for-complete`,
`/reconcile-tasks`, `/find-false-closures` — alongside **four new read-only
Linear GraphQL scripts**. Two of those scripts back the new commands directly
(`linear-scan.py`, `linear-false-closures.py`); the other two harden the
pre-existing claim and reoptimize flows (`linear-ready.py`,
`linear-relations.py`).

### The central constraint was too absolute — Linear _reads_ are now scripted

The audit's "Structural constraint" claimed any MCP-bound Linear flow "is prose
no matter how mechanical it looks, because its actual work happens through MCP
calls the agent alone can make." That is now only half-true. The raw-API-key
GraphQL route the audit already saw in `linear-archive.py` has been generalized
into a **fast-path/floor** pattern, and the mechanical Linear reads have been
extracted into code:

| Script                                              | Lines | Replaces (per-issue MCP loop)                                                            | Consumers                                                 |
| --------------------------------------------------- | ----- | ---------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `commands/handlers/assets/linear-scan.py`           | 255   | in-flight scan + linked-PR attachment fetch                                              | `/sweep-for-complete` (row 1), `/reconcile-tasks` (row 2) |
| `commands/handlers/assets/linear-ready.py`          | 310   | ready-selection gates for the claim                                                      | `linear-claim.md`                                         |
| `commands/handlers/assets/linear-relations.py`      | 334   | `get_issue includeRelations` per-issue loop — the biggest single token spend in the repo | `/reoptimize-tasks` graph load                            |
| `commands/handlers/assets/linear-false-closures.py` | 438   | the entire over-close detection flow                                                     | `/find-false-closures`                                    |

> **Reconciled 2026-07-17 with PR #119 (`dpegan/linear-mcp-token-cost`).** The
> `linear-ready.py` fast-path this section frames as an emergent surprise was in
> fact a _deliberately planned_ build: PR #119 is the plan-only doc
> (`findings.md` + a 3-task slice) that specified it — task 1 the canonical
> "Ready-candidate selection" spec, task 2 `linear-ready.py`, task 3 the
> host-gated `linear-claim.md` wiring. All three shipped on main via **PRE-356
> (#158)**, hardened by **#161** — so #119 is a plan whose work is **fully
> realized and can be closed** (nothing left to merge from it; its `findings.md`
> is the design record). The audit and #119 reach the _same_ conclusion from
> opposite directions: the surface audit inferred "Linear reads are scriptable"
> after the fact; #119 argued it first-principles from the token cost of the
> find-candidates fan-out. Where they agree is the fast-path/floor pattern that
> now backs `/do-tasks`.

The handler prose no longer _re-derives_ these reads — it names one source of
truth (`linear-common.md`'s "In-flight scan" / "ready selection" blocks) that
the script and the MCP floor both implement, and each handler **tries the
script first (when `Bash` is available), falling back to the MCP floor on any
non-zero exit.** That fast-path/floor shape is the answer to the audit's
dilemma: reads over Linear's GraphQL _can_ be scripted (raw API key, exactly the
`linear-archive.py` precedent), while only **mutations and interactive/auth-bound
MCP calls** are genuinely prose-only. The refined rule: _Linear read/scan/graph
paths are scriptable and now mostly are; Linear writes stay MCP._

### `/find-false-closures` refutes the "ownership judgment resists scripting" presumption

§6's fact-reviewer note presumed that deciding whether something "counts as"
owned/broken is "NL judgment on response bodies" and so resists scripting. The
new `/find-false-closures` backstop is the counter-example: its entire
"is this completed issue actually owned by delivered work?" decision is
**deterministic**, encoded in `linear-false-closures.py` as four explicit
ownership signals — head-branch identifier regex, attachment-URL canonical
`owner/repo/pull/<n>` match, closing-keyword match, and sub-issue rollup — with
the command reduced to a thin wrapper that resolves scope+repo from config and
runs the asset. Ownership that looked like judgment turned out to be a rule set.
The remaining judgment (which PRE-407/PRE-408 demotion rules are safe to add)
correctly stays out of code, gated behind the reconciler's bounded-rule-set
doctrine.

### The four new commands already follow the audit's prescription

All four are thin, handler-dispatched dispatchers. The three that do a
deterministic _read_ push it into a script and keep only judgment +
MCP-mutation glue in prose; `/complete-task` is the pure mutation primitive —
correctly prose-only, since it has no read to extract:

| Command / handler            | Command | Handler                        | Deterministic core                                          |
| ---------------------------- | ------- | ------------------------------ | ----------------------------------------------------------- |
| `/complete-task` (primitive) | 87      | `linear-complete.md` 108       | one state-transition mutation (MCP write — correctly prose) |
| `/sweep-for-complete`        | 76      | `linear-sweep-complete.md` 279 | `linear-scan.py` fast-path                                  |
| `/reconcile-tasks`           | 85      | `linear-reconcile.md` 269      | `linear-scan.py` fast-path (rows 1–2)                       |
| `/find-false-closures`       | 79      | `linear-false-closures.md` 146 | `linear-false-closures.py` (whole flow)                     |

New prose added: ~327 command + ~802 handler lines. New code added: ~1,337
asset lines across four scripts. The 5:1 prose-to-code ratio the surface audit
computed has moved materially toward code **on the Linear path** — the exact
path the audit had marked "prose no matter how mechanical."

### What this leaves for the original recommendations

- **Finding #1 (`task-scan.py`)** — thesis validated (scan/rank/readiness _is_
  the hottest re-derivation, and it _is_ worth scripting). Its stated caveat
  "only serves the `repo-pr` handler … Linear … isn't covered" is now **fully
  closed on both sides**: `linear-scan.py` covers the Linear scan/readiness
  read, and the **repo-pr** `task-scan.py` over `dev_docs/tasks/**/*.md`
  **shipped** (PRE-609, PR #235), then gained an `--archive-candidates` mode
  (PRE-614, PR #240) that also absorbed recommendation #6's `repo-pr-archive`
  selection logic.
- **Finding #2 (`plan-graph.py`)** — the `--audit` companion the audit imagined
  for `/reoptimize-tasks` landed as `linear-relations.py` (the graph _load_),
  and `/push-plan`'s own topological ordering + cycle detection — the push-side
  `plan-graph.py` — **shipped** as `scripts/plan-graph.py` (PRE-610, PR #236),
  which `push-plan.md` §4.3/§5.3/§5b.3 now call and cite instead of ordering by
  hand.
- **Finding #3 (`validate.py` for `/doctor` + path bug)** — **delivered**
  (PRE-611, PR #237): `validate.py` gained the field/expiry checks `/doctor`
  had been hand-simulating, and the consumer-repo path bug (validating the
  plugin's own tasks instead of the consumer's) was fixed.
- **Finding #4 (`claim-scan.sh`)** — **delivered** (PRE-612, PR #238): the
  whole-line `Claims-task:` claim/WIP query is now one tested script that every
  orchestrator-side consumer cites (the remote-dispatch prose copy excepted).
- **Finding #5 (auto-pilot supervisor)** — **delivered** (verified under
  PRE-613; see "Finding #5 — delivered" below), spread across
  `scripts/spawn-orchestrator.sh`, `scripts/claude-auto-resume.sh`, and
  `scripts/claude-usage.sh`. One recommended element (the crash-loop
  exponential backoff + "4 consecutive pauses" numbers) is a genuine residual
  gap filed as PRE-619; a second (compute `resume_at`) is delivered via a
  different, arguably-better design — the resume time is agent-authored
  (`paused_until`) and the supervisor corroborates + gates on it rather than
  computing it — see below.

Bottom line: the audit's core bet — extract the re-derived reads, keep the
judgment and the mutations in prose — is now demonstrated in production on both
the Linear path and, via the deterministic-script extraction workstream
(PRE-610/611/612/614, merged 2026-07-21) plus the earlier PRE-609, the
**repo-pr** path the audit had marked "prose no matter how mechanical." Every
"extract this" recommendation (#1–#5) is delivered; #5 is delivered bar the two
PRE-619 gaps. Recommendation #6's verdict was "mostly _don't_ script," and its
one deterministic slice (`repo-pr-archive` candidate selection) folded into #1
and shipped — so no scan/graph/validate/claim extraction remains open.

### Finding #5 — delivered (2026-07-20, verified under PRE-613)

The exception this audit called out — "no script in `scripts/` implements
[the outer supervisor] yet" — has since shipped, spread across three scripts
rather than the single `scripts/auto-pilot-supervisor.sh` the finding
imagined. Point-by-point against the original recommendation:

| Recommended element                                   | Shipped as                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Relaunch wrapper (no model call, inspects exit codes) | `scripts/spawn-orchestrator.sh classify_exit()` (dispatched via `classify-exit`) — parses the wake's error surface for auth-failure phrases, a context-scoped `401`, and rate-limit signals (`429`, `rate_limit_error`, `overloaded`); returns `done` / `fatal: …` / `retry: …`.                                                                                                                                                                                                                                                                                                                                                                   |
| Classify rate-limit errors                            | `classify_exit()` (above) plus `supervisor_check()` (dispatched via `supervisor-check`), which consumes the classification and the agent's declared `exit_reason` (`done`/`deadline`/`systemic`/`paused`/`continuing`) to decide teardown vs. relaunch vs. halt.                                                                                                                                                                                                                                                                                                                                                                                   |
| Compute `resume_at` / pause-marker write              | **Delivered via a corroboration design, not a supervisor-_computed_ resume time.** The resume time (`paused_until`) is **agent-authored** in RUN.md; the supervisor does not compute its own `resume_at`. `supervisor_check()` writes the no-progress corroboration ledger (`count`/`head`/`exempt_since` in supervisor-state, which the jailed agent cannot forge), and the shell-only pre-invoke gate `supervisor_gate()` (`supervisor-gate`) _reads_ that agent-authored `paused_until` before every wake, skipping invocation while it's still live. Functionally covers the pause, sourcing the time from the agent rather than computing it. |
| Exponential backoff (30 m → 1 h → 2 h, cap ~6 h)      | **Not implemented as such — residual gap, see below.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Consecutive-pause stop condition                      | Implemented, but as a **flat no-progress counter** (default limit **3**, not the "4" the run-budget.md prose still states), not a dedicated "N consecutive supervisor pauses" count — see residual gap below.                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `claude-usage.sh --near-cap <pct>` add-on             | No literal `--near-cap` flag was added. `claude-usage.sh --session-status` emits `<percent> <reset_epoch>`; near-cap comparison against a caller-chosen threshold is done by the caller (`scripts/claude-auto-resume.sh` compares it to `CAR_CAP_PCT`) rather than baked into the usage script as a tested exit-0/1 mode.                                                                                                                                                                                                                                                                                                                          |

Two scripts beyond `spawn-orchestrator.sh` round out the supervisor:
`scripts/claude-auto-resume.sh` owns the interactive-session 5-hour
rate-limit wall (on process death, asks `claude-usage.sh --session-status`
whether the wall killed it, sleeps to `reset_epoch + buffer`, resumes via
`claude --continue`), and `scripts/claude-usage.sh` is the structured-JSON
usage read both the pre-invoke reserve check and the wall-detection above
consume. `spawn-orchestrator.sh supervisor-scan` (`--park-limit`,
`--pause-exempt-max`, `--report-every`) additionally implements a backstop
run-budget.md's own text calls for but that isn't in the original Finding #5
wording: a **cumulative pause-exempt ledger** (`PAUSE_EXEMPT_MAX_SECONDS_DEFAULT`
= 21600s = 6h) that halts a run stuck re-declaring legitimate-looking pauses
past that cumulative budget, written to a supervisor-state file the jailed
agent cannot touch.

**Residual gap (filed as [PRE-619](https://linear.app/prethinkio/issue/PRE-619),
part of the Deterministic-script extraction project).**
`skills/auto-pilot/references/run-budget.md`'s "Crash-loop guard" section
still describes an attempt-keyed exponential backoff (30 min → 1 h → 2 h,
capped ~6 h) and a "default 4 consecutive supervisor pauses → halt"
condition. Neither is in the shipped code — verified by grepping
`scripts/spawn-orchestrator.sh`, `scripts/claude-auto-resume.sh`, and
`scripts/claude-usage.sh` for `attempt`/`resume_at`/`1800`/`7200`/`exponential`/
`backoff`/`doubl` (zero hits). The actual relaunch cadence is a fixed
launchd `StartInterval`, with no escalating delay between wakes; the
no-progress guard's default is 3, not 4 (`spawn-orchestrator.sh:3137`,
`:1234`); and the only "6h" figure in code is the differently-shaped
cumulative pause-exempt ledger described above, not a per-attempt doubling
cap. PRE-619 asks a human (or a follow-on session) to either implement the
stated backoff/threshold numbers or correct `run-budget.md` to describe the
shipped mechanism — this task does not prejudge which direction is right.

---

_Original 2026-07-10 audit follows unchanged._

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

| Source                              | Lines      |
| ----------------------------------- | ---------- |
| `skills/*/SKILL.md`                 | ~2,221     |
| `skills/*/` reference/support `.md` | ~1,319     |
| `commands/*.md` (slash commands)    | ~2,394     |
| `commands/handlers/*.md`            | ~3,495     |
| `agents/fact-reviewer.md`           | 116        |
| **Total prose**                     | **~9,545** |

Existing deterministic code, for comparison:

| Source                                                                          | Lines      |
| ------------------------------------------------------------------------------- | ---------- |
| `scripts/*.py`, `*.sh` (incl. tests)                                            | ~1,565     |
| `skills/analysis-pipeline/example/*.py` (a worked example, not shipped tooling) | ~203       |
| `commands/handlers/assets/linear-archive.py`                                    | 206        |
| **Total code**                                                                  | **~1,974** |

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

> **Refined 2026-07-15 — see the Update above.** This section overstates the
> constraint. Linear _reads/scans/graph loads_ have since been scripted via the
> raw-API-key GraphQL route (`linear-scan.py`, `linear-ready.py`,
> `linear-relations.py`, `linear-false-closures.py`) behind a fast-path/floor
> fallback. Only Linear **mutations** and interactive/auth-bound MCP calls are
> genuinely prose-only. Read the paragraph below with that correction in mind.

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
in the plugin is _scan `dev_docs/tasks/**/*.md` → parse frontmatter → classify
→ compute readiness → rank_. It's restated, with local variations, in five
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
(jira) — these are _not_ three full re-explanations (§5.3/§5b.3 are ~8-line
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
plugin install dir it would validate the _plugin's own_
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

> **Delivered 2026-07-20 — see [Finding #5 — delivered](#finding-5--delivered-2026-07-20-verified-under-pre-613)
> above.** The "exception" below (the missing outer-supervisor script) has
> since shipped as `scripts/spawn-orchestrator.sh`'s supervisor subcommands
> plus `scripts/claude-auto-resume.sh` / `scripts/claude-usage.sh`, verified
> under PRE-613. One residual gap (backoff/consecutive-pause numbers) is
> filed as PRE-619. Read the paragraph below with that correction in mind.

**Verdict: mostly leave as prose — but one designed component is missing
code the design already requires.**

**Where:** `skills/auto-pilot/references/run-budget.md`, `run-state.md`,
`SKILL.md`.

`run-budget.md` is ~70% rationale and policy ("why exit rather than sleep",
"two pause kinds", caveats stated rather than hidden), not per-run
computation — and its one genuinely mechanical read (the usage query,
percent-consumed semantics) is _already scripted_ in `claude-usage.sh`,
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

> **Status as of 2026-07-21 (see Update above):** recommendations #1–#5 are all
> **delivered.** The Linear analogues of #1/#2 shipped earlier (`linear-scan.py`,
> `linear-relations.py`), and the **repo-pr** side of each now ships too:
> `task-scan.py` over `dev_docs/tasks/**/*.md` (#1; PR #235 + `--archive-candidates`
> PR #240), `plan-graph.py` for `/push-plan`'s topological ordering (#2; PR #236),
> `validate.py`'s `/doctor` checks + path-bug fix (#3; PR #237), and
> `claim-scan.sh` (#4; PR #238). **#5 (auto-pilot supervisor) is delivered** —
> see "Finding #5 — delivered" above; one residual gap filed as PRE-619. #6's
> verdict is "mostly don't script"; its one deterministic slice folded into #1.

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
5. **Auto-pilot supervisor script — delivered 2026-07-20.** Not a
   prose-to-code conversion but a component the design already mandated as
   code; shipped as `scripts/spawn-orchestrator.sh`'s `classify-exit` /
   `supervisor-check` / `supervisor-gate` / `supervisor-scan` subcommands,
   plus `scripts/claude-auto-resume.sh` (the 5-hour wall) and
   `scripts/claude-usage.sh` (the usage read). Verified under PRE-613 — see
   "Finding #5 — delivered" above. One residual gap (the exponential-backoff
   and consecutive-pause-count numbers `run-budget.md` still states in prose
   don't match the shipped code) is filed as PRE-619.

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
