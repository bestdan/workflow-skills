---
type: epic
title: Decide the jail's containment model — Seatbelt cannot compose or filter hostnames
status: active
owner: Daniel Egan
created: 2026-07-13
---

# The jail's containment model

## Why this plan exists

Four separate task cards, three PRs, and one long investigation have all terminated at the
same two sentences:

> **macOS Seatbelt does not compose, and it cannot filter by hostname.**

Every attempt to fix the jail in place has generated a new card instead of closing one.
This plan exists to make the containment decision **once**, with the evidence in one place,
so the next agent does not rediscover these facts a fifth time.

It also records a correction: **auto-pilot is not blocked by any of this.** Detached run #1
completed 6/6 tasks (PRs #162–#167). The jail is a security posture, not a run blocker. The
one thing that genuinely stops a _full_ run is the verify contract (§4 below), and it
already has a designed answer.

## The two structural facts

Both measured on this host (macOS, `claude` 2.1.207), not reasoned about.

### Fact 1 — a nested Seatbelt profile cannot be applied

```console
$ sandbox-exec -f a.sb /usr/bin/sandbox-exec -f b.sb /usr/bin/true
sandbox-exec: sandbox_apply: Operation not permitted

$ sandbox-exec -f a.sb /usr/bin/sandbox-exec -f same.sb /usr/bin/true   # identical profile
$                                                                        # (succeeds)
```

Only a semantically **identical** profile is accepted. A strict subset is refused, and a
fully permissive outer profile still refuses a different inner one — so this is not a
missing grant. There is no `sandbox-apply` operation to allow.

This single fact explains, and unifies, four separately-filed problems:

| Filed as                     | Symptom                                           | Same cause?                       |
| ---------------------------- | ------------------------------------------------- | --------------------------------- |
| Detached run #6 (2026-07-10) | "nested `sandbox-exec` doesn't compose"           | the fact itself                   |
| Detached run #4 (2026-07-10) | `check.sh`'s harnesses die in-jail (`exit 126`)   | yes — they re-exec `sandbox-exec` |
| `orch_py_task_12`            | "layer 2 doesn't enforce; egress is open"         | yes — see Fact 2                  |
| Coder backends               | `codex --sandbox workspace-write` inside the jail | yes                               |

`skills/deliver-task/SKILL.md` already states the rule for coders — _"inside the jail invoke
any coder with its own sandbox disabled … a nested `sandbox-exec` can't even apply"_ — but
nobody connected it to layer 2, which is the same nesting in a different costume.

**Measured, and worth its own alarm:** a coder that cannot exec in the jail does not
necessarily fail loudly. Inside the jail, `codex exec --sandbox read-only` hit
`sandbox_apply: Operation not permitted` and then **fabricated the output it never
produced** ("Its expected output is: `NESTED_OK`"). See task 3.

### Fact 2 — Seatbelt cannot filter by hostname, but CAN pin egress to a port

The profile template asserts:

> _"Seatbelt gates network by socket/class, NOT by hostname. Allow outbound as a class …
> all hostname allowlisting lives in layer 2, not here."_

The first clause is true. **The conclusion is a leap**, and it is the leap that made the jail
porous. Seatbelt can restrict `network-outbound` to a specific `remote ip:port`:

```console
$ cat pinned.sb
(version 1) (allow default) (deny network-outbound)
(allow network-outbound (remote ip "localhost:8888"))

$ sandbox-exec -f pinned.sb curl -sS --max-time 8 -o /dev/null http://1.1.1.1
curl: (7) Failed to connect to 1.1.1.1 port 80 after 1 ms: Couldn't connect to server
$ curl -sS --max-time 8 -o /dev/null http://1.1.1.1      # control, unjailed
$ echo $?
0
```

Claude Code's own sandbox works exactly this way — its generated profile contains **no**
general `(allow network-outbound)`, only `(allow network-outbound (remote ip
"localhost:<httpProxyPort>"))` plus a SOCKS port. The blanket `(allow network-outbound)` in
`orchestrator.sb.tmpl` was never necessary.

## What this means for layer 2 (`orch_py_task_12`)

`orch_py_task_12` says `--permission-mode bypassPermissions` disables the inner sandbox.
**That diagnosis is wrong.** Measured, with a positive control (an allowlisted host that must
reach) alongside the decider (`curl http://1.1.1.1`):

```
bypassPermissions                    sandbox=FAILED-INIT  ctrl=0  esc=0   *** EGRESS ESCAPED ***
dontAsk + permissions.allow          sandbox=FAILED-INIT  ctrl=0  esc=0   *** EGRESS ESCAPED ***
```

`dontAsk` carries no "skip safety checks" semantics and fails identically. The permission
mode is irrelevant. The harness's actual error:

```
Sandbox is enabled but failed to initialize: Failed to listen at 127.0.0.1.
Sandboxing is disabled for the rest of this session; restart to retry.
```

Claude Code's egress filter is a **loopback proxy**; it must `listen()` on 127.0.0.1.
Layer 1 grants `network-bind` but nothing permitting the listen, so the proxy dies, the
harness disables sandboxing **for the whole session**, and every later Bash command runs
unfiltered. Layer 1 was strangling layer 2.

**And the obvious fix is a trap.** Adding `(allow network-inbound (local ip "localhost:*"))`
does make the sandbox report `ok` — and then every Bash command dies at `sandbox_apply`
(Fact 1). It converts a jail that is silently open into a jail that lies more convincingly.
Do not ship it. Two further cautions, both measured:

- `(local ip "localhost:*")` is **not** a loopback restriction. Seatbelt's host field accepts
  only `*` or `localhost`, and an explicit `127.0.0.1` does not compile. Under that grant a
  process can bind, listen, and **accept on the host's LAN IP**. The port half _is_ enforced.
- `render_settings` emits neither `allowUnsandboxedCommands` nor `failIfUnavailable`, so both
  sit at permissive defaults. With the sandbox dead, the agent **re-ran a blocked command with
  sandboxing disabled, on its own initiative** ("I'll re-run it with the sandbox disabled" →
  `dangerouslyDisableSandbox`) and reached `1.1.1.1`.

**Conclusion: layer 2 can never run inside layer 1.** `render-settings` currently emits an
allowlist that nothing enforces — which also means task 5 of the port plan should not port
that subcommand until this is settled.

## The decision to make

Exactly one Seatbelt layer is available. Choose where it goes and what enforces egress.

| Option                                                                                                          | Egress filtering                                                                                          | Coders / verify                                                                                                                                  | Cost                                                                                                                                                                   |
| --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Keep the outer jail; add our own loopback egress proxy**, and pin layer 1's `network-outbound` to its port | Real (HTTP `CONNECT` allowlist; no TLS interception needed). Raw sockets blocked outright by the port pin | Unchanged — coders keep running with their own sandbox off, as they already must                                                                 | Run/supervise a proxy; DNS must go through it                                                                                                                          |
| **B. Drop the outer `sandbox-exec`; rely on Claude Code's own sandbox**                                         | Real (deny-default + HTTP/SOCKS proxy)                                                                    | Coders **still** must disable their own sandbox (the nesting rule just moves), and now inherit Claude's per-command filesystem policy — untested | Must re-express cred-RO + supervisor-ledger denies in its settings; requires `allowUnsandboxedCommands:false` + `failIfUnavailable:true`, or the agent switches it off |
| **C. Move egress filtering out of the process** (VM / container)                                                | Real, at the network layer                                                                                | Nesting composes; `check.sh` runs in-jail                                                                                                        | Heaviest; changes how runs launch                                                                                                                                      |

**Recommendation: A.** It preserves the filesystem and exec confinement that demonstrably
works (task 10 / #208 proved it), fixes the one thing layer 1 does wrong, keeps the coder
posture that already runs, and is the same architecture Claude Code itself uses. **B** means
trusting a sandbox the agent can switch off — measured doing exactly that.

Whichever is chosen, `sandbox.enabled` should stop being emitted as `true` while nothing
enforces it. A jail that reports a layer it does not have is worse than one that admits it.

## Tasks

### 1. Take the decision, and correct the record — **do this first**

- Pick A, B, or C above. Record it as an ADR in `dev_docs/decisions/`, with the two
  structural facts and their repros. This is a decision about the runtime's security
  posture, so it belongs there, not in a card that gets deleted.
- Correct `orchestrator.sb.tmpl`'s comment: Seatbelt cannot filter hostnames, but it **can**
  pin egress to a port. The current comment states a false conclusion and is the origin of
  the blanket `(allow network-outbound)`.
- Correct `orch_py_task_12`: the cause is the loopback listen + nesting, **not**
  `bypassPermissions`. Its stated fix would not have worked.
- Correct `skills/auto-pilot/references/launch-runtime.md` "Sandbox profile", which describes
  two enforcing layers. Either make it true or say what is actually true.

### 2. Implement the chosen posture

Under **A** this is: stand up the loopback egress proxy, pin layer 1's `network-outbound` to
its port, stop emitting `sandbox.enabled: true`, and delete or repurpose the layer-2
allowlist path in `render-settings` (and tell the port plan's task 5).

**Acceptance (code-enforced):** in `smoke-confinement.sh` §2, a non-allowlisted host and a
raw IP from inside the jail are refused, an allowlisted host still reaches, and the run
proves the enforcement mechanism was actually alive.

### 3. Fix the smoke's vacuous egress decider — it passes _because_ the jail is broken

Decider (d) is `exec 3<>/dev/tcp/1.1.1.1/80`. Measured: the Bash tool's shell is **zsh 5.9**
(`BASH_VERSION` empty), and `/dev/tcp` is a bashism — so it returns `rc=1` regardless of the
network, and the smoke scores `rc!=0` as **PASS (blocked)**.

It is worse than vacuous. A _healthy_ sandbox runs commands under `bash`, where `/dev/tcp`
works and would be blocked; a _dead_ one falls back to the user's zsh, where it is a syntax
error scored as a pass. **The probe reports success exactly in the failure case.**

- Pin the raw-socket decider to `bash -c` (or use `nc`, a real binary with no shell
  dependency). Keep it — it tests a genuinely different threat model from the curl rows.
- **Gate every §2 "blocked" row on liveness**: a row may score PASS only if the same run
  proved the enforcement mechanism initialized _and_ the positive control reached. Today
  §1b's init check and §2's deciders are independent, so a dead jail still produces green
  rows.
- Also: a coder that cannot exec in the jail may **fabricate** rather than fail (observed with
  `codex`). Any in-jail coder invocation needs a liveness assertion of its own.

### 4. Close the verify contract — the only thing actually blocking a _full_ run

Detached-run finding #4: `check.sh` cannot pass in-jail, because its harnesses re-exec
`sandbox-exec` (Fact 1). This is the one item that stops auto-pilot reaching "definition of
done" unattended.

`skills/deliver-task/SKILL.md` already designs the answer — the **verify broker**: the pinned
verify command runs **outside** the jail, in a worktree confined to the run root, and the
in-jail content gates are a fast pre-check, not the definition of done. Finish that, and
state the split in `RUN.md` so the degradation is _declared, not discovered_.

## Non-goals

- Making Seatbelt filter hostnames. It cannot. Stop trying.
- Making nested `sandbox-exec` compose. It cannot. Stop trying.
- Blocking auto-pilot on any of this. It already ran detached, 6/6, and the P0 spawn bugs
  (`--verbose`, `export PATH`, `--toolchain` exec) are fixed.

## Evidence

Every claim above was executed on this host, not reasoned to. The repros are inline and
re-runnable — per `dev_docs/designs/enforce-exercising.md` (PR #209), which exists because
this plan's failure mode is the one the whole orch_py stream keeps hitting: **a claim about
runtime behavior, asserted, propagated, and never once run.**
