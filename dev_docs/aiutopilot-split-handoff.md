---
title: "Handoff — split the E-lite control plane into its own `aiutopilot` repo"
status: new
created: 2026-07-28
owner: bestdan
source_branch: bestdan/aiutopilot-split
worktree: ~/src/worktrees/workflow-skills/aiutopilot-split
---

# Handoff: cut `aiutopilot` from this branch

Read this whole file before touching anything. It records a decision already
taken, the evidence behind it, and the questions deliberately left for you.

## Your task

Produce **the concrete file-by-file split** — what moves to a new `aiutopilot`
repo, what stays in `workflow-skills`, what gets copied — and then settle the
open decisions in the last section. Do not start moving files until the split
plan is agreed with the maintainer.

You are working in the worktree above, on `bestdan/aiutopilot-split`, branched
from `fa2f505` on `bestdan/autopilot-e-lite-design`.

## Why this is happening, in one paragraph

`auto-pilot` has stopped being a skill and become a product living inside a
skills library. `scripts/spawn-orchestrator.sh` is 6,324 lines and its test suite
5,467, against a next-largest script of 532. `dev_docs/elite-spike/` is 1.3M of
the repo's 2.5M of design docs. It ships its own Seatbelt profile template,
launchd plist, supervisor, and watcher, and it carries a multi-probe research
program with spike contracts and kill sheets. Anyone installing the plugin for
`/plan-with-docs` currently also installs kill machinery whose documented failure
mode reaped every SSH login on the box for four days.

## The decision already taken

**Split the new control plane into `aiutopilot`. Do not move
`spawn-orchestrator.sh` into it.**

The design doc already drew this line, in §6 of
`auto-pilot-e-lite-design-2026-07-21.md`:

- **Delete, do not port** — `spawn-orchestrator.sh`, its test suite,
  `orchestrator.sb.tmpl`, `orchestrator.plist.tmpl`, `smoke-confinement.sh`, the
  supervisor pause ledger, exit classification, the in-jail alarm machinery, the
  verify broker, wrapper-specific doctor repairs. Moving 12k lines you have
  committed to deleting into a new repo would make that repo's first act the
  adoption of the thing the redesign exists to escape. It stays in
  `workflow-skills` as the explicitly unsupported fallback until Stage 5 plus the
  grep-clean dependency audit, then dies in place.
- **Keep as-is** — `/deliver-task` lifecycle + adapters + co-review + the freeze
  rule; `task-scan.py`, `plan-graph.py`, `claim-scan.sh`, `validate.py`,
  `probe-coders.sh`; `select-coder` / `orchestrate-coders` / `cao-coder.sh`;
  `pr-fix-guard.sh`; the run-state file formats as the human-readable ledger.
  These stay in `workflow-skills` permanently.
- **Greenfield → `aiutopilot`** — `ap-launch`, the watcher (§5.1), the registry
  (§4.2), the token broker (§2.1), `ap-agent-exec`, the admission canary,
  read-only reconciliation, and **the runaway ceiling**, which is row 5b's
  redirect and the first component to be written.

### Why now is the cheap moment

1. **Nothing of the new control plane exists yet.** Verified: no `ap-*`, no
   watcher, no registry, no broker under `scripts/`. The split is "create a
   repo", not "migrate code".
2. **The next artifact belongs to the new repo.** The ceiling is the first piece
   of the new control plane. Writing it in `workflow-skills` means moving it
   later.
3. **The probe program just closed.** All seven Probe 5b tasks are done,
   committed, pushed; the working tree is clean. Nothing is half-finished.
4. **None of the corpus is on `main`.** `bestdan/autopilot-e-lite-design` is 117
   commits ahead, and `dev_docs/` alone is 109 files / 22,930 insertions that
   exist only on that branch, behind draft PR #243. You can route the corpus to
   the right repo without ever performing a migration.

### The ceiling's dependencies are clean — checked, not assumed

The worry that would have killed the split is the ceiling needing
`spawn-orchestrator.sh` internals. It does not. Its three inputs are pinned by
§5.1 to the control plane's own wall clock, the process table, and the
provider-side usage query. `scripts/claude-usage.sh` is 216 lines with **zero**
references to `spawn-orchestrator.sh`. The watcher reads the §4.2 registry, which
does not exist yet.

### The `deliver-task` coupling is shallower than it looks

`skills/deliver-task/SKILL.md` has 27 references to auto-pilot. Eighteen are
`.auto-pilot/` **paths** — the run-state file format, which §6 explicitly keeps —
and exactly one is a real invocation (`spawn-orchestrator.sh write-verify-broker`),
which §6 deletes anyway. The cross-repo surface is a documented directory layout,
not an API. That survives a split.

## What you are being asked to decide

These were left open deliberately. Each needs a real answer, not a default.

1. **Does the E-lite corpus move, and how much of it?**
   `dev_docs/auto-pilot-e-lite-design-2026-07-21.md`, the four review docs, and
   `dev_docs/elite-spike/` (1.3M) are the research record for decisions that will
   live in `aiutopilot`. Moving them means PR #243 closes unmerged.
   **Recommended:** they move; the design doc is the new repo's charter.
   **Except:** `dev_docs/tasks/probe5-incident-evidence/` stays. It is the
   incident record of `spawn-orchestrator.sh` — code that lives and dies in
   `workflow-skills` — and it documents that repo's history, not the new
   product's. It was also explicitly marked as outliving the spike.

2. **What happens to draft PR #243?**
   117 commits, and it carries the design review, an external Codex review, and
   five probe-result comments. Options: (a) close with a pointer to the new repo;
   (b) merge narrowly — only the parts §6 keeps — and route the rest; (c) merge
   whole, then move, which is churn on a repo that should not hold it.
   **Recommended:** (b) if the narrow slice is small enough to be worth it,
   otherwise (a). Do not silently pick (c) by inertia.

3. **How is evidence provenance preserved?**
   `dev_docs/elite-spike/fixtures/runaway/results.json` pins sha256s **by
   filename relative to the fixture directory**, so a wholesale directory move
   keeps `./results.py check-hashes` passing — verify this rather than trusting
   it. But every evidence header records `fixture_git_revision` as a
   `workflow-skills` SHA, which will not resolve in a fresh repo. Decide between
   a `PROVENANCE` note recording origin repo + SHA, or grafting history. The
   probe program's whole value is that its evidence chain is checkable; do not
   break it in the move.

4. **`scripts/claude-usage.sh` — copy or share?**
   §6 has it serving both the agent's reserve gate and the watcher's
   maintainer-side observation. 216 lines, standalone.
   **Recommended:** copy it into `aiutopilot`. Cheaper to duplicate than to
   coordinate across repos, and the two credential contexts want different
   behaviour anyway. Say so explicitly in both copies.

5. **What is the new repo's test and check story on day one?**
   `workflow-skills`'s `scripts/check.sh` currently carries a **57-failing
   environmental baseline**, dominated by the 5,467-line orchestrator suite.
   That noise taxes every unrelated change — during Probe 5b it cost real effort
   to prove a failure was a pre-existing flake rather than a regression. The new
   repo starts with no baseline debt. Decide deliberately what keeps it that way
   before the first test lands.

## State you are inheriting

- **Branch:** `bestdan/aiutopilot-split`, off `bestdan/autopilot-e-lite-design`
  at `fa2f505`. Clean tree.
- **Probe 5b:** complete and classified `falsified`. Evidence in
  `dev_docs/elite-spike/fixtures/runaway/` — kill sheet, fixture, five evidence
  JSONLs, `results.json`. `./results.py check-hashes` and `./results.py selftest`
  both pass; run them before and after any move.
- **Stage 0 plan:** `dev_docs/tasks/elite_stage0_plan/` — statuses corrected on
  2026-07-28 to match §7a. Task 7 (Max-window coherence) was **never run**; task
  10 (spike test repo) has **no evidence either way**. Both are stated as
  absences rather than resolved by inference. Task 11 is this work.
- **Row 6** (thin vertical run) is **stopped at its start line** pending the
  ceiling. Nothing downstream is mid-flight.

## Rules that still bind

- **Rule 4: never promote spike code by renaming.** The Probe 5b fixture is
  disposable evidence. The ceiling is rebuilt as production code under Stage-2
  gates, inheriting Probe 5's construction-time fail-closed rules — not its code.
- **The ceiling arms another kill path** on a machine where kill machinery has
  already caused a four-day outage. Probe 5's incident record is required reading
  before writing any reap.
- Branch names are prefixed `bestdan/`; never commit to `main`; Conventional
  Commits.
- `dev_docs/tasks/` is gitignored and needs `git add -f`.
- `dev_docs/elite-spike/fixtures/` is excluded from dprint and shfmt **on
  purpose** — `results.json` pins sha256s of that tree. Do not format it.

## First moves suggested

1. Run `./results.py check-hashes` in
   `dev_docs/elite-spike/fixtures/runaway/` to confirm the evidence chain is
   intact before anything changes.
2. Draft the file-by-file split as a table — path, destination, rationale —
   covering `scripts/`, `skills/`, `commands/`, `test/`, `dev_docs/`. Do not
   move anything yet.
3. Bring the five open decisions above to the maintainer with a recommendation
   each.
