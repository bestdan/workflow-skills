# Step 2 — agent Claude Max auth (§2.3 canary) — result

**Status: CONFIRMED.** The agent authenticates to the Max subscription and runs;
its login keychain holds only its own token.

## §2.3 canary answers

1. **Auth method that worked:** the **manual browser flow** — copy the login URL
   into a browser, authenticate to Max, paste the returned code back. Plain
   `claude` login (not `setup-token`) succeeded this way. Awkward but functional
   for the headless account (no automatic browser/localhost-callback needed once
   the manual URL+code path is used).
2. **Auth functional:** `claude -p "…"` returned `PONG` as the `agent` user. ✓
3. **Prerequisite that mattered:** the agent had **no login keychain** (headless
   account). It was created + unlocked explicitly before auth:
   `security create-keychain -p <kc-pw> login.keychain-db` +
   `default-keychain -s` + `unlock-keychain`. Claude then stored its OAuth there
   (generic-password `svce="Claude Code-credentials"`, `acct=agent`).

## F5-relevant finding: the agent login keychain is clean

- `security find-generic-password -s "Claude Code-credentials"` → `acct=agent`
  (the agent's own token). ✓
- `security dump-keychain | grep svce` also listed `AirPort`, `BluetoothGlobal`,
  `BluetoothLE`, `MobileBluetooth`, `WiFiAnalytics`, `<NULL>`. **These are from the
  System keychain** (`/Library/Keychains/System.keychain`), which `dump-keychain`
  scans as part of the default search list — **not** the agent login keychain.
  They are metadata listings (names, not secret values).

**Why this satisfies F5:** the agent's *login* keychain (the only one nono's
`claude` profile grants — `~/Library/Keychains`) contains solely the Claude
token. The System keychain lives at `/Library/Keychains`, which the profile does
**not** grant, so it is unreachable inside the sandbox. The formal F5 must prove
this precisely, not via the broad `dump-keychain`:

- `security dump-keychain ~agent/Library/Keychains/login.keychain-db` (keychain
  named explicitly) → expect only `Claude Code-credentials`.
- Inside nono as agent: attempt to read a System-keychain item → expect denied
  (path not granted).

## Unattended-mode note (feeds §2.3 / §3.1 design)

For an unattended run the agent login keychain must be **unlocked
non-interactively** at run start (`security unlock-keychain -p <kc-pw>`), so the
keychain password becomes control-plane state the launcher must hold. Record this
as a design requirement, not solved in the spike.
