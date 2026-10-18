---
title: reset_epoch validation + resume grace for clock skew (hardening #3 + #4)
priority: medium
size: 2
status: done
created: 2026-07-15
source_branch: worktree-bestdan+cao-usage-adapter
related_files:
  - scripts/claude-usage.sh
  - scripts/spawn-orchestrator.sh
  - skills/auto-pilot/references/run-budget.md
is_blocked_by:
parent: cao_usage
tags: [auto-pilot, run-budget, hardening]
---

Part of [[cao_usage_plan]].

## Context

The pause writer writes `paused_until` from the usage endpoint's validated `resets_at` (or an error's `retry-after`), and `supervisor-gate` resumes once `now >= paused_until`. Codex #3 + #4: the reset time is external, untrusted input and the clock may skew — so validate it before persisting, and add a small grace so an early wake (harmless, thanks to the gate) never resumes a hair before the real reset. A failed session-status read is not a successful synthetic reading: if it still warrants a pause, the pause path supplies its existing one-hour fallback.

`claude-usage.sh --session-status` emits `"<percent> <reset_epoch>"`; the pause writer converts `reset_epoch` → `paused_until`.

## Task

- **Validate `reset_epoch`** before it becomes `paused_until` (in `claude-usage.sh` output validation and/or the pause writer): must be a plausible **future** time (`> now`, `< now + cap` — cap ~6h, comfortably past a 5h window); reject a value that moves **backward** relative to the last `reset_epoch` observed **for the same rate window** (i.e. before the current window's known reset — a legitimate reset time only advances or stays equal until the window rolls over, at which point a new, later `reset_epoch` is expected). `claude-usage.sh` fails closed for an invalid reading; when that failure still warrants a pause, the pause path uses the default 1h fallback (the existing no-reset-time fallback in `run-budget.md` "Near-cap → pause") rather than trusting corrupt data. State this as fail-safe.
- **Resume grace**: set `paused_until = reset_epoch + grace` (grace ~60–180s, configurable). Document in `run-budget.md` that early wakes are safe because the shell gate makes them free, so the grace only removes boundary races.
- Write `paused_until` **atomically** with the observation timestamp/source recorded (Codex: record what produced the pause), consistent with `run-state.md` write order.

## Acceptance Criteria

**Code-enforced**
- `claude-usage.sh` (or the pause writer) rejects a `reset_epoch` in the past, absurdly far future, or moving backward; the reader exits nonzero with no status line, and the pause path owns any warranted 1h fallback — covered by a `--from-file` fixture test feeding a bad epoch.
- `run-budget.md` documents the `+grace` and why early wakes are safe.

**User-run**
- Feeding `claude-usage.sh --from-file` a response with a garbage/backwards `resets_at` fails closed rather than emitting a synthetic status; a valid one yields the raw `reset_epoch`, which the pause writer converts to `reset_epoch + grace`.
