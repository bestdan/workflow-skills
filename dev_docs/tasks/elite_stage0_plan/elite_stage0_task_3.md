---
title: "probe 1: dedicated-user viability canary — headless Claude auth, sandbox, worker, sentinel unreadability (shell + launchd)"
priority: urgent
size: 3
status: new
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

## Context

Design §7a priority 1. Key assumption: the no-VM `agent` identity is a usable execution substrate with a real filesystem/Keychain boundary. Falsification redirect: stop the no-VM E-lite substrate; test a microVM-per-run boundary or restrict to attended operation. This is the highest-blast-radius probe — it can kill the whole architecture, so it runs first.

Uses the real Max account in the two permitted ways only (§0a): establish the agent user's own Max OAuth, and run the minimal invocations the headless-auth check requires — **one per launch context, two total**; that is the minimum §7a row 1's both-contexts requirement admits, and it is the spike's entire real-Claude budget. The credentials.json + claude.json pairing caveat (§2.3) is a known trap — record what auth method actually worked.

## Task

From **both** an interactive shell as `agent` (`sudo -u agent -i`, per the plan's agent-access note) and a per-user launchd test job (no-GUI context), per the kill sheet (`dev_docs/elite-spike/kill-sheet.md`, [[elite_stage0_task_2]]):

- For the launchd context: install a LaunchAgent plist under `~agent/Library/LaunchAgents/`, then attempt bootstrap into both `launchctl bootstrap gui/$(id -u agent)` and `launchctl bootstrap user/$(id -u agent)`, recording which domain accepts and runs the job without a GUI login — the accepted domain is itself probe evidence.
- Establish agent Claude Max auth (interactive OAuth preferred; `setup-token` fallback); then verify a **headless** `claude` invocation succeeds in each context — one minimal invocation per context, two total (§0a). Instrument the interactive one for double duty: launch `claude` through a minimal disposable `setsid(2)→execve` wrapper, record the **session UUID** (feeds the coherence probe, [[elite_stage0_task_7]]) and capture the live **shim→Claude process topology** (`{pid, pgid, sid, executable}` immediately before and after the exec — the real-binary observation the process-binding probe, [[elite_stage0_task_5]], cross-checks its surrogate against).
- Start Claude Code with the native sandbox in `failIfUnavailable` mode; confirm sandbox startup succeeds in both contexts.
- Clone the spike test repo ([[elite_stage0_task_10]]) into a disposable spike directory (e.g. `/Users/agent/spike/` — never `/Users/agent/work/`, the production layout), dispatch one trivial worker (subagent) against it, and confirm it completes.
- Verify tool/cache access: run one shared Homebrew binary (`git --version`) as `agent`, and write + read back a file in `~agent/Library/Caches/` and in the npm/uv cache dir — each command exits 0.
- Sentinel unreadability from inside the session's sandboxed Bash: `cat ~danielegan/.autopilot-sentinel` (planted by the maintainer), `ls ~danielegan/.ssh/`, `cat ~danielegan/.aws/credentials`, `cat ~danielegan/.gitconfig`, and a read of `~danielegan/Library/Keychains/login.keychain-db` — pass = every attempt returns permission-denied.
- Record uid, groups, HOME, PATH, TMPDIR, CWD, tool versions per context into `dev_docs/elite-spike/fixtures/probe1/env-<context>.txt`.
- Close the probe per the plan's probe close protocol; write the row in `dev_docs/elite-spike/measurements.md` with evidence sanitized per the plan checklist.

## Acceptance Criteria

- **User-run:** every check above executed in both launch contexts; probe closed per the plan's probe close protocol (terminal `confirmed`/`falsified`/`inconclusive`, ½-day cap); evidence + fixture commands checked into `dev_docs/elite-spike/` (sanitized per the plan checklist).
- If `falsified` or load-bearing `inconclusive`: dependent tasks ([[elite_stage0_task_5]], [[elite_stage0_task_7]]) stop at their next safe checkpoint and the named redirect is taken or the dependent feature deferred (§7a rule 5) — record which in the measurement row.
