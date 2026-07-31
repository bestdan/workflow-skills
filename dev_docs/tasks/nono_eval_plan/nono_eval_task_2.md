---
title: "nono credential injection — hide github/linear, boundary-against-the-agent, Max-token MITM (F3/F4/F6)"
priority: high
size: 2
status: new
created: 2026-07-22
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/nono-evaluation.md
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: [nono_eval_task_1]
parent: nono_eval
tags: [e-lite, nono, spike, probe, containment, credentials, user-run]
---

Plan: [[nono_eval_plan]]

## Context

Probe spec: [../../../nono-evaluation.md](../../../nono-evaluation.md), falsifiers F3, F4, F6. Runs **only if [[nono_eval_task_1]] confirmed F1/F2/F5** — this is the MITM half of the CONNECT-vs-MITM seam. Credential injection is what makes nono more than a nicer Seatbelt: a phantom token sits in the sandbox env and the proxy swaps it for the real key at the HTTP-header level. It requires **path-scoped `--allow-domain` → layer-7 TLS MITM with nono's CA** (host-level CONNECT tunnels see no plaintext and cannot inject), and it requires the tool to trust nono's CA (`NODE_EXTRA_CA_CERTS` for Claude's Node runtime).

Two subtleties this task must resolve, not gloss:

- **F4 — boundary against *the agent*, not just the sandboxed child.** nono forks and sandboxes the child; the parent (holding real creds + CA) is unsandboxed. If the parent must run same-uid as the agent, then the agent user — which by the design's premise runs other, unsandboxed things — could read the parent's memory/CA/creds, so injection is not a boundary against the agent. The load-bearing question is whether nono supports **parent=maintainer-uid / child=agent-uid**, or whether a same-uid agent can still recover the creds.
- **F6 — Max token is strictly harder than F1.** Hiding the Anthropic bearer needs MITM of `api.anthropic.com` (custom CA in Claude's trust), where F1 only needed a CONNECT tunnel. F6 failing does **not** reject — it narrows Decision #1 (github/linear hide, Max token stays readable).

Default-reject bias: an inconclusive on F3/F4/F6 **degrades to the weaker adoption tier, never up** (unlike task 1's parity claims, which reject on inconclusive).

**Progress from the preliminary session (feeds this task):**
- **F6a already confirmed** (`fixtures/nono/f6-anthropic-mitm-preliminary.md`): Claude works under nono MITM of `api.anthropic.com` (audit-proven decrypted paths). So F6's *prerequisite* is met; F6b (actually injecting the Max token so `managed_credential_active` is true and Claude never holds it) is the open part.
- **Open question for F3/F6b:** does nono ship credential providers for GitHub and Anthropic (`nono run --credential <service>`), and do they satisfy the real auth flows (GitHub App installation token; Anthropic OAuth bearer+refresh)? Investigate `nono`'s `--credential`/`--allow-endpoint` and the registry before assuming injection is possible.

**Checkpoints (stop and report at each):**
- **A — sandbox-layer F5** (no App needed): agent `nono pull nolabs-ai/claude`, then run the F5 battery from *inside* `nono run` as agent; confirm maintainer secrets denied AND the System keychain (`/Library/Keychains`) unreachable (profile grants only `~/Library/Keychains`). Completes F5.
- **B — test App** (user, browser): create the disposable test GitHub App on the spike repo; enables F2 write loop + F3.
- **C — F3** github/linear injection + the recovery-attempt battery.
- **D — F4** the two-uid injection boundary (cross-uid parent/child, or same-uid recovery).
- **E — F6b** Anthropic credential injection (full-tier gate).

## Task

Building on task 1's allow-list, all under `nono run` against the spike repo:

- **F3 — hide github/linear tokens.** Inject the GitHub (and Linear) token proxy-side via phantom tokens with path-scoped `--allow-domain` (layer-7 MITM). Confirm the delivery ops from task 1's F2 still work. Then, from inside the sandbox, attempt to recover the real secret: read env, dump the process's own memory, read the injected CA, and exfiltrate the phantom token to a non-allowed host. **Pass = delivery ops work AND every recovery attempt fails (the real bearer never appears in the sandbox).** Fail ⇒ DEGRADE to no-injection (network allow-list only; tokens stay readable); record and continue to re-score, do not delete task.
- **F4 — boundary against the agent.** Determine whether nono supports parent=maintainer-uid / child=agent-uid. If **not** (parent same-uid as agent): from a **plain unsandboxed agent shell** (`sudo -u agent ...`), attempt to read the proxy parent's memory / CA key / injected creds. **Pass = either cross-uid parent/child is supported (injection is maintainer-owned) OR the same-uid agent cannot recover the real creds.** Fail ⇒ DEGRADE the trust claim: injection protects only sandboxed code, not the agent user; report as "convenience sandbox inside the agent plane" and do **not** rewrite §2.1's boundary language around it.
- **F6 — Max-token MITM (stretch; only if F3 confirmed).** Attempt credential injection for `api.anthropic.com`: MITM Anthropic TLS with nono's CA, put the CA in Claude's trust (`NODE_EXTRA_CA_CERTS`), inject the Max bearer. Confirm Claude still works and the Max bearer never appears in the sandbox. **Pass = Claude runs under MITM AND the Max token is injected, not agent-readable.** Fail ⇒ NARROW (not reject): Decision #1's risk shrinks from three readable secrets to one.
- Close F3/F4/F6 `confirmed`/`falsified`/`inconclusive` against their probe-spec rows; write the rows in `dev_docs/elite-spike/measurements.md`; fixtures under `dev_docs/elite-spike/fixtures/nono/`, sanitized per the plan checklist (**never** persist the CA private key or any bearer).

## Acceptance Criteria

- **User-run:** F3, F4, F6 each executed as described (F6 only if F3 confirmed) under `nono run` on the mac mini; each falsifier closed terminal against its probe-spec row with the correct degrade/narrow/pass disposition (inconclusive degrades, never upgrades); evidence + fixture commands checked into `dev_docs/elite-spike/fixtures/nono/` and one row per falsifier in `dev_docs/elite-spike/measurements.md`, sanitized per the plan checklist.
- **Feeds the decision:** the three rows plus task 1's determine which of the four adoption tiers [[nono_eval_task_3]] applies (full / degraded / network-only / reject).
