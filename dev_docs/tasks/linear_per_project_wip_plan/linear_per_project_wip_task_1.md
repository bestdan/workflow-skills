---
title: Linear per-project WIP + claim-scope prompt (single PR)
priority: medium
size: 5
status: new
created: 2026-07-04
source_branch: main
related_files:
  - commands/handlers/linear-common.md  # Config block (16-61), Resolve configured projects (62-73), new Resolve claim scope step
  - commands/handlers/linear-claim.md   # Find candidates (20-51), direct-pick synthesis (51)
  - commands/do-tasks.md                # Pre-claim WIP gate (227-273), Claim/execute re-check (292-298), --project flag
  - commands/handlers/linear-config.md  # projects loop + global_wip_limit prompt (30-61)
  - commands/handlers/linear-add.md     # step 2 project prompt to mirror (9-27)
is_blocked_by:
parent: linear_per_project_wip
tags: [linear, wip, config, prompt, ux]
---

Part of [[linear_per_project_wip_plan]].

## Context

The Linear handler already implements per-project WIP for **configured** projects,
but two gaps make `/do-tasks` decline with a whole-team "N in flight" aggregate
(finplan's "6 in flight, can't claim"):

1. finplan's `.task-config.yml` has no `linear.projects`, so the **"Resolve
   configured projects"** helper (`commands/handlers/linear-common.md`, lines
   62–73) collapses to one whole-team scope (`id: null`) with `wip_limit: 6`, and
   the WIP gate counts every `started` issue team-wide.
2. The claim path — unlike `/add-task` (`commands/handlers/linear-add.md` step 2,
   lines 9–27, which prompts for a project when the scope is ambiguous) — never
   asks which project, so whole-team aggregation is the silent default.

There is also a latent bug: the direct-identifier path (`linear-claim.md:51`)
**synthesizes a fresh scope per out-of-project issue**, giving each its own full
`wip_limit` → collective unassigned WIP is effectively unbounded.

The Linear MCP `list_issues` `project` param takes a name/id/slug only — **no
null-project filter** — so Unassigned membership must be computed by exclusion.
**Note: the Linear MCP is one of the most token-expensive MCPs**, so this task must
minimize `list_issues` calls (reuse counts/candidate results, avoid redundant
sweeps) — see the ready-work probe note below.

Settled design decisions (all resolved with the user):
- **Unassigned membership** = no project **or** a project not in `linear.projects`.
- **Unassigned cap** = `linear.unassigned_wip_limit ?? wip_limit`; `0` = never
  ranked-claim unassigned. Bucket exists only when ≥1 project is configured.
- **Prompt trigger** = ambiguity (no `--project`, 2+ projects with ready work),
  gated on interactivity; non-interactive → **Any**. `--project` skips the prompt.
- **`--project`** accepts `<name|id|unassigned|any>` (`any` is a keyword —
  ranked-across-all even interactively).

This is a single cohesive PR touching five handler files. Sized 5 (top of budget);
if execution reveals it's larger, split along the four numbered edit groups below.

## Task

**1. Config + resolver foundation** — `commands/handlers/linear-common.md`:
- Config block (16–61): document `linear.unassigned_wip_limit` (optional, default =
  top-level `wip_limit`; `0` = never ranked-claim unassigned; lives under `linear:`
  like `global_wip_limit`; takes effect only with ≥1 project configured).
- Resolve configured projects (62–73): when the mapped list is **non-empty**,
  append a synthetic scope
  `{ id: <UNASSIGNED sentinel>, name: "Unassigned", wip_limit: <unassigned_wip_limit ?? wip_limit>, max_estimate: <linear.max_estimate>, unassigned: true }`.
  Define the sentinel and distinguish it from `id: null` (whole team). Document that
  its in-flight is counted **by exclusion/subtraction**, not a `projectId` filter.
  Leave the empty-`projects` whole-team scope unchanged (no bucket in that case).
- `commands/handlers/linear-config.md`: alongside the `global_wip_limit` prompt
  (step 3c, line 45) prompt for `unassigned_wip_limit` only when ≥1 project is
  configured; emit only when set non-default.

**2. Candidate discovery + direct-pick fix** — `commands/handlers/linear-claim.md`:
- Find candidates step 4: when the scope list includes the Unassigned bucket, add
  **one** whole-team `unstarted` query, keep only issues whose `projectId` is null
  or ∉ configured set, tag Unassigned, union in (no dedup — disjoint by exclusion).
- Steps 5–6: Unassigned-tagged candidates filter/rank using their scope's
  `max_estimate`; no new logic.
- Step 7 / line 51: out-of-project direct picks resolve to the **shared** Unassigned
  scope, not a per-issue synthesized cap. Keep configured-project picks as-is.

**3. WIP gate — per-project counting + Unassigned by subtraction** —
`commands/do-tasks.md` "Pre-claim WIP gate" (227–273):
- Count step: keep per-configured-project `projectId` counts; when the bucket
  exists, run **one** whole-team count and set
  `unassigned_in_flight = whole_team − Σ(configured)`, `slack = unassigned_wip_limit
  − unassigned_in_flight`. State the whole-team count **doubles** as the
  `global_wip_limit` total (computed once — do **not** re-sum per-project counts for
  the ceiling; double-count guard). `unassigned_wip_limit: 0` → slack ≤ 0.
- Gate paths (ranked skip / direct stop / batch reservation, incl. 268–273) treat
  Unassigned as an ordinary scope; the 292–298 re-check decrements its remaining
  slack on an in-run Unassigned claim. Report strings render `Unassigned` (parallel
  to `the whole team` for `id: null`).

**4. Resolve claim scope + `--project` flag** — `commands/handlers/linear-common.md`
new step (mirrors `linear-add.md` step 2) + `commands/do-tasks.md` wiring:
- `--project <name|id|unassigned|any>` given → resolve directly, no prompt (`any`
  → full scope list; `unassigned` → bucket; name/id → that scope; push back on
  unresolvable). Add the flag to the `/do-tasks` argument surface.
- No flag → find which projects (incl. Unassigned) have ready (`unstarted`) work.
  **Token-cost flag (Linear MCP is expensive):** do **not** add a separate probe
  sweep — derive "has ready work" from the candidate query results the claim flow
  already runs (task group 2), or note the exact call count if a light probe is
  unavoidable. Then: 0/1 project with work → use it, no prompt; 2+ with work +
  interactive → `AskUserQuestion` (header "Claim from") among ready-work projects
  (≤3) + Unassigned + Any + Other; 2+ with work + non-interactive → **Any**.
- Call the step **before** the WIP gate; feed the chosen scope(s) to both the gate
  and Find candidates. Document the headless rule (no TTY + no flag → Any) by the gate.
- **Offer to persist an unconfigured project.** When the resolved scope is a
  **concrete live project whose id is not in `linear.projects`** (reached via
  `--project <name|id>` or the prompt's "Other"), after resolving — interactive
  only — ask whether to add it to the config (`AskUserQuestion`, one yes/no). On
  yes, append it as a `{ id, name }` entry to `linear.projects` (reuse the
  `linear-config.md` step 3b add path / shape; inherits the global `wip_limit` /
  `max_estimate`) and write the config. On no, proceed for this run only (the
  project stays in the Unassigned bucket next time). Never fire for `unassigned`,
  `any`, an already-configured project, or non-interactively. Note this is the only
  place `/do-tasks` writes `.task-config.yml`; keep the write minimal (append one
  entry, don't rewrite unrelated keys).

## Acceptance Criteria

**Code-enforced:**
- `scripts/validate.py` passes (handler bundling intact); `dli check` clean.

**User-run:**
- **Root-cause fix:** trace `/do-tasks` (no flag) on finplan-shaped state (0
  configured, 4 live projects with ready work), interactive → **prompts** with
  ready-work projects + Unassigned + Any; WIP scoped to the pick, not whole-team.
  Non-interactive → no prompt, defaults to **Any** with per-project caps.
- **Per-project counting:** team has 6 in-flight across 3 configured projects
  (2/3/1) → each gated against its own cap (no whole-team "6 in flight" decline);
  Unassigned in-flight = `6 − (2+3+1) = 0`. Whole-team count is computed **once**
  and reused for both the subtraction and `global_wip_limit` (no double-count).
- **Unbounded-unassigned fix:** two out-of-project direct picks resolve to the
  **same** shared Unassigned scope/cap, not two synthesized scopes.
- **Cap semantics:** `unassigned_wip_limit: 0` → bucket never claimed (ranked +
  batch reserve 0). Absent → inherits `wip_limit`.
- **Flags:** `--project "CLI Foundation"`, `--project any`, `--project unassigned`
  each skip the prompt and resolve the expected scope.
- **Persist offer:** `/do-tasks --project <a live project not in config>`
  (interactive) prompts to add it to `linear.projects`; on yes the config gains a
  `{ id, name }` entry (other keys untouched) and the project counts as its own
  scope next run; on no it stays in Unassigned. No offer for `any`/`unassigned`, an
  already-configured project, or headless runs.
- **Token cost:** confirm the prose adds **no** dedicated ready-work probe sweep —
  ambiguity is derived from the existing candidate query, or the extra call count
  is explicitly justified.
