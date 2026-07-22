# nono evaluation — measurement summary

One row per falsifier. Detail lives in `fixtures/nono/<file>`. All results so far
are **preliminary** (maintainer uid) or **agent-side uid-layer** — not the full
formal closes, which need the sandbox layer as agent and the disposable test App.

| Falsifier | Result | Evidence | Decision |
| --- | --- | --- | --- |
| **F1** Claude runs headless through nono's proxy | **confirmed** (prelim) | `fixtures/nono/f1-preliminary.md` | go/no-go green; proceed |
| **F2** git + `gh` honor the proxy under default-deny | **confirmed** (prelim, tool-compat) | `fixtures/nono/f2-toolcompat-preliminary.md` | allowlist needs `github.com` **+** `api.github.com`; write loop still needs test App |
| **F5** two-uid boundary (agent can't read maintainer secrets) | **confirmed for secrets** (agent-side); 2 non-secret gaps | `fixtures/nono/f5-uid-boundary.md` | harden with `chmod 700 ~maintainer`; sandbox layer pending |
| **F5** keychain grant is keychain-wide, not item-scoped | **confirmed** (prelim); contained by clean agent keychain | `fixtures/nono/f5-keychain-preliminary.md`, `step2-agent-auth.md` | agent login keychain verified to hold only its Claude token |
| **F6a** Claude works under Anthropic MITM | **confirmed** (prelim, audit-proven) | `fixtures/nono/f6-anthropic-mitm-preliminary.md` | full-tier plausible |
| **F6b** Max token actually injected/hidden | **untested** | `fixtures/nono/f6-anthropic-mitm-preliminary.md` | needs nono Anthropic credential provider (task 2) |
| **F3** github/linear creds injected, hidden from agent | **untested** | — | needs test App + MITM config (task 2) |
| **F4** injection is a boundary against the agent USER | **untested** | — | needs credential injection set up (task 2) |

## Supporting

- Agent identity provisioned (non-admin, `apagent`, zero sudo rules, 0700 home,
  minimal `~/Library`, no FDA granted): `provisioning.md`.
- Agent Claude Max auth (manual OAuth flow, agent keychain created+unlocked):
  `fixtures/nono/step2-agent-auth.md`.

## Standing verdict

No falsifier has killed adoption. The evaluation trends **adopt** — at least the
degraded tier (network allowlist closes the `gh` hole; github/linear creds
hideable) — with full-tier (Max token hidden too) plausible pending F6b. The
two-uid boundary holds for secrets given the `chmod 700 ~maintainer` hardening.
Remaining work (F3/F4/F6b, sandbox-layer F5, F2 write loop) needs the disposable
test App and the credential-injection setup — task 2 of `nono_eval_plan`.
