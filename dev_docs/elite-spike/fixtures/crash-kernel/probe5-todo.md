# Probe 5 — remaining work to close the probe

Probe 5 is **INCONCLUSIVE** ([`probe5-crash-kernel.md`](./probe5-crash-kernel.md)).
The transaction kernel held across every injection; the **containment half was
never exercised** because the dedicated `agent` uid is absent from the host the
2026-07-23 run used. This file is the resume point.

**Written to be machine-portable.** Nothing below assumes the mac mini. If you
pick this up on a different Mac, start at Part A — the host prerequisites are the
whole reason the probe is open, and no evidence from the original host carries
over.

---

## Status

**Updated 2026-07-27.** Two things changed since this file was written on 07-23:
**Part B is done** (landed in the `c6cf804` WIP checkpoint, after this file was
drafted), and an **incident** wiped the Part A surface off the host — see
[`dev_docs/tasks/probe5-incident-evidence/`](../../../tasks/probe5-incident-evidence/).
A supervisor was bootstrapped in uid mode against the *maintainer's* uid and
reaped every SSH login for four days. Read the safety notes in Part A before
provisioning anything.

| | |
| --- | --- |
| **Done** | v5.1 fixture built; 19 rows PASS; v5.2 reconcile defect found and fixed |
| **Done (Part B, `c6cf804`)** | sudo-mediated spawn/reap/measure, `takeover_publish`, `--try-write-db`, and **all five blocked rows promoted** — `BLOCKED` now holds only `PL` |
| **Done (`396bd02`)** | fail-closed reaper guards; verified empirically on this host 07-27 |
| **Blocked on host** | Esc, Churn, Writer, Uid, Tw — all now need **only** Part A |
| **Inconclusive, needs a harness** | Io (real ENOSPC/EIO), PL (power-loss) |
| **Established** | invariants 1, 2, 3, 7 |
| **Not established** | invariants 4, 5, 6 (containment), 8 (enforced sole-writer) |

### The remaining sequence

Part A (attended, below) → run the matrix in uid mode → Part D → Part E.
**Part B is no longer work.**

---

## Part A — host prerequisites (attended, one time per machine)

Every command here is entered by a human in a real terminal. An agent session has
no tty for the password prompt.

> **On the mac mini as of 2026-07-27:** A1–A3 are **already satisfied** — `agent`
> exists at **uid 503** (not 502, which is a live human account), in `staff` +
> `apagent`, not `admin`. A4–A7 are **not**: the incident cleanup removed
> `/usr/local/probe5` and `/etc/sudoers.d/probe5` entirely. Start at A4.
>
> **The old `/usr/local/probe5` was owned by `danielegan`, not `root`**
> (`privileged-surface.txt`) — the `chown root:wheel` below was skipped on the
> first pass, so the trust boundary the fixture documents was never actually
> there. A7 now verifies ownership rather than assuming it.
>
> **Never pin the domain to a number.** Resolve `agent` by name; the fixture does
> (`scenarios._dedicated_agent_uid`), and `reaper.Domain` independently refuses
> `agent_uid ∈ {0, caller's uid}`.

Set these once per shell; everything below uses them.

```sh
export P5_MAINTAINER="$(id -un)"
export P5_PY="$(command -v python3.12 || echo /opt/homebrew/opt/python@3.12/bin/python3.12)"
export P5_FIXTURE="<repo>/dev_docs/elite-spike/fixtures/crash-kernel"
```

`P5_PY` **must not** be Apple's system Python — its SQLite is Apple's build, whose
`F_FULLFSYNC` handling is not a stable public contract (see `prior-art-research.md`
§1). `kernel.assert_stock_sqlite()` fails closed on it. Verify:

```sh
"$P5_PY" -c "import sqlite3,sys;print(sys.executable, sqlite3.sqlite_version)"
```

### A1 — pick a free uid

```sh
dscl . -list /Users UniqueID | awk '$2>500 && $2<600 {print}' | sort -k2 -n
```

Take the lowest unused number ≥503 as `<P5UID>`.

**Do not assume 502.** On the original host `agent` held 502, the account later
vanished, and 502 was reassigned to an unrelated human. Since the containment
primitive is `kill(-1)` *as that uid*, a fixture pinned to the number rather than
the name would have signalled every process of a live user's account. Pin by name
and record the uid you actually get.

### A2 — create the account

Do **not** create the home directory first. `sysadminctl` registers the record but
does not build the home, and `createhomedir` silently no-ops on a directory that
already exists — after which macOS refuses to `rm -rf` it. That sequence wedged
the original provisioning (`provisioning.md` § Notes).

```sh
sudo sysadminctl -addUser agent -fullName "Autopilot Agent" -UID <P5UID> \
     -password - -home /Users/agent -shell /bin/zsh
sudo createhomedir -c -u agent
sudo chown -R agent:staff /Users/agent && sudo chmod 700 /Users/agent
sudo mkdir -p /Users/agent/work && sudo chown agent:staff /Users/agent/work
sudo chmod 700 /Users/agent/work
sudo dseditgroup -o create apagent
sudo dseditgroup -o edit -a agent -t user apagent
```

If `createhomedir` trips a TCC "administer your computer" prompt, grant the
terminal Full Disk Access and retry.

### A3 — the agent checks

All of these must pass before any row is worth running.

```sh
echo "== identity ==";       id agent
echo "== shell/home ==";     dscl . -read /Users/agent UserShell NFSHomeDirectory
echo "== zero sudo ==";      sudo -l -U agent
echo "== not admin ==";      dsmemberutil checkmembership -U agent -G admin
echo "== home perms ==";     ls -ld /Users/agent /Users/agent/work
echo "== no autologin ==";   sudo defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>&1
echo "== uid unique ==";     dscl . -list /Users UniqueID | awk '$2>500 && $2<600'
```

Expected: member of `apagent` and `staff`, **not** `admin`; `UserShell: /bin/zsh`;
`sudo -l -U agent` → *"User agent is not allowed to run sudo"*; membership check →
*is not a member*; both dirs `drwx------ agent staff`; autologin → *does not
exist*; one account per uid.

### A4 — privileged spawn/reap/measure helpers

The supervisor runs as the maintainer and cannot spawn-as, signal, or *measure*
another uid, so all three go through a scoped `sudo -u agent` helper. The helpers
must be **root-owned and not agent-writable**, or the agent could rewrite what the
maintainer is about to execute.

**There are THREE helpers, not two.** Earlier drafts of this file listed only
`p5-spawn` and `p5-reap`; `supervisor.measure_run` also calls
`reaper.MEASURE_HELPER` (`/usr/local/probe5/p5-measure`). `proc_pidinfo` is EPERM
across uids for a non-root caller, so without `p5-measure` the supervisor reads
**every** healthy agent-uid run as dead and reaps it — the same false-reap class
as the v5.2 defect, by another route. It fails *closed*, so the symptom is
"nothing ever adopts", not an obvious error.

```sh
sudo mkdir -p /usr/local/probe5 && sudo chmod 755 /usr/local/probe5

sudo tee /usr/local/probe5/p5-reap >/dev/null <<'SH'
#!/bin/sh
# Runs AS the agent uid. kill(-1) from a non-root uid reaches only that uid.
set -u
[ "$(id -u)" -eq 0 ] && { echo "refusing to run as root" >&2; exit 64; }
case "${1:-}" in TERM|KILL) ;; *) echo "usage: p5-reap TERM|KILL" >&2; exit 64 ;; esac
/bin/kill -s "$1" -1 2>/dev/null
exit 0
SH

sudo tee /usr/local/probe5/p5-spawn >/dev/null <<'SH'
#!/bin/sh
# Runs AS the agent uid. Execs the fixture surrogate, keeping fds 3 and 4.
set -u
exec /opt/homebrew/opt/python@3.12/bin/python3.12 \
     /usr/local/probe5/runsurrogate.py "$@"
SH

sudo tee /usr/local/probe5/p5-measure >/dev/null <<'SH'
#!/bin/sh
# Runs AS the agent uid. Prints one JSON incarnation record for <pid> on stdout.
# The supervisor parses stdout and FAILS CLOSED on anything unparsable, so this
# must emit nothing but that JSON.
set -u
exec /opt/homebrew/opt/python@3.12/bin/python3.12 \
     /usr/local/probe5/incarnation.py "$@"
SH

sudo chown root:wheel /usr/local/probe5/p5-reap /usr/local/probe5/p5-spawn \
                      /usr/local/probe5/p5-measure
sudo chmod 755 /usr/local/probe5/p5-reap /usr/local/probe5/p5-spawn \
               /usr/local/probe5/p5-measure
```

Adjust the interpreter path in `p5-spawn` and `p5-measure` to match `$P5_PY` on
this machine.

### A5 — stage the surrogate somewhere the agent can read

The fixture lives under the maintainer's `0700` home, which the agent cannot
traverse — that is deliberate and is what makes the `Writer` row meaningful. Copy
only the surrogate out, into the **root-owned** staging dir:

Two files, not one: `p5-measure` runs `incarnation.py`, which lives under the same
unreadable home.

```sh
sudo cp "$P5_FIXTURE/runsurrogate.py" "$P5_FIXTURE/incarnation.py" /usr/local/probe5/
sudo chown root:wheel /usr/local/probe5/runsurrogate.py /usr/local/probe5/incarnation.py
sudo chmod 755 /usr/local/probe5/runsurrogate.py /usr/local/probe5/incarnation.py
```

These are **copies**. Any later edit to `runsurrogate.py` or `incarnation.py` in
the fixture must be re-staged, or the uid-mode rows silently run the stale copy.

Prefer this over the `/Users/Shared/p5` (`1777`) staging the fixture brief
suggested: a world-writable directory on a path invoked through `sudo` lets any
local account swap the executable. The escalation would only be maintainer→agent,
but there is no reason to accept it when a root-owned directory works.

### A6 — sudoers

`sudo` closes every fd ≥3 by default, which would destroy the inherited report
pipe and start gate the design depends on. `closefrom_override` preserves them.

All three helpers must be listed. `p5-measure` is invoked with `sudo -n` (no
password prompt possible); if it is missing from the alias, every measurement
returns unparsable output and the supervisor fails closed — reaping healthy runs.

```sh
sudo tee /etc/sudoers.d/probe5 >/dev/null <<'EOF'
Cmnd_Alias P5CMDS = /usr/local/probe5/p5-spawn, /usr/local/probe5/p5-reap, \
                    /usr/local/probe5/p5-measure
Defaults!P5CMDS closefrom_override
<P5_MAINTAINER> ALL=(agent) NOPASSWD: P5CMDS
EOF
sudo chmod 440 /etc/sudoers.d/probe5
sudo visudo -c -f /etc/sudoers.d/probe5
```

Substitute the real username for `<P5_MAINTAINER>`. The runas is `(agent)`, not
`(ALL)` — this entry cannot be used to become root.

### A7 — verify the plumbing

Ownership first — this is the check that was skipped last time. Every path must
read `root wheel`; anything owned by the maintainer means the agent-side trust
boundary does not exist and no containment row is worth running.

```sh
echo "== helpers are root-owned =="; ls -l /usr/local/probe5/
echo "== dir is root-owned ==";      ls -ld /usr/local/probe5
echo "== sudoers frag ==";           sudo ls -l /etc/sudoers.d/probe5
echo "== nothing agent-writable =="; sudo -u agent test -w /usr/local/probe5 && \
     echo "FAIL: agent can write the helper dir" || echo "ok: not agent-writable"
echo "== measure works ==";          sudo -n -u agent /usr/local/probe5/p5-measure $$
```

The last must print **one line of JSON and nothing else** — the supervisor parses
stdout and treats any noise as "not alive", which routes to reap.

The next line is the **first live firing of the uid-wide kill**. It reaps
everything owned by `agent`, so run it only when no agent work is in flight (at
this point that is just `cfprefsd`/`distnoted`, which respawn).

```sh
echo "== reap runs as agent ==";  sudo -u agent /usr/local/probe5/p5-reap TERM; echo "rc=$?"
echo "== root path refused ==";   sudo /usr/local/probe5/p5-reap TERM; echo "rc=$? (expect 64)"
echo "== fd 3 survives sudo ==";  sudo -n -C 5 -u agent /usr/local/probe5/p5-measure $$ >/dev/null; echo "rc=$? (expect 0)"
echo "== agent cannot read fixture =="; ls -ld "$HOME"   # 0700 => agent cannot traverse
```

**Test `-C 5` on a command that is inside `P5CMDS`.** `Defaults!P5CMDS
closefrom_override` scopes the privilege to those three commands, so
`sudo -C 5 -u agent /bin/sh …` (an earlier version of this check) fails with
*"you are not permitted to use the -C option"* even when A6 is perfectly correct —
and sends you off to fix a working sudoers file. Only conclude
`closefrom_override` did not take if `-C 5` is refused on `p5-measure`/`p5-spawn`
itself; then fix A6, or fall back to the socket design in Part B note 3 and record
it as a deviation from the draft.

The other checks not to misread: `sudo -u agent <anything not in P5CMDS>` prompts
for a password and, non-interactively, just fails — so it proves nothing about the
agent's own permissions. Read those off the filesystem instead (a `0700` home the
agent cannot traverse; a root-owned `755` helper dir it cannot write). Likewise
`sudo /usr/local/probe5/p5-reap` as **root** is refused by sudo itself, because A6
grants only `(agent)`; `p5-reap`'s internal `id -u -eq 0` guard is the second
layer, not the first.

### A8 — if this is a fresh machine

Probes 1–4's evidence was gathered on the original host and none of it transfers.
Before trusting Probe 5's containment rows, re-confirm the two facts they depend on:

- `launchctl bootstrap gui/$(id -u) <plist>` returns rc=0 **unsandboxed** (it fails
  `EIO` under the command sandbox — as does `ps`, so the orchestrator must run
  unsandboxed either way).
- `incarnation.py <pid>` returns a non-null `p_uniqueid`.

---

## Part B — fixture code changes — **DONE (`c6cf804`, `396bd02`)**

All five items below landed in the WIP checkpoint after this file was drafted.
Verified present 2026-07-27: `BLOCKED` holds only `PL`; `kernel.takeover_publish`
exists with the reap-before-publish gate; `runsurrogate.py` handles
`--try-write-db`; `supervisor.spawn_run` and `reaper.Domain._sudo_reap` both route
through `sudo -C 5 -u agent`. Kept for the rationale — **do not re-implement.**



### B1 — route spawn through the helper (`supervisor.spawn_run`)

Currently `posix_spawn`s the surrogate directly as the maintainer. Under
`PROBE5_DOMAIN_MODE=uid` it must exec
`sudo -C 5 -u agent /usr/local/probe5/p5-spawn <gen_token> [flags]`, keeping the
dup2 file-actions that place the report pipe on fd 3 and the gate on fd 4. Keep
the existing direct-spawn path for degraded mode so both remain runnable.

### B2 — route the reap through the helper (`reaper.Domain.signal_all`)

Today it takes the true `kill(-1)` path only when `geteuid() == agent_uid`, and
otherwise falls back to enumerate-then-signal — which reintroduces the PID-reuse
race at signal time. Add a third branch: in `uid` mode from the maintainer, shell
out to `sudo -u agent /usr/local/probe5/p5-reap TERM|KILL`. The existing root
refusal must stay in front of all branches.

`Domain.scan()` in `uid` mode already uses `proc_listpids(PROC_UID_ONLY)` and needs
no change. Note that `exclude` defaults to `{os.getpid()}`, which is correct for
degraded mode but should be **empty** in uid mode — the maintainer's pid is not in
the agent's uid set, and excluding anything there could mask a survivor.

### B3 — implement TAKEOVER (`kernel.py`)

Not written. The draft specifies: reap the current generation first (full saga,
verify zero), **then** one `BEGIN IMMEDIATE` transaction publishing `g+1/prepared`.
The ordering is the point — publishing before the reap is the v1 live-orphan hazard
coming back through the side door (third pass, HIGH-6). Add `takeover_publish()`
plus crash points either side of the reap.

### B4 — surrogate flag for the Writer row

`runsurrogate.py` has `--escape`, `--churn`, `--ignore-term`, `--descendant`; the
first three are **never driven by any row** — they were written for the blocked
rows and are untested. Add `--try-write-db <path>`: attempt to open the DB path
read-write, report the errno on the report pipe. Expect `EACCES`.

### B5 — promote the blocked rows (`scenarios.py`)

Move `Esc`, `Churn`, `Writer`, `Uid`, `Tw` out of the `BLOCKED` dict into real row
functions. Delete each entry from `BLOCKED` as its row lands, so a half-finished
pass cannot silently report a row as blocked when it actually ran and failed.

---

## Part C — the remaining rows and their pass bars

| Row | Drive | Passes only if |
| --- | --- | --- |
| **Esc** | run with `--escape` (setsid + double-fork), then stop | uid-wide `kill(-1)` reaps the escaped descendant; rescan → zero. This is the row the degraded mode *cannot* fake: the escapee drops the gen_token, so only a real uid domain sees it. |
| **Churn** | run with `--churn` (continuous fork), then stop | TERM→KILL→**rescan** converges to zero, or the lease stays fenced in `stop_intent`. A reap that only signals the set it enumerated must not report converged. |
| **Writer** | run with `--try-write-db <state.db>` | The open fails `EACCES`. Sole-writer becomes enforced rather than architectural. |
| **Uid** | worker spawns a helper under a different credential | **Documented limitation, not a falsifier.** Record whether it is detectable (privileged uid/token scan) or honestly undetectable. Do not let this row fail the probe. |
| **Tw** | launch gen g with live workers, then takeover | gen g is reaped to **verified zero before** `g+1` is published. Check event order in `state.db`, not just the end state. |
| **Io** | small disk image (`hdiutil create -size 1m`) or a fault-injecting VFS | A genuine mid-write ENOSPC/EIO fails the transaction atomically. The current row only induces a read-only-database refusal, which fails before the first byte — a different class. |
| **PL** | VM or loopback power-fail harness | **Leave inconclusive.** SIGKILL cannot prove power-loss durability, for SQLite exactly as for flat files. The kill sheet says this is never passed; do not let a green matrix imply otherwise. |

Also re-run the 19 passing rows under `PROBE5_DOMAIN_MODE=uid`. They passed in
degraded mode; the uid domain changes what `scan()` returns, so their evidence does
not transfer unexamined.

---

## Part D — closing the probe

1. `PROBE5_DOMAIN_MODE=uid PROBE5_AGENT_UID=<P5UID> "$P5_PY" scenarios.py` —
   **unsandboxed**.
2. Reclassify. `confirmed` here means *"not falsified in the tested process-crash
   environment"*, never "proven correct" — power-loss stays inconclusive. If any
   invariant fails, it is `falsified`, the redirect is **containment option 2**
   (Linux VM / container), and it is **never** a return to the flat-file kernel.
3. Update, in this order: `results.json` (regenerated), the Environment and Results
   sections of `probe5-crash-kernel.md`, §7a row 5 of
   `dev_docs/auto-pilot-e-lite-design-2026-07-21.md`.
4. Ratify the **v5.2** reconcile fix in `draft-state-machine.md` (the re-adopt rule
   no longer requires an empty scan — see the Finding in `probe5-crash-kernel.md`).
5. Re-verify **Probe 1 and Probe 4**. Both certify an `agent` account that has since
   vanished, on a host where its uid was reassigned to a human. Their evidence is
   currently asserting something untrue of the machine.

---

## Part E — teardown

This is a disposable spike (§0a rule 4 — spike code is never promoted by renaming).
None of it should outlive the probe.

```sh
launchctl list | grep probe5     # expect nothing (matches both label generations)
pkill -f runsurrogate.py; pkill -f 'supervisor.py daemon'
sudo rm -f /etc/sudoers.d/probe5
sudo rm -rf /usr/local/probe5
rm -rf "$TMPDIR"/probe5.*
```

Leave the `agent` account in place — Stage 1 and Stage 2 both need it, and deleting
and recreating it is what produced the uid-reassignment hazard in the first place.

---

## Gotchas already paid for

- **The supervisor labels are `com.probe5r2.sup.*`, not `com.probe5.sup.*`.** The
  originals are the incident's two labels; they are booted out **and left
  `disabled`** in the launchd override database as a permanent tripwire. A
  `bootstrap` of a disabled label returns rc=0 and never runs, so reusing them
  reads as a spurious row failure whose obvious "fix" is `launchctl enable` —
  which re-arms exactly those labels. `install_supervisor` now refuses to
  bootstrap any label it finds in `launchctl print-disabled`. Do not enable them;
  pick a fresh prefix if you ever need another generation.
- **uid mode + `KeepAlive` is a loaded gun.** `PROBE5_AGENT_UID` must be the
  dedicated account, resolved by name — never the maintainer's uid, never root.
  `reaper.Domain` and `scenarios.install_supervisor` both refuse it now, but a
  refusal under `KeepAlive` + `ThrottleInterval 1` becomes a 1 Hz *crash* loop
  (noisy, harmless) rather than a reap loop. Have
  `launchctl bootout gui/$(id -u)/<label> && launchctl disable gui/$(id -u)/<label>`
  ready in a second terminal before the first bootstrap of any run.

- **`launchctl` and `ps` both fail under the command sandbox** (`bootstrap` → `EIO`).
  Run `scenarios.py` unsandboxed or every launchd row reads as a spurious failure.
- **A spawned surrogate must not inherit the orchestrator's stdio.** It blocks for
  an hour; inheriting stdout holds the driver's pipe open long after the supervisor
  exits, and every scenario looks like a hang. `spawn_run` redirects 0/1/2 to
  `/dev/null` — keep that when routing through `sudo`.
- **Report the pragmas from an on-disk DB.** An in-memory one cannot use WAL and
  reports `journal_mode=memory`, understating the durability actually configured.
- **A crash before the first commit leaves no lease at all**, and that is the
  correct safe outcome, not a missing one. The safe-outcome predicate must accept
  four shapes: no lease, terminal, fenced in `stop_intent`, or a healthy re-adopted
  run.
- **Do not gate re-adoption on an empty domain scan.** That was the v5.1 defect
  (see the Finding): a live run's own workers are in the domain too, so it
  false-reaps every run that forks. The recorded incarnation is the discriminator.
