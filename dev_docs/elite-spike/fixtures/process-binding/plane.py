#!/usr/bin/env python3
"""Control-plane model for Probe 2. Models ap-launch / ap-stop against an
agent-owned tmux server on a pinned socket. tmux's #{pane_pid} is treated as an
UNTRUSTED CLAIM; authority always comes from incarnation.measure()."""
import json, os, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import incarnation as inc

SOCK = "/tmp/claude/p2.sock"  # short path (macOS sun_path <=104) + writable
SHIM = os.path.join(HERE, "shim.py")
PY = sys.executable


def tmux(*args, check=True):
    return subprocess.run(["tmux", "-S", SOCK, *args],
                          capture_output=True, text=True, check=check)


def claimed_pane_pid(session):
    """The AGENT-OWNED server's claim about the pane's pid. Untrusted."""
    r = tmux("display-message", "-p", "-t", session, "#{pane_pid}", check=False)
    if r.returncode != 0:
        return None
    return int(r.stdout.strip())


def wait_exec_settled(pid, target="/bin/sleep", timeout=5.0):
    """Poll until the pid's image is the surrogate (exec landed) or dead."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        m = inc.measure(pid)
        if not m["alive"]:
            return m
        if m.get("exe") == target:
            return m
        time.sleep(0.02)
    return inc.measure(pid)


def launch(session, runfile, sleep_secs="3600"):
    """ap-launch: start pane, take the untrusted claim, then INDEPENDENTLY
    measure and validate. Returns (claim_pid, recorded_incarnation, shim_claim)."""
    tmux("new-session", "-d", "-s", session,
         f"exec {PY} {SHIM} {runfile} {sleep_secs}")
    # spin for a candidate pane pid
    claim_pid = None
    for _ in range(100):
        claim_pid = claimed_pane_pid(session)
        if claim_pid:
            break
        time.sleep(0.02)
    recorded = wait_exec_settled(claim_pid) if claim_pid else {"alive": False}
    shim_claim = None
    if os.path.exists(runfile):
        try:
            shim_claim = json.load(open(runfile))
        except Exception:
            shim_claim = None
    return claim_pid, recorded, shim_claim


def stop(recorded, *, require_exe="/bin/sleep"):
    """ap-stop: re-validate the recorded incarnation against the live process
    table BEFORE signalling. Refuse if the pid is gone or is a different
    incarnation (PID reuse / replacement). Signal the process GROUP only on a
    confirmed match. Returns a decision dict."""
    pid = recorded.get("pid")
    live = inc.measure(pid) if pid else {"alive": False}
    if not inc.same_incarnation(recorded, live, require_exe=require_exe):
        return {"action": "REFUSE", "reason":
                ("dead" if not live.get("alive") else "identity-mismatch"),
                "recorded": recorded, "live": live}
    # confirmed: signal the whole process group (leader pgid == pid here)
    try:
        os.killpg(recorded["pgid"], signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(50):
        if not inc.measure(pid)["alive"]:
            break
        time.sleep(0.02)
    still = inc.measure(pid)
    if still["alive"]:
        os.killpg(recorded["pgid"], signal.SIGKILL)
        time.sleep(0.1)
        still = inc.measure(pid)
    return {"action": "SIGNALLED", "pgid": recorded["pgid"],
            "dead_after": not still["alive"]}


def kill_session(session):
    tmux("kill-session", "-t", session, check=False)


def cleanup():
    subprocess.run(["tmux", "-S", SOCK, "kill-server"],
                   capture_output=True, text=True)
