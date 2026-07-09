# Auto-pilot launch runtime — reference

The two runtime decisions the launch phase depends on: **how the detached
orchestrator is spawned**, and **the sandbox profile it runs under**. Decisions
and rationale only — the ordered launch steps live in
[`../SKILL.md`](../SKILL.md) "Launch phase," and the durable state formats in
[`run-state.md`](run-state.md).

Design source:
[`auto-pilot.md`](../../../dev_docs/auto-pilot.md)
§"Decisions (locked)" (sandboxed yolo). That decision was left open in the
design doc and is resolved here.

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

A **recurring** supervisor (the `launchd`/`systemd` timer) must be **torn down at
clean end-of-run** (`launchctl bootout` / disable the timer) so a finished run is
never re-woken — the run loop's termination step does this
([`../SKILL.md`](../SKILL.md) "Run phase (unattended)", "Loop termination"). A
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

| Concern                      | Handling                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Logs / observability**     | `claude -p --output-format stream-json` redirected to `.auto-pilot/orchestrator.log`. The real report channel stays the committed `RUN.md` / `QUESTIONS.md` / `REPORT.md`; the log is forensic, not primary.                                                                                                                                                                                                                                                                            |
| **`--until <time>` bound**   | Defense in depth: a hard outer `timeout`/watchdog kill computed from `--until` at launch, **plus** the prompt-level "stop yourself at T" instruction the run loop honors.                                                                                                                                                                                                                                                                                                               |
| **Non-interactive auth**     | The detached process reuses the launching user's stored credentials (`~/.claude`). A launch-time smoke test (`claude -p 'ok' --max-turns 1`) verifies **before** detaching — but it must run **through the exact sandbox wrapper, env, and mounts** the orchestrator will use, or it can pass outside the jail while `~/.claude`/DNS/network fail inside it. A dead credential then fails loudly at launch, not silently at 3am (pre-flight step 2).                                    |
| **Orphan / stale detection** | The launch step records the orchestrator **PID + process start-time + `--until` deadline** on the run-state branch, so `--resume` or a fresh launch can detect and kill a stale orchestrator before starting a new one. The **start-time** (e.g. `ps -p <pid> -o lstart=`) is recorded with the PID so a **recycled PID** — the OS reassigning a dead orchestrator's number to an unrelated process — can't be mistaken for a live run.                                                 |
| **Laptop sleep**             | Detaching survives session exit, **not** machine sleep — and `caffeinate -is` does **not** by itself keep a MacBook awake after **lid close**: clamshell sleep still wins unless clamshell conditions are met (external power + display/keyboard) or the lid stays open. So pre-flight treats "cannot guarantee the machine stays awake for an unattended run" as a **launch blocker** (or the user keeps the lid open / uses a tested clamshell setup), rather than a soft mitigation. |

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

Credential files are mounted **read-only**: the orchestrator reads tokens, never
rewrites them.

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
