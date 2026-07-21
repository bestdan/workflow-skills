---
title: "identity: provision the non-admin `agent` macOS user + `apagent` group — no sudoers, no production paths"
priority: urgent
size: 2
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by:
parent: elite_stage0
tags: [e-lite, spike, stage-0, ops, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

Design §3.1 and §7 Stage 0 (dev_docs/auto-pilot-e-lite-design-2026-07-21.md). Stage 0 provisions the **minimal** agent identity only: a non-admin headless `agent` account and an `apagent` group. Explicitly **not** in this task: any sudoers entry, anything under `/usr/local/autopilot/`, GitHub App or Linear keys, production installation paths. Those arrive in later stages after the spike's evidence. This is the only privileged action the spike permits (§0a).

This is attended ops work on the always-on mac mini — not a coding task, and never dispatched to an unattended agent.

## Task

- Create the `agent` user: non-admin, headless (no auto-login), home `/Users/agent/`, shell zsh.
- Create work root `/Users/agent/work/` owned `agent`, mode 0700.
- Create the `apagent` group with `agent` as a member (the group Stage 1's broker will use for 0640/0750 token/dir modes).
- Verify no ACL leakage from the maintainer home: as `agent`, attempt to read a sentinel file in `~danielegan/` (e.g. a throwaway `~/.autopilot-sentinel`), `~/.ssh/`, and the Keychain — all must fail.
- Confirm the agent user has **zero** sudo rules (`sudo -l -U agent` shows none).
- Record the exact commands used (sysadminctl/dscl) in the spike evidence directory so the setup is reproducible.

## Acceptance Criteria

- **User-run:**
  - `id agent` shows a non-admin user with `apagent` membership; `agent` is absent from the `admin` group.
  - `sudo -l -U agent` reports no rules.
  - As `agent`: maintainer-home sentinel, `~danielegan/.ssh/`, and maintainer Keychain reads all denied.
  - `/Users/agent/work/` exists, `agent`-owned, 0700.
  - Provisioning commands checked into the spike evidence directory (see plan open question on its location).
