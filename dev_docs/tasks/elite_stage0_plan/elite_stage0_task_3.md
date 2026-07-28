---
title: "probe 1: dedicated-user viability canary — headless Claude auth, sandbox, worker, sentinel unreadability (shell + launchd)"
priority: urgent
size: 3
status: done
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: [elite_stage0_task_1, elite_stage0_task_2, elite_stage0_task_10]
parent: elite_stage0
tags: [e-lite, spike, stage-0, probe, user-run]
---

Plan: [[elite_stage0_plan]]

> **Status (2026-07-22): substantially DONE via the nono evaluation.** Confirmed live and recorded under `dev_docs/elite-spike/`: agent provisioned (non-admin, 0700 home, clean login keychain), headless Claude auth, sandbox startup + sentinel unreadability (uid + sandbox layers). **The sandbox is now [nono](../../nono-evaluation.md) (design §3.2, adopted), not a hand-rolled Seatbelt render** — so this probe's "native sandbox startup" means `nono run` with the vendored `claude` profile. Remaining: the full interactive+launchd matrix under one enabled worker. The Max-invocation budget below was already spent by the nono eval's F1.

## Context

Design §7a priority 1. Key assumption: the no-VM `agent` identity is a usable execution substrate with a real filesystem/Keychain boundary. Falsification redirect: stop the no-VM E-lite substrate; test a microVM-per-run boundary or restrict to attended operation. This is the highest-blast-radius probe — it can kill the whole architecture, so it runs first.

Uses the real Max account in the two permitted ways only (§0a): establish the agent user's own Max OAuth, and run the minimal invocations the headless-auth check requires — **one per launch context, two total**; that is the minimum §7a row 1's both-contexts requirement admits, and it is the spike's entire real-Claude budget. The credentials.json + claude.json pairing caveat (§2.3) is a known trap — record what auth method actually worked.

## Task

From **both** an interactive shell as `agent` (`sudo -u agent -i`, per the plan's agent-access note) and a per-user launchd test job (no-GUI context), per the kill sheet (`dev_docs/elite-spike/kill-sheet.md`, [[elite_stage0_task_2]]):

- For the launchd context: install a LaunchAgent plist under `~agent/Library/LaunchAgents/`, then attempt bootstrap into both `launchctl bootstrap gui/$(id -u agent)` and `launchctl bootstrap user/$(id -u agent)`, recording which domain accepts and runs the job without a GUI login — the accepted domain is itself probe evidence. Domain discovery uses a **no-op payload** (e.g. `/usr/bin/true`); the single launchd Claude invocation then runs only in the one accepted domain (a Claude payload accepted by both domains would run twice and breach the one-per-context budget). The plist sets `StandardOutPath`/`StandardErrorPath` under `/Users/agent/spike/probe1/`.
- Clone the spike test repo ([[elite_stage0_task_10]]) into the disposable spike root `/Users/agent/spike/` (plan's probe-workspace convention — never `/Users/agent/work/`, the production layout). Establish agent Claude Max auth (interactive OAuth preferred; `setup-token` fallback) — auth setup is the separately permitted §0a use, not an invocation.
- Run each context's **single minimal invocation** — together the spike's entire real-Claude budget (one per context, two total; §0a). Every other check below is either an observation of these two launches or a non-Claude shell check (tool/cache, sentinel layer 2) — never an additional `claude` invocation. Each invocation launches `claude` **headless** with the native sandbox in `failIfUnavailable` mode against the spike-repo clone, with a prompt that (a) dispatches one trivial worker (subagent) that writes `/Users/agent/spike/probe1/worker-artifact-<context>.txt` and (b) runs the layer-1 sentinel reads below from the session's sandboxed Bash. Pass, per context = exit 0, non-empty model output, the worker's artifact exists, and the captured log shows the sandbox was active rather than silently degraded — record the exact sandbox setting used and the proving log line in `fixtures/probe1/`.
- Instrument the interactive invocation for double duty: launch it through a minimal disposable `setsid(2)→execve` wrapper, record the **session UUID** (feeds the coherence probe, [[elite_stage0_task_7]]) and capture the live **shim→Claude process topology** (`{pid, pgid, sid, executable}` immediately before and after the exec — the real-binary observation the process-binding probe, [[elite_stage0_task_5]], cross-checks its surrogate against). Record both to `dev_docs/elite-spike/fixtures/probe1/session-uuid.txt` and `fixtures/probe1/topology.txt`.
- Verify tool/cache access: run one shared Homebrew binary (`git --version`) as `agent`, and write + read back a file in `~agent/Library/Caches/`, in `~agent/.npm/`, and in the directory `uv cache dir` reports (run as agent) — each command exits 0.
- Sentinel unreadability, in **both layers**: layer 1 — inside **each** context's invocation, from the session's sandboxed Bash: `cat ~maintainer/.autopilot-sentinel` (planted by the maintainer), `ls ~maintainer/.ssh/`, `cat ~maintainer/.aws/credentials`, `cat ~maintainer/.gitconfig`, and a read of `~maintainer/Library/Keychains/login.keychain-db`; layer 2 — the identical reads from a plain unsandboxed agent shell (`sudo -u agent <cmd>`) — pass = every attempt in both layers returns permission-denied (a sandbox denial alone can mask a uid-level hole). Commands are written `~maintainer` here; substitute the real account locally and generalize any checked-in capture per the plan sanitization checklist.
- Record uid, groups, HOME, PATH, TMPDIR, CWD, tool versions per context into `dev_docs/elite-spike/fixtures/probe1/env-interactive.txt` and `env-launchd.txt`.
- Close the probe per the plan's probe close protocol; write the row in `dev_docs/elite-spike/measurements.md` with evidence sanitized per the plan checklist.

## Acceptance Criteria

- **User-run:** each listed check executed in its named context(s), with both launch contexts covered by the per-context invocation checks; probe closed per the plan's probe close protocol (terminal `confirmed`/`falsified`/`inconclusive`, ½-day cap); evidence + fixture commands checked into `dev_docs/elite-spike/` (sanitized per the plan checklist).
- If `falsified` or load-bearing `inconclusive`: dependent tasks ([[elite_stage0_task_5]], [[elite_stage0_task_7]]) stop at their next safe checkpoint and the named redirect is taken or the dependent feature deferred (§7a rule 5) — record which in the measurement row.
