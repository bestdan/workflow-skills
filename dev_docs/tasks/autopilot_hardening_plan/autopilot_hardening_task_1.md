---
title: "spawn: write-launch emits --verbose + injects PATH (launch works end-to-end)"
priority: urgent
size: 3
status: done # merged as PR #169 (detached run #2, 2026-07-11)
created: 2026-07-10
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/test-spawn-orchestrator.sh
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, spawn, p0]
---

[[autopilot_hardening_plan]]

## Context

Findings **P0 #1, #2, #8** in
[`dev_docs/autopilot-detached-run-1-findings.md`](../../autopilot-detached-run-1-findings.md).
Two fatal bugs in `scripts/spawn-orchestrator.sh` `write_launch()` (and the launch
script it emits, `spawn-orchestrator.sh:365-435`) made the first run dead-on-arrival;
both had to be hand-patched at launch:

1. **`--verbose` missing.** The emitted command is `claude -p … --output-format
   stream-json` with no `--verbose`. `claude` refuses that combination: *"When
   using --print, --output-format=stream-json requires --verbose."* The detached
   process exits on the first line. Confirmed by running the exact invocation.
2. **No `PATH`.** A `launchd` job gets `PATH=/usr/bin:/bin:/usr/sbin:/sbin`. The
   toolchain lives elsewhere — `gh` (`/opt/homebrew/bin`), `claude`/`codex`/`uv`
   (`~/.local/bin`), `node` (`~/.nvm/...`) — so every `gh`/`codex`/`uv` call fails
   command-not-found. The emitted script only `export`s `AUTO_PILOT_UNTIL`.

The `smoke-test` subcommand (`spawn-orchestrator.sh:464-487`) did **not** catch bug
1 because it runs `claude -p 'ok'` *without* `--output-format`, so it validates a
shape the real launch never uses. Because these live *inside* `write_launch`, the
atomic `launch` subcommand (`:516-563`) is unusable — you must hand-edit the script
between write-launch and detach — which is exactly what happened.

## Task

In `scripts/spawn-orchestrator.sh`:

- **`write_launch`:** add `--verbose` to the emitted `claude -p …` command (before
  `--output-format stream-json`).
- **`write_launch`:** accept a new `--path <PATH>` argument and `export PATH=<PATH>`
  in the generated launch script (after the `AUTO_PILOT_UNTIL` export). Fail-closed
  if `--path` is missing (or default it to the dirs of the resolved `--claude-bin`
  + a documented base set — but prefer requiring it so the caller passes the
  fingerprint-resolved dirs). Keep `%q`-quoting.
- **`smoke_test`:** run the **exact** flag set the launch script uses — add
  `--verbose --output-format stream-json` (and assert it still produces parseable
  output) so it can never again green-light an invocation the real run rejects.
- **`launch`:** thread `--path` through to `write_launch` so the atomic path works
  end-to-end without hand-editing (this closes P0 #8).

## Acceptance Criteria

**Code-enforced:**
- Extend `scripts/test-spawn-orchestrator.sh`: assert the generated launch script
  contains `--verbose` and an `export PATH=` line carrying a passed `--path` value;
  assert `write-launch` fails closed when `--path` is omitted (if made required).
- Add a `have` assertion that `smoke_test`'s invocation string includes
  `--verbose` and `--output-format stream-json`.
- `bash scripts/test-spawn-orchestrator.sh` passes; `bash scripts/check.sh` green
  (run **outside** any jail).

**User-run:**
- `spawn-orchestrator.sh launch --dry-run …` shows the 4-step plan; a real
  `launch` against a scratch prompt produces a detached job whose log shows
  stream-json events (not the `requires --verbose` error), and whose bash tool
  calls resolve `gh`/`git`/`uv` (no command-not-found).
