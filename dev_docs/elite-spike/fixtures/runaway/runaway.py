#!/usr/bin/env python3
"""Probe 5b surrogate agent — the thing the supervisor is supervising.

DISPOSABLE SPIKE CODE (design §0a rule 4).

It stands in for `claude -p` and NOTHING launches a real one: no model call, no
network, no `gh`. It is the only faked component; the supervisor path
(`supervisor-scan` / `-gate` / `-check`, `alarm`, the pause-exempt ledger) is
the repo's real `scripts/spawn-orchestrator.sh`, driven against a scratch run
dir. Testing a reimplementation of the supervisor would prove nothing (§7a
rule 2).

**Long-lived and backgrounded, NOT a per-wake callback.** A surrogate invoked
once per wake and returning is *cooperating* — it yields to the supervisor on
schedule, which is the opposite of a runaway. It also makes leg 2's far-future
variant inexpressible: that forgery closes `supervisor-gate`, and on a per-wake
topology the surrogate would never be invoked on a gate-closed wake, so it could
not keep rewriting `paused_until` and spawning workers *behind* the pause.

It runs under the REAL rendered Seatbelt profile, which is what makes the
pause-exempt ledger's authority tested rather than assumed: the profile's
supervisor-state write-deny is the only positive enforcement evidence this
probe can produce. Under that profile it must be a Homebrew interpreter —
/usr/bin/python3 re-execs into CommandLineTools/Library/... and Seatbelt matches
the RESOLVED path, so it is refused at exec. driver.sh smoke-tests that before
any leg rather than discovering it mid-run.

Behaviours are per-leg and additive. Task 2 arms only the healthy ones; the
forgeries and the worker storm are tasks 3-5 and are deliberately absent here
rather than present-but-disabled, so an unbuilt leg cannot silently half-run.
"""
import argparse
import errno
import os
import socket
import sys
import time

# spawn-orchestrator.sh's SUPERVISOR_STATE_NAME — the one file the profile
# denies this (jailed) process write access to.
SUPERVISOR_STATE_NAME = "supervisor-state"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def install_network_tripwire(evidence, leg):
    """Make 'no network' a MECHANISM, not an assertion.

    The rendered profile does NOT help here: layer 1 allows network-outbound as
    a class (hostname filtering is layer 2, the egress proxy, which this fixture
    does not stand up). So the refusal has to live in-process. Every outbound
    connect raises; loopback is refused too, because nothing in this probe has a
    legitimate reason to reach even itself.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _refuse(sock, address, *a, **kw):
        common.emit(evidence, {
            "row": "network_attempt",
            "leg": leg,
            "outcome": "refused-by-fixture-tripwire",
            "family": int(getattr(sock, "family", -1)),
        })
        raise OSError(errno.ENETDOWN,
                      "probe 5b: no network is reachable during a leg run")

    socket.socket.connect = _refuse
    socket.socket.connect_ex = lambda s, a, *r, **k: _refuse(s, a)
    return (real_connect, real_connect_ex)


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def write_run_md(path, status, until, paused_until=None, pause_reason="",
                 parked=0):
    """RUN.md in the shape spawn-orchestrator.sh's own readers expect.

    Front matter is what every breaker on this path reads: `_supervisor_alarm_scan`
    takes `status`/`until`, `_pause_exempt` takes `status`/`paused_until`,
    `supervisor_gate` takes `status`/`paused_until`. The task table below it is
    what `_run_md_parked_count` and status-report walk.
    """
    rows = [
        "| task_a | implementing | branch_a | main | - | - | |",
        "| task_b | pending      | -        | main | - | - | |",
    ]
    for i in range(parked):
        rows.append("| task_p%d | parked | branch_p%d | main | - | - | |" % (i, i))
    lines = [
        "---",
        "run_id: probe5b-smoke",
        "base_branch: main",
        "status: %s" % status,
        "until: %s" % (until or ""),
        "paused_until: %s" % (paused_until or ""),
        "pause_reason: %s" % pause_reason,
        "min_task_budget: 20m",
        "---",
        "",
        "| task | phase | branch | base | base_sha | pr | notes |",
        "| ---- | ----- | ------ | ---- | -------- | -- | ----- |",
    ] + rows + [""]
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines))
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


def beat(ap_dir):
    """The heartbeat is what makes this a RUNAWAY rather than a stall. Probe 3's
    breakers detect a dead agent; this one stays visibly alive throughout."""
    with open(os.path.join(ap_dir, "heartbeat"), "w") as fh:
        fh.write(iso(time.time()) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rundir", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--leg", required=True)
    p.add_argument("--behaviour", required=True,
                   choices=("healthy", "short-pause", "completes-early"))
    p.add_argument("--until", required=True,
                   help="the run's REAL --until, ISO-8601 UTC")
    p.add_argument("--pause-seconds", type=int, default=0,
                   help="short-pause: how long the legitimate pause lasts")
    p.add_argument("--complete-after", type=int, default=0,
                   help="completes-early: seconds before flipping to status: done")
    p.add_argument("--parent-death-fifo", default="")
    p.add_argument("--driver-incarnation-file", default="")
    p.add_argument("--heartbeat-every", type=float, default=1.0)
    args = p.parse_args()

    # Armed BEFORE anything else this process does. A surrogate that starts its
    # loop first has a window where it is long-lived, uncooperative and
    # unguarded — the exact shape the kill sheet refuses.
    common.watch_parent_death(
        args.parent_death_fifo,
        common.load_driver_incarnation(args.driver_incarnation_file),
    )
    install_network_tripwire(args.evidence, args.leg)

    ap = os.path.join(args.rundir, ".auto-pilot")
    run_md = os.path.join(ap, "RUN.md")
    started = time.time()

    common.emit(args.evidence, {
        "row": "surrogate_start",
        "leg": args.leg,
        "behaviour": args.behaviour,
        "pid": os.getpid(),
        "pgid": os.getpgid(0),
        "until": args.until,
    })

    # Announce our own view of the sandbox up front. If the profile did NOT
    # apply, every downstream row is measuring an unjailed process and the leg
    # is `inconclusive — boundary not in force`, not a pass — so the fixture
    # says which it is rather than leaving it to be inferred.
    state_path = os.path.join(ap, SUPERVISOR_STATE_NAME)
    probe = probe_supervisor_state_deny(state_path, ap)
    common.emit(args.evidence, dict(probe, row="boundary_probe", leg=args.leg))

    paused_until = None
    status = "running"
    while True:
        now = time.time()
        elapsed = now - started

        if args.behaviour == "short-pause":
            # A LEGITIMATE pause, well inside --pause-exempt-max: the ledger must
            # stamp exempt_since, then CLEAR it when the pause ends. This is the
            # only place the clear path is exercised — no other leg touches it —
            # and a ledger that halts on this is a false positive, which is the
            # floor Probe 3 made load-bearing.
            if elapsed < args.pause_seconds:
                status = "paused"
                paused_until = iso(started + args.pause_seconds)
            else:
                status = "running"
                paused_until = None
        elif args.behaviour == "completes-early":
            if args.complete_after and elapsed >= args.complete_after:
                status = "done"

        write_run_md(run_md, status, args.until, paused_until,
                     "rate window" if status == "paused" else "")
        beat(ap)
        common.emit(args.evidence, {
            "row": "surrogate_tick",
            "leg": args.leg,
            "elapsed": round(elapsed, 3),
            "status": status,
            "paused_until": paused_until,
        })
        if status == "done":
            # Completed BEFORE --until: it stops writing but stays alive, so the
            # driver can still observe that nothing halted it. A surrogate that
            # exited here would make "no halt" indistinguishable from "no
            # surrogate".
            while True:
                time.sleep(args.heartbeat_every)
        time.sleep(args.heartbeat_every)


def probe_supervisor_state_deny(state_path, ap_dir):
    """Is the boundary actually in force, right now, from inside the jail?

    Two writes, and the CONTROL is mandatory rather than decorative: without it
    an EPERM from a broken path or a missing directory is indistinguishable from
    the profile's supervisor-state literal doing its job. Control fails -> the
    denial is not attributable -> `inconclusive`, never a pass.

    The errno that means 'the profile is in force' is EPERM (1), measured. EACCES
    belongs to the FILESYSTEM uid/mode boundary, which is the rule-6 repeat
    method, not this one.
    """
    result = {"supervisor_state_errno": None, "supervisor_state_wrote": False,
              "control_wrote": False, "control_errno": None}
    try:
        with open(state_path, "a") as fh:
            fh.write("")
        result["supervisor_state_wrote"] = True
    except OSError as exc:
        result["supervisor_state_errno"] = exc.errno
    control = os.path.join(ap_dir, "fixture-control-write")
    try:
        with open(control, "w") as fh:
            fh.write("ok\n")
        result["control_wrote"] = True
    except OSError as exc:
        result["control_errno"] = exc.errno
    result["boundary_in_force"] = bool(
        result["supervisor_state_errno"] == errno.EPERM
        and result["control_wrote"]
        and not result["supervisor_state_wrote"]
    )
    return result


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
