# Probe 5 — resume prompt (new machine)

**Paste the block at the bottom into a fresh session on the new machine.**
Written 2026-07-27, after the non-launchd half of the containment work landed on
the mac mini. It stops there deliberately: **everything still open needs a
launchd `KeepAlive` supervisor, and the mac mini is SSH-only** — the failure mode
that took it down for four days kills SSH ~0.6 s after auth, which makes remote
recovery nearly impossible. The remaining rows want a machine with console access.

---

## Where the work stands

Branch `bestdan/elite-probe5-crash-kernel`, pushed through `7b7c670`. Clean tree.

**Update 2026-07-27 — the move happened.** Work is now on Daniel's MacBook Pro, a
console machine. **Part A is provisioned and verified here** (A1–A7 green, A8 half
done — see the MacBook blockquote in `probe5-todo.md` § Part A for the full record
and the two corrections it forced). `agent` is **uid 502** on this host; leave
`PROBE5_AGENT_UID` unset and let the name lookup resolve it. The mac mini's rows
still stand as mac mini evidence; none of it transfers, so Part C re-runs
Esc/Churn/Writer/Uid here before anything new is claimed.

**Established under the real escape-proof uid domain (`domain_mode=uid`):**

| Row | Verdict | What it establishes |
| --- | --- | --- |
| `Esc` | PASS | A `setsid`+double-fork+`exec` escapee was invisible to the token scan and still reaped to zero. The degraded mode cannot produce this result. |
| `Churn` | PASS | 40 live processes at reap time → converged to zero, lease terminal. |
| `Writer` | PASS | `write_probe=EACCES`. **Invariant 8 is now enforced, not architectural.** |
| `Uid` | DOCUMENTED-LIMITATION | Not a falsifier, by the kill sheet. |

**Still open** — all 21 other rows carry `(gentoken, 07-23)` evidence that does
**not** transfer to uid mode, plus:

- `Tw` — BLOCKED. Needs a launchd supervisor. `kernel.takeover_publish()` exists.
- `Sup-readopt`, `Sup-orphan` — need launchd; passed under gentoken only.
- `Io` — INCONCLUSIVE; needs a real ENOSPC/EIO harness (`hdiutil` image or a
  fault-injecting VFS), not the read-only-DB refusal currently injected.
- `PL` — inconclusive **by construction**. Per the kill sheet this is never
  passed. Do not let a green matrix imply otherwise.

## What changed in the code (read the commits, they explain why)

`396bd02` `ed290ee` `78b3df3` `cc966d4` `12badf3` `7b7c670`

The load-bearing ones:

- **`reaper.py` — a failed scan raises `ScanFailed`; it never returns `[]`.**
  `proc_listpids` signals error with `-1` and empty with `0`, and both used to
  collapse to `[]`. `reap()` decides `converged` from that emptiness and
  `terminalize()` is gated on `converged`, so one transient libproc failure would
  have "verified zero" and released the lease with the run alive. `reap()` now
  fences (`converged=False`, `scan_failed=...`) instead.
- **`reaper._sudo_reap` binds the runas user to the scanned uid.**
  `PROBE5_AGENT_UID` and `PROBE5_AGENT_USER` are independent env vars; nothing
  tied them together, so they could disagree and the reap would empty a domain
  nobody verified.
- **Cleanup actually works in uid mode.** `cleanup_domain`/`final_cleanup` used
  `os.kill`/`pkill`, which are EPERM from the maintainer against an agent-uid
  process, exception swallowed — they reported success while leaking surrogates.
- **`results.json` merges instead of being overwritten** by a subset run, and each
  row records its `domain_mode`. A subset run had already destroyed the 25-row
  evidence once (`c6cf804`); it was recovered from `44b8e98`.
- **Labels are `com.probe5r2.sup.*`.** The originals are the incident's labels,
  left `disabled` as a tripwire; `install_supervisor` refuses to bootstrap any
  disabled label.
- **`KeepAlive` is a template parameter** (`install_supervisor(..., keepalive=False)`)
  so the supervisor can make first contact with the relaunch loop disarmed.

Everything resolves the agent account **by name**, so the new machine's uid does
not need to match the mac mini's 503.

## The incident, in one paragraph

A supervisor was bootstrapped into `gui/501` in uid mode with
`PROBE5_AGENT_UID=501` — the maintainer's own uid. In uid mode a reap is
`kill(-1)` as that uid, so it signalled every process the maintainer owned. Under
`KeepAlive` it killed itself, launchd relaunched it every 1–2 s, and it reaped
every SSH login for four days (~242k cycles). Full evidence:
`dev_docs/tasks/probe5-incident-evidence/`. The guards added since make this
fail closed at construction — but a refusal under `KeepAlive` is still a 1 Hz
crash loop, so the kill switch matters.

---

## PASTE THIS INTO THE NEW SESSION

```
Resume "Probe 5 — crash kernel", a disposable falsification test of a
SQLite+launchd transaction/containment kernel. The previous chunk finished on a
different machine and stopped at the launchd boundary on purpose.

Repo:   github.com/bestdan/workflow-skills
Branch: bestdan/elite-probe5-crash-kernel  (pushed through 7b7c670)
Fixture: dev_docs/elite-spike/fixtures/crash-kernel/

READ FIRST, in this order — they are authoritative and current:
  dev_docs/elite-spike/fixtures/crash-kernel/RESUME-HERE.md   <- state + why we moved
  dev_docs/elite-spike/fixtures/crash-kernel/probe5-todo.md   <- Part A..E plan
  dev_docs/elite-spike/fixtures/crash-kernel/probe5-crash-kernel.md  <- spec + kill sheet
  dev_docs/tasks/probe5-incident-evidence/README.md           <- the 4-day outage

WHY THIS MACHINE: every remaining row needs a launchd KeepAlive supervisor. The
previous host is SSH-only, and this workstream's known failure mode kills every
SSH login ~0.6s after auth, so remote recovery is not realistic. Confirm this
machine has console/physical access before bootstrapping anything.

STATE: Esc, Churn, Writer, Uid PASS under the real uid domain ON THE MAC MINI.
The other 21 rows hold gentoken evidence from 2026-07-23 that does NOT transfer.
Part A host prereqs ARE now provisioned and verified on the MacBook (A1-A7; A8
pending its launchctl half) — but row evidence is per-machine, so nothing from
the mini carries over and Part C re-runs those four here.

SAFETY RULES (non-negotiable):
1. NEVER let PROBE5_AGENT_UID be the maintainer's uid or root. Pin the dedicated
   agent account BY NAME. The code refuses otherwise — trust but verify it.
2. scenarios.py / launchctl / ps must run UNSANDBOXED (they fail EIO sandboxed).
3. sudo steps that are not in the probe5 sudoers alias need a real tty — hand
   those to the maintainer. Once /etc/sudoers.d/probe5 exists, the three helpers
   are NOPASSWD and an agent session can invoke them.
4. Before ANY launchd bootstrap: have the maintainer pre-type the kill switch in
   their own terminal, semicolon not && so a failed bootout still disables:
     launchctl bootout gui/$(id -u)/<label>; launchctl disable gui/$(id -u)/<label>
   Bootstrap first with install_supervisor(..., keepalive=False) and prove
   reconciliation works with the relaunch loop disarmed.
5. Disposable spike code — never promote by renaming. Part E teardown when done,
   but LEAVE the agent account in place.

PLAN:
  A. Part A on this machine (attended; the maintainer runs the sudo block).
     Note it needs THREE helpers — p5-spawn, p5-reap, p5-measure — plus staged
     copies of runsurrogate.py and incarnation.py, all root-owned. Verify
     ownership; on the last host the tree was maintainer-owned and the trust
     boundary silently did not exist.
  B. Re-verify the fail-closed guards on this host before bootstrapping anything.
  C. Re-run Esc/Churn/Writer/Uid here to confirm they reproduce (evidence is
     per-machine).
  D. Then the launchd rows, keepalive=False first: Sup-readopt, Sup-orphan, Tw.
  E. Re-run the 19 transaction rows under PROBE5_DOMAIN_MODE=uid.
  F. Part D close-out (results.json, the Results section, §7a row 5, ratify the
     v5.2 reconcile fix, re-verify Probes 1 and 4) then Part E teardown.

Run rows with:
  cd <fixture> && PROBE5_DOMAIN_MODE=uid python3.12 scenarios.py <Row> [<Row>...]
Subset runs MERGE into results.json and stamp each row with its domain_mode.

Commit and push as you go — intermediate commits are wanted, this workstream has
lost work to a dead session before. Do not push to main; branch is
bestdan/elite-probe5-crash-kernel.

Start by reading the four files above, then inspect actual host state (agent
account? helpers? sudoers? loaded/disabled probe5 jobs? stray $TMPDIR/probe5.*?)
and give me a state assessment plus a plan for Part A. Do not provision anything
or run any row until I approve.
```
