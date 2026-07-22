# Probe 2 — tmux / process-binding spike

**Result: CONFIRMED (mechanism + identity primitive selected).** Two narrow
cross-uid facts are deferred to the two-uid mac mini (below); neither is an open
architectural risk, and the falsification redirect is **not** triggered.

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

| Item        | Value                                                            |
| ----------- | --------------------------------------------------------------- |
| OS          | macOS, Darwin 25.4.0 (arm64)                                     |
| tmux        | 3.6b, pinned socket `/tmp/claude/p2.sock`                       |
| python      | 3.12.13 (ctypes → libproc `proc_pidinfo`)                        |
| uid context | **single-uid dev box** (uid 501). No `apagent`, no sudo here.   |

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

| Scenario                                    | Injection                                                                       | Outcome                                                                                                                              |
| ------------------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **S0** bind + selective stop (positive)     | none                                                                            | `pane_pid` claim == measured pid; exe→`/bin/sleep`; leader (`sid==pgid==pid`); `stop` SIGNALLED the bound pgid, `dead_after: true` ✓ |
| **S1** pane death **before** observation    | `SIGKILL` the pane before the post-start liveness recheck                        | measure → `alive:false` (ESRCH); `would_publish_active_lease: false` → **fail-closed**, no active lease published ✓                  |
| **S2** launcher death before publication    | launcher never publishes; recovery pass returns later                            | pane's parent is the **persistent tmux server**, not the launcher (`ppid` stable); pane survives; recovery re-identifies by incarnation ✓ |
| **S3** replacement pane (same session name) | agent kills pane A, starts pane B reusing `ap-s3`                                | A dead; B has distinct `pid` **and** `p_uniqueid`; B **not** accepted as A; `stop(A)` → **REFUSE (dead)** — B never inherits A's authority ✓ |
| **S4** stop race / PID reuse                | stop request carries A's recorded identity but points at a live bystander pid    | `stop` → **REFUSE (identity-mismatch)**; bystander survives; `p_uniqueid` differs (hard), µs start differs, `ps`-second would have collided ✓ |

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

## Deferred to the two-uid mac mini (bounded follow-up, not blocking)

Both reduce to a single, already-implied requirement — **the control plane's
measure + signal path must run privileged** — which the mini's admin maintainer
satisfies:

1. **Cross-uid `libproc` read.** Confirm a non-root maintainer's
   `proc_pidinfo`/`proc_pidpath` against an **agent-owned** process. If it is
   `EPERM` (likely), the independent incarnation measurement must run as root
   (or matching uid). It must **not** fall back to `ps` lstart — that reintroduces
   the second-granularity reuse hole S4 closes. Co-locate measurement with the
   privileged `ap-stop`/watcher.
2. **Cross-uid signal.** `killpg` of an agent-owned process group requires
   root/matching-euid; `ap-stop` is already maintainer-owned/privileged, and the
   agent-side `ap-agent-exec kill-session` handles the tmux-scoped teardown.
   Confirm the split on the mini.

The agent-owned tmux stays entirely outside the trust path in both cases: it
provides session names and a candidate pid (claims); the privileged control
plane measures and signals.

## Decision

**CONFIRMED.** Fold into the measured revision (§0a): record the run-shim as a
bare `exec` wrapper (no `setsid` helper), and the recorded incarnation as
`{pid, p_uniqueid, start_µs, exe}` with `p_uniqueid` primary and
`p_puniqueid` pinning the tmux-server parent; specify `ap-stop`/watcher
re-validation as measure-then-signal, fail-closed on any mismatch. Schedule the
two cross-uid confirmations on the mini before Stage 2 (they gate _how_ the
measurement is privileged, not _whether_ the model works). The baseline
crash-transaction kernel (priority 5) may now consume this process model.
