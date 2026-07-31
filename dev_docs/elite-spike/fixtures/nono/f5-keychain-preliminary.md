# nono F5 (Keychain scope) — preliminary result (NOT the formal attended close)

**Status:** partial F5 — **the nono `claude` profile's Keychain grant is
keychain-wide, not item-scoped. CONFIRMED (preliminary).** A sandboxed child
read a **non-Claude** secret from the login Keychain.

## Caveat

Run as **maintainer uid** (`danielegan`) against the maintainer's own login
Keychain — not the `agent` user. This tests the *scope of the grant*, which is
identity-independent; it does **not** test the formal two-uid setup.

## Experiment

```
# maintainer plants a throwaway canary (NOT Claude's item)
security add-generic-password -a nono-canary -s nono-keychain-canary -w "<canary>" -U

# inside the nono sandbox under the claude profile:
nono run --profile claude --allow-domain api.anthropic.com --open-port 80 --open-port 443 -- \
  sh -c 'security find-generic-password -s nono-keychain-canary -w'
# -> printed the canary value, exit 0   ← the sandboxed child read a non-Claude item

security delete-generic-password -s nono-keychain-canary   # cleaned up
```

## Finding

The `nolabs-ai/claude` profile grants `$HOME/Library/Keychains` (whole dir,
r+w, with `bypass_protection`) plus the `com.apple.SecurityServer` mach-lookup —
because Claude's OAuth lives in the login Keychain. That grant is **not scoped
to Claude's keychain item**: any login-Keychain secret whose ACL admits the
`security` tool is readable by the sandboxed agent. Keychain access under this
profile is all-or-nothing at the login-Keychain granularity.

## Disposition — contained by design, but adds a normative constraint

This is **not an F5 failure for the E-lite design as written**, because §3.1
runs the agent as a dedicated non-admin macOS user with its **own** login
Keychain. The blast radius is exactly the contents of *that* Keychain.

It becomes a **new normative constraint** the design must state:

> **The `agent` user's login Keychain must contain only the agent's own Claude
> Max OAuth credential — no maintainer, shared, or unrelated secrets.** Under
> nono's `claude` profile the sandboxed agent can read any item in its login
> Keychain, so the Keychain's contents *are* the trust boundary. The §2.3
> credential setup and the formal F5 run must verify the agent Keychain holds
> nothing else.

If we ever could not keep the agent Keychain clean (e.g. shared login), nono's
profile would need tightening to scope Keychain access to Claude's item — which
the current registry profile does not do.

## Feeds the formal F5 (under the agent identity)

- Confirm the `agent` login Keychain contains **only** the agent's Claude
  credential (enumerate it; fail if anything else is present).
- Re-run this canary test as the `agent` user to confirm the grant scope is the
  same there.
- Keep the file-path sentinels (`~maintainer/.ssh`, `.aws`, `.gitconfig`) —
  those looked blocked in the F1 run (home reads outside the grant list denied),
  but confirm formally.
