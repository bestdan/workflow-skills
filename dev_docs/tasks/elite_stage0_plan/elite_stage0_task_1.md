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
- Verify no ACL leakage from the maintainer home: plant a throwaway sentinel (`~danielegan/.autopilot-sentinel`), then from the maintainer shell run `sudo -u agent cat ~danielegan/.autopilot-sentinel`, `sudo -u agent ls ~danielegan/.ssh/`, and `sudo -u agent cat ~danielegan/Library/Keychains/login.keychain-db` — each must exit non-zero with a permission error. Delete the sentinel after the denials are recorded.
- Verify no directory is writable by both `agent` and the maintainer (§3.1 "no shared writable directories"): check `/Users/Shared` and any group-writable paths in both homes.
- Confirm the agent user has **zero** sudo rules (`sudo -l -U agent` shows none).
- Record the exact commands used (sysadminctl/dscl) in `dev_docs/elite-spike/provisioning.md` so the setup is reproducible — with the password argument replaced by `<redacted>`; no password value may appear in checked-in evidence (plan sanitization checklist).

## Acceptance Criteria

- **User-run:**
  - Output of `id agent` includes `apagent` and does not include `admin`.
  - `sudo -l -U agent` reports no rules.
  - Each read-check command in the Task list exits non-zero with a permission error; transcripts captured in `dev_docs/elite-spike/provisioning.md`.
  - No shared-writable directory found (or each one found is removed/justified in `provisioning.md`).
  - `/Users/agent/work/` exists, `agent`-owned, 0700.
  - Provisioning commands (password `<redacted>`) checked into `dev_docs/elite-spike/provisioning.md`.
