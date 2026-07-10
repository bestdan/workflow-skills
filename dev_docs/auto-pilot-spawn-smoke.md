---
title: Auto-pilot spawn jail — confinement smoke (run on macOS)
created: 2026-07-10
audience: whoever verifies PRE-484 before an unattended run is trusted
---

# Confinement smoke for `spawn-orchestrator.sh`

`scripts/check.sh` proves the jail is **generated correctly and compiles** — it
does **not** prove the jail **confines**. That can only be shown by launching it
for real and confirming each wall _refuses_ the disallowed action. Run this on
macOS. **PRE-484 is not done until every check below passes** (and the raw-socket
egress result decides one design question — see the end).

A "✗ denied" result is the **pass** for a confinement check: the cage is supposed
to refuse.

## 0. Build throwaway artifacts

```bash
cd "$(git rev-parse --show-toplevel)"
SO=scripts/spawn-orchestrator.sh
D="$(mktemp -d)"; D="$(cd "$D" && pwd -P)"
mkdir -p "$D/run/wt" "$D/creds"; echo secret > "$D/creds/token"

# Layer 1 (filesystem + exec), confined to the run root:
"$SO" render-profile --confine-under "$D/run" \
  --rw "$D/run/wt" --ro "$(git rev-parse --show-toplevel)" --ro "$HOME/.claude" \
  --exec "$(command -v claude)" --exec "$(command -v bash)" --exec "$(command -v git)" \
  --out "$D/profile.sb"
"$SO" check-profile "$D/profile.sb"

# Layer 2 (egress) — codex+github+anthropic, deny-by-default, no loopback bind:
"$SO" render-settings --source plan --coder codex --out "$D/settings.json"
```

## 1. Layer 1 — filesystem + exec (bare `sandbox-exec`)

Each command runs a single action **inside the seatbelt profile only**.

```bash
# WRITE outside the run worktree — must be DENIED:
sandbox-exec -f "$D/profile.sb" bash -c 'echo x > '"$HOME"'/SHOULD_NOT_EXIST' ; echo "rc=$?"
# WRITE inside the worktree — must SUCCEED (rc=0):
sandbox-exec -f "$D/profile.sb" bash -c 'echo x > '"$D"'/run/wt/ok' ; echo "rc=$?"
# READ a path outside repo+creds (another home file) — must be DENIED:
sandbox-exec -f "$D/profile.sb" bash -c 'cat /etc/sudoers' ; echo "rc=$?"
# EXEC a binary NOT on the list (e.g. /usr/bin/python3) — must be DENIED:
sandbox-exec -f "$D/profile.sb" /usr/bin/python3 -c 'print(1)' ; echo "rc=$?"
```

Expected: the write-inside is rc=0; the other three fail (non-zero / "not
permitted"). A write-outside or unlisted-exec that **succeeds** is a breach.

## 2. Layer 2 — network egress (through `claude --settings`)

Seatbelt does **not** filter by host, so egress is only enforced when the layer-2
settings are applied. Run a shell command _inside a real `claude -p`_ under the
wrapper, so both layers are active. **This is the load-bearing test.**

```bash
JSON="$(cat "$D/settings.json")"
runjail() { sandbox-exec -f "$D/profile.sb" claude -p "$1" \
  --permission-mode bypassPermissions --settings "$JSON" --max-turns 3 ; }

# (a) allowlisted host — should SUCCEED:
runjail 'Run this bash and report the exit code: curl -sS --max-time 8 -o /dev/null https://api.github.com; echo rc=$?'

# (b) non-allowlisted host by NAME — should be DENIED (rc≠0):
runjail 'Run this bash and report the exit code: curl -sS --max-time 8 -o /dev/null https://example.com; echo rc=$?'

# (c) *** raw IP, no DNS *** — the Fable #1 decider. Must be DENIED:
runjail 'Run this bash and report the exit code: curl -sS --max-time 8 -o /dev/null http://1.1.1.1; echo rc=$?'

# (d) *** raw TCP socket via /dev/tcp *** — must be DENIED:
runjail 'Run this bash and report the exit code: timeout 8 bash -c "exec 3<>/dev/tcp/1.1.1.1/80" ; echo rc=$?'
```

**Decision gate (hardening #1):**

- If (c) and (d) are **denied** → layer-2 `sandbox.network` catches raw sockets;
  the template's blanket `(allow network-outbound)` is fine. Done.
- If (c) or (d) **succeeds** → raw sockets bypass the host allowlist. Change
  `orchestrator.sb.tmpl` to **deny direct `network-outbound` and allow only the
  loopback proxy port**, record the layer-2 proxy port the launch must set, and
  re-run (c)/(d) until denied. Do not ship until raw egress is blocked.

## 3. Detach + supervisor lifecycle

```bash
echo 'echo alive; sleep 2' > "$D/run/wt/noop-prompt.txt"   # stand-in prompt
"$SO" write-launch --profile "$D/profile.sb" --settings "$D/settings.json" \
  --workdir "$D/run/wt" --log "$D/orch.log" --prompt-file "$D/run/wt/noop-prompt.txt" \
  --until "$(date -v+1H '+%FT%T' 2>/dev/null || echo T)" --label com.autopilot.smoke \
  --out-script "$D/launch.sh" --out-plist "$D/job.plist"
plutil -lint "$D/job.plist"
launchctl bootstrap "gui/$(id -u)" "$D/job.plist"
launchctl print "gui/$(id -u)/com.autopilot.smoke" | grep -E 'state|pid'   # loaded + a pid
"$SO" teardown --label com.autopilot.smoke
launchctl print "gui/$(id -u)/com.autopilot.smoke" 2>&1 | tail -1          # should be "Could not find"
```

## Sign-off

- [ ] §1 write-outside DENIED, read-outside DENIED, unlisted-exec DENIED, write-inside OK
- [ ] §2(a) allowlisted host OK
- [ ] §2(b) non-allowlisted host DENIED
- [ ] §2(c) raw IP DENIED **and** §2(d) raw socket DENIED — or the layer-1 fix applied and re-verified
- [ ] §3 job loads with a pid and `teardown` removes it

Clean up: `rm -rf "$D"` (and `launchctl bootout` the label if you skipped §3's teardown).
