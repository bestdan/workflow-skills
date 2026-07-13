---
title: layer 2 does not enforce — bypassPermissions disables the inner sandbox, and egress is open
priority: high
size: 3
status: ready
created: 2026-07-13
source_branch: bestdan/orch-py-task-10 # PR #208
parent: orch_py
related_files:
  - scripts/spawn-orchestrator.sh:1497 # the launch script passes --permission-mode bypassPermissions
  - scripts/spawn-orchestrator.sh:1213 # the comment documenting that posture as the design
  - scripts/spawn-orchestrator.sh:1603 # smoke-test mirrors the same posture
  - scripts/orchestrator.sb.tmpl:34 # layer 1 allows network as a CLASS, deferring hostnames to layer 2
  - scripts/smoke-confinement.sh:132 # §2, which is what caught it once it could run at all
tags: [orchestrator, seatbelt, jail, bug, security, egress]
---

← [[orch_py_plan]]

## Context

> **Found while fixing task 10** (PR #208, which targets `main` directly — the fix is in
> `scripts/` and has no dependency on the port). That card's exec fixes let `claude -p` actually
> start inside the jail on this host for the first time, which let §2 of the confinement smoke
> run for the first time. It immediately failed. Filed, not fixed in passing — per this plan's
> scope rule.
>
> **This card is [[orch_py_task_11]]'s thesis, demonstrated.** Layer 2 was asserted in the docs
> and never executed; the moment it was executed it failed. PR #208 already lands task 11's
> archetype fix (the smoke now *runs* git in the jail rather than merely granting it) — and that
> is precisely the change that exposed this.

**The two-layer jail is running on one layer. The jailed agent has unrestricted network
egress.**

`spawn-orchestrator.sh:1497` — the generated launch script — invokes the detached agent as
`claude -p --permission-mode bypassPermissions`. That flag **disables the harness's own inner
sandbox**, which *is* layer 2. The harness logs `Sandbox is enabled but failed to initialize`
and continues with no network enforcement.

Layer 1 cannot cover for it, **by design**: `orchestrator.sb.tmpl:34` allows `network-outbound`
as a *class*, with the comment "Seatbelt gates network by socket/class, NOT by hostname… all
hostname allowlisting lives in layer 2, not here." So when layer 2 is off, nothing is filtering
egress at all.

Measured against a real rendered profile + real rendered settings (the layer-2 allowlist is
`api.anthropic.com`, `api.github.com`, `github.com`, … — none of these are 1.1.1.1):

```
--permission-mode bypassPermissions   raw socket → 1.1.1.1:80  rc=0     inner sandbox: FAILED TO INIT
--permission-mode acceptEdits         raw socket → 1.1.1.1:80  (n/a)    inner sandbox: ok
```

`rc=0` is a **completed TCP connection to an arbitrary host** from inside the jail. The
confinement smoke calls exactly this case "the decider" and prints `EGRESS ESCAPED`. Non-
allowlisted `example.com` over curl escapes the same way.

**This is production, not just the test.** The July-11 detached run's `launch.sh` carries
`--permission-mode bypassPermissions` verbatim, so every detached run to date has had an open
egress path. The exfiltration story is the obvious one: the jailed agent holds `gh` and `op`
credentials, and layer 1 grants it `network-outbound`.

**Why it was never caught.** Three independent maskings, all now removed by task 10:

1. `claude -p` could not start in the smoke's jail at all (it spawns `/usr/bin/security` to read
   the keychain; the grant was missing), so §2 went `????` INDETERMINATE, not FAIL.
2. `curl` was not exec-granted either, so probes (b)/(c) returned rc=126 ("cannot execute") and
   the smoke scored them **PASS (blocked)** — passing while proving nothing, because curl never
   ran to be blocked.
3. `TMPDIR` was not exported to match the launch contract, so the mux-socket grant never matched
   and the inner sandbox failed for a *second*, unrelated reason.

## Task

1. **Decide what `bypassPermissions` is actually for.** It is there to stop the unattended agent
   from blocking on permission prompts — a real requirement. The question is whether the harness
   offers a mode that skips *prompts* without also disabling the *sandbox* (e.g. an explicit
   `--allowedTools` set, or settings-level permission rules) — i.e. whether these two were ever
   meant to be the same switch.
2. **If they cannot be separated, layer 2 cannot be a harness feature** and the egress allowlist
   must move into layer 1 or a real proxy. Note `render-settings` (and its port, task 5) exists
   *only* to emit this allowlist — if the harness never enforces it, that whole subcommand is
   emitting a file nobody reads, which should be stated plainly rather than ported.
3. **Re-verify the documented posture.** `skills/auto-pilot/references/launch-runtime.md`
   "Sandbox profile" describes two enforcing layers. Either make that true or correct the doc.
4. **Make the smoke fail loudly on this** rather than indeterminately. Task 10 already got §2 to
   the point where it reports `EGRESS ESCAPED`; keep it that way.

## Acceptance Criteria

**Code-enforced:**

- `smoke-confinement.sh` §2 (c) and (d) — the deciders — **PASS**: a raw socket and a raw IP from
  inside the jailed, detached-posture agent are refused.
- (a) still reaches `api.github.com` — the allowlist permits what the run genuinely needs.
- The inner sandbox initializes under the **real launch posture**, or the posture changes so that
  layer 2 is enforced by something that does.

**User-run:**

- A real detached run cannot open a socket to a non-allowlisted host.

## Note

Independent of the Python port: this is the *runtime's* security posture, not the language's. But
it lands on task 5 (`render-settings` — the layer-2 allowlist renderer), which should not be
ported as-is until it is known whether anything enforces its output.
