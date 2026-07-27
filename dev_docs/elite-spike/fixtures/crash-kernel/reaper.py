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
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import incarnation  # noqa: E402

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

PROC_UID_ONLY = 4

# A gen_token must be non-blank, whitespace-free, and long enough that it cannot
# match unrelated command lines by accident. A blank or too-broad token in
# gentoken mode would make the argv scan select every process — the enumerated
# analogue of the uid-mode kill(-1) blast radius.
_VALID_GEN_TOKEN = re.compile(r"^[A-Za-z0-9._-]{12,}$")

# The Stage-2 privileged path. Root-owned, agent-unwritable, and scoped in
# sudoers to exactly these commands with runas=(agent) — never root.
REAP_AS_USER = os.environ.get("PROBE5_AGENT_USER", "agent")
REAP_HELPER = os.environ.get("PROBE5_REAP_HELPER", "/usr/local/probe5/p5-reap")
SPAWN_HELPER = os.environ.get("PROBE5_SPAWN_HELPER", "/usr/local/probe5/p5-spawn")
MEASURE_HELPER = os.environ.get("PROBE5_MEASURE_HELPER", "/usr/local/probe5/p5-measure")


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
    """DEGRADED enumeration: processes carrying `gen_token` in argv.

    Never selects a process owned by the CALLER's own uid. A legitimate run
    lives under the dedicated agent uid (spawned via p5-spawn/sudo), never the
    maintainer's uid — so even a token that happens to appear in one of the
    maintainer's own command lines can never become a kill target. This is the
    enumerated-kill counterpart to uid mode's refusal to target its own uid.
    """
    r = subprocess.run(["ps", "-axo", "pid=,uid=,command="], capture_output=True, text=True)
    own_uid = os.getuid()
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or gen_token not in line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, uid = int(parts[0]), int(parts[1])
        if uid == own_uid:
            continue
        out.append(pid)
    me = os.getpid()
    return sorted(p for p in out if p != me)


class Domain:
    """The set of processes a reap is allowed to, and must, empty."""

    def __init__(self, mode, *, agent_uid=None, gen_token=None, exclude=()):
        if mode not in ("uid", "gentoken"):
            raise ValueError(f"unknown domain mode {mode!r}")
        if mode == "uid":
            if agent_uid is None:
                raise ValueError("uid mode needs agent_uid")
            # A uid-mode reap is kill(-1) against agent_uid. If agent_uid is root
            # it targets the whole host; if it is the uid that owns this process
            # (the maintainer / the daemon), it reaps every SSH login and shell
            # that uid owns — the exact defect that took the host down. The
            # domain MUST be a dedicated, non-privileged uid distinct from
            # whoever constructs the reaper. Enforced here, at the one point
            # where agent_uid is bound, so a misconfigured plist fails closed at
            # reconcile instead of nuking the maintainer's sessions.
            #
            # This is not hypothetical: a supervisor was bootstrapped with
            # agent_uid = the maintainer's own uid (scenarios.install_supervisor
            # wrote str(UID)), and under launchd KeepAlive it self-killed and
            # relaunched ~every 1-2s, reaping every SSH login for four days
            # (~242k reap cycles). Incident evidence and the log summary live in
            #   dev_docs/tasks/probe5-incident-evidence/
            if int(agent_uid) == 0:
                raise ValueError("uid mode refuses root: kill(-1) as root is host-wide")
            if int(agent_uid) in (os.getuid(), os.geteuid()):
                raise ValueError(
                    f"uid mode refuses agent_uid={agent_uid}: it is the caller's "
                    "own uid, so kill(-1) would reap the caller's own logins — "
                    "provision a dedicated agent uid")
        if mode == "gentoken" and not (gen_token and _VALID_GEN_TOKEN.match(gen_token)):
            raise ValueError(
                "gentoken mode needs a well-formed, whitespace-free token of "
                f"length >= 12; got {gen_token!r} (a blank or too-broad token "
                "would match unrelated processes)")
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
        # In uid mode nothing is excluded: the supervisor is a different uid, so
        # it is not in this set, and excluding anything here could mask a real
        # survivor and let a reap "verify zero" while work is still running.
        excl = () if self.mode == "uid" else self.exclude
        return [p for p in pids if p not in excl]

    def _sudo_reap(self, sig):
        """Reap via the scoped `sudo -u agent` helper (Stage-2 sudoers).

        THE HELPER IS INSIDE THE DOMAIN IT REAPS. `kill(-1)` as the agent hits
        every process of that uid, including the shell running the helper, so
        the helper routinely dies of its own signal and exits 128+n. That is the
        primitive working, not a failure — and it is exactly why the exit status
        is NOT the authority here. Convergence is decided solely by the
        maintainer-side `proc_listpids` rescan in `reap()`, which cannot be
        fooled by a helper that died mid-flight.

        cwd is forced to "/": sudo inherits the caller's directory, and with the
        maintainer home at 0700 the agent cannot getcwd() there — /bin/sh then
        fails at startup, before any in-script `cd` could run. This already bit
        Probe 4 (see driver.sh) and would bite the launchd-hosted path where
        there is no interactive shell to work around it.
        """
        name = "TERM" if sig == signal.SIGTERM else "KILL"
        r = subprocess.run(
            ["sudo", "-n", "-C", "5", "-u", REAP_AS_USER, REAP_HELPER, name],
            capture_output=True, text=True, cwd="/")
        # subprocess reports a signal death as a NEGATIVE returncode (-9), not
        # the shell's 128+n. Check both so the helper's own death by the signal
        # it just sent is recognised rather than read as an ordinary failure.
        self_killed = r.returncode < 0 or r.returncode >= 128
        return {"method": f"sudo {REAP_HELPER} {name}", "targets": None,
                "rc": r.returncode, "helper_self_killed": self_killed,
                "stderr": r.stderr.strip()[-200:] or None}

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
        if self.mode == "uid":
            return self._sudo_reap(sig)
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


# macOS starts per-user system daemons for ANY uid that runs anything (observed:
# /usr/sbin/distnoted). They are in the containment domain but are not the run's
# work, and they respawn on demand — so a scan taken after a converged reap can
# be non-empty for reasons unrelated to the run. The reap itself still drives the
# domain to ABSOLUTE zero (no allowlist); this list exists only so post-hoc
# evidence can distinguish "the run survived" from "macOS restarted a daemon".
# Narrowing caveat: a hostile run could exec one of these paths to hide.
SYSTEM_DAEMON_EXES = ("/usr/sbin/distnoted", "/usr/libexec/trustd",
                      "/usr/sbin/cfprefsd", "/usr/libexec/secinitd")


def describe(pids):
    """(pid, command) for each pid. `ps` reads cross-uid where proc_pidinfo does not."""
    if not pids:
        return []
    r = subprocess.run(["ps", "-o", "pid=,command=", *[f"-p{p}" for p in pids]],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, cmd = line.partition(" ")
        if pid_s.isdigit():
            out.append({"pid": int(pid_s), "command": cmd.strip()})
    return out


def partition_survivors(pids):
    """Split a scan into (run_attributable, system_daemons)."""
    described = describe(pids)
    sysd, run = [], []
    for d in described:
        (sysd if any(d["command"].startswith(e) for e in SYSTEM_DAEMON_EXES)
         else run).append(d)
    return run, sysd


def verify_incarnation_dead(recorded):
    """True iff the recorded incarnation is gone. Identity-checked, so a reused
    pid reads as DEAD (a different incarnation) rather than as still-alive."""
    live = incarnation.measure(recorded["pid"])
    return not incarnation.same_incarnation(recorded, live)
