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
5,467, against a next-largest script of 579 (`task-scan.py`; the next-largest
shell script is 532). `dev_docs/elite-spike/` is 1.3M of the repo's 2.5M of
design docs. It ships its own Seatbelt profile template,
launchd plist, supervisor, and watcher, and it carries a multi-probe research
program with spike contracts and kill sheets. Anyone installing the plugin for
`/plan-with-docs` currently also installs kill machinery whose documented failure
mode reaped every SSH login on the box for four days.

## The decision already taken

**Split the new control plane into `aiutopilot`. Do not move
`spawn-orchestrator.sh` into it.**

The design doc already drew most of this line. §6 of
`auto-pilot-e-lite-design-2026-07-21.md` sorts the existing code into three
categories — **Keep as-is**, **Port small**, **Delete, do not port**. §6 never
mentions an `aiutopilot` repo: the routing to a new repo below is _this_
document's synthesis of §6 with the components specified in §2, §4, and §5. Read
the §6 bullets as quotation and the last bullet as a proposal.

- **§6 Delete, do not port** — `spawn-orchestrator.sh`, its test suite,
  `orchestrator.sb.tmpl`, `orchestrator.plist.tmpl`, `smoke-confinement.sh`, the
  supervisor pause ledger, exit classification, the in-jail alarm machinery, the
  verify broker, wrapper-specific doctor repairs. Moving 12k lines you have
  committed to deleting into a new repo would make that repo's first act the
  adoption of the thing the redesign exists to escape. It stays in
  `workflow-skills` as the explicitly unsupported fallback until Stage 5 plus the
  grep-clean dependency audit, then dies in place.
- **§6 Keep as-is** — `/deliver-task` lifecycle + adapters + co-review + the freeze
  rule; `task-scan.py`, `plan-graph.py`, `claim-scan.sh`, `validate.py`,
  `probe-coders.sh`; `select-coder` / `orchestrate-coders` / `cao-coder.sh`;
  `pr-fix-guard.sh`; the run-state file formats as the human-readable ledger.
  These stay in `workflow-skills` permanently.
- **§6 Port small** — the admission canary (successor of `preflight.sh`),
  read-only reconciliation (successor of doctor's seven invariants), **resume**
  (as `ap-launch --resume <run_id>`), the **run-loop prose in `SKILL.md`**
  (heartbeat touch + reserve gate + §5.3 stop semantics, wrapped in `nono run`),
  and `scripts/claude-usage.sh`. All five follow the new control plane into
  `aiutopilot`. Resume and the run-loop prose are named explicitly here because
  they are easy to lose: they are §6 items with no §4/§5 component of their own,
  and the run-loop prose in particular is the piece that decides decision 6.

- **Greenfield, from §2/§4/§5 → `aiutopilot`** — `ap-launch`, the watcher
  (§5.1), the registry (§4.2), the token broker (§2.1), `ap-agent-exec`, and
  **the runaway ceiling**, which is row 5b's redirect and the first component to
  be written.

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

### The `deliver-task` coupling is real, and it is not what an earlier draft claimed

An earlier draft of this handoff argued the coupling was eighteen `.auto-pilot/`
run-state **paths** plus one invocation — "a documented directory layout, not an
API." That was measured wrong, and the co-review caught it. `SKILL.md` contains
**zero** literal `.auto-pilot/` paths; that file format is referenced from
`skills/auto-pilot/references/`, not from `deliver-task`. What is actually in
`skills/deliver-task/SKILL.md`, across its 27 auto-pilot-referencing lines:

- **Five relative markdown links** into `skills/auto-pilot/references/` —
  `run-state.md` (`:68`, `:292`), `run-budget.md` (`:77`, `:92`),
  `launch-runtime.md` (`:213`).
- **Exactly one real invocation** — `spawn-orchestrator.sh write-verify-broker`
  at `:212`. That part of the earlier claim was right.
- **Behavioural delegation**, which the earlier draft missed entirely: the
  auto-pilot reserve gate (`:60`-`:98`), pause/exit classification handed to
  auto-pilot's `supervisor-check` (`:98`), and verify routed through the
  un-jailed **verify broker** (`:208`-`:221`).

Do not read this section as a reason the split is cheap. Read it as pre-existing
debt sitting on the legacy side of the line: §6 deletes the verify broker **and**
exit classification, so two of those three behavioural couplings come due at
Stage 5 whether or not the split happens. The split neither creates them nor
worsens them.

What the split does put at stake is the five relative links — whether they still
resolve depends entirely on where `skills/auto-pilot/` ends up, which is
decision 6 below. The case for splitting rests on "nothing of the new control
plane exists yet," not on this section.

## What you are being asked to decide

These were left open deliberately. Each needs a real answer, not a default.

1. **Does the E-lite corpus move, and how much of it?**
   `dev_docs/auto-pilot-e-lite-design-2026-07-21.md`, the **five** review docs
   (`auto-pilot-e-lite-design-review-codex.md` plus `-r2` through `-r5` — an
   earlier draft said four), and
   `dev_docs/elite-spike/` (1.3M) are the research record for decisions that will
   live in `aiutopilot`. Moving them means PR #243 closes unmerged.
   **Recommended:** they move; the design doc is the new repo's charter.
   **Except — and this is a criterion, not a one-off:** docs whose subject is
   `spawn-orchestrator.sh` or its skill surface stay, because they document
   code that lives and dies in `workflow-skills`.
   `dev_docs/tasks/probe5-incident-evidence/` is the clearest case (it was
   also explicitly marked as outliving the spike), but the same rule keeps at
   least `auto-pilot-hardening.md`, `autopilot-detached-run-1-findings.md`,
   `auto-pilot-spawn-smoke.md`, and both
   `auto-pilot-architecture-review-2026-07-21*.md`. `dev_docs/` holds a
   dozen-plus `auto-pilot-*` docs; apply the criterion file by file in the
   split table — each needs a side.
   **Also:** `-r3` (`:53`, `:59`) and `-r4` (`:62`, `:78`) contain absolute
   links into a stale local worktree
   (`/Users/danielegan/src/workflow-skills/.worktrees/e-lite-review/scripts/…`)
   pointing at `claude-usage.sh` and `claude-auto-resume.sh` — files that stay
   behind. Those links are already broken and stay broken after a move; decide
   whether to rewrite them or annotate them.

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
   The moved files also reference code that stays behind: `results.json`,
   `driver.sh`, `runaway.py`, `scenarios.py`, `legs.py`, `probe5b-runaway.md`,
   and all five `evidence-*.jsonl` files name `spawn-orchestrator.sh`
   textually. Their sha256s are pinned in `results.json`, so those references
   cannot be edited out without rebuilding the manifest — which would destroy
   the provenance this decision exists to preserve. The `PROVENANCE` note must
   explain the dangling references, not remove them.
   **This decision is not independent of decision 2.** All four
   `fixture_git_revision` values (`2636bc96`, `8e9be3ba`, `92455b6c`, `c711dbf`)
   are reachable only from `bestdan/autopilot-e-lite-design` and this branch —
   none is on `main`. If #243 closes unmerged and the branch is later deleted,
   those SHAs become unreachable in `workflow-skills` too, and a `PROVENANCE`
   note would record origin SHAs that resolve nowhere. Pinning the branch tip
   (a tag, or an archive branch in `workflow-skills`) is a precondition for the
   `PROVENANCE` option, not an optional extra.

4. **`scripts/claude-usage.sh` — copy or share?**
   §6 has it serving both the agent's reserve gate and the watcher's
   maintainer-side observation. 216 lines, standalone — but not unconsumed:
   inside `workflow-skills` it is called by `claude-auto-resume.sh`,
   `spawn-orchestrator.sh`, and deliver-task's reserve gate, and covered by
   three bats suites (`claude-usage.bats`, `auto-pilot-reserve.bats`,
   `claude-auto-resume.bats`). `claude-auto-resume.sh` outlives the
   orchestrator, so the `workflow-skills` copy is permanent, not
   transitional — both copies are.
   **Recommended:** copy it into `aiutopilot`, which owns the canonical
   version from then on; the `workflow-skills` copy is frozen for its legacy
   consumers. Cheaper to duplicate than to coordinate across repos, and the
   two credential contexts want different behaviour anyway. Say so explicitly
   in both copies. §6's "verified (fixed if needed) for both credential
   contexts" obligation lands on the `aiutopilot` copy. Decide in the split
   table whether the three bats suites fork with it or stay behind.

5. **What is the new repo's test and check story on day one?**
   `workflow-skills`'s `scripts/check.sh` carries an **environmental failing
   baseline** — 57 names at last recording, but the count drifts run to run
   (70, 64, 62, 57 observed) — dominated by the 5,467-line orchestrator suite.
   The recorded artifact is
   `dev_docs/elite-spike/fixtures/runaway/check-baseline.{txt,sh}`: compare
   failing test NAMES against it, never counts. That noise taxes every
   unrelated change — during Probe 5b it cost real effort to prove a failure
   was a pre-existing flake rather than a regression. The new
   repo starts with no baseline debt. Decide deliberately what keeps it that way
   before the first test lands.

6. **Where does the public `/auto-pilot` surface go?**
   Neither §6 nor the first five decisions assign a destination to
   `skills/auto-pilot/` (SKILL.md + six reference docs),
   `commands/auto-pilot.md`, or `test/auto-pilot-reserve.bats` /
   `test/auto-pilot-less-claude.bats`. That layer is the plugin's **install
   surface** — the thing the opening paragraph of this document complains about —
   and it is what `spawn-orchestrator.sh` is invoked _through_. **Settle this
   before moving any file.** The two answers cost very different amounts:
   - **Stays, dies with the orchestrator.** The split remains what it claims to
     be — "create a repo," not "migrate code" — and `deliver-task`'s five
     relative links keep resolving. But installing the plugin for
     `/plan-with-docs` keeps installing the kill machinery until Stage 5.
   - **Moves to `aiutopilot`.** The install-surface complaint is cured
     immediately, but the split is no longer greenfield-only:
     `references/run-state.md` alone carries 15 `.auto-pilot/` references and the
     reference docs are §6's named audit set, and `deliver-task`'s five links
     break on the day it moves.

   **Recommended:** stays. The greenfield control plane is what `aiutopilot` is
   for; the legacy skill surface is scheduled to die, and moving a corpse into
   the new repo is the failure mode §6 already refused for
   `spawn-orchestrator.sh`. But the §6 "Port small" **run-loop prose in
   `SKILL.md`** is written _fresh_ in `aiutopilot`'s own skill — do not read
   "stays" as leaving the new repo without a skill surface.

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
3. Bring the six open decisions above to the maintainer with a recommendation
   each.
4. After any move, prove `workflow-skills` still stands — against the
   recorded baseline, not a bare count: run `check.sh` unsandboxed and feed
   the transcript to `./check-baseline.sh --compare` (fails only on failing
   names not in `check-baseline.txt`); confirm `deliver-task`'s five relative
   links into `skills/auto-pilot/references/` still resolve; re-run
   `./results.py check-hashes` and `selftest` wherever the fixture landed.
