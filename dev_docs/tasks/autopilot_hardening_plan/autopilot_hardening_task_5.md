---
title: "preflight: scripts/preflight.sh — one read-only pre-flight + base freshness"
priority: 2
size: 3
status: new
created: 2026-07-10
source_branch: main
related_files:
  - scripts/preflight.sh
  - scripts/probe-coders.sh
  - scripts/preflight-freshness.sh
  - scripts/claude-usage.sh
  - skills/auto-pilot/SKILL.md
is_blocked_by:
parent: autopilot_hardening
tags: [auto-pilot, preflight, p2]
---

[[autopilot_hardening_plan]]

## Context

Findings **P2 #7, #10**. At launch I ran the supply-side probes ad hoc — `gh auth
status`, `scripts/probe-coders.sh`, `scripts/preflight-freshness.sh`, `gh repo view
--json viewerPermission` (draft-vs-ready), a binary fingerprint, and a manual
`git fetch origin main:main` fast-forward because local `main` was 2 commits
stale. `SKILL.md` step 2 itself says these are "good candidates to extract into a
small pre-flight helper script," and base freshness (#10) isn't in the fail-closed
pre-flight at all even though `preflight-freshness.sh` already exists. Hand-
orchestrating ~20 steps is error-prone and is where launch friction concentrated.

## Task

Create `scripts/preflight.sh` that runs **only read-only probes** and emits a
structured go/no-go plus the resolved inputs the generators need:

- **Auth/env probes:** `gh auth status`; `probe-coders.sh` (coder availability);
  the binary **fingerprint** (absolute paths of `claude`/`git`/`gh`/`codex`/`uv`/
  `node`/`op`, and the **environment class** `local-full` vs `claude-web`);
  `gh repo view --json viewerPermission` (draft-vs-ready capability).
- **Base freshness:** run `preflight-freshness.sh` against the base branch; on a
  clean fast-forward, report it (and optionally emit the FF command — do **not**
  mutate state in a read-only probe; surface the action for the launch step).
- **Emit** (stdout, parseable): the resolved **PATH dirs** and **exec dirs** for
  task 1's `--path` and task 2's toolchain-exec, so launch feeds them straight in;
  and the **add-task destination host** (task 7) for the egress allowlist.
- **Confinement smoke** (shared with task 2's regression check): render a
  toolchain-mode profile from the resolved dirs and assert the two real walls hold —
  `exec` of `sed`/`git`/`env bash` succeeds, write to `$HOME` + off-allowlist
  egress denied. Pre-flight is the natural place to prove the jail this run will use
  actually confines, before spawning.
- **Verdict:** exit non-zero with a specific, fixable message on any hard blocker
  (logged-out coder the run needs, dead `gh`, diverged base), zero on go.
- Wire `SKILL.md` step 2 to call it, and fix the helper-script path references
  (they cite `scripts/…` as if under `skills/auto-pilot/`; the scripts are at
  repo-root `scripts/` — see task 6, keep consistent).

## Acceptance Criteria

**Code-enforced:**
- `scripts/test-preflight.sh` (new): asserts the parseable verdict line, that a
  simulated logged-out-required-coder yields non-zero with a named blocker, and
  that the emitted PATH/exec-dir lines are absolute + existent.
- `bash scripts/check.sh` green (add the new test to `check.sh`).

**User-run:**
- `bash scripts/preflight.sh --source plan --base main` prints the fingerprint,
  coder availability, base-freshness verdict, and the resolved PATH/exec dirs; a
  stale base is reported with the exact `git fetch` fix.
