#!/usr/bin/env python3
"""Independent incarnation measurement via macOS libproc (proc_pidinfo).

This is the CONTROL PLANE's authority: it never trusts a PID handed to it by
the (agent-owned) tmux server. Given a candidate PID it measures a strong,
stable identity tuple directly from the kernel:

    {pid, ppid, pgid, sid, uid, start_tvsec, start_tvusec, exe}

start time is microsecond-granular (pbi_start_tv*), which is strictly stronger
than `ps -o lstart` (second granularity) for distinguishing PID reuse.
"""
import ctypes, ctypes.util, json, os, sys

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

PROC_PIDTBSDINFO = 3
PROC_PIDUNIQIDENTIFINFO = 17
MAXCOMLEN = 16
PROC_PIDPATHINFO_MAXSIZE = 4096


class proc_uniqidentifierinfo(ctypes.Structure):
    _fields_ = [
        ("p_uuid", ctypes.c_uint8 * 16),
        ("p_uniqueid", ctypes.c_uint64),   # kernel-assigned, monotonic, never reused within a boot
        ("p_puniqueid", ctypes.c_uint64),  # parent's p_uniqueid
        ("p_reserve2", ctypes.c_uint64),
        ("p_reserve3", ctypes.c_uint64),
        ("p_reserve4", ctypes.c_uint64),
    ]


class proc_bsdinfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("pbi_rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * MAXCOMLEN),
        ("pbi_name", ctypes.c_char * (2 * MAXCOMLEN)),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _proc_pidpath(pid):
    buf = ctypes.create_string_buffer(PROC_PIDPATHINFO_MAXSIZE)
    n = libc.proc_pidpath(pid, buf, PROC_PIDPATHINFO_MAXSIZE)
    if n <= 0:
        return None
    return buf.value.decode(errors="replace")


def measure(pid):
    """Return the incarnation tuple, or {'alive': False} if the pid is gone /
    unreadable. Raises nothing the caller must special-case beyond alive."""
    info = proc_bsdinfo()
    size = ctypes.sizeof(info)
    ctypes.set_errno(0)
    n = libc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0,
                          ctypes.byref(info), size)
    if n != size:
        return {"alive": False, "pid": pid, "errno": ctypes.get_errno()}
    try:
        sid = os.getsid(pid)
    except OSError:
        sid = -1
    uniq = proc_uniqidentifierinfo()
    usz = ctypes.sizeof(uniq)
    p_uniqueid = p_puniqueid = None
    if libc.proc_pidinfo(pid, PROC_PIDUNIQIDENTIFINFO, 0,
                         ctypes.byref(uniq), usz) == usz:
        p_uniqueid = uniq.p_uniqueid
        p_puniqueid = uniq.p_puniqueid
    return {
        "alive": True,
        "pid": info.pbi_pid,
        "p_uniqueid": p_uniqueid,     # PRIMARY identity: never reused within a boot
        "p_puniqueid": p_puniqueid,   # parent (tmux server) incarnation
        "ppid": info.pbi_ppid,
        "pgid": info.pbi_pgid,
        "sid": sid,
        "uid": info.pbi_uid,
        "start_tvsec": info.pbi_start_tvsec,
        "start_tvusec": info.pbi_start_tvusec,
        "comm": info.pbi_comm.decode(errors="replace"),
        "exe": _proc_pidpath(pid),
    }


# The identity key: what makes two observations "the same incarnation".
# PID alone is NOT the key. Primary key is p_uniqueid (kernel-assigned, never
# reused within a boot); µs start time is corroboration. Both must match.
IDENTITY_FIELDS = ("pid", "p_uniqueid", "start_tvsec", "start_tvusec")


def same_incarnation(recorded, live, *, require_exe=None):
    if not live.get("alive"):
        return False
    for f in IDENTITY_FIELDS:
        if recorded.get(f) != live.get(f):
            return False
    if require_exe is not None and live.get("exe") != require_exe:
        return False
    return True


if __name__ == "__main__":
    pid = int(sys.argv[1])
    print(json.dumps(measure(pid)))
