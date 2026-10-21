---
title: Port write-launch and write-verify-broker — the generators
priority: high
size: 5
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
is_blocked_by: orch_py_task_5
related_files:
  - scripts/spawn-orchestrator.sh:163 # write_launch
  - scripts/test-spawn-orchestrator.sh:586 # write-launch script + plist
  - scripts/test-spawn-orchestrator.sh:1320 # generated script classifies its own exit
tags: [orchestrator, python, port, launchd]
---

← [[orch_py_plan]]

## Context

`write_launch` (327 lines) generates two artifacts: the launch **shell script** and the
launchd **plist**. `write_verify_broker` (126 lines) generates the out-of-jail verify broker.
These are code generators written in bash via a long `printf '%q'` chain — the single most
error-prone construct in the file, and precisely what a template in Python makes legible.

**This task must not widen the wake loop's runtime surface, so read this carefully.** The script it
generates embeds `$self` — the absolute path to `spawn-orchestrator.sh` — and calls back into
it on every supervisor wake:

```
supervisor-scan → heartbeat → supervisor-gate → sandbox-exec … claude → supervisor-check
```

Those callbacks run under **launchd with a pinned minimal PATH** (`write-launch` fail-closes
without `--path` for exactly this reason). Porting the *generator* is safe. Porting what the
generated script *calls* is the constrained tier, whose runtime was decided in **task 2**.

So: the generated script must keep invoking **`spawn-orchestrator.sh`** (the bash entrypoint)
for `supervisor-*` and `heartbeat`, exactly as today. The dispatcher keeps those subcommands
in bash, so the wake loop's runtime dependencies do not change at all. Do not "helpfully"
point the generated script at Python.

## Task

- Implement `write-launch` and `write-verify-broker` in `scripts/orchestrator/launch.py`,
  generating the script and plist from templates.
- Reproduce both artifacts **byte-for-byte**, including all shell quoting. The `%q`-quoted
  paths must survive: a quoting regression here is a command-injection vector, since these
  strings become an executable script.
- Keep `$self` pointing at `scripts/spawn-orchestrator.sh` for every callback. Preserve the
  `--path` fail-closed guard.
- Preserve the generated script's own exit-classification logic
  (`test-spawn-orchestrator.sh:1320`).
- Preserve the safety-critical ordering that `launch --dry-run` asserts: **smoke test runs
  BEFORE detach** (`test-spawn-orchestrator.sh:808`).
- Add both to `PORTED`; delete the two bash functions.

## Acceptance Criteria

**Code-enforced:**

- Golden corpus reproduces byte-for-byte for both the generated launch script and the plist.
- `plutil -lint` accepts the generated plist (already asserted by the harness and by
  `smoke-confinement.sh`).
- `bash scripts/test-spawn-orchestrator.sh` passes unchanged — especially the `write-launch`
  (586), dry-run ordering (808), and generated-script exit-classification (1320) sections.
- The generated script still calls `spawn-orchestrator.sh` (not Python) for `supervisor-*`
  and `heartbeat`. Assert this explicitly with a grep-based test so a future change can't
  quietly widen the launchd runtime surface.
- `just check` green.

**User-run:**

- Boot a real launchd job from the Python-generated plist (`smoke-confinement.sh` section 3
  does this) and confirm it bootstraps with a pid.
