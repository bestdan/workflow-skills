# Auto-pilot launch runtime — reference

The two runtime decisions the launch phase depends on: **how the detached
orchestrator is spawned**, and **the sandbox profile it runs under**. Decisions
and rationale only — the ordered launch steps live in
[`../SKILL.md`](../SKILL.md) "Launch phase," and the durable state formats in
[`run-state.md`](run-state.md).

## Spawn mechanics — detached `claude -p`

**Decision.** The launch phase writes a self-contained **launch script** (env +
sandbox wrapper + log redirection) and starts it **detached** — a background
`claude -p` process fully off the controlling TTY, not an in-session Agent/Task.
The detach primitive is **OS-specific**: on **macOS** use `launchd`/`launchctl`
(or a small daemonizing wrapper) — `setsid` is not shipped and `nohup &` detaches
from the terminal but does **not** create a new session; on **Linux** `setsid`
is the primitive. The launch step picks the right one for the host.

**Rejected alternative.** A backgrounded Agent/Task spawned from the interactive
launch session.

**Relaunchable, not one-shot.** The run-budget pause/wake model
([`run-budget.md`](run-budget.md) "Near-cap → pause + relaunch past reset")
depends on a paused orchestrator being able to exit and then be **woken by
something else** past its `paused_until` reset — a fire-and-forget spawn (a bare
`setsid`, or the deprecated `launchctl submit`) only ever runs once. So the detach
primitive must itself be relaunchable:

- **macOS** — a `launchd` job that wakes on a schedule: a `StartCalendarInterval`
  / `StartInterval` timer, or `KeepAlive` gated on a **`PathState` sentinel file**.
  `launchd`'s `KeepAlive` keys test file existence and process state, **not** a
  timestamp — so the relaunch is gated by the orchestrator dropping/removing a
  sentinel file, never by comparing `paused_until` directly. Load it with
  `launchctl bootstrap` (the modern replacement for the deprecated
  `launchctl submit`).
- **Linux** — the `setsid` spawn needs a companion wake: a `systemd` timer, or a
  single `at` job scheduled for `paused_until`.

A **recurring** supervisor (the `launchd`/`systemd` timer) must be **torn down
whenever the run loop terminates** (`launchctl bootout` / disable the timer) so
no run is re-woken by a timer — this fires on **both** loop-termination branches:
the clean end-of-run (`status: done`, no ready tasks or a budget hard-stop) **and**
the pre-dispatch deadline-guard stop (`status: paused` with ready tasks left).
The `paused` deadline-stop still tears the supervisor down because it must not be
timer-woken past its `--until`; it resumes only by an explicit `--resume` (which
re-spawns a supervisor), never by the recurring timer. The run loop's termination
step does this ([`../SKILL.md`](../SKILL.md) "Run phase (unattended)", "Loop
termination"). A
**one-shot** `at` job fired once at `paused_until` is self-terminating and needs no
teardown. If a relaunchable supervisor genuinely isn't available in an environment,
the documented fallback is a bounded in-process sleep guarded by `--until` — stated
here as the **fallback**, not the default.

This same supervisor is also where the **rate-limit backstop** lives: with no model
call it classifies the orchestrator's exit code / stderr for a rate-limit signal
and records a supervisor-written pause marker before rescheduling — because a
rate-limited agent can't run its own pause bookkeeping (see
[`run-budget.md`](run-budget.md) "Rate-window check" and "Two pause kinds").

**Why.**

- **It must outlive the launch session.** The human runs launch interactively,
  then closes the laptop and goes to bed. An in-session Agent is a child of the
  Claude Code harness process and dies when the session ends; only an
  OS-detached process survives. This requirement alone decides it.
- **The one advantage of an in-session agent is unused.** A background Agent
  gives a live re-attach handle (`SendMessage`/`TaskGet`), but `--resume`
  reconciles from the committed **run-state branch** + git + tracker (see
  [`run-state.md`](run-state.md) "Write order" and "Crash reconciliation") —
  never from a live process handle. So the coupling to the session buys nothing.
- **The sandbox is applied by wrapping the process at spawn.** The launch script
  composes `sandbox-exec <profile> claude -p --permission-mode bypassPermissions
  …` explicitly and auditably. An in-session subagent can only inherit the
  session's permission/sandbox posture — it can't be given a stricter OS jail
  than the interactive session it lives in, and `bypassPermissions` is far safer
  inside a jailed detached process than inside an interactive session.

**Mechanics + mitigations** (carried into the SKILL launch/run steps):

| Concern                      | Handling                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Logs / observability**     | `claude -p --output-format stream-json` redirected to `.auto-pilot/orchestrator.log`. The real report channel stays the committed `RUN.md` / `QUESTIONS.md` / `REPORT.md`; the log is forensic, not primary. Live status (run-level status, per-task phases, the last log event, PID liveness, `--until`) is read in one shot by `spawn-orchestrator.sh status --label <label> [--dir <run-dir>]` — read-only, never mutates. Completion is a **single mechanism**: `spawn-orchestrator.sh teardown --label <label> --done-sentinel <dir>/.auto-pilot/orchestrator.done` writes that sentinel file first, THEN boots the launchd job out, so a watcher never observes "job gone, no done-marker." `status` reports the run done purely from that same sentinel's presence — a `KeepAlive`/`PathState` supervisor variant (above) would gate on this identical file, never a second, independently-written one. A project **`Stop` hook is not compatible with the detached, jailed run** (it can misfire — e.g. `stop-hook-error` — every turn under a non-interactive, sandboxed `claude -p`, cluttering the forensic log). The intended mitigation: launch with a `--settings` JSON that carries an explicit, empty `"hooks"` block, so the ephemeral run settings the detached process starts from define no project `Stop` hook to fire. This is documented intent, not a verified mechanism — **Claude Code's settings merge semantics for `--settings` vs. project-level hook config are not confirmed here**; if project hooks are additively merged rather than replaced, an empty `"hooks"` block in `--settings` may not suppress them, and the honest fallback is that the `stop-hook-error` line is a known-benign log artifact of this run mode, not a real failure signal. |
| **`--until <time>` bound**   | Defense in depth: a hard outer `timeout`/watchdog kill computed from `--until` at launch, **plus** the prompt-level "stop yourself at T" instruction the run loop honors.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Non-interactive auth**     | The detached process reuses the launching user's stored credentials (`~/.claude`). A launch-time smoke test (`claude -p 'ok' --max-turns 1`) verifies **before** detaching — but it must run **through the exact sandbox wrapper, env, and mounts** the orchestrator will use, or it can pass outside the jail while `~/.claude`/DNS/network fail inside it. A dead credential then fails loudly at launch, not silently at 3am (pre-flight step 2).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Orphan / stale detection** | The launch step records the orchestrator **PID + process start-time + `--until` deadline** on the run-state branch, so `--resume` or a fresh launch can detect and kill a stale orchestrator before starting a new one. The **start-time** (e.g. `ps -p <pid> -o lstart=`) is recorded with the PID so a **recycled PID** — the OS reassigning a dead orchestrator's number to an unrelated process — can't be mistaken for a live run.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Laptop sleep**             | Detaching survives session exit, **not** machine sleep — and `caffeinate -is` does **not** by itself keep a MacBook awake after **lid close**: clamshell sleep still wins unless clamshell conditions are met (external power + display/keyboard) or the lid stays open. So pre-flight treats "cannot guarantee the machine stays awake for an unattended run" as a **launch blocker** (or the user keeps the lid open / uses a tested clamshell setup), rather than a soft mitigation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

## Sandbox profile — sandboxed yolo

The orchestrator runs `bypassPermissions` **inside** a sandbox: no per-action
prompts, but the jail — not a human — is what bounds it. Enforcement is **two
layers**, because no single one covers everything:

- **Filesystem + process scope** → the OS sandbox (`sandbox-exec` seatbelt
  profile on macOS / bwrap on Linux). This is what confines writes and gates
  `exec`. Seatbelt does **not** filter network by hostname (it only gates
  network by class/socket), so it is **not** the layer that enforces the egress
  allowlist below.
- **Host-level network egress** → the **Claude Code harness sandbox's own
  network allowlist** (the same mechanism `sandbox.network` configures), or a
  local filtering proxy / PF ruleset where finer control is needed. This is the
  layer that enforces §2's host allowlist.

Four dimensions across those two layers.

### 1. Filesystem

| Scope                                                                                 | Access                 |
| ------------------------------------------------------------------------------------- | ---------------------- |
| The run worktree (`.claude/worktrees/<run>/`) and its run-state branch checkout       | **read-write**         |
| Worker worktrees the orchestrator creates for `/deliver-task` workers                 | **read-write**         |
| `$TMPDIR` / the scratchpad dir                                                        | **read-write**         |
| The rest of the repo and other worktrees                                              | **read-only**          |
| Credential stores (`~/.claude`, `~/.config/gh`, `~/.config/op`, coder-CLI cred files) | **read-only** (see §3) |
| Everything else (system, other home dirs)                                             | **no access**          |

Writes are worktree-confined by construction: bookkeeping lands on the run-state
branch, task code on task branches, worker edits in their own worktrees — the
seams [`run-state.md`](run-state.md) already relies on.

**Residual confidentiality cost.** Reads are broad (§ above) and exec is now
allowed over whole toolchain bin dirs, not just a literal per-binary list — so
neither reads nor exec meaningfully bound what a running process can _see_.
Confidentiality therefore rests entirely on §2's egress allowlist, and that
allowlist includes `github.com`, which is an effectively unbounded
exfiltration channel (a push to any repo the launching user's token can reach).
This isn't new with toolchain exec — a literal `--exec` list has the identical
exposure once any allowed binary can read broadly and reach `github.com` —
toolchain mode just makes the coarseness of the exec wall explicit rather than
incidental.

**Integrity: no out-of-jail launch.** Broadening exec to whole bin dirs does put
`/bin/launchctl` and `/usr/bin/open` within reach, and a job those brokers hand
to `launchd` / LaunchServices runs OUTSIDE this sandbox (it doesn't inherit the
profile) — a straight escape that writes/network confinement can't contain. The
template closes it on two axes: it denies `mach-lookup` to the launchd and
LaunchServices control services (last-match-wins over the blanket allow), and
denies `process-exec` of `launchctl`/`open` outright. Those binaries live only
in non-writable system dirs and RW scopes are never exec-allowed, so an attacker
can't stage a replacement — the deny is complete for these vectors. So while the
exec wall is deliberately coarse for _confidentiality_, it is not a hole in
_integrity_: the jail still can't spawn an unconfined process.

### 2. Network egress allowlist

Default-deny; the launch pre-flight **narrows the allowlist to the tools this
run actually uses** (resolved coder config + the work source), so a linear+codex
run never opens devin's or agy's endpoints. The full set of allowable
purposes → hosts:

| Purpose                          | Hosts                                                                                                                                                                           |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Orchestrator model (`claude -p`) | `api.anthropic.com`                                                                                                                                                             |
| GitHub (PRs, git over HTTPS)     | `api.github.com`, `github.com`, `codeload.github.com`, `*.githubusercontent.com`                                                                                                |
| Linear API (key via `op`)        | `api.linear.app`                                                                                                                                                                |
| npm (verify/build installs)      | `registry.npmjs.org`                                                                                                                                                            |
| Coder CLI — codex                | `api.openai.com`                                                                                                                                                                |
| Coder CLI — devin                | `api.devin.ai`, `server.codeium.com`                                                                                                                                            |
| Coder CLI — agy                  | The concrete Antigravity endpoint host(s), **resolved and recorded at pre-flight** — never a `*.googleapis.com` wildcard (too broad); fail closed if the host can't be resolved |
| MCP endpoints the tasks touch    | resolved per-run at pre-flight; added explicitly, never wildcarded                                                                                                              |
| Local tooling                    | loopback `127.0.0.1` / `localhost` only                                                                                                                                         |

Anything not on the narrowed list is denied — an unexpected egress attempt is a
signal, not a silent allow.

### 3. Credential access model

Every credential must be reachable **non-interactively** from inside the jail;
anything that would prompt (a biometric `op signin`, a browser OAuth) is a
**launch blocker** (pre-flight step 2), because it can't be satisfied at 3am.

| Credential              | Non-interactive path                                                                                                                    |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1Password (`op`)        | An `OP_SERVICE_ACCOUNT_TOKEN` in the orchestrator env — **not** an interactive `op signin` session, which needs biometrics.             |
| GitHub (`gh`)           | The stored `~/.config/gh` token (or `GH_TOKEN` in env); config mounted read-only.                                                       |
| Anthropic (`claude -p`) | The launching user's `~/.claude` credentials, reused by the detached process (smoke-tested at launch).                                  |
| Coder CLIs              | Each CLI's own credential file (`~/.codex`, `~/.local/share/devin/credentials.toml`, `~/.gemini/antigravity-cli/…`), mounted read-only. |

**File-RO vs state-RW — the credential file and its state dir are different
scopes.** A long orchestrator (and its coders) must **write** their own tool
**state**: `~/.claude` (sessions/todos/statsig), `~/.codex` (rollouts/sessions),
`~/.cache` (uv/dprint), `~/.config`. Mounting those read-only breaks a real run,
so the launch flow grants the **state dirs** `--rw`. But a state dir often
**contains** the tool's own **credential file** (a token) — and a
`(subpath)` write allow on the dir silently un-protects that token too. So the
distinction is per-file, not per-tree: the **credential file** is read-only, the
**state dir** around it is read-write.

`render-profile` makes this explicit with `--cred-ro <file>`: it emits a specific
`(deny file-write* (literal <cred-file>))` **after** the state dir's write allow.
Seatbelt honors the **last matching rule**, so the deny overrides the broader
subpath allow for exactly that token while leaving the dir writable (and the token
still readable). The naive split — put the dir in `--rw` and "the creds" in
`--ro` — is **wrong**: the `--ro` read grant does nothing to a write already
allowed by the enclosing `--rw` subpath. The test harness asserts both halves: a
write to the state dir succeeds and a write to the isolated credential file is
denied.

**Not every CLI is file-only.** On macOS some tools reach credentials through the
**Keychain** or a helper process (e.g. `gh` can use the keychain; `op` uses its
own agent), which the read-only file mounts + "everything else: no access" jail
would break. So pre-flight requires, per tool, **either** env/file-token auth
that is verified working **inside the sandbox** (not just outside it), **or** an
explicit grant of the specific Keychain/helper access that tool needs — recorded
in the profile, never left implicit. A tool that can only authenticate via an
interactive Keychain/helper prompt is a launch blocker.

### 4. Worker-CLI composition

`/deliver-task` workers (codex/devin/…) are spawned **by** the orchestrator, in
their **own** worktrees, and need their own network (codex→OpenAI,
devin→Codeium). Rather than nest a fresh sandbox per worker — `sandbox-exec`
does not nest cleanly, and per-worker profiles multiply the surface — the
orchestrator runs under **one sandbox scoped to its whole process tree**: a
seatbelt profile permitting `exec` of the coder binaries (the FS/process layer),
plus a network allowlist that is the **union of the configured coders'
endpoints** (the harness network layer, narrowed at pre-flight, §2). Workers
inherit that jail via `exec`; the profile permits the coder binaries and their
allowlisted hosts, and nothing wider.

**But a worker must not inherit the orchestrator's credential surface.** §1
mounts `~/.claude` / `~/.config/gh` / `~/.config/op` read-only for the
**orchestrator**, which needs them — a worker does **not**, and a worker runs
untrusted third-party code (an npm post-install, a test suite) that could read
any credential in its jail. So the shared process-tree profile is credential-
**subtractive** for workers: a worker's exec drops every mount except **its own
coder's** credential file (a codex worker sees only its OpenAI credential, never
`gh`/`op`/`~/.claude`) and that coder's network hosts. Least privilege per §3
holds down into the workers, not just at the orchestrator.

> **Status — tracked follow-up, not yet implemented.** The file-RO/state-RW split
> in §3 (`--cred-ro`) protects the _orchestrator's own_ tokens, but the per-worker
> credential **subtraction** described here is a separate, larger change (a worker's
> `exec` needs a distinct, narrower profile than the orchestrator's) and is **out of
> scope** for the §3 write-scopes work. Until it lands, a worker inherits the
> orchestrator's credential surface — the residual exposure to keep on the backlog.

**Codex's own sandbox is off inside the jail.** `codex --sandbox workspace-write`
nests a second seatbelt inside the orchestrator's jail — and nested `sandbox-exec`
fails to apply (`sandbox_apply: Operation not permitted`), so a coder dispatched
with its own sandbox on can't even start. Inside the jail, invoke codex with its
**own sandbox disabled**: the outer jail already confines it and is strictly
stronger (write-confinement + egress-allowlist + exec-wall), so codex's inner
sandbox is redundant, not additive. This is a one-line policy so it isn't
re-derived at 3am — set it wherever coders are invoked (`SKILL.md` verify/route
step, `commands/deliver-task.md`).

### 5. Verify broker — running the run's verify OUTSIDE the jail

The run's declared verify (`bash scripts/check.sh`, which runs `bash
scripts/test-*.sh`) **cannot pass inside the jail**: those harnesses execve
`#!/usr/bin/env bash` scripts-under-test, which the jail denies (`bad interpreter:
Operation not permitted`, exit 126) **regardless of the diff** — they fail
identically on pristine `main`. And a `sandbox-exec`-confined process's **children
inherit the profile**, so the jailed orchestrator **cannot** simply spawn an
un-jailed verifier. So verify runs in a **separate, un-jailed launchd job** — the
**verify broker** (`scripts/spawn-orchestrator.sh write-verify-broker` installs it;
`verify-request` / `verify-broker` / `verify-await` are the handshake):

1. The jailed orchestrator drops a **request sentinel** (`verify-request`) into a
   sentinel dir in the run worktree — the **worktree path + a hash** of the pinned
   verify command, **never the command itself**.
2. The **un-jailed broker job** (a launchd job that runs `/bin/bash <script>`
   directly — **no `sandbox-exec`**, so it reaches a working execve) polls the
   sentinel dir, and for each request runs its **own pinned command** in the
   requested worktree, writing a **result** file (`verify-broker`).
3. The orchestrator reads the result (`verify-await`) and treats _that_ as the
   authoritative gate; the in-jail content gates (`dprint`, `validate.py`, `bash
   -n`) become a fast pre-check, not the definition of done.

**Trust boundary (stated, not hidden).** Verify runs **un-jailed**, so it executes
the diff-under-test with **full privilege + network** — the _same_ trust the human
already extends re-running `check.sh` before merge, moved earlier in time. Two
mitigations bound it: **(a) command pinning** — the broker runs a **fixed** command
string baked into its launch script at install (from the run's resolved
`verify_command`); a request carries only a hash both sides must agree on, and the
broker **never** runs a command supplied by a request or an agent; **(b)
worktree-only execution** — the broker refuses any worktree that doesn't
canonicalize **under the run root**. A stronger **third option** (wrap verify in
its own fresh single-layer profile — repo RO, worktree+tmp RW, no network) is a
tracked follow-up, but the two `test-*.sh` harnesses that themselves call
`sandbox-exec` still won't nest under it, so v1 ships the **un-jailed** broker.

**What the broker does _not_ defend: result authority.** The sentinel dir lives in
the run worktree, which the jailed orchestrator can write — so it could forge its
own `<id>.result` (`code: 0`) and skip the broker entirely. This is **deliberately
out of scope**: the boundary the broker enforces is that an un-jailed process only
ever runs the **pinned** command in a **contained** worktree — it stops the
orchestrator from _laundering arbitrary code into an un-jailed exec_ (the actual
jailbreak). It does **not** try to make the pass/fail verdict unforgeable, because
the orchestrator is `claude -p --permission-mode bypassPermissions` — the trusted
loop driver, gating on the result itself. An orchestrator that forges a result is
equivalent to one that lies about its own gate or skips verify, which no in-jail
mechanism can prevent. Defending _that_ (a signed result the orchestrator can't
mint, written where it can't reach) is a separate, larger change tied to the
per-worker least-privilege follow-up in §4.
