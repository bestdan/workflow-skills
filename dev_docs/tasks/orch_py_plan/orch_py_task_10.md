---
title: git cannot exec inside the jail — the CLT shim's re-exec TARGET is not on the exec allowlist
priority: high
size: 2
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
related_files:
  - scripts/spawn-orchestrator.sh:802 # --toolchain's bin-dir list (no CommandLineTools)
  - scripts/spawn-orchestrator.sh:713 # emit_exec — the process-exec allowlist
  - scripts/smoke-confinement.sh:51 # passes --exec git, but never RUNS git jailed
  - scripts/smoke-confinement.sh:81 # asserts unlisted /usr/bin/python3 is denied
tags: [orchestrator, seatbelt, jail, bug, git]
---

← [[orch_py_plan]]

## Context

> **Found while testing task 2's interpreter options against a real rendered profile.** It is
> independent of the runtime decision and was deliberately **not** fixed there (that card is
> decision-only, and its acceptance criteria forbid touching `scripts/`). It is filed here
> instead of being fixed in passing.

**`git` cannot exec inside the Seatbelt jail on a stock macOS host.** On a machine with no
Homebrew git, `/usr/bin/git` is a **Command Line Tools shim**: a 119k stub that re-execs the real
binary at `/Library/Developer/CommandLineTools/usr/bin/git`. The exec allowlist grants
`(subpath "/usr/bin")` — which permits the *shim* — but nothing grants `/Library/Developer/…`, so
the shim's re-exec is refused.

Repro (against a profile from the real renderer):

```
$ sandbox-exec -f <profile> /usr/bin/git --version
git: error: can't exec '/Library/Developer/CommandLineTools/usr/bin/git' (errno=Operation not permitted)
```

Granting the CLT dir fixes it outright:

```
$ spawn-orchestrator.sh render-profile … --toolchain --exec-dir /Library/Developer/CommandLineTools …
$ sandbox-exec -f <profile> /usr/bin/git --version
git version 2.39.5 (Apple Git-154)
```

**Nothing in the repo references `CommandLineTools`** (`rg CommandLineTools scripts/ skills/` is
empty), so no profile has ever granted it.

**Why this was never caught.** `smoke-confinement.sh:51` passes `--exec "$(command -v git)"` —
which resolves to the shim and grants it — but the suite **never runs git inside the jail**. The
grant is a no-op that has never been exercised. Every §1 exec assertion is either a *denial*
(`denied "exec unlisted /usr/bin/python3"`) or uses `bash`, which is a real binary and not a
shim, so it passes.

**Scope — verify before fixing.** The jailed agent's whole job is `git` (commit, push, PR), and
detached runs have merged real PRs (#169–#178), so either those runs had a git that was not a
CLT shim (a Homebrew git since removed), or they were degraded in a way that went unnoticed.
**Establish which before assuming the blast radius.** Any binary reached through an `xcrun` shim
has the same defect — `/usr/bin/python3` fails identically, and that one is currently *masked* by
being a deliberate denial in the smoke test.

## Task

1. **Confirm the blast radius.** Does the jailed agent run git today on this host, or has it been
   silently failing? Check a recent run's `orchestrator.log` for `Operation not permitted` /
   `can't exec`. Note that `--toolchain` is what a launch is expected to pass.
2. **Fix the grant.** Add the active developer-tools dir to `--toolchain`'s bin-dir list
   (`spawn-orchestrator.sh:802`), resolved rather than hard-coded — `xcode-select -p` gives the
   active toolchain, which may be `/Library/Developer/CommandLineTools` *or* an
   `Xcode.app/Contents/Developer` path. Skip it when absent, matching the existing
   "a missing standard dir is not fail-closed — it's just omitted" posture.
3. **Close the test gap — this is the load-bearing part.** Add a smoke assertion that git
   **actually runs** jailed (`allowed "exec git" git --version`), not merely that it is on the
   allowlist. The bug exists precisely because the grant was asserted and the execution never
   was.
4. **Check the neighbours.** Any other shimmed binary the run depends on (`sed`, `head`, and the
   git-core helpers were already flagged in detached-run finding #3) — the same defect class.

## Acceptance Criteria

**Code-enforced:**

- `sandbox-exec -f <rendered profile> git --version` succeeds under a `--toolchain` profile.
- `smoke-confinement.sh` **runs** git inside the jail and fails if it cannot — a regression guard
  that would have caught this.
- The CLT/Xcode path is **resolved** (`xcode-select -p`), never hard-coded, and its absence is
  non-fatal.
- The confinement properties still hold: writes outside the worktree still denied, `/etc/sudoers`
  still unreadable, and the deliberate `exec unlisted /usr/bin/python3` denial is **re-examined,
  not silently flipped** — granting the CLT subpath makes `/usr/bin/python3` executable, so that
  assertion's intent (an un-granted interpreter cannot run) needs restating against a binary that
  is genuinely not on the list.

**User-run:**

- A real `--dry-run` launch renders a profile whose exec block contains the active toolchain dir.

## Note

Interacts with task 2's decision: the port's pinned CPython gets its **own** exec grant (a
subpath over `~/.local/share/uv/python`) and does **not** depend on the CLT dir. Fixing this bug
is therefore not a prerequisite for the port — but it is a prerequisite for the unattended runtime
working at all, which is why it is `priority: high` and sized small.
