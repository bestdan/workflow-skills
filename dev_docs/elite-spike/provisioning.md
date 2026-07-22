# Stage-0 task 1 — agent identity provisioning (evidence)

Attended, user-run on the mac mini. Passwords entered interactively (never in
script/history). Paths generalized (`~maintainer` / `~agent`), hostname redacted.

## Commands (reproducible)

```
# 1. non-admin agent user (interactive password prompt via -password -)
sudo sysadminctl -addUser agent -fullName "Autopilot Agent" -password - -home ~agent -shell /bin/zsh
# 2. work root
sudo mkdir -p ~agent/work && sudo chown agent:staff ~agent/work && sudo chmod 700 ~agent/work
# 3. apagent group + membership
sudo dseditgroup -o create apagent
sudo dseditgroup -o edit -a agent -t user apagent
# 4. home dir fix (sysadminctl assigns but does not create the home; see note)
sudo createhomedir -c -u agent
sudo chown -R agent:staff ~agent
```

## Result — CONFIRMED (2026-07-22)

- `id agent` → `uid=502(agent) gid=20(staff) groups=20(staff),501(apagent),12(everyone),61(localaccounts),701(...sharepoint...),100(_lpoperator)` — **in `apagent`, NOT in `admin`.** ✓
- `UserShell: /bin/zsh` ✓
- `sudo -l -U agent` → "User agent is not allowed to run sudo on `<host>`." — **zero sudo rules.** ✓
- `autoLoginUser` → not configured (agent will not auto-login). ✓
- `~agent/work` → `drwx------ agent staff` (0700). ✓

## Notes / deviations

- **Home not auto-created; fixed with a minimal home (no Full Disk Access).**
  `sysadminctl` logged `Error:-14120 … Home directory is assigned (not created!)`
  — expected: it registers the record but does not build the home. `createhomedir`
  then repeatedly **no-op'd** (it skips a home dir that already exists — `~agent`
  existed root-owned from the earlier `mkdir`), and even `sudo rm -rf ~agent`
  returned `Operation not permitted` (macOS protects an existing account's home).
  Rather than grant the terminal Full Disk Access (too broad), the home was built
  minimally as root: `mkdir -p ~agent/Library/{Keychains,Preferences,Application
  Support,Caches}` + `chown -R agent:staff ~agent` + `chmod 700 ~agent`. Result:
  `~agent` is `drwx------ agent staff` (0700) with an agent-owned `~/Library` and
  its four subdirs. The login keychain is created on first keychain use (step 2).
  **No FDA / no TCC grants were made.**
- **Terminal admin prompt rejected.** During provisioning the terminal (Ghostty)
  was denied an "administer your computer" TCC prompt. The account was still
  created successfully; recorded in case it re-appears for `createhomedir` (if so,
  grant the terminal Full Disk Access and retry).

## Shared-writable scan (§3.1 "no shared writable directories")

`find /Users/Shared /Users/agent /Users/danielegan -type d \( -perm -0002 -o -perm -0020 \)`:

| Path | Mode | Owner | Disposition |
| --- | --- | --- | --- |
| `/Users/Shared` | `1777` (sticky) | root:wheel | macOS default; accepted. |
| `/Users/Shared/SC Info` | `0777` | ~maintainer:wheel | Apple Music/iTunes SC Info; not agent-used; accepted. |
| `~maintainer/Library/Messages` | `0777` | ~maintainer:staff | **World-writable in maintainer home.** TCC-protected, but a real POSIX finding. |
| `~maintainer/Library/com.apple.bluetooth.services.cloud` | `0770` | ~maintainer:staff | **staff-group-writable**; agent shares `staff` → reachable by POSIX. Apple-managed. |
| `~maintainer/Public/Drop Box` | `0733` | ~maintainer:staff | macOS default drop box; accepted. |

**Accepted-risk / follow-up:** `agent` and `maintainer` both default to primary
group `staff` (gid 20), so `staff`-group-writable dirs in the maintainer home are
POSIX-reachable by the agent. The clean containment (per §3.1 "no ACL leakage
from your home") is to make the maintainer home non-traversable by others —
`chmod go-rx ~maintainer` — which is exercised and recorded in the formal F5 /
sentinel test (that test plants a sentinel and drives every read to permission-
denied). Deferred to that test rather than applied blindly here.
