# nono F2 (tool proxy-compat) — preliminary result (NOT the formal attended close)

**Status:** F2 tool-compat — **git and `gh` both honor nono's proxy and the
default-deny allowlist. CONFIRMED (preliminary).** The authenticated write loop
(push, PR create) is **still untested** — needs the disposable test App.

## Caveat

Run as **maintainer uid**, unauthenticated `git clone` + read-only `gh api`. No
`agent` user, no test App, no push/PR. This confirms the *transport* (tools work
behind the proxy), not the *credential* path (task 2 / formal F2).

## Experiments & results

```
# git clone, public repo, github.com allowlisted
nono run --profile claude --allow-cwd --allow-domain github.com --allow-domain api.anthropic.com \
  --open-port 80 --open-port 443 -- git clone --depth 1 https://github.com/octocat/Hello-World.git
# -> exit 0, README present. codeload.github.com NOT needed for smart-HTTP shallow clone.

# gh api WITHOUT api.github.com allowlisted
... --allow-domain github.com ... -- gh api /repos/octocat/Hello-World
# -> Forbidden: host api.github.com:443 is not in the allowlist   (proxy default-denies; gh honors proxy)

# gh api WITH api.github.com allowlisted
... --allow-domain github.com --allow-domain api.github.com ... -- gh api /repos/octocat/Hello-World --jq .full_name
# -> octocat/Hello-World
```

## Findings

1. **Both git and `gh` honor `HTTP(S)_PROXY`** (nono sets them) and the proxy
   enforces default-deny — the explicit "not in the allowlist" rejection is
   proof, not just a timeout.
2. **Allowlist requires two GitHub hosts, not one:** `github.com` (git
   clone/push transport) **and** `api.github.com` (the `gh` REST/GraphQL API).
   The design §3.2 allowlist currently names `github.com` only. *(nono-conditional:
   the raw-Seatbelt design runs `gh` outside the sandbox — the "gh hole" — so
   `api.github.com` is only needed if nono brings `gh` inside the allowlist,
   which is exactly the Risk #2 closure nono offers.)* `codeload.github.com` was
   **not** needed for a shallow clone; confirm whether full fetches/LFS need it.
3. **`gh` (a Go tool) worked without `--trust-proxy-ca`** — re-confirms host-level
   allows are CONNECT tunnels (end-to-end TLS, no MITM). `--trust-proxy-ca` is
   only for credential injection / path-scoped MITM (task 2).
4. Benign filesystem denials seen: my global git hooks (`hooksPath` → dotfiles)
   and mise-managed `gh`/`git` binary paths. The former is **moot for the agent**
   (§2.3 sets agent `hooksPath=/dev/null`); the latter is a maintainer-env quirk
   (agent uses shared Homebrew per §3.1). Neither is a nono problem.

## Feeds the formal F2 (under the agent identity, with the test App)

- Full write loop: clone → commit → **push** a `bestdan/ap/**` branch → `gh` PR
  open+comment+close → Linear R/W, all through the proxy, allow + deny paths.
- Use the two-host allowlist (`github.com` + `api.github.com`) + `api.linear.app`.
- Confirm whether full fetch / LFS pulls need `codeload.github.com`.
