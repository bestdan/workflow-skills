---
title: "jail: permit the harness's own $TMPDIR mux socket + cwd files — stop poisoning every Bash exit code"
priority: 1
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - scripts/orchestrator.sb.tmpl
  - scripts/spawn-orchestrator.sh
  - scripts/smoke-confinement.sh
  - skills/auto-pilot/references/launch-runtime.md
is_blocked_by: autopilot_hardening_task_3
parent: autopilot_hardening
tags: [auto-pilot, sandbox, verify, p1]
---

[[autopilot_hardening_plan]]

## Context

Finding **#20** — surfaced by detached run **#2**. Two coupled symptoms, one root
cause: the jail denies Claude Code's **own internal runtime files** under `$TMPDIR`.

1. **The inner sandbox cannot initialize.** The orchestrator logs
   `Sandbox is enabled but failed to initialize: EPERM … listen '$TMPDIR/srt-mux-*.sock'`
   and then **disables its own sandboxing for the session**. The outer
   `sandbox-exec` jail still holds (writes to `$HOME`, `/etc`, sibling repos remain
   denied — verified), so **confinement is not lost**. But the two-layer design
   silently degrades to one layer, which is not what `launch-runtime.md` claims.
2. **Worse — it poisons verification.** The denied **cwd-tracking write**
   (`/tmp/claude-*-cwd`) makes **every Bash tool call return exit 1** regardless of
   the command's real result. An agent that verifies by exit code is now reading
   pure noise: a passing command looks failed, and the orchestrator cannot
   distinguish a real failure from the jail lying to it. This is a **correctness**
   bug in the verify path, not just noise — it is why run #2's orchestrator had to
   fall back to parsing command *output* instead of trusting `$?`.

This is **related to but distinct from finding #4** (`execve` of repo
`#!/usr/bin/env bash` scripts denied → `check.sh` harnesses die at exit 126). #4 is
about executing the *repo's* scripts; this is about the *harness's own* socket and
cwd files. Fixing #4 (task 4) does **not** fix this, and vice versa. Both must land
for verify to be trustworthy in-jail.

Blocked by task 3, which owns the profile's write-scope model (`--rw` state dirs
and the co-located-credential deny) — this extends that same scope set.

## Task

- Extend the rendered profile's write scopes to permit Claude Code's own runtime
  files: the `srt-mux-*.sock` **socket bind/listen** under `$TMPDIR`, and the
  `claude-*-cwd` cwd-tracking files. Prefer permitting the **specific** paths/
  patterns the harness needs over widening all of `$TMPDIR`; a `(allow network-bind
  (local ...))` / unix-socket grant may be needed alongside the file grant, since a
  `listen()` on a unix socket is not purely a file write.
- Re-verify that the **inner** sandbox now initializes (no `failed to initialize`
  line in the log) so the two-layer posture the design claims is actually the one
  that runs.
- **Add the exit-code integrity check to the confinement smoke**
  (`smoke-confinement.sh`): run a command that must succeed (`true`) and one that
  must fail (`false`) **in-jail** and assert the observed exit codes are `0` and
  `1` respectively. A jail that cannot report a correct exit code is a **broken
  jail**, and nothing currently catches that — this assertion is the real
  regression guard for this task.
- Update `launch-runtime.md` to state that the harness's own `$TMPDIR` runtime
  files are part of the required write surface, and why (an under-permissioned jail
  degrades the inner layer *and* silently corrupts exit-code-based verification).

## Acceptance Criteria

**Code-enforced:**
- `smoke-confinement.sh` gains the exit-code integrity assertion (`true` → 0,
  `false` → 1 in-jail) and passes; it **fails** against the pre-fix profile.
- The containment assertions still hold: `$HOME`/`/etc`/sibling-repo writes and
  off-allowlist egress remain **denied** (this task must widen only the harness's
  own runtime paths, not the jail).
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- A short detached `claude -p` run under the new profile shows **no**
  `Sandbox is enabled but failed to initialize` line, and a Bash tool call running
  `false` reports a **non-zero** exit while `true` reports **zero** — i.e. exit
  codes are trustworthy again.
