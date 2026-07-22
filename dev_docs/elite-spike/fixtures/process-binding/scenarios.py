#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import incarnation as inc
import plane

RUNDIR = os.path.join(HERE, "run")
os.makedirs(RUNDIR, exist_ok=True)
results = {}


def rf(name):
    return os.path.join(RUNDIR, f"{name}.runfile")


def banner(k, v):
    results[k] = v
    print(f"\n===== {k} =====")
    print(json.dumps(v, indent=2))


# ---- S0: setsid->execve continuity + positive selective stop ----
def s0():
    plane.cleanup()
    claim, rec, shim = plane.launch("ap-s0", rf("s0"))
    cont = {
        "claim_pane_pid": claim,
        "recorded_pid": rec.get("pid"),
        "claim_eq_recorded_pid": claim == rec.get("pid"),
        "exe_after_exec": rec.get("exe"),
        "sid_eq_pid_leader": rec.get("sid") == rec.get("pid"),
        "pgid_eq_pid": rec.get("pgid") == rec.get("pid"),
        "ppid": rec.get("ppid"),
        "start_us": [rec.get("start_tvsec"), rec.get("start_tvusec")],
        "shim_already_leader": (shim or {}).get("claim_post", {}).get("already_leader"),
        "shim_setsid_result": (shim or {}).get("claim_post", {}).get("setsid_result"),
        "shim_claim_pre_sid_eq_pid": (shim or {}).get("claim_pre", {}).get("sid")
                                     == (shim or {}).get("claim_pre", {}).get("pid"),
    }
    # positive control: stop the genuinely-bound live incarnation
    dec = plane.stop(rec)
    cont["stop_decision"] = dec
    plane.cleanup()
    banner("S0_setsid_exec_continuity_and_selective_stop", cont)


# ---- S1: pane death BEFORE observation -> fail closed ----
def s1():
    plane.cleanup()
    # launch a short-lived pane, then kill it before we can validate
    plane.tmux("new-session", "-d", "-s", "ap-s1",
               f"exec {plane.PY} {plane.SHIM} {rf('s1')} 3600")
    claim = None
    for _ in range(100):
        claim = plane.claimed_pane_pid("ap-s1")
        if claim:
            break
        time.sleep(0.01)
    # agent/OS kills the pane process immediately (race: dies before validate)
    try:
        os.kill(claim, signal.SIGKILL)
    except ProcessLookupError:
        pass
    time.sleep(0.2)
    live = inc.measure(claim)
    # control plane's post-start liveness recheck:
    publish = live["alive"] and live.get("exe") == "/bin/sleep"
    plane.cleanup()
    banner("S1_pane_death_before_observation", {
        "claim_pane_pid": claim,
        "live_after_kill": live,
        "would_publish_active_lease": publish,
        "fail_closed": not publish,
    })


# ---- S2: launcher death before publication (pane parented to tmux, not launcher) ----
def s2():
    plane.cleanup()
    claim, rec, shim = plane.launch("ap-s2", rf("s2"))
    ppid_before = rec.get("ppid")
    # ap-launch (the launcher) is a separate short-lived process that already
    # returned; the pane's parent is the tmux SERVER, which persists. Simulate
    # "launcher crashed before publishing active" = we simply never publish and
    # come back later via a recovery pass keyed by run_id.
    time.sleep(0.5)
    live = inc.measure(claim)
    recovered = inc.same_incarnation(rec, live, require_exe="/bin/sleep")
    banner("S2_launcher_death_before_publication", {
        "recorded_pid": rec.get("pid"),
        "ppid_at_launch": ppid_before,
        "ppid_now": live.get("ppid"),
        "pane_survived_launcher_death": live["alive"],
        "parent_is_persistent_tmux_not_launcher": live.get("ppid") == ppid_before,
        "recovery_pass_reidentifies_by_incarnation": recovered,
    })
    plane.cleanup()


# ---- S3: replacement pane cannot masquerade as the bound incarnation ----
def s3():
    plane.cleanup()
    claim_a, rec_a, _ = plane.launch("ap-s3", rf("s3a"))
    # agent (owns tmux) destroys the pane and starts a NEW one, same session name
    plane.kill_session("ap-s3")
    time.sleep(0.2)
    a_dead = not inc.measure(rec_a["pid"])["alive"]
    claim_b, rec_b, _ = plane.launch("ap-s3", rf("s3b"))
    substitute_accepted_as_a = inc.same_incarnation(
        rec_a, inc.measure(claim_b), require_exe="/bin/sleep")
    # control plane still holds incarnation A; stop(A) must refuse (A is gone)
    dec = plane.stop(rec_a)
    banner("S3_replacement_pane", {
        "incarnation_a_pid": rec_a["pid"],
        "incarnation_b_pid": rec_b["pid"],
        "a_dead_after_replacement": a_dead,
        "distinct_pids": rec_a["pid"] != rec_b["pid"],
        "a_uniqueid": rec_a["p_uniqueid"], "b_uniqueid": rec_b["p_uniqueid"],
        "distinct_uniqueid": rec_a["p_uniqueid"] != rec_b["p_uniqueid"],
        "distinct_start_us": [rec_a["start_tvsec"], rec_a["start_tvusec"]]
                             != [rec_b["start_tvsec"], rec_b["start_tvusec"]],
        "substitute_B_accepted_as_A": substitute_accepted_as_a,
        "stop_A_decision": dec,
    })
    plane.cleanup()


# ---- S4: stop race / PID reuse -> refuse to signal a mismatched incarnation ----
def s4():
    plane.cleanup()
    # bound incarnation A
    claim_a, rec_a, _ = plane.launch("ap-s4", rf("s4"))
    plane.kill_session("ap-s4")
    time.sleep(0.2)
    # innocent bystander B (models the process that later occupies a reused pid)
    b = subprocess.Popen(["/bin/sleep", "3600"])
    time.sleep(0.1)
    rec_b = inc.measure(b.pid)
    # ADVERSARIAL/RACY stop request: recorded A's identity, but pointed at B's
    # live pid (models pid X freed by A then reused by B).
    # keep A's recorded p_uniqueid/start but point at B's live pid+pgid
    forged = dict(rec_a)
    forged["pid"] = b.pid
    forged["pgid"] = rec_b["pgid"]
    dec_reuse = plane.stop(forged)          # must REFUSE (uniqueid/start mismatch)
    b_alive_after = inc.measure(b.pid)["alive"]
    # granularity check: back-to-back procs get distinct microsecond starts
    p1 = subprocess.Popen(["/bin/sleep", "60"]); time.sleep(0.005)
    p2 = subprocess.Popen(["/bin/sleep", "60"])
    time.sleep(0.05)
    m1, m2 = inc.measure(p1.pid), inc.measure(p2.pid)
    same_second = m1["start_tvsec"] == m2["start_tvsec"]
    distinct_us = (m1["start_tvsec"], m1["start_tvusec"]) != (m2["start_tvsec"], m2["start_tvusec"])
    for p in (b, p1, p2):
        p.kill()
    banner("S4_stop_race_pid_reuse", {
        "recorded_A_pid": rec_a["pid"], "recorded_A_uniqueid": rec_a["p_uniqueid"],
        "bystander_B_pid": rec_b["pid"], "bystander_B_uniqueid": rec_b["p_uniqueid"],
        "reuse_stop_decision": dec_reuse,
        "bystander_survived_reuse_stop": b_alive_after,
        "ps_second_granularity_would_collide": same_second,
        "libproc_us_granularity_distinguishes": distinct_us,
    })
    plane.cleanup()


if __name__ == "__main__":
    try:
        s0(); s1(); s2(); s3(); s4()
    finally:
        plane.cleanup()
    print("\n\n########## MACHINE-READABLE ##########")
    print(json.dumps(results))
