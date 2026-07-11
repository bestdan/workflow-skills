---
title: "preflight: enforce the no-interactive-prompt rule — detaching via launchd re-triggers macOS consent gates nobody can answer"
priority: urgent
size: 3
status: new
created: 2026-07-11
source_branch: main
related_files:
  - scripts/preflight.sh
  - scripts/spawn-orchestrator.sh
  - scripts/claude-usage.sh
  - skills/auto-pilot/references/launch-runtime.md
  - skills/auto-pilot/SKILL.md
is_blocked_by: autopilot_hardening_task_5
parent: autopilot_hardening
tags: [auto-pilot, preflight, macos, tcc, credentials, p1]
---

[[autopilot_hardening_plan]]

## Context

Finding **#24** — reported by the human during detached run #2: *"I get lots of mac
security access requests that I don't see till I come back."*

**Mechanism — the act of detaching is what creates the prompts.** macOS TCC grants
are attributed to a **responsible process**. When Claude runs in a terminal, the
folder-access grants belong to **Terminal/iTerm**, which the user authorized long
ago — so no prompt ever appears. The auto-pilot orchestrator is spawned by
**`launchd`**, which makes the orchestrator binary *itself* the responsible process:
a **different TCC identity, holding none of the user's existing grants**. Every
protected resource it touches therefore raises a **fresh consent dialog** — on a
locked screen, addressed to nobody, at 3am.

This is the sharpest form of the problem, because the property that makes auto-pilot
valuable (it detaches and outlives your session) is the *same* property that
invalidates the permissions you already granted. **Nothing about the run being
correct prevents this.**

**Compounding — the dialog doesn't even say "Claude."** `claude` is a symlink to
`~/.local/share/claude/versions/<version>` — a bare Mach-O executable, not an `.app`
bundle. It is properly signed (`com.anthropic.claude-code`, Team `Q6L2SF6YDW`), but
with no bundle there is no display name, so macOS falls back to the **filename**. The
user sees a dialog from something called **"2.1.207"** asking for folder access. That
is indistinguishable from malware, and the most likely human response — *deny, or
ignore it* — is the one that breaks the run.

**Why this is the important part.** `launch-runtime.md` §3 **already states the
rule**:

> "A tool that can only authenticate via an interactive Keychain/helper prompt is a
> **launch blocker**."

The rule is written down. **Nothing enforces it.** The pre-flight never probes
whether the *detached, launchd-attributed* process can run prompt-free, so run #2
launched straight into the condition the reference forbids. Note the rule is also
**too narrow**: it says *Keychain/helper*, but the live failure is **TCC folder
consent** — so the fix must generalize to *any* interactive consent gate, not just
credentials.

This is the third instance of one structural pattern (with #22 and #23): **the rule
is written down, nothing checks it, and the violation is silent. A rule with no
enforcement is a comment.**

## Task

- **Probe under the real attribution, not the terminal's.** In `scripts/preflight.sh`
  (task 5 — hence the block), add a **consent-gate probe** that runs the orchestrator's
  actual entry path **as `launchd` will run it** — detached, no controlling TTY, stdin
  closed, through the sandbox wrapper — and exercises the resources the run will touch.
  A probe that passes from an interactive terminal proves **nothing**: the terminal's
  grants are exactly what the detached job will not have. This is the whole point.
- **Any consent gate that would prompt BLOCKS LAUNCH**, per `launch-runtime.md` §3,
  naming the resource and the fix. Generalize the reference's rule from
  "Keychain/helper" to **any interactive consent gate**: TCC folder access
  (Documents/Desktop/Downloads/removable/network volumes), Full Disk Access, Keychain,
  biometric, browser OAuth.
- **Tell the human how to pre-grant, precisely** — a blocked launch must be ~1 minute
  from fixed, not a dead end. The message should name the **real binary path**
  (`~/.local/share/claude/versions/<ver>`, resolved — not the symlink) and say to add
  it under **System Settings → Privacy & Security → Full Disk Access**, and warn that
  the dialog will identify it only by its **version number**, not as "Claude".
- **Prefer not needing the grant at all.** Where the run touches a protected location
  only incidentally, the better fix is to **not touch it**: the jail currently allows
  `(allow file-read*)` globally (deliberately — a narrow read list broke traversal),
  which means nothing stops an incidental read of `~/Documents` from raising a gate.
  Record which protected locations the run actually needs; if the answer is *none*,
  the run should never trip a gate in the first place.
- Record the outcome in the environment fingerprint so `--resume` under a different
  attribution (relaunched from a terminal vs. launchd) re-checks it.

## Acceptance Criteria

**Code-enforced:**
- A test asserts the probe runs **detached, with no TTY and stdin closed, through the
  sandbox wrapper** — and that a probe passing in a terminal but gated under launchd
  attribution **fails** the pre-flight.
- A test asserts the pre-flight **blocks** on any interactive consent gate and emits a
  message naming the resolved binary path and the Full Disk Access remedy.
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- With Full Disk Access revoked for the Claude binary, `scripts/preflight.sh` refuses
  to launch and explains exactly what to grant and where — instead of launching into a
  night of unanswered dialogs from a program the user cannot even identify.
