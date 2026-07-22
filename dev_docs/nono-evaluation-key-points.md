---
type: analysis
title: "nono evaluation — key points from the code-level security review"
status: active
owner: bestdan
created: 2026-07-22
sources:
  - ./nono-evaluation.md
  - ./tasks/elite_stage0_plan/nono_security_review.md
  - ./auto-pilot-e-lite-design-2026-07-21.md
---

# nono evaluation — key points (from the security review)

Folds the code-level [security review](./tasks/elite_stage0_plan/nono_security_review.md)
into the [falsification framework](./nono-evaluation.md) and the
[E-lite design](./auto-pilot-e-lite-design-2026-07-21.md). It does **not** replace either —
it says what the review changes about the eval's tests and the design's decision rule.

## Notation

- **Eval falsifiers** F1–F6 = [nono-evaluation.md §4](./nono-evaluation.md).
- **Review findings** are renumbered **SR-1…SR-6** here to avoid colliding with the eval's
  F-numbers. Mapping to the review doc's own headings:
  - SR-1 = review F1 (Linux, UDP egress unrestricted)
  - SR-2 = review F2 (Linux, proxy egress port-only not IP-scoped)
  - SR-3 = review F3 (L7 `normalize_path` dot-segment traversal)
  - SR-4 = review F4 (silent auto-pull runs wiring)
  - SR-5 = review F5 (Linux, `/proc` widening exposes supervisor env)
  - SR-6 = review F6 (namespace-hijack via org rename)

## Reframe

nono is a candidate for exactly two jobs in the design: the **domain/endpoint filter that
closes the `gh` hole** ([design §3.2](./auto-pilot-e-lite-design-2026-07-21.md), Risk #2) and
**credential-hiding**. The review hits both — but the two highest-severity findings turn out
not to apply to the substrate.

## 1. Platform scoping — the two worst findings are Linux-only (good news for E-lite)

SR-1 (UDP egress unrestricted under Landlock ≥V4) and SR-2 (proxy egress enforced by **port
only**, not destination IP) are **Linux-specific**. The E-lite substrate is a **macOS mac
mini**, where Seatbelt scopes egress to `remote tcp "localhost:PORT"` — neither applies. The
eval's F1/F2/F5 liveness tests run on macOS and should still pass.

**Caveat to record as an eval non-goal:** this holds only while *every* execution path is
macOS. If any worker backend runs `nono` on Linux ≥6.7 (a container, CI runner, devcontainer),
the "network is contained" claim silently breaks (SR-1: full HTTP/3-over-UDP egress; SR-2:
raw TCP to any host on the proxy port). Pin the platform assumption explicitly.

## 2. SR-3 (L7 path traversal) — this changes the decision rule

**The one finding that alters an ADOPT outcome.** `normalize_path` percent-decodes but does
**not** resolve `.`/`..` dot-segments, so the **path-scoped endpoint policy** (globs like
`/repos/nolabs-ai/nono/issues/**`) is bypassable — deterministically on the reverse-proxy
path, with the injected token attached. Consequences for the eval and design:

- **Host-level** domain allowlisting (reaches `github.com` vs `evil.com`) is sound.
  **Path/method-level** endpoint policy is **not** a reliable boundary.
- A full **ADOPT** must **not** retire [§2.2](./auto-pilot-e-lite-design-2026-07-21.md)'s
  server-side GitHub ruleset in favour of nono's L7 path filter. Keep the ruleset as the real
  bound on *what the token can do*; treat nono's endpoint policy as defense-in-depth only.
- **Add a `..`/dot-segment traversal case to F2/F3's deny-path battery.** The eval already
  says "verify the deny path, don't just verify the allow path" — this is a concrete deny case
  it currently omits.

## 3. Keychain regression — the eval's F5 is corroborated, not preliminary

[f5-keychain-preliminary.md](./elite-spike/fixtures/nono/f5-keychain-preliminary.md) ("the
`claude` profile's Keychain grant is keychain-wide; a sandboxed child read a non-Claude
secret") is the **same defect** the review found independently, and the review supplies the
mechanism: **any keychain-DB file grant disables *all* keychain Mach-service denies**, plus a
blanket `(allow mach-lookup)`. So F5 ("containment does not regress below raw Seatbelt") is a
**confirmed regression**: headless Claude auth needs keychain access, and nono's stock profile
grants it wholesale where raw Seatbelt could deny it.

- Keep the login-keychain sentinel in probe 1
  ([elite_stage0_task_3](./tasks/elite_stage0_plan/elite_stage0_task_3.md)), and test it **with
  the Claude keychain grant active** — that is the config that ships, and the one that leaks.

## 4. Supply chain — a dimension the eval doesn't cover, and it matters more unattended

The eval scope is containment + injection, explicitly not supply chain. But E-lite is
**unattended**, and two findings are unattended-relevant:

- **SR-4:** `nono --profile ns/name` **silently auto-pulls and runs "wiring"** (can drop
  executable hooks under `$HOME`) with no confirmation — bad on a headless box. Transitive
  `extends` pulls are also silent.
- **SR-6:** the `always-further`→`nolabs-ai` rename is a live **namespace-hijack** surface, and
  the design docs still reference both namespaces.

**Recommendation (new eval assumption / Stage-2 note):** pin a nono **binary version** and
**vendor/pin the profile** as reviewed, checked-in config; do not rely on live registry
auto-pull in the unattended substrate. Treat profile provenance as part of the supply chain.

## 5. Cross-uid boundary (eval F4) — mixed

The command-broker review is mildly **supportive** of F4 (shim auth is strong: PID→path→inode;
child env is an allowlist; no same-uid supervisor impersonation found). But:

- On **Linux**, SR-5 (`/proc` widening exposes the supervisor's `NONO_PROXY_TOKEN` and injected
  keys) would **falsify F4** — again Linux-only, so out of scope for the mac mini.
- On **macOS**, if `--trust-proxy-ca` persistent mode is ever used, the CA private key is stored
  **extractable** in the login keychain, so a same-uid agent can read it. **F4's test should
  include "can the agent uid extract the persistent CA key."** (The default ephemeral CA is
  in-memory only and clean.)

## 6. Housekeeping

- **Naming collision:** review F1–F6 ≠ eval F1–F6 (see the SR-N mapping above). Use SR-N when
  referencing the review from eval material.
- **Alpha / versioning:** nono is self-declared alpha ("not recommended for production") with a
  live escape-and-fix cadence (published Linux escape advisory GHSA-27vp-2mmc-vmh3, AF_UNIX
  bypass fix, several trust-path traversal fixes). A "confirmed" F-result is **version-specific**
  — pin the version the spike blessed and re-review on bump.

## Proposed edits to nono-evaluation.md (not yet applied)

1. F2/F3 deny-path battery: add a `..`/dot-segment traversal case (SR-3).
2. F4 test: add "agent uid cannot extract the persistent CA key from the keychain" (SR-1 review).
3. §2 non-goals: add the macOS-only platform assumption (SR-1/SR-2) and the supply-chain /
   version-pinning assumption (SR-4/SR-6).
4. §6 decision rule: on ADOPT, keep §2.2's server-side ruleset — nono's L7 path filter is
   defense-in-depth, not a replacement boundary (SR-3).
5. Cross-link the [security review](./tasks/elite_stage0_plan/nono_security_review.md) from the
   eval's sources list.
