---
type: epic
title: Linear per-project WIP + claim-scope prompt
status: active
owner: Daniel Egan
created: 2026-07-04
---

## Goal

Fix `/do-tasks` on the Linear handler declining with a whole-team "N in flight"
aggregate (e.g. finplan's "6 in flight, can't claim") when the user asks for the
next task without pinning a project. WIP should be counted **per project**, an
**Unassigned** bucket should catch work outside configured projects with its own
shared cap, and the claim path should **prompt for scope** when the work is
ambiguously spread across multiple projects — mirroring how `/add-task` already
prompts for a project.

## Root cause

finplan's `.task-config.yml` has no `linear.projects`, so the "Resolve configured
projects" helper collapses to a single whole-team scope (`id: null`) with
`wip_limit: 6`. The WIP gate then counts every `started`-type issue across the
entire team. The claim path, unlike `/add-task`, never asks which project, so
whole-team aggregation is the silent default.

## Scope / non-goals

- **In scope:** Linear handler only — the `Resolve claim scope` step, the
  Unassigned bucket, per-project WIP counting, the `--project` flag, and the
  `linear.unassigned_wip_limit` config key.
- **Non-goals:** No changes to the `repo-pr` or `gh-issue` handlers (their global
  `wip_limit` semantics are untouched). No new machine-enforced config schema
  file (config remains prose-validated). No changes to `/add-task`'s existing
  project prompt beyond reusing its shape.

## Approach

The Linear handler already implements per-project WIP for **configured** projects;
the gap is (a) work outside configured projects and (b) the claim path defaulting
to whole-team aggregation. We close both by:

1. Extending the existing **"Resolve configured projects"** helper to append a
   synthetic **Unassigned** scope, and adding an `unassigned_wip_limit` config key.
2. Teaching **candidate discovery** to find Unassigned issues (by exclusion, since
   the Linear MCP has no null-project filter) and routing out-of-project direct
   picks to the **shared** Unassigned bucket instead of a per-issue synthesized cap.
3. Reworking the **WIP gate** to count per chosen scope, deriving Unassigned
   in-flight as `whole_team − Σ(configured)` (one whole-team count that also serves
   `global_wip_limit` for free — no double-count).
4. Adding a **`Resolve claim scope`** step (mirrors `/add-task` step 2) that
   prompts when ≥2 projects have ready work **and** the session is interactive,
   otherwise defaults to **Any** (ranked across all, per-project caps). A
   `--project <name|id|unassigned|any>` flag skips the prompt.

Key settled decisions:

- **Unassigned membership** = no project **or** a project not in `linear.projects`.
- **Unassigned cap** = `linear.unassigned_wip_limit ?? wip_limit`; `0` = never
  ranked-claim unassigned work.
- **Prompt trigger** = ambiguity (2+ projects with ready work, no `--project`),
  gated on interactivity; non-interactive → **Any**.

## Tasks

1. [[linear_per_project_wip_task_1]] — The whole change as one PR (size 5), in four
   internal edit groups: (a) config + resolver foundation
   (`unassigned_wip_limit` + Unassigned scope), (b) candidate discovery +
   direct-pick shared-bucket fix (`linear-claim.md:51`), (c) WIP gate per-project
   counting with Unassigned-by-subtraction, (d) `Resolve claim scope` step +
   `--project` flag + ambiguity-gated prompt.

## Notes

- **Single PR** (user decision). The four edit groups are cohesive and share the
  same resolver/scope plumbing; if group (c)'s counting rework turns out larger
  than eyeballed, split there first (it carries the double-count guard risk).
- **Token cost:** the Linear MCP is token-expensive. The ambiguity check must
  **not** add a dedicated ready-work probe — it derives "has ready work" from the
  candidate query the claim flow already runs (called out in the task's group (d)).
