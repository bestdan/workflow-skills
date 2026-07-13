---
title: Port render-settings — the layer-2 egress allowlist
priority: medium
size: 3
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_2
related_files:
  - scripts/spawn-orchestrator.sh:325 # render-settings docs
  - scripts/test-spawn-orchestrator.sh:306 # egress allowlist narrowing
tags: [orchestrator, python, port, security]
---

← [[orch_py_plan]]

## Context

`render-settings` emits the ephemeral JSON handed to `claude -p --settings`: layer 2 of the
jail, the network egress allowlist. It is smaller than `render-profile` and is the clearest
win of the whole port — it builds JSON **by string-concatenating in bash**, which is exactly
the class of code that JSON-native Python removes outright. The orchestrator shells out to
`jq` 16 times across the file largely because of work like this.

Covered by `scripts/test-spawn-orchestrator.sh:306` (the task-2 egress-narrowing section) and
by `smoke-confinement.sh`'s egress checks (a)–(d), which prove an allowlisted host is
reachable and that a non-allowlisted host, a raw IP, and a raw `/dev/tcp` socket are all
blocked from inside the jail.

## Task

- Implement `render-settings` in `scripts/orchestrator/settings.py`, using `json` rather than
  string assembly.
- Reproduce the current output byte-for-byte — **including key order and whitespace**. If the
  bash emits compact JSON, emit compact JSON; if it pretty-prints, match the indentation.
  (`json.dumps(..., sort_keys=..., separators=...)` — pin whichever reproduces the goldens.)
- Add to the `PORTED` list; delete the bash `render_settings`.

## Acceptance Criteria

**Code-enforced:**

- Every `render-settings` case in `test/golden/` reproduces byte-for-byte.
- `bash scripts/test-spawn-orchestrator.sh` passes unchanged.
- `bash scripts/smoke-confinement.sh` passes on macOS — in particular egress checks (a)–(d),
  which are the only proof that the emitted allowlist actually constrains the network.
- `just check` green.
