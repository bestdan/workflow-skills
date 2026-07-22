# nono evaluation — measurement summary

One row per falsifier. Detail lives in `fixtures/nono/<file>`. All results so far
are **preliminary** (maintainer uid) or **agent-side uid-layer** — not the full
formal closes, which need the sandbox layer as agent and the disposable test App.

| Falsifier                                                     | Result                                                                       | Evidence                                                          | Decision                                                                             |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| **F1** Claude runs headless through nono's proxy              | **confirmed** (prelim)                                                       | `fixtures/nono/f1-preliminary.md`                                 | go/no-go green; proceed                                                              |
| **F2** git + `gh` honor the proxy under default-deny          | **confirmed** (prelim, tool-compat)                                          | `fixtures/nono/f2-toolcompat-preliminary.md`                      | allowlist needs `github.com` **+** `api.github.com`; write loop still needs test App |
| **F5** two-uid boundary (agent can't read maintainer secrets) | **confirmed** — uid + sandbox layers; gaps closed by `chmod 700 ~maintainer` | `fixtures/nono/f5-uid-boundary.md`                                | done; System keychain unreachable inside nono confirmed                              |
| **F5** keychain grant is keychain-wide, not item-scoped       | **confirmed** (prelim); contained by clean agent keychain                    | `fixtures/nono/f5-keychain-preliminary.md`, `step2-agent-auth.md` | agent login keychain verified to hold only its Claude token                          |
| **F6a** Claude works under Anthropic MITM                     | **confirmed** (prelim, audit-proven)                                         | `fixtures/nono/f6-anthropic-mitm-preliminary.md`                  | full-tier plausible                                                                  |
| **F6b** Max token actually injected/hidden                    | **untested**                                                                 | `fixtures/nono/f6-anthropic-mitm-preliminary.md`                  | needs nono Anthropic credential provider (task 2)                                    |
| **F3** github/linear creds injected, hidden from agent        | **untested**                                                                 | —                                                                 | needs test App + MITM config (task 2)                                                |
| **F4** injection is a boundary against the agent USER         | **untested**                                                                 | —                                                                 | needs credential injection set up (task 2)                                           |

## Supporting

- Agent identity provisioned (non-admin, `apagent`, zero sudo rules, 0700 home,
  minimal `~/Library`, no FDA granted): `provisioning.md`.
- Agent Claude Max auth (manual OAuth flow, agent keychain created+unlocked):
  `fixtures/nono/step2-agent-auth.md`.

## Security-review reframe (2026-07-22)

A separate code-level security review of nono (`tasks/elite_stage0_plan/nono_security_review.md`,
synthesized in `../nono-evaluation-key-points.md`) reshapes the verdict:

- **The two worst findings are Linux-only** (SR-1 UDP egress, SR-2 port-not-IP
  proxy scoping) — **do not affect the macOS mac mini.** Record macOS-only as a
  hard platform assumption.
- **SR-3 (L7 dot-segment traversal) caps credential injection.** nono's
  _host-level_ domain allowlist is sound (closes the `gh` hole ✓), but its
  _path-scoped_ endpoint policy is bypassable — so **§2.2's server-side GitHub
  ruleset must stay** as the real token bound on every adopt tier. Endpoint-scoped
  injection is not a boundary.
- **F5 keychain grant is a _confirmed_ regression** (review found it
  independently: any keychain-DB grant disables all keychain Mach denies).
- **`--trust-proxy-ca` residue confirmed empirically:** the extractable
  `nono-proxy-ca` CA private key + a `127.0.0.1` proxy token persisted in the
  login keychain and were **not** removed by `security delete-certificate` —
  deleted manually. Argues to use only the default **ephemeral** CA.
- **Supply chain (SR-4/SR-6):** `nono --profile` silently auto-pulls + runs
  wiring; pin the binary version and **vendor the reviewed profile** — don't
  auto-pull in the unattended substrate.

## Standing verdict

No falsifier has killed adoption, but the review **caps the ceiling**: nono is
**defense-in-depth on macOS, not a boundary to bet a secret on** (self-declared
alpha, active escape cadence). Realistic target is **ADOPT (network-only /
degraded)** — take the domain allowlist to close the `gh` hole and keep the agent
off the maintainer's files, while §2.2's server-side ruleset stays as the token
boundary. Full-tier credential injection (F3/F6b) is now _lower_ value: SR-3 makes
endpoint scoping unreliable and the alpha status argues against betting high-value
secrets on it. The two-uid boundary is fully closed (F5, both layers, post `chmod 700`).

**Decision locked (2026-07-22): ADOPT — defense-in-depth / network-only.** Design
edits applied (§3.2, Risk #2, Decision #1, §2.1; commit `7743516`). The injection
checkpoints (F3/F4/F6b) are **dropped** as low-value per the review. The only
remaining confirmable item is the **F2 write loop** (clone→push→PR through the
proxy under the disposable test App) — not tier-changing. Task 3's graduate-then-
delete cleanup waits on it.
