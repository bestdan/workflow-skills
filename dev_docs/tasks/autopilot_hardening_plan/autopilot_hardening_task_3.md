---
title: "spawn: profile write-scopes — credential files RO vs tool state dirs RW"
priority: high
size: 2
status: done # merged as PR #177 (detached run #2, 2026-07-11)
created: 2026-07-10
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/orchestrator.sb.tmpl
  - skills/auto-pilot/references/launch-runtime.md
is_blocked_by: autopilot_hardening_task_2
parent: autopilot_hardening
tags: [auto-pilot, spawn, sandbox, p1]
---

[[autopilot_hardening_plan]]

## Context

Finding **P1 #5**. `launch-runtime.md` §3 (Credential access model) mounts
`~/.claude`, `~/.config/gh`, `~/.codex` **read-only**. But a long orchestrator and
its coders must **write** their own state: `~/.claude` (sessions/todos/statsig),
`~/.codex` (rollouts/sessions), `~/.cache` (uv/dprint caches), `~/.config`. RO on
those breaks a real run — at launch I made them RW (decision Q1). The design's
"creds RO" conflates two things: the **credential file** (a token, legitimately RO)
and the **tool state dir** (must be RW).

Blocked by task 2 because both edit `render_profile` in
`scripts/spawn-orchestrator.sh`; land the exec-mode change first, then this.

## Task

- In the launch flow / `render_profile` inputs, treat tool **state dirs**
  (`~/.claude`, `~/.codex`, `~/.cache`, `~/.config`) as `--rw` and the specific
  **credential file(s)** as `--ro`.
- **The naive split is wrong and must be done explicitly.** When the credential
  file lives *inside* an RW state dir (codex's token under `~/.codex`), the
  `(allow file-write* (subpath "~/.codex"))` **also covers the cred file** — the RW
  grant silently un-protects it. Seatbelt honors a **specific deny over a subpath
  allow**, so emit `(deny file-write* (literal <cred-file>))` **after** the state-dir
  allow, per co-located credential. Add a smoke assertion that the cred file is in
  fact **unwritable** in-jail while the state dir is writable — otherwise this task
  regresses the very thing it claims to protect.
- Update `skills/auto-pilot/references/launch-runtime.md` §3 to state the
  file-RO / state-RW distinction explicitly, replacing the blanket "creds RO".
- Note the per-worker **credential-subtractive** model (§4) as a tracked
  follow-up (out of scope here) — a worker should not inherit the orchestrator's
  full credential surface, but implementing subtraction is its own task.

## Acceptance Criteria

**Code-enforced:**
- A rendered profile grants `file-write*` to the state dirs and (where isolable)
  keeps the credential file read-only; a test asserts a write to a state dir is
  allowed and a write to the isolated credential file is denied.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- A short detached `claude -p` run under the profile can write `~/.claude` session
  state without an `Operation not permitted`, and `launch-runtime.md` §3 reads
  correctly (file-RO vs state-RW).
