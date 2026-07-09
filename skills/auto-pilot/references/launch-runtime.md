# Auto-pilot launch runtime — reference

The two runtime decisions the launch phase depends on: **how the detached
orchestrator is spawned**, and **the sandbox profile it runs under**. Decisions
and rationale only — the ordered launch steps live in
[`../SKILL.md`](../SKILL.md) "Launch phase," and the durable state formats in
[`run-state.md`](run-state.md).

Design source:
[`auto-pilot-mode-design.md`](../../../dev_docs/tasks/auto-pilot-mode-design.md)
§"Decisions" (sandboxed yolo) + §"Open implementation questions". Both were left
open in the design doc and are resolved here.

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
| **Logs / observability**     | `claude -p --output-format stream-json` redirected to `.auto-pilot/orchestrator.log`. The real report channel stays the committed `RUN.md` / `QUESTIONS.md` / `MORNING.md`; the log is forensic, not primary.                                                                                                                                                                                                                                                                           |
| **`--until <time>` bound**   | Defense in depth: a hard outer `timeout`/watchdog kill computed from `--until` at launch, **plus** the prompt-level "stop yourself at T" instruction the run loop honors.                                                                                                                                                                                                                                                                                                               |
| **Non-interactive auth**     | The detached process reuses the launching user's stored credentials (`~/.claude`). A launch-time smoke test (`claude -p 'ok' --max-turns 1`) verifies **before** detaching — but it must run **through the exact sandbox wrapper, env, and mounts** the orchestrator will use, or it can pass outside the jail while `~/.claude`/DNS/network fail inside it. A dead credential then fails loudly at launch, not silently at 3am (pre-flight step 2).                                    |
| **Orphan / stale detection** | The launch step records the orchestrator **PID + `--until` deadline** on the run-state branch, so `--resume` or a fresh launch can detect and kill a stale orchestrator before starting a new one.                                                                                                                                                                                                                                                                                      |
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
inherit both via `exec`; the profile permits the coder binaries and their
allowlisted hosts, and nothing wider.
