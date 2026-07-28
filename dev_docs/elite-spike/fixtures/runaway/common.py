#!/usr/bin/env python3
"""Probe 5b shared harness: evidence emitter + the containment primitives.

DISPOSABLE SPIKE CODE (design §0a rule 4). Never promoted to production by
renaming; the reap is rebuilt under Stage-2 gates, not lifted from here.

Two jobs, deliberately in one file because they are not separable in practice:

1. **Evidence.** Append-only JSONL, one row per wake, plus a header row that
   pins the sha256 of every fixture file and the fixture's git revision. The
   metadata block is an explicit ALLOWLIST OF FIELD NAMES (`ENV_METADATA`):
   no environment dict is ever serialized, so there is no denylist of token
   substrings to get wrong. `selftest` asserts that structurally.

2. **Containment.** Every spawn and every signal in this fixture goes through
   here rather than through shell, because the outage this probe is modelled on
   was an empty shell variable: `kill -- -$pgid` with $pgid unset signals the
   CALLER'S OWN group. There is no code path in this fixture that formats a
   pgid into a shell word. `killpg` is called from Python with an integer that
   has already passed `_validated_pgid` and `same_incarnation`.

Probe 5's incident record (dev_docs/tasks/probe5-incident-evidence/) is a
supervisor bootstrapped against the maintainer's own uid that reaped every SSH
login for four days. This fixture spawns processes and asserts they get reaped:
the same hazard class.
"""
import argparse
import errno
import hashlib
import json
import os
import platform
import signal as signalmod
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import incarnation  # noqa: E402  (Probe 2's reader, copied verbatim)

FIXTURE_DIR = os.path.dirname(os.path.abspath(__file__))

# Files whose sha256 is pinned into the header row. Enumerated, not globbed:
# a glob would silently drop a file from the evidence chain the day someone
# adds one, and results.json cites this list.
FIXTURE_FILES = (
    "check-baseline.sh",
    "check-baseline.txt",
    "common.py",
    "driver.sh",
    "incarnation.py",
    "legs.py",
    "parent-death-drill.sh",
    "runaway.py",
    "scenarios.py",
)

# The metadata allowlist (rule 4 / the kill sheet's evidence section). These are
# FIELD NAMES this emitter is permitted to produce, and each is a DERIVED fact,
# never a copied environment value. Adding a row here is the only way to widen
# what is emitted, and `selftest` fails if the emitted key set diverges.
ENV_METADATA = (
    "os_name",
    "os_release",
    "os_machine",
    "python_version",
    "python_executable",
    "sandbox_exec_present",
    "xcode_select_path",
    "uid_is_root",
    "login_name_is_resolvable",
)


class ContainmentError(RuntimeError):
    """A construction-time assertion failed. The fixture fails closed."""


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


def _scrub_home(path):
    """Never emit an absolute path under $HOME verbatim."""
    home = os.path.expanduser("~")
    if path and home and path.startswith(home):
        return "~" + path[len(home):]
    return path


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fixture_digests():
    out = {}
    for name in FIXTURE_FILES:
        p = os.path.join(FIXTURE_DIR, name)
        out[name] = sha256_file(p) if os.path.exists(p) else None
    return out


def fixture_revision():
    """The revision that PRODUCED the evidence, plus a dirty flag — not HEAD
    taken later at write-up time."""
    def git(*args):
        try:
            return subprocess.run(
                ("git", "-C", FIXTURE_DIR) + args,
                capture_output=True, text=True, timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    rev = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--", FIXTURE_DIR)
    return {"fixture_git_revision": rev or None, "fixture_tree_dirty": bool(dirty)}


def env_metadata():
    """Derived facts only. No os.environ value is read into a returned field."""
    try:
        xcode = subprocess.run(
            ("xcode-select", "-p"), capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        xcode = ""
    try:
        login_resolvable = bool(os.getlogin())
    except OSError:
        login_resolvable = False
    return {
        "os_name": platform.system(),
        "os_release": platform.release(),
        "os_machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_executable": _scrub_home(sys.executable),
        "sandbox_exec_present": os.path.exists("/usr/bin/sandbox-exec"),
        "xcode_select_path": xcode or None,
        "uid_is_root": os.geteuid() == 0,
        "login_name_is_resolvable": login_resolvable,
    }


def emit(evidence_path, row):
    """Append one JSONL row. Append-only by construction: mode 'a', one
    json.dumps, one write, flush + fsync so a killed driver cannot truncate the
    last row into malformed JSON (which the kill sheet classifies as
    `error — fixture defect`, not `inconclusive`)."""
    row = dict(row)
    row.setdefault("t", time.time())
    row.setdefault("t_iso", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    line = json.dumps(row, sort_keys=True) + "\n"
    with open(evidence_path, "a") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return row


def header_row(leg, wakes, driver_pgid, extra=None):
    row = {
        "row": "header",
        "probe": "5b",
        "leg": leg,
        "planned_wakes": wakes,
        "driver_pgid": driver_pgid,
        "driver_pid": os.getpid(),
        "sha256": fixture_digests(),
        "env": env_metadata(),
    }
    row.update(fixture_revision())
    if extra:
        row.update(extra)
    return row


# --------------------------------------------------------------------------
# Containment
# --------------------------------------------------------------------------


def _validated_pgid(pgid, driver_pgid):
    """The signal gate. Every condition here has a named failure mode in the
    kill sheet; none is decorative.

    An empty/garbage value is the outage's exact symptom shape, so it is
    rejected before it can become an integer. `pgid == pid` is what proves the
    surrogate is its own group leader — a child spawned without setpgid/setsid
    inherits the DRIVER's group, which satisfies a naive "is it the group we
    recorded?" check while killpg takes out the driver, the shell, and the SSH
    session (Probe 3's exact bug). Both halves, or nothing is sent.
    """
    if pgid is None or pgid == "":
        raise ContainmentError("refusing to signal: pgid is empty")
    if not isinstance(pgid, int):
        s = str(pgid)
        if not s.isdigit():
            raise ContainmentError("refusing to signal: pgid is not numeric: %r" % (pgid,))
        pgid = int(s)
    if pgid <= 1:
        raise ContainmentError(
            "refusing to signal: pgid is not a positive non-init group: %d" % pgid
        )
    if driver_pgid is not None and pgid == int(driver_pgid):
        raise ContainmentError(
            "refusing to signal: target pgid %d IS the driver's own group — this is "
            "the four-day-outage shape (killpg would take the driver, the invoking "
            "shell, and the SSH session)" % pgid
        )
    return pgid


def assert_own_group_leader(pid, driver_pgid):
    """Both halves of the containment assertion, measured from the kernel via
    proc_pidinfo rather than from anything the fixture remembers."""
    live = incarnation.measure(int(pid))
    if not live.get("alive"):
        raise ContainmentError("surrogate pid %s is not alive; refusing to arm" % pid)
    pgid = live.get("pgid")
    pid = int(live.get("pid"))
    if pgid != pid:
        raise ContainmentError(
            "surrogate pgid (%s) != its own pid (%s): it is NOT its own group "
            "leader, so it inherited a group the fixture did not create" % (pgid, pid)
        )
    if int(pgid) == int(driver_pgid):
        raise ContainmentError(
            "surrogate pgid (%s) IS the driver's group (%s)" % (pgid, driver_pgid)
        )
    if live.get("uid") != os.getuid():
        raise ContainmentError(
            "surrogate uid (%s) is not the fixture's uid (%s)" % (live.get("uid"), os.getuid())
        )
    return live


def signal_group(recorded, driver_pgid, signum):
    """Re-validate identity IMMEDIATELY before the signal, then killpg.

    A saved pgid names a SLOT, not a process: once the fixture's group dies the
    number is reusable, and signalling a reused slot is how a fixture reaps
    something it never created. On mismatch or ESRCH we do not widen the search
    and we do not retry — we skip the signal and record it.
    """
    pid = int(recorded["pid"])
    live = incarnation.measure(pid)
    if not live.get("alive"):
        return {"sent": False, "outcome": "already-dead", "pid": pid}
    if not incarnation.same_incarnation(recorded, live):
        return {
            "sent": False,
            "outcome": "escaped",
            "pid": pid,
            "detail": "p_uniqueid/start-time mismatch: this pid is a DIFFERENT "
                      "incarnation than the one recorded; the fixture's process is gone "
                      "and something else holds the slot",
        }
    if live.get("pgid") != pid:
        return {
            "sent": False,
            "outcome": "escaped",
            "pid": pid,
            "detail": "live pgid %s no longer equals pid %s (it left the group the "
                      "fixture created); reported, never chased" % (live.get("pgid"), pid),
        }
    pgid = _validated_pgid(live.get("pgid"), driver_pgid)
    try:
        os.killpg(pgid, signum)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return {"sent": False, "outcome": "already-dead", "pid": pid}
        return {"sent": False, "outcome": "error", "pid": pid, "errno": exc.errno}
    return {"sent": True, "outcome": "signalled", "pid": pid, "pgid": pgid,
            "signal": signum}


def watch_parent_death(fifo_path, driver_incarnation=None):
    """The parent-death channel: the driver dies, everything downstream exits,
    without anyone deciding to.

    This is half of the two-mechanism deadline the kill sheet requires (the
    watchdog is the independent other half). "The driver enforces its own
    absolute deadline and self-terminates" is too weak — a crashed or wedged
    driver enforces nothing, and the surrogate is deliberately long-lived and
    uncooperative.

    TWO detectors, because the FIFO alone was MEASURED to fail here:

    1. FIFO EOF. The driver holds a write end; readers exit when the last writer
       closes. Correct in principle and silently fragile in practice — ANY child
       that inherits the driver's write-end fd keeps the FIFO open after the
       driver dies, so EOF never fires. Reproduced directly: a plain `sleep`
       spawned without `9>&-` was enough to strand the surrogate past a `kill -9`
       of the driver. That makes the channel depend on remembering an explicit
       fd-close on every single spawn, which is exactly the kind of invariant
       that holds until the one spawn someone forgets.

    2. Driver incarnation. Poll the driver's recorded identity through the same
       proc_pidinfo authority every signal here already uses, and exit when it no
       longer matches. This depends on no fd hygiene at all, and it is not fooled
       by pid reuse: `same_incarnation` compares p_uniqueid and the microsecond
       start time, so a NEW process landing on the driver's old pid reads as
       death, which is the safe direction.

    Detector 2 is what actually makes the predicate pass. Detector 1 is kept
    because it is instantaneous where it works, and because leg 3's workers
    inherit the read end from the surrogate rather than being told anything.
    """
    def _die(code):
        os._exit(code)

    def _fifo():
        try:
            fd = os.open(fifo_path, os.O_RDONLY)
        except OSError:
            return
        while True:
            try:
                if os.read(fd, 1) == b"":
                    break
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                break
        _die(96)

    def _driver():
        pid = int(driver_incarnation["pid"])
        while True:
            if not incarnation.same_incarnation(driver_incarnation,
                                                incarnation.measure(pid)):
                _die(95)
            time.sleep(0.5)

    threads = []
    if fifo_path:
        threads.append(threading.Thread(target=_fifo, daemon=True))
    if driver_incarnation:
        threads.append(threading.Thread(target=_driver, daemon=True))
    for t in threads:
        t.start()
    return threads


def load_driver_incarnation(path):
    if not path:
        return None
    with open(path) as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# CLI — driver.sh calls these rather than formatting pgids into shell words
# --------------------------------------------------------------------------


def _cmd_spawn(args):
    """Become our own group leader, record the incarnation, then exec.

    exec (not fork) so the pid the driver captured with $! IS the surrogate:
    a wrapper that forks would have the driver recording the WRAPPER's identity
    and signalling a group the real surrogate may have already left.
    """
    try:
        os.setsid()
    except OSError as exc:
        # Already a group leader (job control gave us our own group): setsid
        # refuses, but the property we actually need already holds. Anything
        # else fails closed.
        if exc.errno != errno.EPERM:
            raise
        if os.getpgid(0) != os.getpid():
            raise ContainmentError(
                "setsid failed with EPERM and we are not already a group leader "
                "(pgid=%d pid=%d): refusing to exec, because the driver would then "
                "record and signal a group it did not create"
                % (os.getpgid(0), os.getpid())
            )
    me = incarnation.measure(os.getpid())
    if me.get("pgid") != os.getpid():
        raise ContainmentError(
            "post-setsid pgid (%s) != pid (%s)" % (me.get("pgid"), os.getpid())
        )
    tmp = args.incarnation_file + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(me, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, args.incarnation_file)
    cmd = [a for a in args.exec_argv if a != "--"]
    if not cmd:
        raise ContainmentError("spawn requires a command after --")
    os.execvp(cmd[0], cmd)


def _cmd_record_incarnation(args):
    """Record another process's identity — the driver's own, taken as `$$`.

    A subcommand rather than `os.getpid()` because this runs as a CHILD of the
    driver: recording our own pid here would give every downstream watcher the
    identity of a process that exits a millisecond later, and they would all
    conclude the driver had died before the first wake.
    """
    me = incarnation.measure(int(args.pid))
    if not me.get("alive"):
        raise ContainmentError("pid %d is not alive; cannot record it" % args.pid)
    tmp = args.incarnation_file + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(me, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, args.incarnation_file)
    print(json.dumps(me, sort_keys=True))
    return 0


def _cmd_signal(args):
    with open(args.incarnation_file) as fh:
        recorded = json.load(fh)
    signum = getattr(signalmod, "SIG" + args.signal.upper())
    result = signal_group(recorded, args.driver_pgid, signum)
    result.update({"row": "signal", "leg": args.leg, "requested_signal": args.signal})
    emit(args.evidence, result)
    print(json.dumps(result, sort_keys=True))
    return 0


def _cmd_assert_containment(args):
    with open(args.incarnation_file) as fh:
        recorded = json.load(fh)
    live = assert_own_group_leader(recorded["pid"], args.driver_pgid)
    if not incarnation.same_incarnation(recorded, live):
        raise ContainmentError(
            "recorded and live incarnations differ at arm time: the pid was reused"
        )
    emit(args.evidence, {
        "row": "containment",
        "leg": args.leg,
        "assertions": {
            "surrogate_is_own_group_leader": True,
            "surrogate_group_is_not_drivers": True,
            "surrogate_uid_is_fixture_uid": True,
            "incarnation_stable_since_record": True,
        },
        "surrogate_pid": live["pid"],
        "surrogate_pgid": live["pgid"],
        "driver_pgid": args.driver_pgid,
    })
    print("containment OK: pid=%s pgid=%s driver_pgid=%s"
          % (live["pid"], live["pgid"], args.driver_pgid))
    return 0


def _cmd_header(args):
    extra = json.loads(args.extra) if args.extra else None
    row = header_row(args.leg, args.wakes, args.driver_pgid, extra)
    emit(args.evidence, row)
    print(json.dumps(row["sha256"], sort_keys=True, indent=2))
    return 0


def _cmd_emit(args):
    emit(args.evidence, json.loads(args.row))
    return 0


def _cmd_merge(args):
    """Fold the surrogate's in-jail evidence into the canonical JSONL.

    The surrogate CANNOT write the canonical file: it runs under the rendered
    profile, whose only RW scopes are the scratch rundir and tmpdir, so the
    checked-in fixture tree is read-only to it. That is the boundary working,
    not an obstacle to route around — so its rows are written inside its own RW
    scope and merged here, tagged `in_jail`, by the unjailed driver.

    A malformed row is a hard error (`error - fixture defect`), never silently
    dropped: a merge that skips what it cannot parse produces evidence that
    looks complete and is not.
    """
    n = 0
    if not os.path.exists(args.source):
        emit(args.evidence, {"row": "merge", "leg": args.leg, "source_missing": True,
                             "merged": 0})
        return 0
    with open(args.source) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                print("merge: %s line %d is malformed JSON" % (args.source, lineno),
                      file=sys.stderr)
                return 1
            row["in_jail"] = True
            emit(args.evidence, row)
            n += 1
    emit(args.evidence, {"row": "merge", "leg": args.leg, "merged": n})
    return 0


def _cmd_watchdog(args):
    """The independent second deadline mechanism: its own process, its own
    group, spawned FIRST — before the surrogate it will signal.

    Because it is spawned first, the surrogate's incarnation file does not exist
    yet, so it is read LAZILY at fire time. That ordering is the requirement (a
    watchdog armed after the thing it guards has a window where nothing guards
    it), and the laziness is what makes the ordering possible.
    """
    deadline = float(args.deadline)
    while time.time() < deadline:
        time.sleep(min(1.0, max(0.0, deadline - time.time())))
    if not os.path.exists(args.incarnation_file):
        emit(args.evidence, {
            "row": "watchdog",
            "leg": args.leg,
            "outcome": "no-target",
            "detail": "deadline reached with no recorded surrogate incarnation",
        })
        return 0
    with open(args.incarnation_file) as fh:
        recorded = json.load(fh)
    for signum in (signalmod.SIGTERM, signalmod.SIGKILL):
        result = signal_group(recorded, args.driver_pgid, signum)
        result.update({"row": "watchdog", "leg": args.leg,
                       "requested_signal": signum})
        emit(args.evidence, result)
        if not result["sent"]:
            break
        time.sleep(2)
        if not incarnation.measure(int(recorded["pid"])).get("alive"):
            break
    return 0


def _cmd_selftest(args):
    """The evidence-hygiene assertions, run as a pre-leg check by driver.sh.

    The criterion is that metadata is an allowlist of FIELD NAMES, so the
    primary assertion is structural: the emitted key set IS the allowlist. The
    value-level checks are a backstop against a future field that derives its
    value by reading the environment.
    """
    failures = []

    # The evidence chain is only as complete as FIXTURE_FILES. A file added by a
    # later task and not registered here would be silently absent from every
    # header row, so results.json would pin a digest set that does not cover the
    # code that produced the evidence — and nothing would say so. Enumerated
    # rather than globbed for exactly that reason, which only works if the
    # enumeration is checked.
    code_ext = (".py", ".sh")
    on_disk = {f for f in os.listdir(FIXTURE_DIR)
               if f.endswith(code_ext) and not f.startswith(".")}
    unregistered = on_disk - set(FIXTURE_FILES)
    missing = {f for f in FIXTURE_FILES
               if not os.path.exists(os.path.join(FIXTURE_DIR, f))}
    if unregistered:
        failures.append("fixture files on disk but not in FIXTURE_FILES (they "
                        "would be absent from every header row): %s"
                        % sorted(unregistered))
    if missing:
        failures.append("FIXTURE_FILES names files that do not exist: %s"
                        % sorted(missing))

    meta = env_metadata()
    if tuple(sorted(meta)) != tuple(sorted(ENV_METADATA)):
        failures.append("metadata key set diverges from ENV_METADATA allowlist: %r"
                        % (sorted(set(meta) ^ set(ENV_METADATA)),))

    # No environment VALUE is copied through. Exact equality, not substring:
    # a substring test would flag "/opt/homebrew/bin/python3.12" for merely
    # sharing a prefix with $PATH, and a check that cries wolf gets deleted.
    serialized = {k: v for k, v in meta.items() if isinstance(v, str)}
    envvals = {v for v in os.environ.values() if v}
    for key, val in serialized.items():
        if val in envvals:
            failures.append("metadata field %r copies an environment value verbatim" % key)

    # No credential path, by shape rather than by token grep.
    credish = (".ssh", ".netrc", "credential", "keychain", "token", ".config/gh",
               ".config/op", ".claude/.credentials")
    for key, val in serialized.items():
        low = val.lower()
        for frag in credish:
            if frag in low:
                failures.append("metadata field %r looks like a credential path" % key)

    # The signal gate refuses every shape of the outage's symptom.
    for bad in ("", None, "abc", "-1", "0", "1"):
        try:
            _validated_pgid(bad, 4242)
            failures.append("_validated_pgid accepted %r" % (bad,))
        except ContainmentError:
            pass
    try:
        _validated_pgid(4242, 4242)
        failures.append("_validated_pgid accepted the driver's own group")
    except ContainmentError:
        pass
    if _validated_pgid("12345", 4242) != 12345:
        failures.append("_validated_pgid rejected a legitimate pgid")

    for f in failures:
        print("selftest FAIL: %s" % f, file=sys.stderr)
    if failures:
        return 1
    print("selftest OK (%d metadata fields, allowlisted; signal gate refuses "
          "empty/non-numeric/non-positive/self-group)" % len(meta))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="subcmd", required=True)

    sp = sub.add_parser("spawn")
    sp.add_argument("--incarnation-file", required=True)
    sp.add_argument("exec_argv", nargs=argparse.REMAINDER)
    sp.set_defaults(fn=_cmd_spawn)

    sg = sub.add_parser("signal")
    sg.add_argument("--incarnation-file", required=True)
    sg.add_argument("--driver-pgid", type=int, required=True)
    sg.add_argument("--evidence", required=True)
    sg.add_argument("--leg", default="")
    sg.add_argument("--signal", default="TERM")
    sg.set_defaults(fn=_cmd_signal)

    sa = sub.add_parser("assert-containment")
    sa.add_argument("--incarnation-file", required=True)
    sa.add_argument("--driver-pgid", type=int, required=True)
    sa.add_argument("--evidence", required=True)
    sa.add_argument("--leg", default="")
    sa.set_defaults(fn=_cmd_assert_containment)

    sh = sub.add_parser("header")
    sh.add_argument("--evidence", required=True)
    sh.add_argument("--leg", required=True)
    sh.add_argument("--wakes", type=int, default=0)
    sh.add_argument("--driver-pgid", type=int, required=True)
    sh.add_argument("--extra", default="")
    sh.set_defaults(fn=_cmd_header)

    se = sub.add_parser("emit")
    se.add_argument("--evidence", required=True)
    se.add_argument("--row", required=True)
    se.set_defaults(fn=_cmd_emit)

    sm = sub.add_parser("merge")
    sm.add_argument("--evidence", required=True)
    sm.add_argument("--source", required=True)
    sm.add_argument("--leg", default="")
    sm.set_defaults(fn=_cmd_merge)

    sw = sub.add_parser("watchdog")
    sw.add_argument("--incarnation-file", required=True)
    sw.add_argument("--driver-pgid", type=int, required=True)
    sw.add_argument("--evidence", required=True)
    sw.add_argument("--deadline", required=True)
    sw.add_argument("--leg", default="")
    sw.add_argument("--parent-death-fifo", default="")
    sw.add_argument("--driver-incarnation-file", default="")
    sw.set_defaults(fn=_cmd_watchdog)

    sr = sub.add_parser("record-incarnation")
    sr.add_argument("--incarnation-file", required=True)
    sr.add_argument("--pid", type=int, required=True)
    sr.set_defaults(fn=_cmd_record_incarnation)

    st = sub.add_parser("selftest")
    st.set_defaults(fn=_cmd_selftest)

    args = p.parse_args(argv)
    if args.subcmd == "watchdog":
        watch_parent_death(args.parent_death_fifo,
                           load_driver_incarnation(args.driver_incarnation_file))
    try:
        return args.fn(args)
    except ContainmentError as exc:
        print("CONTAINMENT FAIL-CLOSED: %s" % exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
