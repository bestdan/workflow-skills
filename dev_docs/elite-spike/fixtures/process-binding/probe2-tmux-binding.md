# Probe 2 — tmux / process-binding spike

**Result: CONFIRMED — fully closed.** Both cross-uid facts are now confirmed on
the two-uid mini: the `libproc` read (non-root EPERM/fails-closed, root reads the
full tuple) and the `killpg` signal (non-root EPERM, root terminates the
agent-owned group). The falsification redirect is **not** triggered.

> **Note added 2026-07-27 — the `uid 502` in this document is a mac mini
> particular, not a fact about the agent account.** On that host the `agent`
> account later vanished and **502 was reassigned to an unrelated human**; on the
> MacBook the agent is **502** by coincidence of ordering, and on the mini it was
> re-provisioned as **503**. Read every `502` below as "the agent uid _on that
> host, that day_". Resolve the account **by name** — pinning the number is what
> produced the four-day incident recorded in
> `dev_docs/tasks/probe5-incident-evidence/`.
>
> **The finding itself is unaffected and has since been independently
> corroborated.** This probe's load-bearing result is that `libproc` reads are
> **EPERM across uids for a non-root caller and fail closed**. That is a kernel
> property, uid-invariant, and Probe 5 measured it again on different hardware
> with different uids: a maintainer measuring a live agent-uid pid gets
> `{"alive": false, "errno": 1}` while the same pid measured _as_ the agent
> returns the full tuple. It is the entire reason Probe 5 needs the privileged
> `p5-measure` helper — without it the supervisor reads every healthy run as dead
> and reaps it. So this probe needed no re-run: unlike Probe 1 (host state, which
> can rot) and Probe 4 (disposable GitHub infrastructure, now gone), its claim is
> about the kernel and does not decay with an account.

Disposable spike under §0a's contract. Fixture code (`incarnation.py`,
`shim.py`, `plane.py`, `scenarios.py`, `results.json`) lives beside this file
and is **never** promoted into `/usr/local/autopilot` by renaming (rule 4). No
secrets touched: the surrogate is `/bin/sleep`, no `claude` launch, no
credentials, no network.

## Kill sheet (from §7a, priority 2)

- **Falsifier / assumption**: _The maintainer can uniquely bind a requested
  launch to one live incarnation and stop only that incarnation despite the
  agent-owned tmux server._
- **Method**: start a trivial shim → harmless exec surrogate through the
  fixed-wrapper shape; measure PID/PPID/PGID/SID/executable continuity; inject
  pane death before observation, launcher death, replacement panes, and stop
  races.
- **Pass threshold**: a unique control-plane binding survives every injection,
  and stop signals only the bound incarnation — never a recycled PID, a
  replacement pane, or a superseded generation.
- **Redirect if falsified**: abandon pane identity as authority; evaluate a
  directly held maintainer guardian / process handle or a different session
  host before designing leases.

## Environment (non-secret)

| Item        | Value                                                                                                                                                                                                                                     |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OS          | macOS, Darwin 25.4.0 (arm64)                                                                                                                                                                                                              |
| tmux        | 3.6b, pinned socket `/tmp/claude/p2.sock`                                                                                                                                                                                                 |
| python      | 3.12.13 (ctypes → libproc `proc_pidinfo`)                                                                                                                                                                                                 |
| uid context | ran **single-uid as the maintainer (501)**; the two-uid path (the `agent` account + sudo) was not exercised in this section — see the cross-uid confirmations below, which were run on the mini where the agent held uid 502 at the time. |

The agent-owned tmux server is modeled faithfully: tmux's `#{pane_pid}` is
treated as an **untrusted claim**; all authority comes from an independent
`libproc` measurement (`incarnation.measure()`). Kernel semantics under test
(setsid/exec continuity, PID reuse, `p_uniqueid`) are **uid-invariant**, so the
mechanism verdict holds regardless of which uid owns tmux. Two facts that _do_
depend on the uid split are deferred to the mini (last section).

> Sandbox note: creating the tmux Unix socket returned `EPERM` under the
> command sandbox; the fixture runs with the sandbox disabled (local socket
> only). Manageable via `/sandbox`.

## Reproduce

```
python3 scenarios.py          # runs S0–S4, prints a MACHINE-READABLE JSON tail
```

Raw output captured in `results.json`.

## Two substrate primitives this probe settled

1. **The run-shim needs no `setsid(2)`.** tmux already creates each pane as its
   own session leader: the shim observed `sid == pgid == pid` **before** any
   `setsid` call (`already_leader: true`, `setsid_result:
   skipped-already-leader`). Across the `execve` to `/bin/sleep`, `pid`, `pgid`,
   and `sid` all persist and only the executable image changes. So the shim is a
   bare `exec` wrapper — this resolves the opportunistic "`setsid(2) → execve`
   topology" question (§7a) in favor of **no helper**: `pgid == pid` gives a
   clean process-group kill target for stop.
2. **`p_uniqueid` is the incarnation key, not `ps lstart`.** macOS exposes
   `proc_pidinfo(PROC_PIDUNIQIDENTIFINFO)` → `p_uniqueid`, a monotonic,
   **never-reused-within-a-boot** 64-bit kernel process id (back-to-back
   children: `13464245` → `13464251`), plus `p_puniqueid` = the parent's unique
   id. This is the macOS analogue of a pidfd/generation counter — strictly
   stronger than the design's provisional `{pid, ps lstart}` (second
   granularity). Two back-to-back processes shared the **same whole second**
   (`ps` would false-match a reused PID) yet had distinct µs start times _and_
   distinct `p_uniqueid`. The recorded incarnation is therefore
   `{pid, p_uniqueid, start_µs, exe}`, primary key `p_uniqueid`; PID reuse is a
   hard mismatch, not a probabilistic one. `p_puniqueid` additionally pins the
   pane to the **expected tmux-server incarnation**, catching an agent that
   restarts its tmux server under the same socket/session name.

## Fault-injection results

| Scenario                                    | Injection                                                                     | Outcome                                                                                                                                       |
| ------------------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **S0** bind + selective stop (positive)     | none                                                                          | `pane_pid` claim == measured pid; exe→`/bin/sleep`; leader (`sid==pgid==pid`); `stop` SIGNALLED the bound pgid, `dead_after: true` ✓          |
| **S1** pane death **before** observation    | `SIGKILL` the pane before the post-start liveness recheck                     | measure → `alive:false` (ESRCH); `would_publish_active_lease: false` → **fail-closed**, no active lease published ✓                           |
| **S2** launcher death before publication    | launcher never publishes; recovery pass returns later                         | pane's parent is the **persistent tmux server**, not the launcher (`ppid` stable); pane survives; recovery re-identifies by incarnation ✓     |
| **S3** replacement pane (same session name) | agent kills pane A, starts pane B reusing `ap-s3`                             | A dead; B has distinct `pid` **and** `p_uniqueid`; B **not** accepted as A; `stop(A)` → **REFUSE (dead)** — B never inherits A's authority ✓  |
| **S4** stop race / PID reuse                | stop request carries A's recorded identity but points at a live bystander pid | `stop` → **REFUSE (identity-mismatch)**; bystander survives; `p_uniqueid` differs (hard), µs start differs, `ps`-second would have collided ✓ |

Full JSON: `results.json`.

## Findings

- **Unique binding holds.** The pane_pid handed back by the agent-owned tmux is
  only ever a claim; the maintainer's independent `libproc` measurement of
  `{pid, p_uniqueid, start_µs, exe}` is the authority, and it is unique and
  stable across the launch. Pane identity was never the authority — so the
  redirect ("abandon pane identity … evaluate a guardian/process handle") is
  **not** triggered: `{pid, p_uniqueid}` _is_ the macOS process handle.
- **Selective stop holds.** `ap-stop` re-validates the recorded incarnation
  against the live process table **before** signalling and refuses on any
  mismatch (dead, replaced, or PID-reused). It never signals a PID it cannot
  positively re-identify. Group kill is clean because `pgid == pid`.
- **Fail-closed on the observation gap.** A pane that dies before the post-start
  recheck yields no active lease (S1); a superseded/replaced incarnation is
  refused, not adopted (S3).
- **Launcher crash is benign.** The pane is anchored to the long-lived agent
  tmux server, so `ap-launch` dying cannot orphan or kill it; a recovery pass
  re-adopts the exact incarnation by `run_id` (S2). Requires the
  `launch_prepared` → incarnation binding to be journaled **before** start so
  recovery knows what to look for (already in the §4.1 draft).

## Two-uid confirmations on the mac mini

Both reduce to a single, already-implied requirement — **the control plane's
measure + signal path must run privileged** — which the mini's admin maintainer
satisfies. Run on the provisioned host, where the `agent` account held uid 502 on
that date. **To repeat this on any host: resolve `agent` by name, never by
number** — the uid differs per machine and 502 was later reassigned to a human on
the mini.

Probe 5 reached the same requirement from the other direction and had to build
the privileged path this section implies: `sudo -u agent /usr/local/probe5/p5-measure`
exists precisely because a non-root maintainer cannot measure an agent-uid
process, and its reap helper exists because the same is true of signalling.

### 1. Cross-uid `libproc` read — CONFIRMED

Drill: `crossuid-read-drill.sh` measures one `agent`-owned process three ways.

| Reader                    | Result                                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| non-root maintainer (501) | `{"alive": false, "pid": 3875, "errno": 1}` — **EPERM, fails closed**                                                                                  |
| root (`sudo`)             | full tuple: `p_uniqueid 13532476`, `start_µs`, `exe /bin/sleep`, `uid 502` ✓                                                                           |
| agent (same-uid)          | n/a — file-perm `EACCES` reading the maintainer's checkout, not a `libproc` result; same-uid read already proven by the whole probe running as uid 501 |

**Finding.** A non-root maintainer cannot read an agent-owned process's
incarnation — and it fails **cleanly closed** (`EPERM`), not the dangerous
partial-read case (`alive:true` with null `p_uniqueid`/`exe`). Root reads the
full tuple. So the independent incarnation measurement **runs as root**,
co-located with the privileged `ap-stop`/watcher. It must **never** fall back to
`ps` lstart (that reopens the S4 second-granularity reuse hole), and it retains a
reject-incomplete-tuple check as defense-in-depth even though the partial case
did not occur here.

### 2. Cross-uid signal — CONFIRMED

Drill: `crossuid-signal-drill.sh` signals one `agent`-owned group leader.

| Signaller                 | `killpg(SIGTERM)` result                           |
| ------------------------- | -------------------------------------------------- |
| non-root maintainer (501) | `EPERM` — denied; target stays `alive: true`       |
| root (`sudo`)             | terminates the group; incarnation `alive: false` ✓ |

**Finding.** `killpg` of an agent-owned process group requires
root/matching-euid; a non-root maintainer is denied (negative control) and root
terminates it. `ap-stop` is already maintainer-owned/privileged, so the signal
runs there. The agent-side `ap-agent-exec kill-session` handles the tmux-scoped
teardown — that half is **same-uid** and already exercised by S0/S3, so it is
not a new cross-uid fact and was not repeated in this drill.

The agent-owned tmux stays entirely outside the trust path in both cases: it
provides session names and a candidate pid (claims); the privileged control
plane measures and signals.

## Decision

**CONFIRMED.** Fold into the measured revision (§0a): record the run-shim as a
bare `exec` wrapper (no `setsid` helper), and the recorded incarnation as
`{pid, p_uniqueid, start_µs, exe}` with `p_uniqueid` primary and
`p_puniqueid` pinning the tmux-server parent; specify `ap-stop`/watcher
re-validation as measure-then-signal, fail-closed on any mismatch, both running
**privileged (root)** — confirmed by the two mini drills (non-root fails closed
on read and signal; root does both). The agent-owned tmux never enters the trust
path. Probe 2 is fully closed; the baseline crash-transaction kernel (priority 5)
may now consume this process model.
