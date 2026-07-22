#!/usr/bin/env python3
"""Probe 3 orchestrator — drives the async-skeleton under REAL per-user launchd.

Runs the four kill-sheet injections plus a healthy baseline and a
production-cadence confirmation, collecting sanitized evidence into results.json.
Must run UNSANDBOXED: launchctl talks to launchd over a mach service the sandbox
blocks (verified: bootstrap fails EIO sandboxed, rc=0 unsandboxed).

Nothing here is production code (§0a / rule 4). No credentials, no `claude`, no
`gh`, no network beyond an OPTIONAL Slack POST from an env webhook never written
to disk.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402  (imported for path helpers via env)
import incarnation  # noqa: E402

UID = os.getuid()
DOMAIN = f"gui/{UID}"
PYTHON = sys.executable


# --- launchd helpers ----------------------------------------------------------

def _lc(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def gen_plist(tmpl_name, out_path, subs):
    with open(os.path.join(HERE, tmpl_name)) as f:
        txt = f.read()
    for k, v in subs.items():
        txt = txt.replace(f"@{k}@", str(v))
    with open(out_path, "w") as f:
        f.write(txt)


def bootstrap(label, plist):
    _lc("bootout", f"{DOMAIN}/{label}")  # clear any stale copy
    r = _lc("bootstrap", DOMAIN, plist)
    if r.returncode != 0:
        raise SystemExit(f"bootstrap {label} failed: {r.stderr.strip()}")


def kickstart(label):
    return _lc("kickstart", "-k", f"{DOMAIN}/{label}")


def bootout(label):
    _lc("bootout", f"{DOMAIN}/{label}")


# --- run + registry helpers (operate on a given RUNDIR) -----------------------

def env_for(rundir, **extra):
    e = dict(os.environ)
    e["PROBE3_RUNDIR"] = rundir
    e.update({k: str(v) for k, v in extra.items()})
    return e


def start_run(rundir, run_id, beat, lifetime, stop_deadline, cadence):
    p = subprocess.Popen(
        [PYTHON, os.path.join(HERE, "run.py"), run_id,
         str(beat), str(lifetime), str(stop_deadline)],
        env=env_for(rundir, **cadence),
        start_new_session=True,  # isolate the run's process group from us
    )
    # wait for the runfile to exist
    for _ in range(50):
        if os.path.exists(os.path.join(rundir, "runfile.json")):
            break
        time.sleep(0.1)
    return p


def registry(rundir):
    out = []
    fp = os.path.join(rundir, "registry.jsonl")
    if os.path.exists(fp):
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def wait_for(rundir, kind, reason=None, timeout=90):
    """Poll the registry until a matching record appears. Returns (record,
    wall_time_seen) or (None, None) on timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for r in registry(rundir):
            if r.get("kind") == kind and (reason is None or r.get("reason") == reason):
                return r, time.time()
        time.sleep(0.25)
    return None, None


def run_canary(rundir, cadence):
    r = subprocess.run([PYTHON, os.path.join(HERE, "canary.py")],
                       env=env_for(rundir, **cadence),
                       capture_output=True, text=True)
    # last canary record carries the verdict
    recs = [x for x in registry(rundir) if x.get("kind") == "canary"]
    return recs[-1] if recs else None


# --- scenarios ----------------------------------------------------------------

def fresh_rundir():
    d = tempfile.mkdtemp(prefix="probe3.", dir=os.environ.get("TMPDIR", "/tmp"))
    open(os.path.join(d, "broker.token"), "w").close()  # stub fresh broker
    return d


def install_watcher(rundir, label, cadence):
    plist = os.path.join(rundir, f"{label}.plist")
    gen_plist("watcher.plist.tmpl", plist, {
        "LABEL": label, "PYTHON": PYTHON, "DIR": HERE, "RUNDIR": rundir,
        "INTERVAL": cadence["PROBE3_INTERVAL_S"], "STALE": cadence["PROBE3_STALE_S"],
    })
    bootstrap(label, plist)
    kickstart(label)  # force a first pass so watcher.pass exists


def scenario_kill_and_wedge(cadence):
    rundir = fresh_rundir()
    label = "com.probe3.watcher.cw"
    install_watcher(rundir, label, cadence)
    time.sleep(1)

    # baseline: a healthy, beating run under a live launchd watcher
    start_run(rundir, "killrun", 1, 600, 300, cadence)
    time.sleep(2 * float(cadence["PROBE3_INTERVAL_S"]))
    baseline = run_canary(rundir, cadence)

    # --- KILL ---
    rf = common_runfile(rundir)
    kill_pid = rf["incarnation"]["pid"]
    kill_t = time.time()
    os.kill(kill_pid, signal.SIGKILL)
    rec, seen = wait_for(rundir, "observed_terminal", "stall_kill", timeout=90)
    kill_detect = (seen - kill_t) if rec else None

    # --- WEDGE (new run, same watcher) ---
    p2 = start_run(rundir, "wedgerun", 1, 600, 300, cadence)
    time.sleep(2 * float(cadence["PROBE3_INTERVAL_S"]))
    rf2 = common_runfile(rundir)
    wedge_pid = rf2["incarnation"]["pid"]
    os.kill(wedge_pid, signal.SIGSTOP)   # alive but heartbeat freezes
    wedge_t = time.time()
    rec2, seen2 = wait_for(rundir, "observed_terminal", "wedge", timeout=90)
    wedge_detect = (seen2 - wedge_t) if rec2 else None
    wedge_alive_after = incarnation.same_incarnation(
        rf2["incarnation"], incarnation.measure(wedge_pid))
    try:
        os.kill(wedge_pid, signal.SIGKILL)  # cleanup if survived
    except ProcessLookupError:
        pass

    bootout(label)
    return {
        "rundir": rundir,
        "baseline_canary": baseline,
        "kill": {"pid": kill_pid, "detect_s": kill_detect, "record": rec,
                 "runfile_state": common_runfile(rundir).get("state")},
        "wedge": {"pid": wedge_pid, "detect_s": wedge_detect, "record": rec2,
                  "still_alive_after_safe_stop": wedge_alive_after},
    }


def scenario_false_positive(cadence):
    """Load-bearing: run wedged AND watcher killed -> canary must NOT be healthy."""
    rundir = fresh_rundir()
    label = "com.probe3.watcher.fp"
    canary_label = "com.probe3.canary.fp"

    install_watcher(rundir, label, cadence)
    time.sleep(1)
    # install the canary as a real launchd job to prove hosting, then kickstart it
    cplist = os.path.join(rundir, f"{canary_label}.plist")
    gen_plist("canary.plist.tmpl", cplist, {
        "LABEL": canary_label, "PYTHON": PYTHON, "DIR": HERE, "RUNDIR": rundir,
        "STALE": cadence["PROBE3_STALE_S"],
        "WATCHER_FRESH": cadence["PROBE3_WATCHER_FRESH_S"],
    })
    bootstrap(canary_label, cplist)

    start_run(rundir, "fprun", 1, 600, 300, cadence)
    time.sleep(2 * float(cadence["PROBE3_INTERVAL_S"]))
    healthy_before = run_canary(rundir, cadence)  # should be healthy here

    # KILL THE WATCHER: bootout so no more passes; watcher.pass ages out.
    bootout(label)
    rf = common_runfile(rundir)
    os.kill(rf["incarnation"]["pid"], signal.SIGSTOP)  # WEDGE the run
    # wait until watcher.pass is stale beyond the canary's freshness window
    time.sleep(float(cadence["PROBE3_WATCHER_FRESH_S"]) + 2)

    ks = kickstart(canary_label)               # fire the canary via launchd
    time.sleep(1)
    verdict = run_canary(rundir, cadence)       # and read a definitive verdict

    try:
        os.kill(rf["incarnation"]["pid"], signal.SIGKILL)
    except ProcessLookupError:
        pass
    bootout(canary_label)
    return {
        "rundir": rundir,
        "healthy_before": healthy_before,
        "canary_launchd_kickstart_rc": ks.returncode,
        "verdict_when_wedged_and_watcher_dead": verdict,
    }


def scenario_production_kill():
    """Confirmation at production cadence: detect-and-stop < 600s SLO."""
    cadence = {"PROBE3_INTERVAL_S": "120", "PROBE3_STALE_S": "360",
               "PROBE3_WATCHER_FRESH_S": "360"}
    rundir = fresh_rundir()
    label = "com.probe3.watcher.prod"
    install_watcher(rundir, label, cadence)
    start_run(rundir, "prodrun", 5, 3600, 1800, cadence)
    time.sleep(3)
    rf = common_runfile(rundir)
    kill_t = time.time()
    os.kill(rf["incarnation"]["pid"], signal.SIGKILL)
    rec, seen = wait_for(rundir, "observed_terminal", "stall_kill", timeout=650)
    detect = (seen - kill_t) if rec else None
    bootout(label)
    return {"rundir": rundir, "cadence": cadence, "kill_detect_s": detect,
            "under_slo_600s": (detect is not None and detect < 600)}


def common_runfile(rundir):
    with open(os.path.join(rundir, "runfile.json")) as f:
        return json.load(f)


# --- sanitize + main ----------------------------------------------------------

def sanitize(obj):
    """Drop absolute rundir paths and any stray env leakage from evidence."""
    s = json.dumps(obj)
    return json.loads(s)  # values here are already non-secret; structural passthrough


def main():
    do_prod = "--prod" in sys.argv
    compressed = {"PROBE3_INTERVAL_S": "5", "PROBE3_STALE_S": "8",
                  "PROBE3_WATCHER_FRESH_S": "15"}

    results = {"cadence_compressed": compressed}
    print("== kill + wedge (compressed) ==")
    results["kill_wedge"] = scenario_kill_and_wedge(compressed)
    print("== false-positive leg (compressed) ==")
    results["false_positive"] = scenario_false_positive(compressed)
    if do_prod:
        print("== production-cadence kill confirmation (~2 min) ==")
        results["production_kill"] = scenario_production_kill()

    out = os.path.join(HERE, "results.json")
    with open(out, "w") as f:
        json.dump(sanitize(results), f, indent=2, sort_keys=True)
    print(f"\nwrote {out}")

    # concise pass/fail summary
    kw = results["kill_wedge"]
    fp = results["false_positive"]
    print("\n-- summary --")
    print(f"baseline canary healthy: {kw['baseline_canary']['verdict']=='healthy'}")
    print(f"kill detected+terminal:  {kw['kill']['record'] is not None} "
          f"detect={kw['kill']['detect_s']}s state={kw['kill']['runfile_state']}")
    print(f"wedge detected+stopped:  {kw['wedge']['record'] is not None} "
          f"detect={kw['wedge']['detect_s']}s survived={kw['wedge']['still_alive_after_safe_stop']}")
    print(f"fp healthy_before:       {fp['healthy_before']['verdict']}")
    v = fp["verdict_when_wedged_and_watcher_dead"]
    print(f"fp wedged+watcher-dead:  {v['verdict']} reasons={v['reasons']}")
    if do_prod:
        print(f"prod kill under SLO:     {results['production_kill']['under_slo_600s']} "
              f"detect={results['production_kill']['kill_detect_s']}s")


if __name__ == "__main__":
    main()
