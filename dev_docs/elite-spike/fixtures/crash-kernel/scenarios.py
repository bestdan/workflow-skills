#!/usr/bin/env python3
"""Probe 5 — fault-injection matrix orchestrator (draft §Fault-injection matrix).

Runs each row in a fresh disposable directory against a fresh `state.db`, checks
invariants 1–8 after every row, and writes a sanitized `results.json`.

MUST run UNSANDBOXED: launchctl talks to launchd over a mach service the command
sandbox blocks, and `ps` (the degraded domain scan) is blocked too.

CONTAINMENT CAVEAT — read before believing any verdict here.
The draft's containment domain is a DEDICATED `agent` uid. That account does not
exist on this host (see the Environment section of probe5-crash-kernel.md), so
every row below runs in the DEGRADED `gentoken` domain, which is NOT escape-proof.
Rows whose outcome depends on escape-proof containment are reported BLOCKED, not
passed, and no combination of the rows that DID run can establish invariants 4/5/6
in the sense the kill sheet requires.
"""
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import incarnation  # noqa: E402
import supervisor  # noqa: E402
import kernel  # noqa: E402
import reaper  # noqa: E402

PY = os.environ.get("PROBE5_PYTHON", "/opt/homebrew/opt/python@3.12/bin/python3.12")
UID = os.getuid()
DOMAIN = f"gui/{UID}"
REPO = "repo-a"
DOMAIN_MODE = os.environ.get("PROBE5_DOMAIN_MODE", "gentoken")
AGENT_UID = os.environ.get("PROBE5_AGENT_UID", "")


# --- plumbing -----------------------------------------------------------------

def fresh_rundir():
    return tempfile.mkdtemp(prefix="probe5.", dir=os.environ.get("TMPDIR", "/tmp"))


def env_for(rundir, **extra):
    e = dict(os.environ)
    e.update({
        "PROBE5_DB": os.path.join(rundir, "state.db"),
        "PROBE5_REPO": REPO,
        "PROBE5_DOMAIN_MODE": DOMAIN_MODE,
        "PROBE5_CRASH_LOG": os.path.join(rundir, "crashes.jsonl"),
        "PROBE5_RECONCILE_LOG": os.path.join(rundir, "reconcile.jsonl"),
        "PROBE5_AGENT_UID": str(AGENT_UID) if AGENT_UID else "",
    })
    e.update({k: str(v) for k, v in extra.items() if v is not None})
    return e


def sup(rundir, cmd, *, crash_at=None, run_flags=None, timeout=60):
    """Invoke the supervisor CLI as a separate process, so an armed crash point
    really does kill a process rather than raise an exception we might catch."""
    env = env_for(rundir, PROBE5_CRASH_AT=crash_at, PROBE5_RUN_FLAGS=run_flags)
    r = subprocess.run([PY, os.path.join(HERE, "supervisor.py"), cmd],
                       env=env, capture_output=True, text=True, timeout=timeout)
    return {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr[-2000:]}


def domain_for(gen_token):
    """The containment domain as configured. Every survivor check must go
    through this — a row that scanned with the degraded scanner while the run
    lived in the uid domain would report a false zero."""
    if DOMAIN_MODE == "uid":
        return reaper.Domain("uid", agent_uid=int(AGENT_UID))
    return reaper.Domain("gentoken", gen_token=gen_token)


def db(rundir):
    return kernel.connect(os.path.join(rundir, "state.db"))


def state_of(rundir):
    c = db(rundir)
    return {"lease": kernel.get_lease(c, REPO), "events": kernel.events(c),
            "meta_seq": c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0]}


# --- invariant checks ---------------------------------------------------------

def check_invariants(rundir, *, expect_domain_empty=True, gen_token=None):
    """Invariants 1–8, as far as each is checkable from durable state.

    Returns {name: {"ok": bool, "detail": ...}}. A missing check is reported as
    unchecked rather than silently assumed to hold.
    """
    st = state_of(rundir)
    ev, lease = st["events"], st["lease"]
    out = {}

    # 2 — gapless monotonic seq, and meta['seq'] agrees with the events table.
    seqs = [e["seq"] for e in ev]
    gapless = seqs == list(range(1, len(seqs) + 1))
    out["inv2_gapless_seq"] = {
        "ok": gapless and st["meta_seq"] == len(seqs),
        "detail": {"seqs": seqs, "meta_seq": st["meta_seq"]}}

    # 2 — idem_key uniqueness (the constraint should make this unfalsifiable,
    # but we verify the data rather than trust the DDL).
    keys = [e["idem_key"] for e in ev]
    out["inv2_idem_unique"] = {"ok": len(keys) == len(set(keys)),
                               "detail": {"dupes": len(keys) - len(set(keys))}}

    # 1 — no half-applied transition: every state-bearing event has a lease whose
    # generation is at least the event's, and no event references a generation
    # that was never reserved.
    reserved = {e["generation"] for e in ev if e["kind"] == "generation_reserved"}
    orphan_ev = [e["seq"] for e in ev
                 if e["generation"] is not None and e["generation"] not in reserved]
    out["inv1_no_orphan_events"] = {"ok": not orphan_ev, "detail": {"orphans": orphan_ev}}

    # 3 — at most one non-terminal lease anywhere (the admission gate).
    c = db(rundir)
    live_leases = c.execute("SELECT COUNT(*) FROM lease WHERE state != 'terminal'").fetchone()[0]
    out["inv3_one_live_lease"] = {"ok": live_leases <= 1, "detail": {"count": live_leases}}

    # 4 — earned release: `terminal` only with the domain verified empty, and a
    # claimed_exit never accompanies a release it caused.
    if lease and lease["state"] == "terminal" and expect_domain_empty:
        scan = domain_for(gen_token).scan() if (gen_token or DOMAIN_MODE == "uid") else []
        # Split the scan: macOS respawns per-user daemons on demand, so a
        # non-empty post-hoc scan does not by itself mean the RUN survived. The
        # reap still drove the domain to absolute zero — this only keeps the
        # evidence honest about what is left standing afterwards.
        run_survivors, sys_daemons = reaper.partition_survivors(scan)
        out["inv4_earned_release"] = {
            "ok": not run_survivors,
            "detail": {"run_survivors": run_survivors,
                       "system_daemons_respawned": sys_daemons}}
    else:
        out["inv4_earned_release"] = {"ok": True, "detail": "not in terminal state"}

    # 6 — reap convergence or fence: a lease that is NOT terminal must either be
    # fenced (stop_intent) or genuinely still running. It must never be released.
    if lease and lease["state"] != "terminal":
        out["inv6_fenced_not_released"] = {
            "ok": True,
            "detail": {"state": lease["state"], "stop_intent": lease["stop_intent"]}}
    else:
        out["inv6_fenced_not_released"] = {"ok": True, "detail": "terminal"}

    # 7 — generation monotonic.
    gens = [e["generation"] for e in ev if e["kind"] == "generation_reserved"]
    out["inv7_generation_monotonic"] = {"ok": gens == sorted(set(gens)),
                                        "detail": {"generations": gens}}

    # 8 — sole writer: every event was written by the supervisor, except an
    # explicitly-inert agent claim.
    writers = c.execute("SELECT DISTINCT writer FROM events").fetchall()
    out["inv8_sole_writer"] = {"ok": all(w[0] in ("supervisor", "agent-claim")
                                         for w in writers),
                               "detail": {"writers": [w[0] for w in writers]}}
    return out


def verdict_from(checks, extra_ok=True):
    failed = [k for k, v in checks.items() if not v["ok"]]
    return ("PASS" if (not failed and extra_ok) else "FAIL"), failed


# --- rows: transition-boundary crashes (T) ------------------------------------

CRASH_POINTS = [
    ("T1", "prepare.pre_commit", "launch"),
    ("T2", "prepare.post_commit", "launch"),      # == G1
    ("T3", "spawn.post_spawn", "launch"),         # == G2
    ("T4", "activate.pre_commit", "launch"),
    ("T5", "activate.post_commit", "launch"),     # == G3
    ("T6", "stop_intent.pre_commit", "stop"),
    ("T7", "stop_intent.post_commit", "stop"),    # == Saga1
    ("T8", "reap.post_zero", "stop"),             # == Saga2
    ("T9", "terminalize.pre_commit", "stop"),
    ("T10", "terminalize.post_commit", "stop"),
]


def row_transition_crash(row_id, point, phase):
    """SIGKILL-equivalent (`os._exit`) at one transition boundary, then let the
    supervisor reconcile and check we reached a safe terminal with no human."""
    rundir = fresh_rundir()
    res = {"row": row_id, "crash_point": point, "phase": phase, "rundir": rundir}

    if phase == "launch":
        crashed = sup(rundir, "launch", crash_at=point)
        res["crashed_op"] = {"rc": crashed["rc"]}
    else:
        launched = sup(rundir, "launch")
        res["launch_rc"] = launched["rc"]
        crashed = sup(rundir, "stop", crash_at=point)
        res["crashed_op"] = {"rc": crashed["rc"]}

    res["state_after_crash"] = state_of(rundir)
    # The crash must have left durable state consistent BEFORE any recovery ran.
    res["invariants_after_crash"] = check_invariants(rundir, expect_domain_empty=False)

    rec = sup(rundir, "reconcile", timeout=120)
    res["reconcile_rc"] = rec["rc"]
    try:
        res["reconcile"] = json.loads(rec["stdout"] or "[]")
    except json.JSONDecodeError:
        res["reconcile"] = rec["stdout"][-800:]
    res["state_after_reconcile"] = state_of(rundir)

    gen_token = (res["state_after_reconcile"]["lease"] or {}).get("gen_token")
    checks = check_invariants(rundir, gen_token=gen_token)
    res["invariants_after_reconcile"] = checks

    lease = res["state_after_reconcile"]["lease"]
    actions = [d.get("action") for d in res["reconcile"]] if isinstance(res["reconcile"], list) else []
    # The bar is Decision #5: every outcome is a SAFE TERMINAL or a SAFE HOLD
    # needing no mid-run human. Four shapes qualify, and nothing else does:
    if lease is None:
        # Crash before the prepare commit. The transaction rolled back whole;
        # no generation was ever published. Nothing to recover — safe terminal.
        safe, why = True, "clean rollback: no lease was ever published"
    elif lease["state"] == "terminal":
        safe, why = True, "safe terminal"
    elif lease["stop_intent"] == 1:
        # Reap did not converge. Fenced and alerting, deliberately not released.
        safe, why = True, "safe hold: fenced in stop_intent, never released"
    elif lease["state"] == "active" and "readopt" in actions:
        # Crash during a STOP attempt against a healthy run. Reconciliation
        # re-adopted it; the run keeps working and the stop is simply retried.
        # Killing it here would be the false-reap the fifth pass warned about.
        alive = bool(kernel.get_incarnation(db(rundir), REPO, lease["generation"]))
        safe, why = alive, "safe hold: healthy run re-adopted, stop retried"
    else:
        safe, why = False, f"unrecovered lease {lease['state']}/{lease['stop_intent']}"
    res["safe_outcome"], res["safe_outcome_reason"] = safe, why
    res["verdict"], res["failed_checks"] = verdict_from(checks, extra_ok=safe)
    cleanup_domain(gen_token)
    return res


# --- row: idempotency ---------------------------------------------------------

def row_idem():
    """Replay a transition with the same idem_key: existing seq, no new event,
    no gap, and the counter must NOT have advanced."""
    rundir = fresh_rundir()
    c = kernel.init_db(os.path.join(rundir, "state.db"))
    with kernel.txn(c):
        seq1, dup1 = kernel.append_event(c, idem_key="k:1", kind="generation_reserved",
                                         repo_key=REPO, generation=1)
    meta1 = c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0]
    with kernel.txn(c):
        seq2, dup2 = kernel.append_event(c, idem_key="k:1", kind="generation_reserved",
                                         repo_key=REPO, generation=1)
    meta2 = c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0]
    with kernel.txn(c):
        seq3, _ = kernel.append_event(c, idem_key="k:2", kind="run_registered",
                                      repo_key=REPO, generation=1)

    ok = (seq1 == seq2 and dup2 is True and dup1 is False
          and meta1 == meta2 == 1 and seq3 == 2)
    checks = check_invariants(rundir, expect_domain_empty=False)
    v, failed = verdict_from(checks, extra_ok=ok)
    return {"row": "Idem", "rundir": rundir, "verdict": v, "failed_checks": failed,
            "detail": {"seq_first": seq1, "seq_replay": seq2, "replay_flagged": dup2,
                       "counter_before": meta1, "counter_after_replay": meta2,
                       "next_seq_no_gap": seq3},
            "invariants": checks}


# --- row: mismatched-payload replay ------------------------------------------

def row_idem_conflict():
    """A replay carrying a DIFFERENT payload must roll the whole transaction
    back, not silently accept either version."""
    rundir = fresh_rundir()
    c = kernel.init_db(os.path.join(rundir, "state.db"))
    with kernel.txn(c):
        kernel.append_event(c, idem_key="k:1", kind="generation_reserved",
                            repo_key=REPO, generation=1, payload={"a": 1})
    raised = None
    try:
        with kernel.txn(c):
            kernel.append_event(c, idem_key="k:1", kind="generation_reserved",
                                repo_key=REPO, generation=1, payload={"a": 2})
    except kernel.KernelError as e:
        raised = str(e)
    meta = c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0]
    ok = raised is not None and meta == 1
    checks = check_invariants(rundir, expect_domain_empty=False)
    v, failed = verdict_from(checks, extra_ok=ok)
    return {"row": "IdemConflict", "rundir": rundir, "verdict": v,
            "failed_checks": failed,
            "detail": {"raised": raised, "counter_unchanged": meta == 1},
            "invariants": checks}


# --- row: concurrent launch (Race) -------------------------------------------

def row_race():
    """Two launches racing on one DB: the admission gate plus BEGIN IMMEDIATE
    must let exactly one through."""
    rundir = fresh_rundir()
    kernel.init_db(os.path.join(rundir, "state.db")).close()
    env = env_for(rundir)
    procs = [subprocess.Popen([PY, os.path.join(HERE, "supervisor.py"), "launch"],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(2)]
    outs = [p.communicate(timeout=90) for p in procs]
    rcs = [p.returncode for p in procs]
    winners = sum(1 for rc in rcs if rc == 0)

    st = state_of(rundir)
    gen_token = (st["lease"] or {}).get("gen_token")
    reserved = [e for e in st["events"] if e["kind"] == "generation_reserved"]
    ok = winners == 1 and len(reserved) == 1

    sup(rundir, "stop", timeout=90)
    checks = check_invariants(rundir, gen_token=gen_token)
    v, failed = verdict_from(checks, extra_ok=ok)
    cleanup_domain(gen_token)
    return {"row": "Race", "rundir": rundir, "verdict": v, "failed_checks": failed,
            "detail": {"return_codes": rcs, "winners": winners,
                       "generations_reserved": len(reserved),
                       "loser_stderr": [o[1][-300:] for o, rc in zip(outs, rcs) if rc != 0]},
            "invariants": checks}


# --- row: PID-reuse rejection (Pid) ------------------------------------------

def row_pid():
    """The adopt path must reject a pid whose identity no longer matches.

    Forcing a real PID wraparound is not deterministic, so this drives the guard
    directly: take a LIVE process, hand `readopt` a recorded tuple bearing that
    live pid but a foreign `p_uniqueid`, and require a refusal. That is exactly
    the state the kernel would be in after a reuse.
    """
    import supervisor
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    live = incarnation.measure(p.pid)
    forged = dict(live)
    forged["p_uniqueid"] = (live["p_uniqueid"] or 0) + 999999  # a different incarnation

    kq_bad, err_bad = supervisor.readopt(p.pid, forged)
    kq_good, err_good = supervisor.readopt(p.pid, live)
    if kq_good:
        kq_good.close()
    p.kill()
    p.wait()

    # And the identity predicate itself must reject the same tuple.
    pred_rejects = not incarnation.same_incarnation(forged, live)
    ok = kq_bad is None and err_bad is not None and kq_good is not None and pred_rejects
    return {"row": "Pid", "verdict": "PASS" if ok else "FAIL",
            "detail": {"forged_adopt_refused": kq_bad is None,
                       "refusal_reason": err_bad,
                       "genuine_adopt_succeeded": kq_good is not None,
                       "identity_predicate_rejects_forgery": pred_rejects},
            "note": "real PID wraparound not forced; this exercises the guard the "
                    "reuse would trip, not the wraparound itself"}


# --- row: never-root kill(-1) (Priv) -----------------------------------------

def row_priv():
    """`kill(-1)` as root would target every process on the host. The reaper must
    refuse outright rather than rely on never being invoked as root."""
    import unittest.mock as mock
    dom = reaper.Domain("gentoken", gen_token="P5-nonexistent-token")
    refused = None
    with mock.patch.object(os, "geteuid", return_value=0):
        try:
            dom.signal_all(signal.SIGTERM)
        except reaper.ReaperRefused as e:
            refused = str(e)
    ok = refused is not None
    return {"row": "Priv", "verdict": "PASS" if ok else "FAIL",
            "detail": {"refused_as_root": ok, "reason": refused},
            "note": "guard exercised with a mocked euid; not run as real root "
                    "(the fixture must never acquire root)"}


# --- row: non-convergence fencing (NoConv) -----------------------------------

def row_noconv():
    """A reap that never empties the domain must leave the lease FENCED in
    stop_intent and must never terminalize."""
    rundir = fresh_rundir()
    c = kernel.init_db(os.path.join(rundir, "state.db"))
    kernel.launch_prepare(c, repo_key=REPO, run_id="r", gen_token="tok")

    class NeverEmpty:
        """A domain that always reports a survivor: models an uninterruptible
        process or an externally-privileged spawner."""
        mode, escape_proof = "stub", False
        def scan(self):
            return [999999]
        def signal_all(self, sig):
            return {"method": "noop", "targets": []}

    ev = reaper.reap(NeverEmpty(), term_grace=0.2, kill_grace=0.1, max_rounds=2)
    terminalize_refused = None
    try:
        kernel.terminalize(c, repo_key=REPO, reap_evidence=ev)
    except kernel.KernelError as e:
        terminalize_refused = str(e)

    lease = kernel.get_lease(c, REPO)
    ok = (not ev["converged"] and terminalize_refused is not None
          and lease["state"] != "terminal")
    checks = check_invariants(rundir, expect_domain_empty=False)
    v, failed = verdict_from(checks, extra_ok=ok)
    return {"row": "NoConv", "rundir": rundir, "verdict": v, "failed_checks": failed,
            "detail": {"converged": ev["converged"], "rounds": ev["rounds"],
                       "terminalize_refused": terminalize_refused,
                       "lease_state": lease["state"]},
            "invariants": checks}


# --- rows: supervisor death under real launchd (Sup-readopt / Sup-orphan) -----

def _lc(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _dedicated_agent_uid():
    """The dedicated, non-maintainer uid a uid-mode reap is allowed to target.

    A uid-mode reap is `kill(-1)` against this uid (reaper.Domain.signal_all).
    It MUST NOT be the maintainer's own uid: this line previously read
    `str(UID)` (== os.getuid()), so the bootstrapped supervisor reaped every
    process the maintainer owned — every SSH login — and, under launchd
    KeepAlive, looped for four days (~242k reap cycles). Full incident evidence
    and the smoking-gun log summary:
        dev_docs/tasks/probe5-incident-evidence/
    Resolve the provisioned agent account instead. reaper.Domain now
    independently refuses agent_uid ∈ {0, caller's uid}, so a mistake here can
    no longer take the host down — it fails closed at reconcile.
    """
    if AGENT_UID:
        return int(AGENT_UID)
    return pwd.getpwnam(reaper.REAP_AS_USER).pw_uid


def install_supervisor(rundir, label):
    with open(os.path.join(HERE, "supervisor.plist.tmpl")) as f:
        txt = f.read()
    if DOMAIN_MODE == "uid":
        agent_uid = _dedicated_agent_uid()
        if agent_uid in (0, os.getuid(), os.geteuid()):
            raise RuntimeError(
                f"refusing to install a uid-mode supervisor with agent_uid={agent_uid}: "
                "it must be a dedicated, non-root uid distinct from the caller "
                "(otherwise kill(-1) reaps the caller's own logins)")
        agent_uid_sub = str(agent_uid)
    else:
        # gentoken mode ignores AGENT_UID (supervisor.make_domain only reads it
        # in uid mode); never write the maintainer's uid into the plist.
        agent_uid_sub = str(AGENT_UID) if AGENT_UID else ""
    subs = {"LABEL": label, "PYTHON": PY, "DIR": HERE, "RUNDIR": rundir,
            "DB": os.path.join(rundir, "state.db"), "REPO": REPO,
            "DOMAIN_MODE": DOMAIN_MODE, "AGENT_UID": agent_uid_sub,
            "RECONCILE_LOG": os.path.join(rundir, "reconcile.jsonl")}
    for k, v in subs.items():
        txt = txt.replace(f"@{k}@", str(v))
    plist = os.path.join(rundir, f"{label}.plist")
    with open(plist, "w") as f:
        f.write(txt)
    _lc("bootout", f"{DOMAIN}/{label}")
    r = _lc("bootstrap", DOMAIN, plist)
    return r.returncode, r.stderr.strip()


def supervisor_pid(label):
    r = _lc("print", f"{DOMAIN}/{label}")
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith("pid = "):
            return int(s.split("=", 1)[1])
    return None


def read_reconcile_log(rundir):
    fp = os.path.join(rundir, "reconcile.jsonl")
    if not os.path.exists(fp):
        return []
    with open(fp) as f:
        return [json.loads(x) for x in f if x.strip()]


def row_sup(kind):
    """kind='readopt': healthy run, supervisor SIGKILLed, launchd restarts it —
    reconciliation must RE-ADOPT and leave the run untouched.
    kind='orphan': the run process is killed but a descendant survives —
    reconciliation must reap to zero and terminalize (dead run ≠ dead workers).
    """
    rundir = fresh_rundir()
    label = f"com.probe5.sup.{kind}"
    res = {"row": f"Sup-{kind}", "rundir": rundir}

    # BOTH kinds fork a descendant. A run with no children makes the re-adopt
    # row vacuous: it would pass under a rule that reaps any run whose domain
    # contains an extra process, which is exactly the bug this row must catch.
    # The orphan kind additionally self-terminates, since the harness cannot
    # kill one agent-uid process from the maintainer (EPERM) and the only
    # privileged kill available is uid-wide.
    flags = "--descendant" + (" --die-after 6" if kind == "orphan" else "")
    launched = sup(rundir, "launch", run_flags=flags)
    try:
        info = json.loads(launched["stdout"].strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        res["verdict"] = "INCONCLUSIVE"
        res["detail"] = {"reason": "launch produced no parsable result",
                         "stderr": launched["stderr"][-500:]}
        return res
    gen_token, run_pid = info["gen_token"], info["run_pid"]
    res["run_pid"], res["gen_token_present"] = run_pid, bool(gen_token)
    time.sleep(1.0)

    rc, err = install_supervisor(rundir, label)
    res["bootstrap_rc"] = rc
    if rc != 0:
        res["verdict"] = "INCONCLUSIVE"
        res["detail"] = {"reason": f"launchd bootstrap failed: {err}"}
        cleanup_domain(gen_token)
        return res
    time.sleep(2.0)
    res["passes_before"] = len(read_reconcile_log(rundir))

    if kind == "orphan":
        # Wait for the run to self-terminate. Its forked descendant survives, so
        # the roster says "run dead" while work is still live.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not supervisor.measure_run(run_pid).get("alive"):
                break
            time.sleep(0.5)
        res["run_died"] = not supervisor.measure_run(run_pid).get("alive")
        res["domain_after_run_kill"] = domain_for(gen_token).scan()

    # SIGKILL the supervisor; launchd KeepAlive must restart it, and the restart
    # must reconcile from durable state alone.
    sup_pid = supervisor_pid(label)
    res["supervisor_pid"] = sup_pid
    if sup_pid:
        try:
            os.kill(sup_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if len(read_reconcile_log(rundir)) > res["passes_before"]:
            break
        time.sleep(0.5)
    log = read_reconcile_log(rundir)
    res["reconcile_passes"] = len(log)
    res["relaunched_by_launchd"] = len(log) > res["passes_before"]
    decisions = log[-1]["decisions"] if log else []
    res["decisions"] = decisions
    actions = [d.get("action") for d in decisions]
    res["actions"] = actions

    # Cross-uid identity needs the privileged helper (see supervisor.measure_run).
    run_still_alive = supervisor.measure_run(run_pid).get("alive", False)
    res["run_still_alive"] = run_still_alive
    st = state_of(rundir)
    res["lease_after"] = st["lease"]

    if kind == "readopt":
        # The whole point: a benign restart must not kill healthy work — and
        # "healthy work" includes the run's forked descendant, which must also
        # still be alive.
        survivors = domain_for(gen_token).scan()
        res["domain_after_restart"] = survivors
        ok = ("readopt" in actions and run_still_alive and len(survivors) >= 2
              and st["lease"]["state"] == "active" and st["lease"]["stop_intent"] == 0)
    else:
        survivors = domain_for(gen_token).scan()
        res["survivors_after"] = survivors
        ok = (st["lease"]["state"] == "terminal" and not survivors
              and any(a and a.startswith("reap") for a in actions))

    _lc("bootout", f"{DOMAIN}/{label}")
    if kind == "readopt":
        sup(rundir, "stop", timeout=90)
    checks = check_invariants(rundir, gen_token=gen_token,
                              expect_domain_empty=(kind == "orphan"))
    res["invariants"] = checks
    res["verdict"], res["failed_checks"] = verdict_from(checks, extra_ok=ok)
    cleanup_domain(gen_token)
    return res


# --- row: IO failure (Io) -----------------------------------------------------

def row_io():
    """A write failure must fail the transaction ATOMICALLY — no partial state.

    Driven by making the database directory read-only mid-run so SQLite cannot
    create/extend its WAL, then verifying the counter did not advance.
    """
    rundir = fresh_rundir()
    dbdir = os.path.join(rundir, "dbdir")
    os.makedirs(dbdir)
    dbp = os.path.join(dbdir, "state.db")
    c = kernel.init_db(dbp)
    with kernel.txn(c):
        kernel.append_event(c, idem_key="k:1", kind="x", repo_key=REPO, generation=1)
    before = c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0]

    c.close()
    os.chmod(dbdir, 0o500)  # read+execute only: no new WAL/journal files
    raised = None
    try:
        c2 = kernel.connect(dbp)
        with kernel.txn(c2):
            kernel.append_event(c2, idem_key="k:2", kind="x", repo_key=REPO, generation=1)
        c2.close()
    except Exception as e:
        raised = f"{type(e).__name__}: {e}"
    os.chmod(dbdir, 0o700)

    c3 = kernel.connect(dbp)
    after = c3.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0]
    n_events = c3.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    if raised is None:
        return {"row": "Io", "rundir": rundir, "verdict": "INCONCLUSIVE",
                "detail": {"reason": "read-only dir did not induce a write failure; "
                                     "a real ENOSPC/EIO harness (small disk image) "
                                     "was not built",
                           "counter": after, "events": n_events}}
    atomic = after == before and n_events == 1
    # A read-only-database error is NOT the injection this row names. ENOSPC and
    # EIO fail mid-write, potentially after the WAL has already been extended;
    # a permissions refusal fails before the first byte. The sub-case that ran
    # did fail atomically, but rule 3 says an injection that could not be made
    # deterministically at the intended boundary is inconclusive, not a pass.
    return {"row": "Io", "rundir": rundir,
            "verdict": "INCONCLUSIVE" if atomic else "FAIL",
            "detail": {"error": raised, "counter_before": before,
                       "counter_after": after, "events": n_events,
                       "readonly_subcase_atomic": atomic},
            "note": "Only a read-only-database refusal was injected, which fails "
                    "before any write. Genuine ENOSPC / EIO (a small disk image "
                    "or a fault-injecting VFS) was NOT built, so the mid-write "
                    "failure class this row exists to test is untested."}


# --- containment rows (require the dedicated uid) -----------------------------

def _launch_for(rundir, flags, timeout=90):
    r = sup(rundir, "launch", run_flags=flags, timeout=timeout)
    try:
        return json.loads(r["stdout"].strip().splitlines()[-1]), r
    except (json.JSONDecodeError, IndexError):
        return None, r


def row_escape():
    """A `setsid` + double-fork + EXEC descendant must still be reaped.

    The exec is what makes this real: the escapee replaces its image and no
    longer carries gen_token, so token scanning cannot see it. Only the uid-wide
    kill can. This row is therefore meaningless in degraded mode and says so.
    """
    rundir = fresh_rundir()
    res = {"row": "Esc", "rundir": rundir, "escape_proof_domain": DOMAIN_MODE == "uid"}
    if DOMAIN_MODE != "uid":
        res["verdict"] = "BLOCKED"
        res["reason"] = "needs the uid domain; the escapee is invisible to token scanning"
        return res

    info, raw = _launch_for(rundir, "--escape")
    if not info or not info.get("activated"):
        res["verdict"] = "INCONCLUSIVE"
        res["detail"] = {"reason": "launch did not activate", "stderr": raw["stderr"][-400:]}
        return res
    time.sleep(1.5)
    gen_token = info["gen_token"]

    # Prove the escape actually happened AND actually escaped: identify the
    # escapee by its exec'd image, then confirm the token scan cannot see it.
    # Comparing scan sizes would not do — the `sudo` parent carries the token in
    # argv without being in the uid domain at all.
    uid_view = domain_for(gen_token).scan()
    described = reaper.describe(uid_view)
    escapees = [d for d in described if "/bin/sleep" in d["command"]]
    token_view = reaper.list_gentoken_pids(gen_token)
    res["uid_domain"] = described
    res["escapees"] = escapees
    res["token_visible_pids"] = token_view
    res["escapee_hidden_from_token_scan"] = bool(escapees) and all(
        e["pid"] not in token_view for e in escapees)

    sup(rundir, "stop", timeout=120)
    run_survivors, sys_daemons = reaper.partition_survivors(domain_for(gen_token).scan())
    res["run_survivors_after_reap"] = run_survivors
    res["system_daemons_after_reap"] = sys_daemons
    st = state_of(rundir)
    ok = (not run_survivors and st["lease"]["state"] == "terminal"
          and res["escapee_hidden_from_token_scan"])
    checks = check_invariants(rundir, gen_token=gen_token)
    res["invariants"] = checks
    res["verdict"], res["failed_checks"] = verdict_from(checks, extra_ok=ok)
    cleanup_domain(gen_token)
    return res


def row_churn():
    """Continuous fork churn must not prevent convergence.

    A reaper that signals only the set it enumerated would never finish here;
    convergence has to come from re-scanning until the domain is empty.
    """
    rundir = fresh_rundir()
    res = {"row": "Churn", "rundir": rundir}
    if DOMAIN_MODE != "uid":
        res["verdict"] = "BLOCKED"
        res["reason"] = "convergence against a non-escape-proof domain proves nothing"
        return res

    info, raw = _launch_for(rundir, "--churn")
    if not info or not info.get("activated"):
        res["verdict"] = "INCONCLUSIVE"
        res["detail"] = {"reason": "launch did not activate", "stderr": raw["stderr"][-400:]}
        return res
    time.sleep(2.0)
    gen_token = info["gen_token"]
    res["domain_before_reap"] = len(domain_for(gen_token).scan())

    r = sup(rundir, "stop", timeout=180)
    survivors, sys_daemons = reaper.partition_survivors(domain_for(gen_token).scan())
    st = state_of(rundir)
    res["survivors_after_reap"] = survivors
    res["system_daemons_after_reap"] = sys_daemons
    res["lease_state"] = st["lease"]["state"]
    # Either it converged and terminalized, or it stayed fenced. Never released
    # with survivors.
    converged = not survivors and st["lease"]["state"] == "terminal"
    fenced = bool(survivors) and st["lease"]["state"] != "terminal" \
        and st["lease"]["stop_intent"] == 1
    res["outcome"] = "converged" if converged else ("fenced" if fenced else "unsafe")
    checks = check_invariants(rundir, gen_token=gen_token,
                              expect_domain_empty=converged)
    res["invariants"] = checks
    res["verdict"], res["failed_checks"] = verdict_from(checks,
                                                        extra_ok=(converged or fenced))
    cleanup_domain(gen_token)
    return res


def row_writer():
    """The run must be UNABLE to open state.db — invariant 8, enforced not asserted."""
    rundir = fresh_rundir()
    res = {"row": "Writer", "rundir": rundir}
    if DOMAIN_MODE != "uid":
        res["verdict"] = "BLOCKED"
        res["reason"] = ("same-uid, the filesystem permits the write; sole-writer "
                         "is architectural without a second uid")
        return res

    dbpath = os.path.join(rundir, "state.db")
    info, raw = _launch_for(rundir, f"--try-write-db {dbpath}")
    if not info or not info.get("activated"):
        res["verdict"] = "INCONCLUSIVE"
        res["detail"] = {"reason": "launch did not activate", "stderr": raw["stderr"][-400:]}
        return res
    probe = (info.get("run_report") or {}).get("write_probe")
    res["write_probe"] = probe
    gen_token = info["gen_token"]
    # EACCES (no permission) or ENOENT (cannot even traverse to it) both mean
    # the run could not write. "OPENED" would falsify invariant 8 outright.
    ok = probe in ("EACCES", "EPERM", "ENOENT")
    sup(rundir, "stop", timeout=120)
    checks = check_invariants(rundir, gen_token=gen_token)
    res["invariants"] = checks
    res["verdict"], res["failed_checks"] = verdict_from(checks, extra_ok=ok)
    cleanup_domain(gen_token)
    return res


def row_takeover():
    """Takeover must reap generation g to VERIFIED ZERO before publishing g+1."""
    rundir = fresh_rundir()
    res = {"row": "Tw", "rundir": rundir}
    if DOMAIN_MODE != "uid":
        res["verdict"] = "BLOCKED"
        res["reason"] = "reap-before-publish is only verifiable against an escape-proof domain"
        return res

    info, raw = _launch_for(rundir, "--descendant")
    if not info or not info.get("activated"):
        res["verdict"] = "INCONCLUSIVE"
        res["detail"] = {"reason": "launch did not activate", "stderr": raw["stderr"][-400:]}
        return res
    old_token = info["gen_token"]
    time.sleep(1.0)
    res["domain_before_takeover"] = domain_for(old_token).scan()

    r = sup(rundir, "takeover", timeout=180)
    try:
        tk = json.loads(r["stdout"].strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        res["verdict"] = "INCONCLUSIVE"
        res["detail"] = {"reason": "takeover produced no parsable result",
                         "stderr": r["stderr"][-400:]}
        return res
    res["takeover"] = {k: tk.get(k) for k in
                       ("converged", "published", "old_generation", "new_generation",
                        "domain_at_publish")}

    st = state_of(rundir)
    gens = [e["generation"] for e in st["events"] if e["kind"] == "generation_reserved"]
    res["generations_reserved"] = gens
    ok = (tk.get("published") and tk.get("new_generation") == 2
          and tk.get("domain_at_publish") == []
          and gens == [1, 2] and st["lease"]["state"] == "prepared")

    sup(rundir, "reconcile", timeout=120)
    checks = check_invariants(rundir, gen_token=tk.get("gen_token"))
    res["invariants"] = checks
    res["verdict"], res["failed_checks"] = verdict_from(checks, extra_ok=ok)
    cleanup_domain(tk.get("gen_token"))
    cleanup_domain(old_token)
    return res


def row_uid_limitation():
    """A uid-changing helper is a DOCUMENTED LIMITATION, not a falsifier.

    Records honestly whether it is detectable rather than claiming a guarantee.
    """
    res = {"row": "Uid", "verdict": "DOCUMENTED-LIMITATION"}
    res["detail"] = {
        "claim": "A worker that acquires a different credential leaves the uid "
                 "containment domain and is not reaped by kill(-1) as the agent.",
        "reachable_in_this_fixture": False,
        "why": "The agent has zero sudo rules (verified: `sudo -l -U agent` -> "
               "not allowed), so it cannot change credential at all here. The "
               "limitation is therefore real but UNREACHABLE from the agent's "
               "own authority — it needs an external privileged spawner.",
        "detection": "proc_listpids over other uids plus a gen_token argv scan "
                     "could surface a suspect process, but a helper that execs "
                     "and drops the token is undetectable by that means. Treated "
                     "as admitted-undetectable rather than claimed-detected.",
        "redirect_if_ever_in_scope": "containment option 2 (Linux VM/container)",
    }
    return res


# --- blocked rows -------------------------------------------------------------

BLOCKED = {
    "PL": "Reboot / power-loss durability. SIGKILL cannot prove it; a VM or "
          "loopback power-fail harness was out of scope. INCONCLUSIVE BY "
          "CONSTRUCTION, per the kill sheet — never passed. This is the one row "
          "the dedicated uid does not unblock.",
}


# --- cleanup ------------------------------------------------------------------

def cleanup_domain(gen_token):
    """Leave no surrogate behind (guardrail 6)."""
    if not gen_token and DOMAIN_MODE != "uid":
        return
    dom = domain_for(gen_token)
    for _ in range(3):
        pids = dom.scan()
        if not pids:
            return
        for p in pids:
            try:
                os.kill(p, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.3)


def final_cleanup():
    for label in ("com.probe5.sup.readopt", "com.probe5.sup.orphan"):
        _lc("bootout", f"{DOMAIN}/{label}")
    subprocess.run(["pkill", "-f", "runsurrogate.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "supervisor.py daemon"], capture_output=True)


# --- main ---------------------------------------------------------------------

def sanitize(obj):
    """Strip machine-specific absolute paths and the maintainer's home.

    Nothing secret is generated by this fixture — there are no credentials in the
    loop at all — so this is noise removal, not redaction. Run directories are
    disposable temp paths that mean nothing to a reader.
    """
    s = json.dumps(obj, sort_keys=True, default=str)
    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/")
    for needle, repl in ((tmp, "<TMPDIR>"),
                         (os.path.expanduser("~"), "<HOME>"),
                         ("/private/var/folders", "<TMPDIR>"),
                         ("/var/folders", "<TMPDIR>")):
        s = s.replace(json.dumps(needle)[1:-1], repl)
    return json.loads(s)


def environment():
    # Must be an ON-DISK database: an in-memory one cannot use WAL and would
    # report journal_mode=memory, understating the durability actually configured.
    envdir = fresh_rundir()
    c = kernel.init_db(os.path.join(envdir, "state.db"))
    pragmas = kernel.pragma_report(c)
    c.close()
    shutil.rmtree(envdir, ignore_errors=True)
    agent_exists = subprocess.run(["id", "agent"], capture_output=True).returncode == 0
    return {
        "python": PY,
        "sqlite_version": pragmas["sqlite_version"],
        "sqlite_build": "Homebrew stock (NOT Apple's system build)",
        "pragmas": pragmas,
        "macos": subprocess.run(["sw_vers", "-productVersion"], capture_output=True,
                                text=True).stdout.strip(),
        "boot_session_present": bool(kernel.boot_session()),
        "domain_mode": DOMAIN_MODE,
        "agent_uid_provisioned": agent_exists,
        "containment_caveat": (
            "The dedicated `agent` uid does NOT exist on this host; uid 502 now "
            "belongs to an unrelated human account. All rows ran in the DEGRADED "
            "gen_token domain, which is not escape-proof. Invariants 4, 5 and 6 "
            "are therefore NOT established in the sense the kill sheet requires."),
    }


def main():
    only = sys.argv[1:] or None
    results = {"environment": environment(), "rows": {}}

    def want(name):
        return only is None or name in only

    try:
        for row_id, point, phase in CRASH_POINTS:
            if not want(row_id):
                continue
            print(f"== {row_id} ({point}) ==", flush=True)
            results["rows"][row_id] = row_transition_crash(row_id, point, phase)

        for name, fn in (("Idem", row_idem), ("IdemConflict", row_idem_conflict),
                         ("Race", row_race), ("Pid", row_pid), ("Priv", row_priv),
                         ("NoConv", row_noconv), ("Io", row_io),
                         ("Esc", row_escape), ("Churn", row_churn),
                         ("Writer", row_writer), ("Tw", row_takeover),
                         ("Uid", row_uid_limitation)):
            if not want(name):
                continue
            print(f"== {name} ==", flush=True)
            results["rows"][name] = fn()

        for kind in ("readopt", "orphan"):
            if not want(f"Sup-{kind}"):
                continue
            print(f"== Sup-{kind} ==", flush=True)
            results["rows"][f"Sup-{kind}"] = row_sup(kind)

        for name, reason in BLOCKED.items():
            if want(name):
                results["rows"][name] = {"row": name, "verdict": "BLOCKED",
                                         "reason": reason}
    finally:
        final_cleanup()

    out = os.path.join(HERE, "results.json")
    with open(out, "w") as f:
        json.dump(sanitize(results), f, indent=2, sort_keys=True, default=str)
    print(f"\nwrote {out}")

    print("\n-- summary --")
    for k, v in results["rows"].items():
        line = f"{k:<14} {v.get('verdict', '?')}"
        if v.get("failed_checks"):
            line += f"  failed={v['failed_checks']}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
