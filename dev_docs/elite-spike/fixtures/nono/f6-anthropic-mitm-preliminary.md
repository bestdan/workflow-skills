# nono F6 (Anthropic MITM / hide the Max token) — preliminary (NOT a formal close)

**Status, split:**
- **F6a — Claude Code works under nono MITM of `api.anthropic.com`: CONFIRMED
  (preliminary), proven by the audit log.**
- **F6b — the Max token actually injected / hidden from the agent: NOT TESTED.**
  The audit shows `managed_credential_active: false` — Claude used its own
  keychain OAuth; nono did not inject. Full-tier adoption depends on F6b, still open.

## Caveat

Maintainer uid, throwaway dir. `--trust-proxy-ca` was used (adds nono's
ephemeral daily CA to the macOS user trust store — removed in cleanup, see below).

## How MITM was proven (not assumed)

Path-scoped allow forces layer-7 interception:

```
nono run --profile claude --allow-cwd --trust-proxy-ca \
  --allow-domain 'https://api.anthropic.com/**' --open-port 80 --open-port 443 \
  -- claude -p "Reply with exactly the word: PONG"     # -> PONG, exit 0
```

The audit trail (`~/.local/state/nono/audit/<id>/audit-events.ndjson`,
hash-chained/tamper-evident) logged **decrypted HTTP request lines** to
`api.anthropic.com` — only possible if the proxy terminated TLS:

```
mode=connect_intercept  POST /v1/messages?beta=true                 status 200
mode=connect_intercept  GET  /api/oauth/account/settings
mode=connect_intercept  GET  /api/claude_cli/bootstrap?...&model=claude-opus-4-8
mode=connect_intercept  GET  /v1/mcp_servers?limit=1000
mode=connect_intercept  POST /api/event_logging/v2/batch            status 200
```

A CONNECT tunnel logs only `method=CONNECT, path=null`. The presence of decrypted
paths = real MITM, and Claude completed the request (200) under it. A discriminator
run without `--trust-proxy-ca` was **inconclusive** (the flag shares the CA across
sessions via Keychain for the day, so the CA was already trusted) — the audit log,
not that run, is the proof.

## Findings

1. **F6a confirmed:** Claude tolerates nono MITM of Anthropic. This was the
   uncertain prerequisite (a pinned/embedded CA bundle could have refused nono's
   leaf); it did not. So hiding the Max token is *possible in principle*.
2. **F6b open:** injection was not exercised (`managed_credential_active: false`).
   Closing F6b needs nono to inject the Anthropic OAuth as a managed credential
   **and** Claude to run with keychain auth denied — non-trivial, because Claude's
   auth is an OAuth bearer+refresh in the Keychain, not a simple static header.
   Whether nono ships an Anthropic credential provider that satisfies Claude's
   OAuth flow is the task-2 question.
3. **Observability bonus:** under MITM the audit log captures every Anthropic API
   call path — a real supervision signal the raw-Seatbelt design lacks.
4. **Telemetry denial seen:** `http-intake.logs.us5.datadoghq.com:443` blocked by
   the allowlist (Claude Code Datadog logs) — benign; confirms default-deny bites
   real egress.

## Tier implication

- Full-tier adoption (Max token hidden too) is now **plausible but unproven** —
  gated on F6b.
- Degraded-tier (github/linear injected, Max token stays agent-readable) remains
  the safe floor and does not depend on F6b.

## Feeds task 2

- Does nono have an Anthropic credential provider? Can Claude run with
  `--credential` injecting the bearer while keychain auth is denied?
- If yes → F6b path to full-tier. If no → degraded-tier is the ceiling for the
  Max token, and Decision #1 narrows rather than closes.
