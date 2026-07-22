---
title: "nono base viability gate — Claude through the proxy, delivery loop, no regression (F1/F2/F5)"
priority: high
size: 2
status: new
created: 2026-07-22
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/nono-evaluation.md
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: []
parent: nono_eval
tags: [e-lite, nono, spike, probe, containment, user-run]
---

Plan: [[nono_eval_plan]]

## Context

Probe spec: [../../../nono-evaluation.md](../../../nono-evaluation.md), falsifiers F1, F2, F5. This is the **go/no-go gate** for the whole nono evaluation — it exercises only host-level allows (CONNECT tunnels, end-to-end TLS, no MITM), which is the base case. Credential injection (task 2) is pointless if this fails, so [[nono_eval_task_2]] is blocked on this task.

Key facts about nono that shape the tests (from its docs): it sandboxes via Seatbelt `sandbox_init()` (no sudo); the child can only reach `localhost:<proxy>` and all other outbound TCP is kernel-blocked; host-level `--allow` is a CONNECT tunnel; domain filtering only binds tools that honor `HTTP_PROXY`/`HTTPS_PROXY` (`git`/`gh` do; Claude's Node runtime needs `NODE_USE_ENV_PROXY=1`, which nono sets). Verify the **deny** path, not just the allow path — a tool that ignores the proxy and still reaches the network is an F2/F5 **failure**, not a pass.

Spike contract per the plan: disposable dir + spike test repo, disposable/read-only creds, nono via Homebrew, nothing under `/usr/local/autopilot`. Evidence sanitized per the Stage-0 plan checklist; fixtures under `dev_docs/elite-spike/fixtures/nono/`. F2 uses the **single disposable test GitHub App** on the spike repo (the same throwaway App Stage-1's probe 4 stands up — reuse it, don't create a second); never the production App key.

**Preliminary F1 already confirmed** (maintainer uid, not the formal agent-identity close — see `dev_docs/elite-spike/fixtures/nono/f1-preliminary.md`): Claude runs headless behind the proxy (returns output, exit 0); allow path reachable via CONNECT tunnel, deny path blocked. Two findings the formal run must carry: (1) Claude requires nono's registry profile `nolabs-ai/claude` — a **supply-chain input to review**; (2) that profile grants the **whole `$HOME/Library/Keychains`** (with `bypass_protection`) because Claude's OAuth is in the login Keychain — so **F5's sharpest sub-test is whether that Keychain grant exposes other apps' Keychain items**, not just the file-path sentinels.

## Task

Install nono (Homebrew). Configure a single allow-list: `api.anthropic.com`, `github.com`, `api.linear.app`, loopback; deny all else. Then, all under `nono run` against the spike test repo:

- **F1 — Claude through the proxy.** Launch a headless `claude` session that completes one trivial task in the spike repo. Capture the invocation, exit code, and proxy/TLS logs. **Pass = exit 0, non-empty model output, no proxy/TLS/network error.** Fail ⇒ REJECT and stop the whole evaluation (record in the measurement row; do not run task 2).
- **F2 — delivery loop.** Inside the same sandbox: `git clone` the spike repo, commit, `git push` a `bestdan/ap/**` branch, open + comment + close a PR via `gh`, one Linear read + write, and the GraphQL reads `gh` actually issues. Also attempt one op against a **denied** host and confirm it is blocked. **Pass = every required op succeeds through the proxy AND nothing reaches a denied host AND no op silently bypasses the allow-list.**
- **F5 — no regression below raw Seatbelt.** From inside `nono run`, repeat probe-1's sentinel battery: `cat ~maintainer/.ssh/*`, `cat ~maintainer/.aws/credentials`, `cat ~maintainer/.gitconfig`, read `~maintainer/Library/Keychains/login.keychain-db`. Confirm the sandbox actually engaged (a proving log line), and that startup **fails closed** when the sandbox is forced unavailable (the `failIfUnavailable` analogue). **Pass = every sentinel read denied AND sandbox provably active AND forced-unavailable start fails closed.** (Commands written `~maintainer` — substitute the real account locally; generalize any checked-in capture per the plan sanitization checklist.)
- Record uid, groups, HOME, PATH, TMPDIR, CWD, tool versions, and the nono version into `dev_docs/elite-spike/fixtures/nono/env.txt`.
- Close each falsifier `confirmed` (adoption survives) / `falsified` / `inconclusive` against its `nono-evaluation.md` row — no fourth state. An inconclusive on F1, F2, or F5 is a **reject for this run** (they are parity/liveness claims). Write the three rows in `dev_docs/elite-spike/measurements.md`.

## Acceptance Criteria

- **User-run:** F1, F2, F5 each executed as described under `nono run` on the mac mini; the deny path verified for F2 and F5, not just the allow path; each falsifier closed terminal (`confirmed`/`falsified`/`inconclusive`) against its probe-spec row; evidence + fixture commands checked into `dev_docs/elite-spike/fixtures/nono/` and one row per falsifier in `dev_docs/elite-spike/measurements.md`, sanitized per the plan checklist.
- **Gate:** if any of F1/F2/F5 is `falsified` or load-bearing `inconclusive`, the measurement rows record REJECT, [[nono_eval_task_2]] does not run, and [[nono_eval_task_3]] applies the reject branch of the decision rule.
