#!/usr/bin/env python3
"""Watcher — one short-lived launchd pass (§5.1). No daemon to wedge.

Invoked by launchd on StartInterval. Guarded by a non-blocking lockf so an
overrun never stacks. Each pass, independently:

  * measures the registered run's incarnation (never trusts a claimed pid);
  * checks heartbeat freshness vs the staleness threshold;
  * on a dead incarnation (kill) or a stale heartbeat (wedge), DRIVES the run to
    a safe terminal state autonomously (no human in the loop) and records a
    durable async notice;
  * records watcher_slow if the previous successful pass is older than 2
    intervals;
  * touches watcher.pass — the liveness beacon the canary couples to.

Hard prohibitions (§5.1): it never re-mints, never edits a live run's work,
never classifies a claim as fact without its own observation. Its only mutation
is terminalizing a run it has itself observed dead/wedged, plus appending
evidence.
"""
import fcntl
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import incarnation

STALE_S = float(os.environ.get("PROBE3_STALE_S", "20"))       # heartbeat staleness threshold
INTERVAL_S = float(os.environ.get("PROBE3_INTERVAL_S", "10"))  # watcher StartInterval


def _safe_stop_wedged(rf, live):
    """Drive a wedged-but-alive incarnation to a safe terminal. Verify identity
    against the recorded incarnation FIRST (Probe 2) so we never signal a reused
    pid, then SIGKILL the process group. A SIGSTOPped process still dies."""
    recorded = rf["incarnation"]
    if not incarnation.same_incarnation(recorded, live):
        return False, "incarnation_mismatch_refused"
    pgid = live.get("pgid") or recorded.get("pgid")
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        return False, "killpg_eperm"
    # Confirm it is gone.
    time.sleep(0.3)
    after = incarnation.measure(recorded["pid"])
    return (not incarnation.same_incarnation(recorded, after)), "killed"


def _terminalize(rf, run_id, reason, detail):
    """Record the durable safe-terminal outcome + async notice, idempotently."""
    dedup = f"{run_id}+{reason}"
    first = common.registry_append("observed_terminal", dedup=dedup,
                                   run_id=run_id, reason=reason, detail=detail)
    if first:
        rf["state"] = "terminal"
        rf["terminal_reason"] = reason
        rf["terminalized_by"] = "watcher"
        common.write_json_atomic("runfile.json", rf)
        common.registry_append("alert", run_id=run_id, condition=reason)
        common.notice(reason, f"run {run_id}: {reason} — driven to safe stop ({detail})",
                      run_id=run_id)
    return first


def _pass():
    rf = common.read_json("runfile.json")

    # watcher_slow: account for a silence longer than 2 intervals (§5.1).
    last_pass = common.mtime("watcher.pass")
    if last_pass is not None:
        gap = time.time() - last_pass
        if gap > 2 * INTERVAL_S:
            common.registry_append("watcher_slow", gap_s=round(gap, 2))

    if rf and rf.get("state") == "active":
        run_id = rf["run_id"]
        recorded = rf["incarnation"]
        live = incarnation.measure(recorded["pid"])

        if not incarnation.same_incarnation(recorded, live):
            # KILL: the bound incarnation is gone (dead or pid reused by another).
            _terminalize(rf, run_id, "stall_kill",
                         f"incarnation_dead pid={recorded['pid']}")
        else:
            hb = common.mtime("heartbeat")
            stale = (time.time() - hb) if hb is not None else None
            if stale is not None and stale > STALE_S:
                # WEDGE: alive but not beating. Drive to safe stop, then record.
                ok, how = _safe_stop_wedged(rf, live)
                _terminalize(rf, run_id, "wedge",
                             f"heartbeat_stale={round(stale,1)}s safe_stop={how}")
            # else: fresh heartbeat + live incarnation -> no stall condition.

    common.touch("watcher.pass")


def main():
    lock = common.path("watcher.lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return  # previous pass still running — launchd fires the next one
    try:
        _pass()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == "__main__":
    main()
