#!/usr/bin/env python3
"""The dummy 'agent run' for the Probe 3 skeleton.

It is deliberately trivial: it writes a durable runfile binding itself to its
own kernel incarnation (Probe 2's p_uniqueid tuple), then loops touching
`.heartbeat` every `beat` seconds. It carries NO authority and knows nothing
about the watcher — the whole point of the probe is that supervision is external
and autonomous.

Fault injection is done from outside by the orchestrator:
  * kill  = SIGKILL this pid            -> incarnation dies (watcher: dead)
  * wedge = SIGSTOP this pid            -> alive but heartbeat goes stale
             (watcher: alive + stale -> drive safe stop)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import incarnation


def main():
    run_id = sys.argv[1]
    beat = float(sys.argv[2])              # heartbeat period (s)
    lifetime = float(sys.argv[3])          # how long a healthy run lives (s)
    stop_deadline_s = float(sys.argv[4])   # wall seconds from now the run MUST
                                           # be terminal by (canary couples here)

    # Become our own session/process-group leader so the group the watcher
    # signals for a safe stop contains only this run — mirroring production,
    # where Probe 2 confirmed the tmux pane is already a session leader (pgid ==
    # sid == pid). Without this, a `&`-backgrounded run shares its shell's group
    # and killpg would take out the supervisor too.
    try:
        os.setsid()
    except OSError:
        pass

    pid = os.getpid()
    me = incarnation.measure(pid)
    started = common.now()
    common.write_json_atomic("runfile.json", {
        "run_id": run_id,
        "generation": 1,
        "incarnation": me,
        "state": "active",
        "started": started,
        "stop_deadline_wall": started["wall"] + stop_deadline_s,
        "beat": beat,
    })
    common.touch("heartbeat")
    common.registry_append("run_start", run_id=run_id, pid=pid,
                           p_uniqueid=me.get("p_uniqueid"))

    deadline = time.monotonic() + lifetime
    while time.monotonic() < deadline:
        common.touch("heartbeat")
        time.sleep(beat)

    # Natural, healthy completion: the run marks its own terminal claim. (The
    # watcher still independently confirms; a claim is not fact — §4.1.)
    rf = common.read_json("runfile.json")
    if rf and rf.get("state") == "active":
        rf["state"] = "terminal"
        rf["terminal_reason"] = "completed"
        common.write_json_atomic("runfile.json", rf)
        common.registry_append("claimed_exit", run_id=run_id, reason="completed")


if __name__ == "__main__":
    main()
