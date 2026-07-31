---
type: epic
title: "nono evaluation — falsification probe for the E-lite containment layer"
status: active
owner: bestdan
created: 2026-07-22
---

# nono evaluation plan

Source probe spec: [../../../nono-evaluation.md](../../../nono-evaluation.md).
Target design: [../../../auto-pilot-e-lite-design-2026-07-21.md](../../../auto-pilot-e-lite-design-2026-07-21.md) (§3.2, §2.1, Risk #2, Decision #1).

## Goal

Decide, by falsification, whether [nono](https://nono.sh) can serve as E-lite's containment + credential-injection layer — closing the `gh` hole (Risk #2) and shrinking or closing agent-readable bearer tokens (Decision #1) — or whether the hand-rolled Seatbelt + server-side bounding stays. Every test is built to **disprove** adoption; load-bearing ambiguity resolves to reject.

## Scope / non-goals

- **In scope:** the six falsifiers F1–F6 of `nono-evaluation.md`, split along the CONNECT-vs-MITM seam into a base-viability gate (F1/F2/F5) and a credential-injection task (F3/F4/F6) blocked on it.
- **Out of scope (nono provides none of these — do not test, do not let a pass expand into them):** the control plane (§0, §4), the lease/registry state machine, the watcher / supervision / unattended recovery (§5), continuation (§5.3), and nono ghost-sessions as a tmux substitute (§4). A pass rewrites **only** the containment layer; it does not shorten the control-plane work.
- **Spike contract (§0a, inherited):** disposable directory + the spike test repository; a **disposable test GitHub App** installed only on that repo (never the production App key); throwaway/read-only Linear key; nothing under `/usr/local/autopilot`; nono via Homebrew; real Max account only for the test identity's own OAuth and F1/F6's minimal invocations. Evidence sanitized per the Stage-0 plan checklist; fixtures under `dev_docs/elite-spike/fixtures/nono/`; one measurement row per falsifier in `dev_docs/elite-spike/measurements.md`. Spike code is never promoted by renaming.

## Approach

Falsifier-first, and **gated on a hard seam**: F1 (can Claude Code even run headless behind the proxy) is the cheapest and most likely to end the evaluation, so the base-viability task runs first and is a go/no-go for the credential-injection task. Splitting on CONNECT-vs-MITM is deliberate — host-allow tunnels can't inject credentials (end-to-end TLS), while injection needs path-scoped TLS MITM with nono's CA; the MITM work in task 2 is wasted if task 1 shows the base case fails. Both tasks are **attended, user-run on the mac mini** (real credentials, real device); neither should be promoted into unattended `/do-tasks` or auto-pilot runs.

## Tasks

1. [[nono_eval_task_1]] — Base viability gate (F1 Claude-through-proxy, F2 delivery loop, F5 no-regression) — the go/no-go.
2. [[nono_eval_task_2]] — Credential injection (F3 hide github/linear, F4 boundary-against-the-agent, F6 Max-token MITM), blocked on task 1.
3. [[nono_eval_task_3]] — Record the decision, apply the four-tier rule to the design, then graduate durable findings and delete this scaffolding.

## Decisions (2026-07-22)

- **Evidence home:** shared with the Stage-0 spike — fixtures under `dev_docs/elite-spike/fixtures/nono/`, one measurement row per falsifier in `dev_docs/elite-spike/measurements.md`. No separate `dev_docs/nono-spike/` root.
- **Test App:** reuse the single disposable test GitHub App on the spike repo (the same throwaway App Stage-1's probe 4 stands up); do not create a second. nono's proxy and server-side policy are tested against the one App.

## Progress (2026-07-22)

Task 1 substantially done (preliminary + agent-side). Confirmed: **F1** (Claude through proxy), **F2** tool-compat (git/gh honor proxy; allowlist needs `github.com` + `api.github.com`; write loop pending App), **F5** two-uid boundary for secrets + agent keychain clean + `chmod 700 ~maintainer` hardening applied, **F6a** (Claude works under Anthropic MITM, audit-proven). Agent identity provisioned; agent Max auth working. Standing verdict: **adopt-leaning**, degraded tier at minimum, full tier pending F6b. See `dev_docs/elite-spike/measurements.md`.

**Remaining in task 1:** sandbox-layer F5 (agent inside nono) and the F2 write loop (needs App) — folded into task 2's checkpoints A/B for continuity.

## Decision (2026-07-22): ADOPT — defense-in-depth / network-only tier

After the code-level security review (`../../nono_security_review.md` via `../../nono-evaluation-key-points.md`), the verdict is **adopt nono as the macOS sandbox + host-level domain-allowlist layer** — closing the `gh` hole and keeping the agent off maintainer files — **but not** for credential hiding. SR-3 (L7 path-traversal) makes endpoint-scoped injection unreliable, and nono's alpha status argues against betting secrets on it, so §2.2's server-side rulesets stay as the token boundary and Decision #1 stands. Constraints: pin the binary, vendor the reviewed profile (no live auto-pull), ephemeral CA only, macOS-only.

**Design edits applied** (task 3 partial): design §3.2, Risk #2, Decision #1, §2.1 updated to reflect the adoption (commit `7743516`).

**Task-2 injection checkpoints C/D/E (F3/F4/F6b) — DROPPED** as low-value per this decision (injection is not the value proposition). Checkpoint A (sandbox-layer F5) remains to complete F5; Checkpoint B (test App → F2 write loop) remains to complete F2 — neither expected to change the tier.

**Still open before full close:** Checkpoint A (sandbox-layer F5), F2 write loop (needs test App). Task 3's graduate-then-delete cleanup waits on those.
