# nono F1 — preliminary result (NOT the formal attended close)

**Status:** F1 (Claude Code runs headless through nono's proxy allowlist) —
**CONFIRMED, preliminary.** Allow and deny paths both verified.

## Caveats — why this is preliminary, not the formal probe close

- Run as the **maintainer uid** (`danielegan`) in a throwaway `~/nono-f1-scratch`,
  **not** the provisioned `agent` user. This does **not** test the two-uid
  boundary (that's F4 / the formal task-1 run under the agent identity).
- Used the maintainer's real Max OAuth (from the login Keychain). No `agent`
  identity, no test App, no spike repo yet.
- Pure API round-trip only. **F2** (git/`gh`/Linear write loop through the
  proxy) and the **formal F5** sentinel battery are still untested.
- nono `0.69.0`, macOS `26.4.1` (build `25E253`), `claude` arm64 standalone.

## Commands & evidence

Round-trip (the go/no-go):

```
cd ~/nono-f1-scratch
NONO_AUTO_MIGRATE=1 nono run --profile claude \
  --allow-domain api.anthropic.com --allow-domain github.com \
  --allow-domain api.linear.app --open-port 80 --open-port 443 \
  -- claude -p "Reply with exactly the word: PONG"
# -> "PONG", exit 0
```

Allow vs deny (proxy filter is default-deny):

```
nono run --profile claude --allow-domain api.anthropic.com --open-port 80 --open-port 443 \
  -- curl ... https://api.anthropic.com   # -> HTTP 404 (reachable; CONNECT tunnel works)
nono run --profile claude --allow-domain api.anthropic.com --open-port 80 --open-port 443 \
  -- curl ... https://example.com         # -> HTTP 000, curl exit 56 (blocked)
```

## Material findings that update the evaluation

1. **Claude Code requires nono's registry profile `nolabs-ai/claude`**
   (`nono pull nolabs-ai/claude`, v0.1.0, sig-verified). This is a
   **supply-chain input the real evaluation must review** — the profile is
   fetched from nono's registry, not authored by us. Without it, a raw
   allowlist run fails at auth ("Not logged in") because Claude's OAuth lives in
   the macOS login Keychain, which the base sandbox blocks
   (`mach-lookup com.apple.SecurityServer`).

2. **The profile grants `$HOME/Library/Keychains` (read+write) with a
   `bypass_protection` override.** This is the load-bearing finding for **F5**:
   to run Claude under nono, the sandbox must expose the **whole login Keychain**
   to the sandboxed child, not just Claude's item. The formal F5 battery must
   therefore test not only `~/.ssh` / `~/.aws` / `.gitconfig` file sentinels
   (which the profile's filesystem deny-list looks likely to block — home reads
   outside the grant list *were* denied, e.g. `~/src`, `~/src/dotfiles`) but
   specifically **whether the Keychain grant lets the agent read other apps'
   Keychain items** (SSH keys, other OAuth tokens, saved passwords). A broad
   Keychain grant could be an F5 failure even though file-path sentinels pass.

3. **Network filter behaves as documented.** Host-level `--allow-domain` is a
   CONNECT tunnel (end-to-end TLS, HTTP 404 proves reachability without MITM);
   non-allowlisted hosts are blocked at the proxy (curl 56). Confirms F1's
   network model and the CONNECT-vs-MITM seam the plan splits on.

## Next (formal, attended, under the agent identity)

- Task 1 F2: git clone/commit/push + `gh` PR + Linear R/W through the proxy,
  under the disposable test App, verifying the deny path.
- Task 1 F5: the sentinel battery **plus the Keychain-grant question in finding
  2** — this is now the sharpest F5 sub-test.
- Task 2 (F3/F4/F6): credential injection + the two-uid boundary + Anthropic
  MITM — all still open.
