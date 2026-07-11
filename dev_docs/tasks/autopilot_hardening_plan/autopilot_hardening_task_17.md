---
title: "preflight: enforce the no-interactive-prompt rule — a Keychain/TCC gate must block launch, not surprise the human at 3am"
priority: 1
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - scripts/preflight.sh
  - scripts/claude-usage.sh
  - scripts/spawn-orchestrator.sh
  - skills/auto-pilot/references/launch-runtime.md
  - skills/auto-pilot/SKILL.md
is_blocked_by: autopilot_hardening_task_5
parent: autopilot_hardening
tags: [auto-pilot, preflight, credentials, macos, p1]
---

[[autopilot_hardening_plan]]

## Context

Finding **#24** — hit in detached run #2, reported by the human as *"I get lots of
mac security access requests that I don't see till I come back."*

**Mechanism.** `scripts/claude-usage.sh` is the orchestrator's **rate-window check,
run after every task**. It resolves the OAuth token via the macOS Keychain:

```sh
security find-generic-password -s "Claude Code-credentials" -w
```

Every relaunched `claude -p` is a **new sandboxed process**, so macOS re-prompts for
Keychain authorization on each one. The prompts pile up on a locked screen with
nobody there to click them. The documented fallback (`~/.claude/.credentials.json`)
**does not exist on macOS** — the token lives only in the Keychain — so there is
nothing to fall through to.

**What it breaks.** Not the run directly (it degrades gracefully), but the **budget
check**, which is worse than it sounds: with `claude-usage.sh` unable to read the
token, the orchestrator falls back to the conservative time/dispatch proxy. The
entire "checkpoint and pause cleanly before hitting the rate cap" machinery
(`run-budget.md`) is then running on a **guess** rather than the real number it was
designed around.

**Why this is the important part.** `launch-runtime.md` §3 **already states the
rule**:

> "A tool that can only authenticate via an interactive Keychain/helper prompt is a
> **launch blocker**."

The design called it a launch blocker. **Nothing enforces it.** The pre-flight never
probes whether a credential path is interactive, so the run launched into precisely
the condition the reference forbids. This is the third instance of the same
structural pattern (with #22 and #23): **the rule is written down, nothing checks
it, and the violation is silent.** A rule with no enforcement is a comment.

## Task

- **Probe interactivity, don't assume it.** In `scripts/preflight.sh` (task 5 —
  hence the block), add a **non-interactive credential probe** per credential path:
  run each auth resolution **through the sandbox wrapper, with stdin closed and no
  controlling TTY** — the exact conditions the detached job runs under. If a probe
  would prompt (Keychain / TCC / biometric / browser OAuth), it fails there — and per
  `launch-runtime.md` §3 that **BLOCKS LAUNCH**, naming the tool and the fix. This is
  the enforcement the reference already demands.
  - Concretely for this case: probe `claude-usage.sh` itself. If it cannot resolve a
    token non-interactively, the rate-window check is dead and the budget machinery
    is blind — that is a launch blocker, not a warning.
- **Offer the documented escape hatches** in the failure message, so a blocked launch
  is 30 seconds from fixed rather than a dead end:
  1. Pre-authorize the Keychain item for the binary (the "Always Allow" grant), and
     **verify the grant holds inside the sandbox**, not just outside it — an
     unverified grant is how this shipped.
  2. Or supply a file/env token path (`~/.claude/.credentials.json`) so no Keychain
     read is attempted at all.
- **Make `claude-usage.sh` fail loudly, not silently.** Today a failed Keychain read
  is swallowed (`2>/dev/null || creds_raw=""`) and the caller quietly degrades to the
  proxy. It should be **distinguishable**: "token unavailable → proxy fallback" must
  be recorded in `REPORT.md` §6 (Spend) so a human can see the run was budgeting on a
  guess, and must feed the alarm channel (task 16) rather than passing as normal.
- Record the resolved credential path + whether it is prompt-free in the run's
  environment fingerprint, so `--resume` in a different environment re-checks it.

## Acceptance Criteria

**Code-enforced:**
- A test asserts the pre-flight **blocks** when a credential can only be resolved
  interactively (simulate: a Keychain-only token with the grant absent), and
  **passes** when a file/env token or a verified grant is present.
- A test asserts the probe runs with **no TTY and stdin closed**, through the sandbox
  wrapper — a probe that passes outside the jail but prompts inside it must fail.
- A test asserts a proxy-fallback (token unavailable) is **recorded**, not swallowed.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- With the Keychain grant removed, `scripts/preflight.sh` refuses to launch and says
  exactly which credential prompts and how to fix it — instead of launching into a
  night of unanswered dialogs and a blind budget check.
