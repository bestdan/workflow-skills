---
title: Auto-pilot launch feedback — the shipped v1 launch path fails on a real macOS/local-full host
created: 2026-07-14
reporter: bestdan (via a real /auto-pilot launch attempt)
skill_version: 1.56.1
status: open
audience: whoever owns skills/auto-pilot launch + scripts/spawn-orchestrator.sh
---

# Auto-pilot launch feedback

An attempt to run `/auto-pilot` for real (a `plan` source, `local-full` macOS
host, `--until now+8h`) got through the entire fail-closed pre-flight **green**
and then, during the pre-detach confinement verification, hit **multiple hard,
reproducible blockers that make the shipped v1 launch non-functional on this
host**. None of them were caught by `scripts/preflight.sh` — its verdict was
`go` — and each would have surfaced as a silently-wedged run at 3am, which is
exactly the failure class the design's own findings log (`#22`, `#23`) exists to
prevent.

This doc records the blockers with evidence, root cause, and a proposed fix for
each, plus the pre-flight gaps that let a broken launch read as green.

## Environment

- **Host:** macOS (`darwin`), `local-full` (all coder CLIs on PATH).
- **Repo:** `bestdan/dotfiles`, a git repo with machine-specific global git config
  (mise/bootstrap).
- **Preflight verdict:** `go`. `gh` ADMIN, base fresh, codex ✓ / agy ✓ (devin
  absent), `SMOKE_EXEC`/`SMOKE_HOME_WRITE`/`SMOKE_EGRESS` all pass.
- **Source:** a `dev_docs/tasks/<name>_plan/` directory (plan adapter → `repo-pr`).

The pre-flight smoke (`preflight.sh` → `smoke-confinement.sh`) exercises
`sed/git/env bash` exec, a `$HOME` write-deny, and layer-2 egress rendering. It
does **not** exercise the one operation the orchestrator performs on every single
wake: **a git commit from the run worktree**. That gap is why all of the below
passed pre-flight.

---

## Blocker 1 — a linked run worktree cannot commit under the jail (repo mounted RO)

**Severity: fatal.** This wedges every wake.

The launch design places the run worktree at `.claude/worktrees/<run>/` and mounts
"the rest of the repo and other worktrees" **read-only**
(`references/launch-runtime.md` §1 filesystem table). But a git **linked**
worktree keeps its administrative files (`HEAD`, `index`, `index.lock`, `logs/`)
in the **main** repo's `.git/worktrees/<name>/`, and its objects/refs in the main
repo's `.git/`. All of that is inside the read-only mount.

### Evidence

Render the profile exactly as `dev_docs/auto-pilot-spawn-smoke.md` prescribes
(repo `--ro`, run worktree `--rw`), then attempt a commit inside the jail:

```
fatal: Unable to create '/Users/danegan/src/dotfiles/.git/worktrees/wt/index.lock':
Operation not permitted
```

The orchestrator commits `RUN.md`/`QUESTIONS.md`/`REPORT.md` on the run-state
branch after every task and every state transition. With this denied, the run
cannot record a single state update — it wedges immediately and (per the exit
contract) looks like a clean `exit 0`.

### Root cause

The "run worktree is RW, rest of repo is RO" split assumes the run worktree is
**self-contained**, but a git linked worktree is not — its writes fan out into
the main repo's `.git`.

### Proposed fix

Make the run worktree a **standalone clone** rooted inside the confinement root
(so every git write — objects, refs, worktree admin — lands in the RW jail), not
a linked worktree of the main repo. This also cleanly contains worker worktrees:
they become linked worktrees of the *run clone*, whose `.git` is already RW.

- `references/launch-runtime.md` §1 and SKILL "Step 1" should specify a clone, not
  a linked worktree, and stop describing the main repo's `.git` as part of the RO
  reference surface for the run worktree's own operations.
- Origin of the clone must be set to the **remote** (`github.com/...`), since a
  `git clone --local` points origin at the local path — task branches and PRs
  need the real remote.

Verified: a standalone clone rooted in the confine root **does** commit inside
the jail (once Blocker 2 is also fixed).

---

## Blocker 2 — a host-global git hook is exec-denied by the jail, killing all git ops

**Severity: fatal.** Independent of Blocker 1; hits even a standalone clone.

This host sets a git hook path outside the repo
(`~/.bootstrap/resources/git_config_hooks/`, a mise/bootstrap `mise-trust-worktree`
hook that git execs on worktree/checkout/commit operations). The jail's exec wall
does not include that path, so git dies:

```
fatal: cannot exec '/Users/danegan/.bootstrap/resources/git_config_hooks/mise-trust-worktree':
Operation not permitted
```

### Root cause

The sandbox exec allowlist (even `--toolchain`) covers standard bin dirs, not
arbitrary host-configured git `core.hooksPath` locations. Any developer whose
global/system git config points `core.hooksPath` at a non-bin directory (mise,
Husky-global, corporate wrappers) will hit this.

### Proposed fix

Neutralize hooks for the run's git operations rather than trying to allowlist an
unknowable hook path:

- Set `core.hooksPath` to an empty/no-op directory (or `/dev/null`) in the **run
  clone's local `.git/config`** at launch, so it applies to the clone and all its
  linked worker worktrees. Verified this unblocks in-jail commits.
- Document it as a launch step and add it to the render/launch flow, or at minimum
  have `preflight.sh` **detect** a non-standard `core.hooksPath` and either fix it
  in the clone or block with a specific message.

---

## Blocker 3 — push credential path (`gh` / osxkeychain) not provisioned in the profile

**Severity: likely fatal for push/PR** (not yet exercised end-to-end because
Blockers 1–2 stop the run before a push).

This host authenticates git push to GitHub via a per-host credential helper
`credential.https://github.com.helper = !/opt/homebrew/bin/gh auth git-credential`
(with `osxkeychain` as the global default). The spawn-smoke profile recipe grants
neither `--ro ~/.config/gh` nor `--exec` for `gh`, and `osxkeychain` needs
Keychain/Security access that the jail denies by design.

### Proposed fix

- The launch profile must mount the credential store the resolved helper needs
  (`~/.config/gh` RO) and exec-allow the helper binary (`gh`).
- Pre-flight should resolve the **effective** `credential.helper` for the remote
  host and verify a **non-interactive** credential retrieval works *inside the
  sandbox*, not just that `gh auth status` passes outside it. An osxkeychain-only
  host (no `gh` helper) should block with a specific message, since Keychain
  access is denied in the jail.

---

## Blocker 4 — no orchestrator prompt template ships; verify broker is hand-wired

**Severity: launch-blocking (assembly gap), not a correctness bug.**

`write-launch --prompt-file <f>` requires a prompt file, but no orchestrator
run-loop prompt template ships under `skills/auto-pilot/`. The launcher is left to
author the entire "Run phase (unattended)" prompt from scratch, which (a) is
error-prone and (b) means every launcher's orchestrator behaves slightly
differently — defeating "compose, never duplicate."

### Proposed fix

Ship a canonical orchestrator prompt template (parameterized by run_id / run-dir /
handler / questions path) as a skill asset, and have the launch flow (or a
`spawn-orchestrator.sh` subcommand) render it. Same for the verify-broker wiring:
the SKILL references `write-verify-broker` but the end-to-end handshake assembly is
left implicit.

---

## Meta-finding — the pre-flight passes a launch that cannot commit

The unifying problem: `preflight.sh` / `smoke-confinement.sh` verify **exec**, a
**$HOME write-deny**, and **egress rendering**, but never verify the run's
**actual load-bearing operation** — *a git commit from the run worktree, on this
host's git config, inside the jail*. Because that check is missing, four
independent fatal/near-fatal conditions all read as `PREFLIGHT VERDICT: go`.

### Proposed pre-flight addition

Add a **jailed-commit smoke** to `smoke-confinement.sh` (or a new pre-flight
step): create the throwaway run clone exactly as launch would, render the real
profile, and run `git commit` **and** a dry-run credential fetch **inside** the
jail. Make a failure a hard `no-go` with the specific cause. This single check
would have caught Blockers 1, 2, and 3.

---

## Repro (condensed)

```bash
SO=scripts/spawn-orchestrator.sh
D="$(mktemp -d)"; RUNROOT="$D/run"; WT="$RUNROOT/wt"; mkdir -p "$RUNROOT"

# Linked worktree (as documented) — commit is DENIED:
git worktree add -q --detach "$WT" HEAD
"$SO" render-profile --confine-under "$RUNROOT" --rw "$WT" --tmpdir "$RUNROOT/tmp" \
  --ro "$(git rev-parse --show-toplevel)" --ro "$HOME/.claude" \
  --exec "$(command -v git)" --exec "$(command -v bash)" --toolchain --out "$RUNROOT/profile.sb"
mkdir -p "$RUNROOT/tmp"
sandbox-exec -f "$RUNROOT/profile.sb" bash -c \
  "cd '$WT' && echo x>t && git add t && git commit -m t"
#  -> fatal: Unable to create '<repo>/.git/worktrees/wt/index.lock': Operation not permitted

# Standalone clone — commit still DENIED by the host git hook (Blocker 2):
#  -> fatal: cannot exec '~/.bootstrap/.../mise-trust-worktree': Operation not permitted
# Only after `git -c core.hooksPath=/dev/null` (or setting it in the clone) does the commit succeed.
```

## Suggested triage order

1. **Blocker 2** (git-hooks exec) + the **jailed-commit smoke** — cheap, and the
   smoke gates everything else.
2. **Blocker 1** (clone vs linked worktree) — the topology fix; largest change.
3. **Blocker 3** (credential provisioning + in-jail credential smoke).
4. **Blocker 4** (ship prompt/broker assets).
