#!/usr/bin/env python3
"""Probe 5 — containment-domain scan + the bounded, verified reap.

The draft's containment primitive is a DEDICATED uid: `kill(-1, sig)` sent AS
that uid signals every process of that uid, with no `setsid`/double-fork escape.
`p_uniqueid` verifies a specific process; it never selects the kill target.

TWO domain modes, and the difference is load-bearing for what this fixture can
claim:

  uid       — the real primitive. proc_listpids(PROC_UID_ONLY) enumerates, and
              `kill(-1)` executed AS the agent uid reaps. ESCAPE-PROOF.
              REQUIRES the provisioned `agent` uid.

  gentoken  — DEGRADED fallback. Enumerates by scanning argv for the run's
              `gen_token`. This is the draft's *pre-registration discovery*
              mechanism, NOT a containment boundary: any descendant that execs
              without the token, or that the scan races, is invisible. It cannot
              establish invariants 4/5/6 and any row that depends on
              escape-proofness must be classified BLOCKED, never passed.

Never `root` `kill(-1)` — root's target set is every process on the machine.
`reap()` refuses outright if it finds itself running as uid 0.
"""
import ctypes
import ctypes.util
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import incarnation  # noqa: E402

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

PROC_UID_ONLY = 4


class ReaperRefused(Exception):
    pass


def list_uid_pids(uid):
    """Every live pid whose effective uid is `uid`, straight from libproc."""
    n = libc.proc_listpids(PROC_UID_ONLY, ctypes.c_uint32(uid), None, 0)
    if n <= 0:
        return []
    # Size up generously: the process table can grow between the sizing call and
    # the fetch, and a truncated buffer would silently under-report survivors —
    # which would let a reap "verify zero" while processes are still alive.
    cap = n * 4 + 4096
    buf = (ctypes.c_int32 * (cap // 4))()
    got = libc.proc_listpids(PROC_UID_ONLY, ctypes.c_uint32(uid), buf, cap)
    if got <= 0:
        return []
    return sorted({p for p in buf[:got // 4] if p > 0})


def list_gentoken_pids(gen_token):
    """DEGRADED enumeration: processes carrying `gen_token` in argv."""
    r = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or gen_token not in line:
            continue
        pid_s = line.split(None, 1)[0]
        if pid_s.isdigit():
            out.append(int(pid_s))
    me = os.getpid()
    return sorted(p for p in out if p != me)


class Domain:
    """The set of processes a reap is allowed to, and must, empty."""

    def __init__(self, mode, *, agent_uid=None, gen_token=None, exclude=()):
        if mode not in ("uid", "gentoken"):
            raise ValueError(f"unknown domain mode {mode!r}")
        if mode == "uid" and agent_uid is None:
            raise ValueError("uid mode needs agent_uid")
        if mode == "gentoken" and not gen_token:
            raise ValueError("gentoken mode needs gen_token")
        self.mode = mode
        self.agent_uid = agent_uid
        self.gen_token = gen_token
        # Only ever used in uid mode when the fixture is (wrongly) sharing a uid
        # with the supervisor; in a real deployment the domain is exclusive.
        self.exclude = set(exclude) | {os.getpid()}

    @property
    def escape_proof(self):
        return self.mode == "uid"

    def scan(self):
        pids = (list_uid_pids(self.agent_uid) if self.mode == "uid"
                else list_gentoken_pids(self.gen_token))
        return [p for p in pids if p not in self.exclude]

    def signal_all(self, sig):
        """Send `sig` to the whole domain.

        In uid mode the correct call is `kill(-1, sig)` issued AS the agent uid —
        one syscall, no enumerate-then-signal window, so no PID-reuse TOCTOU. We
        only get to make that call when we ARE the agent uid; otherwise we
        enumerate, which reintroduces the race and is recorded as such.
        """
        if os.geteuid() == 0:
            raise ReaperRefused("refusing to signal as root: kill(-1) as root "
                                "targets every process on the host")
        if self.mode == "uid" and os.geteuid() == self.agent_uid:
            os.kill(-1, sig)
            return {"method": "kill(-1)", "targets": None}
        targets = self.scan()
        for p in targets:
            try:
                os.kill(p, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
        return {"method": "enumerated-kill", "targets": targets}


def reap(domain, *, term_grace=2.0, kill_grace=2.0, max_rounds=15, poll=0.1):
    """TERM → bounded wait → KILL → RE-SCAN UNTIL ZERO.

    Returns evidence including `converged`. Convergence is the ONLY thing that
    authorizes terminalization; on non-convergence the caller must leave the
    lease fenced in `stop_intent` and alert (invariant 6). It never claims
    success from "we sent the signals".
    """
    ev = {"mode": domain.mode, "escape_proof": domain.escape_proof,
          "rounds": 0, "converged": False, "survivors": [], "signals": []}
    before = domain.scan()
    ev["initial"] = before
    if not before:
        ev["converged"] = True
        return ev

    ev["signals"].append({"sig": "TERM", **domain.signal_all(signal.SIGTERM)})
    deadline = time.monotonic() + term_grace
    while time.monotonic() < deadline:
        if not domain.scan():
            ev["converged"] = True
            ev["rounds"] = 1
            return ev
        time.sleep(poll)

    for rnd in range(1, max_rounds + 1):
        ev["rounds"] = rnd
        # Re-scan every round: fork churn means the survivor set at round N is
        # not the set we signalled at round N-1. Converging requires the SCAN to
        # come back empty, not the signal to have been sent.
        if not domain.scan():
            ev["converged"] = True
            return ev
        ev["signals"].append({"sig": "KILL", "round": rnd,
                              **domain.signal_all(signal.SIGKILL)})
        deadline = time.monotonic() + kill_grace
        while time.monotonic() < deadline:
            if not domain.scan():
                ev["converged"] = True
                return ev
            time.sleep(poll)

    ev["survivors"] = domain.scan()
    ev["converged"] = not ev["survivors"]
    return ev


def verify_incarnation_dead(recorded):
    """True iff the recorded incarnation is gone. Identity-checked, so a reused
    pid reads as DEAD (a different incarnation) rather than as still-alive."""
    live = incarnation.measure(recorded["pid"])
    return not incarnation.same_incarnation(recorded, live)
