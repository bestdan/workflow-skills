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
  - scripts/claude-usage.sh
is_blocked_by: [elite_stage0_task_1, elite_stage0_task_2, elite_stage0_task_10]
parent: elite_stage0
tags: [e-lite, spike, stage-0, probe, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

Design §7a priority 1. Key assumption: the no-VM `agent` identity is a usable execution substrate with a real filesystem/Keychain boundary. Falsification redirect: stop the no-VM E-lite substrate; test a microVM-per-run boundary or restrict to attended operation. This is the highest-blast-radius probe — it can kill the whole architecture, so it runs first.

Uses the real Max account in the two permitted ways only (§0a): establish the agent user's own Max OAuth, and run the minimal invocation the headless-auth check requires. The credentials.json + claude.json pairing caveat (§2.3) is a known trap — record what auth method actually worked.

## Task

From **both** an interactive shell as `agent` and a per-user launchd test job (no-GUI context), per the kill sheet ([[elite_stage0_task_2]]):

- Establish agent Claude Max auth (interactive OAuth preferred; `setup-token` fallback); then verify a **headless** `claude` invocation succeeds. This is the spike's one authorized real-Claude invocation — instrument it for double duty: record the **session UUID** (feeds the coherence probe, [[elite_stage0_task_7]]) and capture the live **shim→Claude process topology** (`{pid, pgid, sid, executable}` before/after — the real-binary observation the process-binding probe, [[elite_stage0_task_5]], cross-checks its surrogate against).
- Start Claude Code with the native sandbox in `failIfUnavailable` mode; confirm sandbox startup succeeds in both contexts.
- Dispatch one trivial worker (subagent) against the spike test repo ([[elite_stage0_task_10]]) and confirm it completes.
- Verify tool/cache access: Homebrew binaries shared, agent-local caches/globals writable.
- Sentinel unreadability from inside the session: maintainer home sentinel, Keychain, `~/.ssh`, `~/.aws`, personal gitconfig — all unreadable.
- Record uid, groups, HOME, PATH, TMPDIR, CWD, tool versions per context.
- Close the probe against the kill sheet: `confirmed` / `falsified` / `inconclusive`; write the row in `measurements.md` with sanitized evidence.

## Acceptance Criteria

- **User-run:** every check above executed in both launch contexts; probe closed with a classified result; evidence + fixture commands checked in (no secrets, no tokens).
- If `falsified` or load-bearing `inconclusive`: dependent tasks ([[elite_stage0_task_5]], [[elite_stage0_task_7]]) stop at their next safe checkpoint and the redirect is taken — record that decision in the measurement row.
