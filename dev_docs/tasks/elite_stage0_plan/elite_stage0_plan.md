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

## Scope / non-goals

- **In scope:** `agent` user + `apagent` group provisioning (no sudoers, no production paths); the kill sheet (probe 0); tranche-1 probes 1–3 and the two opportunistic probes; the measured revision + re-plan checkpoint.
- **Out of scope (deliberately):**
  - **Stage 1** (broker, GitHub App, rulesets, probe 4 / GitHub authority canary — needs the disposable test App, planned with Stage 1).
  - **Probe 5** (baseline crash-transaction kernel) — its input is the draft state machine produced by the measured revision (task 8); it is spawned by the re-plan checkpoint, not pre-planned here.
  - Any production control-plane implementation (Stage 2+), continuation, CAO.
- **Spike contract (§0a) applies to every probe task:** disposable directory + dedicated test repository, no production sudoers, nothing under `/usr/local/autopilot`, no Linear writes, no production App credential, never promote spike code by renaming. Real Max account used only for the agent's own OAuth + probe 1's minimal invocation and read-only coherence queries.
- **Tranche discipline:** probes run in one-working-day tranches; every started probe closes as `confirmed` / `falsified` / `inconclusive` against its pre-written kill sheet. No `unfinished` state.

## Approach

Falsifier first, fixture second, production component last (§7a). The kill sheet is written before any fixture. Probes 1 (user canary) and 3 (alert skeleton) may run in parallel after the kill sheet; the topology probe selects the run-shim shape for probe 2 (process binding); Max coherence runs opportunistically once the agent's Claude auth exists. All evidence is checked in under `dev_docs/elite-spike/` (measurement table, fixture commands, sanitized raw evidence — never secrets). The plan ends in a measured-revision + re-plan task, because a falsified probe can delete downstream work — later stages are intentionally not encoded as tasks yet.

All probe tasks are **attended, user-run on the mac mini** — they need real credentials, real launchd, and a real device for alerts. None should be promoted into unattended `/do-tasks` or auto-pilot runs.

## Tasks

1. [[elite_stage0_task_1]] — Provision the `agent` user + `apagent` group (ops; no sudoers, no production paths).
2. [[elite_stage0_task_2]] — Write the kill sheet (probe 0): falsifier, pass threshold, inconclusive condition, time cap, dependent work, redirect for every tranche-1 probe.
3. [[elite_stage0_task_3]] — Probe 1: dedicated-user viability canary (headless Claude auth, sandbox startup, one worker, sentinel unreadability — interactive + launchd contexts).
4. [[elite_stage0_task_4]] — Opportunistic probe: `setsid(2) → execve` topology capture (selects the run-shim implementation).
5. [[elite_stage0_task_5]] — Probe 2: tmux/process-binding spike (incarnation identity, pane death, launcher death, replacement panes, stop races).
6. [[elite_stage0_task_6]] — Probe 3: real alert walking skeleton (kill + wedge a launchd heartbeat process; device notified within 10 minutes).
7. [[elite_stage0_task_7]] — Opportunistic probe: Max-window coherence (agent vs maintainer usage query on the exact same test session).
8. [[elite_stage0_task_8]] — Measured revision + re-plan checkpoint: classify all results, draft the trusted manifest / registry schema / launch-lease state machine / rollback table; plan Stage 1 and probe 5.
9. [[elite_stage0_task_9]] — Graduate durable findings into `dev_docs/` and delete this plan's scaffolding.
10. [[elite_stage0_task_10]] — Create the dedicated spike test repository (harmless target for probes 1–2, later probe 4).

## Decisions (2026-07-21)

- **Evidence home:** `dev_docs/elite-spike/` in this repo (kill sheet, measurement table, fixtures).
- **Push channel:** chosen in the kill sheet (task 2) with rationale; probe 3 executes against that choice.
- **Test repository:** created by task 10 (e.g. `bestdan/autopilot-spike-target`), name recorded in the evidence directory.

## Open questions

- **Tranche packing:** tasks 3, 4, 6 are one tranche if they fit one working day; the kill sheet (task 2) decides the actual packing. The task files encode dependencies, not tranche membership.
