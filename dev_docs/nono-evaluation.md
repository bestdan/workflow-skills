---
title: nono as the E-lite containment + credential-injection layer — falsification probe
status: proposal — evaluation probe (not yet scheduled)
created: 2026-07-22
audience: reviewer, then whoever runs the probe
targets:
  - "§3.2 native sandbox + the `gh` hole"
  - "§2.1 token broker / credential readability"
  - "Risk #2 (`gh` outside the sandbox)"
  - "Decision #1 (Max OAuth is an agent-readable bearer secret)"
related:
  - ./auto-pilot-e-lite-design-2026-07-21.md
  - ./tasks/elite_stage0_plan/elite_stage0_plan.md
---

# nono evaluation — falsification probe

## 0. What this probe decides

[nono](https://nono.sh) (`nolabs-ai/nono`, Apache-2.0, Rust) is a per-command
Seatbelt sandbox for AI agents that adds two things E-lite's hand-rolled
containment does **not** have: a local proxy giving **domain-level** network
allow-lists (Seatbelt alone is binary allow/deny), and **credential injection**
— a phantom token sits in the sandbox env and the proxy swaps it for the real
key at the HTTP-header level, so the sandboxed process never sees the secret.

Those two features land exactly on E-lite's weakest accepted concessions:
the `gh` hole (Risk #2 — `gh` runs _outside_ the sandbox because Seatbelt
can't filter by destination) and the readable bearer tokens (Decision #1 and
§2.1's admission that the broker is "a workflow correctness invariant, not a
security boundary" _because the agent can read the token and call another HTTP
client_).

**This probe decides one thing:** whether nono can serve as E-lite's
containment + credential-injection layer, closing Risk #2 and shrinking or
closing Decision #1 — or whether it cannot, and the hand-rolled Seatbelt +
server-side bounding stays. It is a falsification probe: every test is built to
**disprove** adoption, and any load-bearing ambiguity resolves to reject, not
adopt.

## 1. The claim under test

> nono, wrapping the agent's execution as a maintainer-owned parent with a
> domain allow-list and credential injection, is a drop-in containment layer
> that (a) runs the real delivery loop unmodified, (b) hides bearer secrets
> from the agent, and (c) does not regress the filesystem/identity boundary the
> design already assumes.

If any of (a)/(b)/(c)'s load-bearing falsifiers below fires, the claim is false
at the corresponding strength.

## 2. Scope — and loud non-goals

**In scope (what nono would replace/augment):** §3.2 (native sandbox + the
`gh` hole) and the _reachability_ half of §2.1 (whether the agent can read the
token). nono is evaluated as the execution wrapper for the agent's `claude`
session and its git/`gh`/Linear traffic.

**Explicit non-goals — nono provides none of these; do not test them here and
do not let a passing result expand into them:**

- **The control plane (§0, §4).** nono has no maintainer/agent trust split as
  an architecture, no run manifest, no lease/generation state machine, no
  registry, no incarnation identity. The crash-transaction kernel (probe 5)
  gains nothing from nono.
- **Unattended supervision & recovery (§5).** nono's own docs state its
  execution modes do not handle "crash recovery or unattended restarts." Its
  "ghost sessions" survive terminal disconnect (like tmux) but there is no
  watcher / heartbeat / launchd-wake / remote-alert / usage-limit-continuation
  equivalent, and reboot survival is unclaimed. The watcher (§5) and probe-3
  "unattended promise" remain 100% E-lite's.
- **Session hosting (§4 tmux).** nono ghost sessions could _substitute_ for
  tmux, but that would make nono load-bearing in incarnation identity (probe 2)
  while running same-plane. **Not** in scope; keep plain tmux regardless of this
  probe's outcome.

A nono pass buys a better containment layer. It does **not** shorten the
control-plane or supervision work, and this probe must not be reported as if it
did.

## 3. Spike contract (inherits §0a discipline)

Disposable directory and the dedicated spike test repository (elite Stage-0
[[elite_stage0_task_10]]); **a disposable test GitHub App** installed only on
that repo (never the production App key); a throwaway Linear key or a
read-only-scoped bot; no writes under `/usr/local/autopilot`; nono installed
via Homebrew into the normal user prefix. Real Max account used only to
establish the test identity's OAuth and the minimal invocations F1/F6 require.
Checked-in evidence is sanitized per the Stage-0 plan's sanitization checklist
(no bearer tokens, CA private keys, provider keys, hostnames, or absolute home
paths). Fixtures live under `dev_docs/elite-spike/fixtures/nono/`; one
measurement row per falsifier in `dev_docs/elite-spike/measurements.md`. Spike
code is never promoted by renaming.

## 4. Falsifiers (ordered by blast radius; run in order, stop at the first that kills adoption)

Falsifier-first: **F1 is the cheapest and the most likely to end the
evaluation** — if the agent's own model can't run behind the proxy, nothing
downstream matters. Each row pre-registers its kill condition; run the test
only to try to trip it.

| #      | Load-bearing claim                                                                 | Test                                                                                                                                                                                                                                                                                                 | Pass threshold                                                                                                                                       | Fail ⇒                                                                                                                                                                                                       |
| ------ | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **F1** | Claude Code runs headless through nono to `api.anthropic.com`.                     | `nono run` (host-allow `api.anthropic.com`, `github.com`, `api.linear.app`, loopback; deny all else) a headless `claude` session that completes one trivial task in the spike repo.                                                                                                                  | Exit 0, non-empty model output, no proxy/TLS/network error in logs.                                                                                  | **REJECT** — nono cannot host our agent. Stop the evaluation.                                                                                                                                                |
| **F2** | The real delivery loop runs through the allow-list.                                | Inside the same sandbox: `git clone` (test repo), commit, `git push` a `bestdan/ap/**` branch, open+comment+close a PR via `gh`, one Linear read+write, and the GraphQL reads `gh` actually issues.                                                                                                  | Every op succeeds through the proxy; nothing reaches a denied host; no op silently bypasses the allow-list.                                          | **REJECT** — the product loop doesn't survive containment.                                                                                                                                                   |
| **F3** | Credential injection hides the token from the sandboxed child.                     | Inject the GitHub (and Linear) token proxy-side via phantom tokens (path-scoped `--allow-domain` → layer-7 MITM). From inside the sandbox attempt to recover the real secret: read env, dump the process's own memory, read the injected CA, and exfiltrate the phantom token to a non-allowed host. | Delivery ops still work **and** every recovery attempt fails — the real bearer never appears in the sandbox.                                         | **DEGRADE to no-injection** — keep nono for the network allow-list only; tokens stay readable (Decision #1 unchanged). Re-score against F5.                                                                  |
| **F4** | Injection is a boundary _against the agent_, not just against the sandboxed child. | Determine whether nono supports parent=maintainer-uid / child=agent-uid. If not (parent runs same-uid as agent): from a **plain unsandboxed agent shell** (the agent user runs other things — that is the design's premise), attempt to read the proxy parent's memory / CA key / injected creds.    | Either cross-uid parent/child is supported (injection is maintainer-owned), **or** the same-uid agent cannot recover the real creds from the parent. | **DEGRADE the trust claim** — injection protects only sandboxed code, not the agent user; report as "convenience sandbox inside the agent plane," and do **not** rewrite §2.1's boundary language around it. |
| **F5** | Containment does not regress below raw Seatbelt.                                   | Repeat probe-1's sentinel-unreadability battery (`~maintainer/.ssh`, `.aws/credentials`, `.gitconfig`, login keychain) from inside `nono run`; confirm the sandbox actually engaged (fail-closed if it can't start — the `failIfUnavailable` analogue), not silently degraded.                       | Every sentinel read denied; a proving log line shows the sandbox active; startup fails closed when forced unavailable.                               | **REJECT** — nono is not at parity with the boundary the design already assumes.                                                                                                                             |
| **F6** | The Max OAuth token can _also_ be hidden (closing Decision #1 fully).              | Attempt credential injection for `api.anthropic.com` — requires MITM of Anthropic TLS (custom CA in Claude's Node trust via `NODE_EXTRA_CA_CERTS`). Confirm Claude still works and the Max bearer never appears in the sandbox.                                                                      | Claude runs under MITM **and** the Max token is injected, not agent-readable.                                                                        | **NARROW, don't reject** — github/linear creds hide (F3) but the Max token stays agent-readable; Decision #1's risk shrinks from three readable secrets to one. Record as the expected partial outcome.      |

### Notes that shape the tests

- **CONNECT vs MITM.** Host-level allows are CONNECT tunnels — end-to-end TLS,
  proxy sees no plaintext, **no credential injection possible**. Injection
  requires path-scoped `--allow-domain` and TLS MITM with nono's CA. So F1 (run
  Claude) and F3/F6 (inject creds) exercise _different_ proxy modes; F6 is
  strictly harder than F1 because it adds MITM of a pinned-ish endpoint.
- **Proxy honoring.** Domain filtering only binds tools that honor
  `HTTP_PROXY`/`HTTPS_PROXY`. `git` and `gh` do; Claude's Node runtime needs
  `NODE_USE_ENV_PROXY=1` (nono sets it) and, for F6, `NODE_EXTRA_CA_CERTS`. A
  tool that ignores the proxy and still reaches the network is an **F2/F5
  failure**, not a pass — verify the deny path, don't just verify the allow
  path.
- **Default-reject.** An inconclusive result on F1, F2, or F5 is a reject for
  that run (they are parity/liveness claims). An inconclusive on F3/F4/F6
  degrades to the weaker adoption tier, never up.

## 5. Time cap & tranche

Half a working day for F1–F5; F6 gets a second half-day only if F1–F5 all pass
(no point MITM-ing Anthropic if the base case already failed). One tranche,
attended, user-run on the mac mini (needs the real device only if you also want
to confirm nothing about alerting — you don't; alerting is a non-goal here).
Classify each falsifier `confirmed` (adoption survives) / `falsified` /
`inconclusive` against the row above; no fourth state.

## 6. Decision rule

- **ADOPT (full):** F1, F2, F5 confirmed; F3, F4, F6 confirmed. ⇒ nono replaces
  §3.2, closes Risk #2, and closes Decision #1 (agent sees no bearer secret).
  Rewrite §2.1 (broker still _mints_, but the token is injected, never
  agent-readable), §3.2 (delete the `gh` hole), Risk #2 (downgraded to
  "closed"). The broker's "workflow correctness invariant, not a security
  boundary" caveat is deleted.
- **ADOPT (degraded — the most likely good outcome):** F1, F2, F5 confirmed;
  F3 confirmed; F4 and/or F6 falsified. ⇒ nono closes the `gh` hole and hides
  github/linear creds, but either injection isn't a boundary against the agent
  user (F4) or the Max token stays readable (F6). Still a net win: rewrite §3.2
  - Risk #2; narrow Decision #1's language; **do not** overclaim the trust
    boundary.
- **ADOPT (network-only):** F1, F2, F5 confirmed; F3 falsified. ⇒ take the
  domain allow-list (closes the `gh` hole) but no credential benefit; tokens
  stay readable exactly as today. Small win; weigh against the added dependency.
- **REJECT:** any of F1, F2, F5 falsified. ⇒ nono does not fit; keep hand-rolled
  Seatbelt + server-side bounding. Record why so no one builds broker or
  containment changes around a capability nono doesn't actually provide.

## 7. What a result changes in the design

A pass edits **only** §2.1, §3.2, Risk #2, and Decision #1 — plus a new
dependency note (nono pinned version, its CA handling, the proxy as a
maintainer-owned parent). It changes **nothing** in §0, §4, §5, §5.3, or the
§7a substrate probes: the control plane, lease/registry, watcher, and
unattended promise are untouched, because nono does not address them. A pass is
permission to rewrite the containment layer, not evidence that the harder work
shrank.
