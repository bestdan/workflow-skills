---
title: "spawn: render-profile toolchain-exec mode (subpath bin dirs, drift-proof)"
priority: urgent
size: 3
status: done # merged as PR #170 (detached run #2, 2026-07-11)
created: 2026-07-10
source_branch: main
related_files:
  - scripts/spawn-orchestrator.sh
  - scripts/orchestrator.sb.tmpl
  - scripts/test-spawn-orchestrator.sh
  - scripts/smoke-confinement.sh
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, spawn, sandbox, p0]
---

[[autopilot_hardening_plan]]

## Context

Finding **P0 #3**. Seatbelt `process-exec` is strictly allowlisted — verified at
launch that unlisted `sed`/`git`/`head` are denied. `render_profile`
(`scripts/spawn-orchestrator.sh:130-210`) emits only `(literal <resolved-bin>)`
for the handful of `--exec` args, but the real workload shells out to **dozens** of
coreutils, `git`-core helpers, `uv`→python, `node`, and `codex`→`sandbox-exec`. So
`bash scripts/check.sh` and the coders **cannot run** under an as-shipped profile.

Two compounding traps hit at launch:
- **Symlink/version drift.** `claude` → `~/.local/share/claude/versions/2.1.207`,
  `codex` → `~/.codex/packages/standalone/current/bin/codex`. A version-pinned
  literal breaks after the next `claude update`; a caller passing bin *dirs* must
  know to also add `~/.local/share/claude` + `~/.codex`, which is non-obvious.
- The template already allows **all reads** "BROAD by necessity"
  (`scripts/orchestrator.sb.tmpl:30-40`) for the same traversal reason; exec has the
  identical reality.

The fix used at launch: append `(allow process-exec (subpath …))` over the standard
bin dirs (`/bin /usr/bin /usr/sbin /usr/libexec /opt/homebrew ~/.local ~/.codex
~/.nvm`), which let the toolchain run while **write-confinement and network-egress
stayed verified-tight** (writes to `$HOME`/`/etc`/sibling `~/src` still denied).

## Task

- Add a **toolchain-exec mode** to `render_profile`: a repeatable `--exec-dir
  <dir>` (subpath) alongside the existing `--exec <file>` (literal), OR a
  `--toolchain` flag that expands to the resolved standard bin dirs. Emit
  `(allow process-exec (subpath "<dir>"))` for each, canonicalized + absolute +
  fail-closed exactly like the existing path handling (`canonicalize`, the `--rw /`
  floor analogue — reject `/` as an exec subpath).
- Resolve **version-agnostic** roots for symlinked tools: given `--claude-bin`/
  coder bins, include their `~/.local/share/claude`, `~/.codex` parents so a tool
  update doesn't break the jail. Prefer feeding the **per-run resolved** exec dirs
  from pre-flight (task 5) — mirroring how `render_network_allowlist` narrows egress
  per-run — rather than hardcoding a universal list in the template.
- Update `scripts/orchestrator.sb.tmpl` if a new token is needed (mirror
  `@@EXEC_PATHS@@`), and document in the template comment that exec breadth is the
  coarse guard while **writes + network** are the enforced walls.
- **Document the residual confidentiality cost** in `launch-runtime.md` "Sandbox
  profile": with reads *and* exec broad, confidentiality rests **entirely** on the
  egress allowlist — which includes `github.com` (an effectively unbounded exfil
  channel: push to any repo). This is true under a literal-exec list too; make it
  explicit rather than implied.
- Keep `check-profile` (compile check) working.

## Acceptance Criteria

**Code-enforced:**
- Extend `scripts/test-spawn-orchestrator.sh`: a profile rendered with
  `--exec-dir /usr/bin` compiles and, under it, `sandbox-exec … bash -c 'sed …'`
  (an unlisted-literal but in-subpath binary) is **allowed**, while a write to
  `$HOME/outside` is still **denied** and off-subpath exec (e.g. a temp binary
  outside all dirs) is denied; reject `--exec-dir /` fail-closed.
- `scripts/smoke-confinement.sh` still passes its write/egress walls (update its
  exec expectation if it hard-codes the deny-`python3` case that toolchain mode
  intentionally relaxes — keep the write/network deciders authoritative).
- **Confinement regression check for the fix itself** (the walls run #1 verified
  by hand): under a toolchain-mode profile, assert `exec` of `sed`/`git`/`env bash`
  **succeeds**, while a write to `$HOME` and an off-allowlist egress attempt are
  **still denied**. This is the only proof that widening exec didn't breach the two
  real walls; `smoke-test` (auth-only) can't show it. (Shared with task 5's
  pre-flight confinement smoke.)
- `bash scripts/check.sh` green (outside the jail).

**User-run:**
- Render a profile in toolchain mode and run `bash scripts/check.sh`'s content
  gates (`dprint check`, `claude plugin validate`, `uv run validate.py`) under it —
  they execute (they exec their own subprocesses) rather than dying on `execve`.
