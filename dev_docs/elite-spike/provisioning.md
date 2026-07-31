# Stage-0 task 1 — agent identity provisioning (evidence)

Attended, user-run on the mac mini. Passwords entered interactively (never in
script/history). Paths generalized (`~maintainer` / `~agent`), hostname redacted.

> **RE-VERIFIED 2026-07-27 on a second host. The 2026-07-22 result below is mac
> mini evidence and no longer describes that machine.**
>
> On the mini the `agent` account later **vanished and uid 502 was reassigned to
> an unrelated human**, which made every claim below false of that host — while
> the document went on asserting them. Probe 5's containment rows depend on this
> probe, so the discrepancy mattered: a fixture pinning the _number_ rather than
> the name would have signalled a live user's processes.
>
> Re-verification is therefore **per-host, and this is a different host** (Daniel's
> MacBook Pro, macOS 26.4.1). Full current-state check in the section at the end.
> Result: every identity claim holds here, and the deferred containment item is
> **resolved rather than accepted** — see below.

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

| Path                                                     | Mode            | Owner             | Disposition                                                                         |
| -------------------------------------------------------- | --------------- | ----------------- | ----------------------------------------------------------------------------------- |
| `/Users/Shared`                                          | `1777` (sticky) | root:wheel        | macOS default; accepted.                                                            |
| `/Users/Shared/SC Info`                                  | `0777`          | ~maintainer:wheel | Apple Music/iTunes SC Info; not agent-used; accepted.                               |
| `~maintainer/Library/Messages`                           | `0777`          | ~maintainer:staff | **World-writable in maintainer home.** TCC-protected, but a real POSIX finding.     |
| `~maintainer/Library/com.apple.bluetooth.services.cloud` | `0770`          | ~maintainer:staff | **staff-group-writable**; agent shares `staff` → reachable by POSIX. Apple-managed. |
| `~maintainer/Public/Drop Box`                            | `0733`          | ~maintainer:staff | macOS default drop box; accepted.                                                   |

**Accepted-risk / follow-up:** `agent` and `maintainer` both default to primary
group `staff` (gid 20), so `staff`-group-writable dirs in the maintainer home are
POSIX-reachable by the agent. The clean containment (per §3.1 "no ACL leakage
from your home") is to make the maintainer home non-traversable by others —
`chmod go-rx ~maintainer` — which is exercised and recorded in the formal F5 /
sentinel test (that test plants a sentinel and drives every read to permission-
denied). Deferred to that test rather than applied blindly here.

---

## Re-verification — 2026-07-27, Daniel's MacBook Pro (macOS 26.4.1)

Maintainer `danielegan` uid 501. Every claim above re-checked live:

| Claim                     | Result                                                                                                                                                           |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id agent`                | `uid=502(agent) gid=20(staff) groups=20(staff),501(apagent),12(everyone),61(localaccounts),701(…sharepoint…),100(_lpoperator)` — in `apagent`, **not** `admin` ✓ |
| `dsmemberutil … -G admin` | _is not a member_ ✓                                                                                                                                              |
| `UserShell`               | `/bin/zsh` ✓                                                                                                                                                     |
| `sudo -l -U agent`        | _"User agent is not allowed to run sudo on Mac."_ — zero sudo rules ✓ (run attended by the maintainer, 2026-07-27)                                               |
| `autoLoginUser`           | domain/default pair does not exist ✓                                                                                                                             |
| `~agent`                  | `drwx------ agent:staff` ✓                                                                                                                                       |
| `~agent/work`             | `drwx------ agent:staff` ✓                                                                                                                                       |
| uid uniqueness (500–600)  | exactly `danielegan 501`, `agent 502` — no reassignment ✓                                                                                                        |

**`agent` is 502 on this host and was 503 on the mini.** That is not a discrepancy
to reconcile: the accounts were provisioned independently. Everything downstream
resolves the account **by name** (`pwd.getpwnam`), and pinning the number is the
mistake that produced the incident. Do not "fix" a uid difference between hosts.

### The accepted-risk item is resolved here, not accepted

The shared-writable scan finds **14** directories on this host versus 5 on the
mini (extra: `Library/Logs/DiagnosticReports`, `Library/Application Support/zoom.us`,
four `com.apple.bluetooth.services.cloud/CachedRecords/*`, and `…/Retired`).

That count is not the interesting part. **All but `/Users/Shared` and
`/Users/Shared/SC Info` are inside `/Users/danielegan`, which is `drwx------`** —
so the agent cannot traverse into any of them regardless of their own modes. The
mitigation the mini deferred to the F5 test (`chmod go-rx ~maintainer`) is already
in force on this machine, which converts the mini's accepted risk into a
non-issue here.

This is not a paper claim: Probe 5's `Writer` row exercised exactly this boundary
under the real uid domain and recorded `write_probe=EACCES`, and its whole
privileged-helper design exists _because_ the agent cannot reach the maintainer
home. Both primary groups are still `staff`, so the group-reachability condition
holds in principle — it is the 0700 home that defeats it, and if that mode is ever
relaxed the original risk returns.
