---
type: security-review
title: "Security review — nono (nolabs-ai/nono) agent sandbox"
status: complete
owner: bestdan
created: 2026-07-22
reviewer: claude-opus-4-8
target: https://github.com/nolabs-ai/nono
target_ref: main (cloned 2026-07-22)
method: 5-way parallel code review (sandbox enforcement, TLS-MITM/credential broker, L7 filter, supply-chain/trust, command broker) + installer fetch
context: adoption-decision review for running AI agents under nono
---

# Security review: nono (nolabs-ai/nono)

## Bottom line

nono is genuinely well-engineered for an alpha — the cryptographic and process-isolation
primitives are mostly done right. But it is **self-declared alpha, explicitly "not recommended
for production,"** and this review found **several reachable gaps in the core confinement
claims**, concentrated in Linux network isolation and the L7 filter. **macOS confinement is
noticeably stronger than Linux.** Whether these matter depends heavily on *how* it is used.

Findings are code-review results against current `main`, not weaponized exploits. A couple
(notably F1, Linux UDP) should be confirmed empirically on the target kernel before relying on
the opposite.

## Project self-assessment

`SECURITY.md` is unusually candid: *"currently in alpha… security issues are expected… Use in
production environments is not recommended at this stage,"* with a third-party audit promised
only *before* v1.0. The changelog corroborates an active escape-and-fix cadence:

- Published Linux sandbox-escape advisory **GHSA-27vp-2mmc-vmh3** (via D-Bus).
- AF_UNIX datagram network-bypass fix (#1096).
- Multiple path-traversal / symlink-escape fixes in the trust bundle code (#201, #1195, #1197, #2785).
- Documented child-process command-blocking bypass; `--net-block` bypass via proxy credential activation (#202).
- An explicit `unsafe_macos_seatbelt_rules` raw escape hatch.

Healthy disclosure track record — and evidence that escapes are still surfacing regularly.

## Highest-priority findings (reachable in realistic configs)

| # | Sev | Area | Issue | Locus |
|---|-----|------|-------|-------|
| F1 | **High** | Linux net | On modern kernels (Landlock ≥V4, Linux 6.7+), "network blocked"/proxy-only installs **no seccomp UDP filter** — Landlock has no UDP hook. Agent can do DNS exfil (UDP/53) and **full web/C2 over HTTP-3/QUIC (UDP/443)** while nono reports the network as locked. *Older* kernels are safer here. macOS unaffected. | `sandbox/linux.rs:586-635,918-926`; `execution_runtime.rs:577-583` |
| F2 | **High** | Linux net | Proxy-only TCP egress is enforced **by port only, not destination IP** (their own comment admits this). Agent knows the proxy port → raw TCP to `attacker:proxyport`, bypassing the credential proxy *and* the domain allowlist. macOS unaffected (Seatbelt scopes to localhost). | `sandbox/linux.rs:720-731`; `capability.rs:926-929` |
| F3 | **High** | L7 filter | `normalize_path` percent-decodes but **does not resolve `.`/`..` dot-segments**. Policy matches one path; upstream resolves another. `GET /gh/repos/…/issues/1/../../../../user/emails` matches an "issues-only" allow rule but hits `/user/emails` **with the injected token attached**. Deterministic on the reverse-proxy path. | `config.rs:1235-1254`; `reverse.rs:1843`; `handle.rs:1083` |
| F4 | **High** | Supply chain | `nono --profile ns/name` **silently auto-pulls and runs "wiring"** with no confirmation, and wiring has **no destination allowlist** — a signed pack can drop new executable hooks/symlinks anywhere under `$HOME` that run on the next agent/shell session. Transitive `extends` pulls are also silent. | `profile/mod.rs:2949-2962`; `wiring.rs:616-730` |
| F5 | **Med-High** | Linux | A `/proc/self` read grant (common) is **widened to all of `/proc`**, letting the agent read other processes' `cmdline`/`environ` — potentially the supervisor's `NONO_PROXY_TOKEN` and injected keys (gated by ptrace_scope, weak in many container setups). Contradicts the profile invariant at `policy.rs:2772`. | `capability.rs:1443-1457`; `exec_strategy.rs:858` |
| F6 | **Med** | Supply chain | Package identity is only "signer's GitHub org string == namespace." nono just renamed `always-further`→`nolabs-ai`; if a retired org name becomes reclaimable, stale lockfile/`extends` refs re-pull attacker-signed packs that verify cleanly (TOFU pinning doesn't help — same workflow path reproduces the pin). | `package_cmd.rs:1081-1140` |

## Config-gated / opt-in risks (only bite if the feature is enabled)

- **`--trust-proxy-ca`** stores the MITM CA private key in the login keychain as an *extractable*
  generic password and trusts it as a user root — keychain compromise → universal MITM of real
  traffic. Default ephemeral mode is clean (in-memory 24h key, never persisted, never in system
  store). `macos_trust.rs:113-183`; `ca.rs:176-195`.
- **OAuth-capture persistence** writes real access/refresh tokens to disk in **plaintext**
  (0600, 90-day TTL). Off by default. `oauth_capture/persist.rs:85-121`.
- **Argv deny-rule footgun:** `invocation_policy` deny rules using `prefix: ["push"]` are trivially
  evaded by `git -c x=y push` / `git --no-pager push` (prefix anchored at argv[0]; leading global
  flags slip past). Only `contains`-style matching is flag-robust. `tool-sandbox/policy.rs:414-437`.
- **`GIT_SSH_COMMAND`/`GIT_PAGER`/`PAGER`/`EDITOR`** absent from the dangerous-env denylist, so a
  profile with broad `allow_vars` on a credential-bearing git child yields arbitrary code execution
  + token exfil inside that child. `exec_strategy/env_sanitization.rs:14-53`.
- macOS: blanket `(allow mach-lookup)` with only a 5-service denylist (default-allow Mach IPC);
  any keychain-DB file grant disables *all* keychain Mach denies. `macos.rs:255-285,561-582`.
- Others (lower): `store_secret_file` symlink-follow (`keystore.rs:1044`); subject-name not bound
  to filename on pull (`package_cmd.rs:701-711`); `.`/`..` allowed in package components
  (`package.rs:306-317`); capability-elevation openat-notify symlink TOCTOU (opt-in,
  `supervisor_linux.rs:148`); IPv6 IMDS `fd00:ec2::254` / alternate-encoding metadata gaps
  (`net_filter.rs:79-102`).

## What held up well

- **Upstream TLS fully verified everywhere** — no `danger_accept_invalid_certs` in the tree; MITM
  intercepts only the agent side.
- **MITM CA never added to the system trust store** (user domain only); default CA ephemeral and zeroized.
- **Package signatures enforced, not advisory** — embedded/pinned Sigstore root (no
  bootstrap-from-server), digest-bound artifacts, keyed identities rejected, no
  auto-update-and-execute path.
- **Filesystem/Landlock confinement fails closed**, canonicalizes before checks, no reliance on
  user namespaces; DNS-rebinding/metadata SSRF largely handled (resolve-once-connect-to-IP,
  link-local blocks).
- Constant-time proxy-token comparison; secrets redacted in logs/Debug; strong broker shim auth
  (PID→path→inode); no command-injection-to-shell found; env passed to children is an allowlist.
- Installer verifies SHA-256 over HTTPS (but `SHA256SUMS.txt` is unsigned → protects transport,
  not a compromised release).

## Recommendation

- **Not for production or high-value credentials yet** — the project says so, and F1–F5 agree.
- **macOS convenience isolation** ("keep the agent in this repo, off my SSH keys"): reasonable —
  macOS filesystem confinement is solid and the default ephemeral-CA path is clean. Treat as
  defense-in-depth, not a boundary to bet a secret on.
- **Linux:** do not trust the network-isolation claim (F1, F2) without testing on the target
  kernel; assume a determined agent can still reach the network.
- **Pin exact profile versions; avoid silent auto-pull** (F4/F6): pre-pull known packs, review
  wiring, be wary of `extends` chains and the retired `always-further` namespace.
- Avoid `--trust-proxy-ca` and OAuth-capture persistence unless the tradeoffs are accepted.

## Follow-ups (optional)

- Empirically confirm F1 (UDP/QUIC egress under `--block-net` on Linux 6.7+) and F3
  (dot-segment traversal on the reverse-proxy path) with PoC tests.
- If confirmed, report via nono's private advisory process
  (`https://github.com/nolabs-ai/nono/security/advisories/new`) — do not open public issues.
