---
type: epic
title: "E-lite Stage 0 — minimal agent identity + bounded measurement spike"
status: active
owner: bestdan
created: 2026-07-21
---

# E-lite Stage 0 plan

Source design: [../../auto-pilot-e-lite-design-2026-07-21.md](../../auto-pilot-e-lite-design-2026-07-21.md) (§0a, §7 Stage 0, §7a).

## Goal

Provision the minimal `agent` identity and run the Stage-0 measurement spike: falsify the load-bearing substrate assumptions (dedicated-user viability, process binding, alert delivery, `setsid→execve` topology, Max-window coherence) before any control-plane code exists, then fold the results into the measured design revision that gates Stage 2.

> **Update (2026-07-22):** the containment layer is now **nono** (adopted; design §3.2, evaluation in `../../nono-evaluation.md`), not a hand-rolled Seatbelt render. **Probe 1 ([[elite_stage0_task_3]]) is substantially done** via that evaluation — agent identity provisioned, headless Claude auth, sandbox startup, and sentinel unreadability all confirmed under `../../elite-spike/`. Probes 2 (tmux/process binding) and 3 (alert skeleton) — the control-plane substrate nono does not touch — remain the open critical path.

## Scope / non-goals

- **In scope:** `agent` user + `apagent` group provisioning (no sudoers, no production paths); the kill sheet (probe 0); tranche-1 probes 1–3 and the two opportunistic probes; the measured revision; the re-plan checkpoint (gated on the revision's approval).
- **Out of scope (deliberately):**
  - **Stage 1** (broker, GitHub App, rulesets, probe 4 / GitHub authority canary — needs the disposable test App, planned with Stage 1).
  - **Probe 5** (baseline crash-transaction kernel) — its input is the draft state machine produced by the measured revision (task 8); it is spawned by the re-plan checkpoint, not pre-planned here.
  - Any production control-plane implementation (Stage 2+), continuation, CAO.
- **Spike contract (§0a) applies to every probe task:** disposable directory + dedicated test repository, no production sudoers, nothing under `/usr/local/autopilot`, no Linear writes, no production App credential, never promote spike code by renaming. Real Max account used only for the agent's own OAuth + probe 1's minimal invocation and read-only coherence queries.
- **Tranche discipline:** probes run in one-working-day tranches; every started probe closes as `confirmed` / `falsified` / `inconclusive` against its pre-written kill sheet. No `unfinished` state.

## Approach

Falsifier first, fixture second, production component last (§7a). The kill sheet is written before any fixture. Probes 1 (user canary) and 3 (alert skeleton) may run in parallel after the kill sheet; the topology probe selects the run-shim shape for probe 2 (process binding), which waits only on the agent identity per §7a; Max coherence runs opportunistically, read-only, against probe 1's recorded session. Probe 1's single authorized Claude invocation does double duty — it also records the session UUID and the real shim→Claude topology, so no other probe consumes Max usage. All evidence is checked in under `dev_docs/elite-spike/` (measurement table, fixture commands, sanitized raw evidence — never secrets). The plan ends in a measured revision followed by a separately gated re-plan checkpoint, because a falsified probe can delete downstream work — later stages are intentionally not encoded as tasks yet.

All probe tasks are **attended, user-run on the mac mini** — they need real credentials, real launchd, and a real device for alerts. None should be promoted into unattended `/do-tasks` or auto-pilot runs.

## Binding conventions (every task cites these instead of restating them)

### Evidence layout

All spike artifacts live under `dev_docs/elite-spike/`:

- `kill-sheet.md` — probe 0's output ([[elite_stage0_task_2]]); one row per probe.
- `measurements.md` — **exactly one row per probe**: probe → fixture command/test → sanitized evidence link → non-secret environment metadata → result → decision. Anything larger than a row (fault matrices, per-scenario detail, side-by-side captures) lives under `fixtures/<probe>/` and is linked from the row.
- `fixtures/<probe>/` — fixture scripts, plists, and raw captures, one directory per probe (`fixtures/probe1/`, `fixtures/setsid-topology/`, `fixtures/probe2/`, `fixtures/probe3/`, `fixtures/max-coherence/`).
- `provisioning.md` — exact identity-provisioning commands ([[elite_stage0_task_1]]), password redacted.
- `environment.md` — durable environment facts: spike repo URL, macOS version/build, tool versions.
- `replan-decision.md` — the re-plan checkpoint's decision record ([[elite_stage0_task_11]]), written only if the checkpoint redirects or stops instead of producing a next-tranche plan.

### Sanitization (§7a rule 4, binding for every checked-in artifact)

Never persist bearer tokens, credential files, secret-bearing headers, or secret environment values. Additionally redact: the agent account password, push-provider tokens/topics/user keys/device identifiers, hostnames, and absolute home paths (generalize to `~maintainer` / `~agent`). Kept in the clear as required metadata: usage-window percentages/boundaries/`reset_epoch`, macOS version/build, tool versions. Any per-task restatement of this checklist is non-normative — this list governs in full for every checked-in artifact.

### Probe close protocol (tasks 3–7)

Every started probe closes as exactly one of `confirmed` / `falsified` / `inconclusive` against its pre-written kill-sheet row — no fourth state, no automatic extension (§0a). Time cap: half a working day (§7a rule 3) unless the kill sheet records an override; at the cap, stop and classify. On `falsified` or load-bearing `inconclusive`, take the named redirect **or defer the dependent feature** (§7a rule 5) and record which in the measurement row; a rerun requires a changed kill sheet naming the new discriminating evidence (§7a rule 6).

### Running as `agent` (no sudoers)

Stage 0 installs no sudoers entry. Probes obtain agent execution two ways: (a) an interactive agent shell via the maintainer's own admin sudo — `sudo -u agent -i` or `sudo -u agent <cmd>` from an attended shell (the stock `%admin` rule; nothing installed, and the agent itself still has zero sudo rules); (b) the per-user launchd test job for the no-GUI context (probe 1). Observers always run as the maintainer uid from a separate shell.

### Probe workspaces & reproducibility

- All probe scratch work lives under the agent-owned disposable root `/Users/agent/spike/` — one subdirectory per probe (`/Users/agent/spike/<probe>/`, e.g. `/Users/agent/spike/probe2/`); the spike-repo clone made by [[elite_stage0_task_3]] lives under the same root. Never `/Users/agent/work/` (the production layout) and never `/usr/local/autopilot` (§0a).
- Where a task's acceptance criteria say a fixture "reproduces from a clean checkout", that means: from a clean checkout of this repo on a host with the task-1 `agent` identity provisioned, invoked per the Running-as-`agent` note above (agent-side commands via `sudo -u agent`, observer as maintainer), with the fixture's documented prerequisites (e.g. tmux) installed.

## Tasks

1. [[elite_stage0_task_1]] — Provision the `agent` user + `apagent` group (ops; no sudoers, no production paths).
2. [[elite_stage0_task_2]] — Write the kill sheet (probe 0): falsifier, pass threshold, inconclusive condition, time cap, dependent work, redirect for every tranche-1 probe.
3. [[elite_stage0_task_3]] — Probe 1: dedicated-user viability canary (headless Claude auth, sandbox startup, one worker, sentinel unreadability — interactive + launchd contexts).
4. [[elite_stage0_task_4]] — Opportunistic probe: `setsid(2) → execve` topology capture (selects the run-shim implementation).
5. [[elite_stage0_task_5]] — Probe 2: tmux/process-binding spike (incarnation identity, pane death, launcher death, replacement panes, stop races).
6. [[elite_stage0_task_6]] — Probe 3: real alert walking skeleton (kill + wedge a launchd heartbeat process; device notified within 10 minutes).
7. [[elite_stage0_task_7]] — Opportunistic probe: Max-window coherence (agent vs maintainer usage query on the exact same test session).
8. [[elite_stage0_task_8]] — Measured revision: classify all results; complete (or explicitly block) every §0a design-choice contract; draft the state machine probe 5 will falsify.
9. [[elite_stage0_task_9]] — Graduate durable findings into `dev_docs/` and delete this plan's scaffolding.
10. [[elite_stage0_task_10]] — Create the dedicated spike test repository (harmless target for probes 1–2, later probe 4).
11. [[elite_stage0_task_11]] — Re-plan checkpoint: plan Stage 1 + probe 5 from the **approved** measured revision (graph-gated so falsifications can't leak into downstream planning).

## Decisions (2026-07-21)

- **Evidence home:** `dev_docs/elite-spike/` in this repo (kill sheet, measurement table, fixtures).
- **Push channel:** chosen in the kill sheet (task 2) with rationale; probe 3 executes against that choice.
- **Test repository:** created by task 10 (e.g. `bestdan/autopilot-spike-target`), name recorded in the evidence directory.

## Open questions

- **Tranche packing:** tasks 3, 4, 6 are one tranche if they fit one working day; the kill sheet (task 2) decides the actual packing. The task files encode dependencies, not tranche membership.
