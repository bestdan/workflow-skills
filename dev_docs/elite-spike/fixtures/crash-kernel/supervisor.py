#!/usr/bin/env python3
"""Probe 5 — the launchd-hosted supervisor: SOLE WRITER of state.db.

Owns three things:
  launch()     — prepare txn → spawn into the containment domain → activate txn
                 → open the start gate. Two transactions, one per transition;
                 the spawn sits BETWEEN them, which is where G1/G2/G3 live.
  reconcile()  — run at every (re)start. ALWAYS scans the containment domain
                 first, then re-adopts a verified-healthy run or reaps.
  stop()       — the termination saga: stop_intent commit → reap OUTSIDE any
                 transaction → terminalize only after the domain is verified empty.

The agent-uid run never opens state.db; it reports a pid over an inherited pipe
and the supervisor measures the identity itself from the kernel.

usage: supervisor.py {launch,reconcile,stop,daemon,dump} [...]
env:   PROBE5_DB, PROBE5_REPO, PROBE5_DOMAIN_MODE, PROBE5_AGENT_UID,
       PROBE5_CRASH_AT, PROBE5_CRASH_LOG, PROBE5_RUN_FLAGS
"""
import json
import os
import select
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import incarnation  # noqa: E402
import kernel  # noqa: E402
import reaper  # noqa: E402

DB = os.environ.get("PROBE5_DB", "")
REPO = os.environ.get("PROBE5_REPO", "repo-a")
REPORT_TIMEOUT_S = float(os.environ.get("PROBE5_REPORT_TIMEOUT_S", "10"))


def log(msg):
    print(f"[sup {os.getpid()} {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_domain(gen_token=None):
    mode = os.environ.get("PROBE5_DOMAIN_MODE", "gentoken")
    if mode == "uid":
        return reaper.Domain("uid", agent_uid=int(os.environ["PROBE5_AGENT_UID"]))
    return reaper.Domain("gentoken", gen_token=gen_token)


# --- launch -------------------------------------------------------------------

def spawn_run(gen_token, run_flags):
    """Spawn the run with an inherited report pipe (fd 3) and start gate (fd 4).

    posix_spawn rather than fork+exec so no un-exec'd copy of this process (with
    its SQLite fds and its half of every pipe) ever exists. Python opens all fds
    CLOEXEC, so state.db cannot leak into the child; the two pipe ends are placed
    deliberately by dup2, which clears CLOEXEC only on the duplicate.
    """
    report_r, report_w = os.pipe()
    gate_r, gate_w = os.pipe()
    argv = [sys.executable, os.path.join(HERE, "runsurrogate.py"), gen_token, *run_flags]
    # The run must not inherit our stdio: a surrogate that blocks for an hour
    # would hold the orchestrator's output pipe open long after the supervisor
    # exits, and every scenario would read as a hang.
    devnull = os.open(os.devnull, os.O_RDWR)
    pid = os.posix_spawn(
        sys.executable, argv, os.environ,
        file_actions=[(os.POSIX_SPAWN_DUP2, devnull, 0),
                      (os.POSIX_SPAWN_DUP2, devnull, 1),
                      (os.POSIX_SPAWN_DUP2, devnull, 2),
                      (os.POSIX_SPAWN_DUP2, report_w, 3),
                      (os.POSIX_SPAWN_DUP2, gate_r, 4)])
    os.close(devnull)
    # The parent must drop the child's ends or it will never see EOF on the
    # report pipe, and the child would hold a writable gate that outlives us.
    os.close(report_w)
    os.close(gate_r)
    return pid, report_r, gate_w


def read_reported_pid(report_r):
    """Read the child's pid, bounded. A child that never reports must not wedge
    the supervisor — the reconciliation path handles it."""
    deadline = time.monotonic() + REPORT_TIMEOUT_S
    buf = b""
    while time.monotonic() < deadline:
        r, _, _ = select.select([report_r], [], [], 0.2)
        if r:
            chunk = os.read(report_r, 64)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
    os.close(report_r)
    line = buf.decode(errors="replace").strip()
    return int(line) if line.isdigit() else None


def launch(run_id=None, run_flags=()):
    c = kernel.init_db(DB)
    run_id = run_id or f"run-{int(time.time())}"
    gen_token = f"P5TOKEN-{run_id}-{os.getpid()}"

    g = kernel.launch_prepare(c, repo_key=REPO, run_id=run_id, gen_token=gen_token)
    log(f"prepared generation {g} token={gen_token}")

    pid, report_r, gate_w = spawn_run(gen_token, list(run_flags))
    log(f"spawned run pid={pid}")
    kernel.crash("spawn.post_spawn")  # G2: live blocked child, nothing recorded

    reported = read_reported_pid(report_r)
    if reported is None:
        log("run never reported; leaving lease for reconciliation")
        return {"generation": g, "gen_token": gen_token, "pid": pid, "activated": False}

    # Measure the identity ourselves. The child told us WHICH pid; the kernel
    # tells us WHAT it is.
    inc = incarnation.measure(reported)
    if not inc.get("alive"):
        log(f"reported pid {reported} already gone; leaving for reconciliation")
        return {"generation": g, "gen_token": gen_token, "pid": pid, "activated": False}

    kernel.launch_activate(c, repo_key=REPO, generation=g, run_id=run_id, incarnation=inc)
    log(f"activated generation {g} p_uniqueid={inc['p_uniqueid']}")

    os.write(gate_w, b"go")
    log("gate opened")
    return {"generation": g, "gen_token": gen_token, "pid": pid, "activated": True,
            "p_uniqueid": inc["p_uniqueid"], "gate_fd": gate_w}


# --- the termination saga -----------------------------------------------------

def stop(reason="requested", gen_token=None):
    """stop_intent (txn) → reap (NO txn) → terminalize (txn), in that order.

    Terminalization is gated on the domain scan returning zero — never on the
    recorded run looking dead. A run that died after daemonizing descendants is
    exactly the case where "the run is gone" and "the work is stopped" differ.
    """
    c = kernel.init_db(DB)
    lease = kernel.get_lease(c, REPO)
    if lease is None or lease["state"] == "terminal":
        return {"noop": True, "lease": lease}
    gen_token = gen_token or lease["gen_token"]

    kernel.commit_stop_intent(c, repo_key=REPO, reason=reason)
    log(f"stop_intent committed ({reason})")

    ev = reaper.reap(make_domain(gen_token))
    kernel.crash("reap.post_zero")  # Saga2: domain empty, terminal not yet committed
    log(f"reap converged={ev['converged']} rounds={ev['rounds']}")

    if not ev["converged"]:
        # Fenced, not terminalized. The lease stays in stop_intent so the next
        # reconciliation retries; a human gets alerted. Never a false release.
        log("REAP DID NOT CONVERGE — lease stays fenced in stop_intent (alert)")
        return {"converged": False, "reap": ev, "terminalized": False}

    kernel.terminalize(c, repo_key=REPO, reap_evidence=ev)
    log("terminalized")
    return {"converged": True, "reap": ev, "terminalized": True}


# --- startup reconciliation ---------------------------------------------------

def readopt(pid, recorded):
    """Monitored re-adoption: register NOTE_EXIT, THEN re-verify identity.

    Registering by pid and trusting an earlier liveness check would race PID
    reuse — between the check and the attach the pid can become someone else.
    Re-reading p_uniqueid AFTER the kqueue registration closes that window: if
    the identity still matches, the thing we are now watching is the thing we
    meant to watch. This is observation, never parenthood; we never wait() a
    process that is not our child.
    """
    import select as _select
    kq = _select.kqueue()
    ev = _select.kevent(pid, filter=_select.KQ_FILTER_PROC,
                        flags=_select.KQ_EV_ADD | _select.KQ_EV_ENABLE,
                        fflags=_select.KQ_NOTE_EXIT)
    try:
        kq.control([ev], 0, 0)
    except (ProcessLookupError, OSError) as e:
        kq.close()
        return None, f"kqueue attach failed: {e}"
    live = incarnation.measure(pid)
    if not incarnation.same_incarnation(recorded, live):
        kq.close()
        return None, "identity changed after attach (PID reuse) — refusing to adopt"
    return kq, None


def reconcile():
    """Runs at every supervisor (re)start. Returns per-lease decisions."""
    c = kernel.init_db(DB)
    out = []
    for lease in kernel.non_terminal_leases(c):
        repo, g = lease["repo_key"], lease["generation"]
        domain = make_domain(lease["gen_token"])
        live = domain.scan()  # ALWAYS scan first — before consulting the roster
        rec = kernel.get_incarnation(c, repo, g, "run")
        d = {"repo_key": repo, "generation": g, "state": lease["state"],
             "stop_intent": lease["stop_intent"], "domain_live": live,
             "recorded_incarnation": bool(rec), "escape_proof": domain.escape_proof}

        if lease["state"] == "prepared":
            # G1/G2/G3 all land here. There may be a live, blocked, UNRECORDED
            # child; the gate EOF should already have killed it when we died,
            # but we verify zero before aborting rather than assume it.
            ev = reaper.reap(domain)
            d["reap"] = ev
            if ev["converged"]:
                kernel.terminalize(c, repo_key=repo, kind="launch_aborted",
                                   reap_evidence=ev)
                d["action"] = "launch_aborted"
            else:
                d["action"] = "fenced_stop_intent"
            out.append(d)
            continue

        # state == 'active'
        alive_verified = bool(rec) and incarnation.same_incarnation(
            rec, incarnation.measure(rec["pid"]))
        d["alive_verified"] = alive_verified
        # Processes in the domain other than the recorded run. NOT evidence of a
        # fault: a healthy run's own workers live here too.
        d["other_domain_procs"] = [p for p in live if not rec or p != rec["pid"]]

        if lease["stop_intent"]:
            d["action"] = "reap_stop_intent"
        elif not alive_verified:
            # Dead run ≠ dead workers. The run's identity is gone, so EVERY
            # process left in the domain is an orphan — reap and verify zero
            # regardless of how the roster looks.
            d["action"] = "reap_dead_run"
        else:
            # DELIBERATELY not conditioned on `other_domain_procs` being empty.
            # v5.1's re-adopt rule also required "no unexpected extra agent
            # processes", but a uid-wide scan cannot tell a live run's legitimate
            # descendants from a dead run's orphans — both are just processes of
            # the uid. Gating adoption on an empty scan therefore false-reaps
            # every run that forks a worker, on the first benign supervisor
            # start. The recorded incarnation is the discriminator: verified
            # alive ⇒ the extras are its children (one-globally-active-lease
            # means no prior generation can still be running).
            kq, err = readopt(rec["pid"], rec)
            if kq is None:
                d["action"] = "reap_adopt_failed"
                d["adopt_error"] = err
            else:
                kq.close()  # the daemon loop re-attaches; this proves adoptability
                d["action"] = "readopt"
                out.append(d)
                continue

        r = stop(reason=d["action"], gen_token=lease["gen_token"])
        d["stop"] = {"converged": r.get("converged"), "terminalized": r.get("terminalized")}
        out.append(d)
    return out


def daemon():
    """launchd KeepAlive entry point: reconcile, then idle so launchd keeps us."""
    decisions = reconcile()
    log(f"reconciled: {json.dumps(decisions, default=str)}")
    marker = os.environ.get("PROBE5_RECONCILE_LOG")
    if marker:
        with open(marker, "a") as f:
            f.write(json.dumps({"ts": time.time(), "pid": os.getpid(),
                                "decisions": decisions}, default=str) + "\n")
    while True:
        time.sleep(5)


def dump():
    c = kernel.init_db(DB)
    return {"lease": kernel.get_lease(c, REPO), "events": kernel.events(c),
            "seq": c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0],
            "pragmas": kernel.pragma_report(c)}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    flags = os.environ.get("PROBE5_RUN_FLAGS", "").split()
    if cmd == "launch":
        print(json.dumps(launch(run_flags=flags), default=str))
    elif cmd == "reconcile":
        print(json.dumps(reconcile(), default=str, indent=2))
    elif cmd == "stop":
        print(json.dumps(stop(), default=str, indent=2))
    elif cmd == "daemon":
        daemon()
    elif cmd == "dump":
        print(json.dumps(dump(), default=str, indent=2))
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
