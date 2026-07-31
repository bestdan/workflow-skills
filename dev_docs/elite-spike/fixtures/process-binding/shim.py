#!/usr/bin/env python3
"""Trivial run-shim surrogate (§4.1). Runs INSIDE the tmux pane, agent uid on
the mini. It:
  1. records its own {pid,ppid,pgid,sid,start} to a runfile  -- a CLAIM only,
     corroboration, never authority.
  2. attempts setsid(2), recording whether it was already a session leader
     (tmux may already have made the pane its own session).
  3. execve()s a harmless surrogate (/bin/sleep) -- NOT claude. This captures
     PID/PGID/SID/executable continuity across the exec boundary; the real
     shim->claude exec is captured only under probe 1's authorized launches.
"""
import json, os, sys, time

runfile = sys.argv[1]
sleep_secs = sys.argv[2] if len(sys.argv) > 2 else "3600"

pre = {
    "phase": "pre-setsid",
    "pid": os.getpid(),
    "ppid": os.getppid(),
    "pgid": os.getpgrp(),
    "sid": os.getsid(0),
    "t": time.time(),
}

already_leader = pre["sid"] == pre["pid"]
setsid_result = "skipped-already-leader"
if not already_leader:
    try:
        os.setsid()
        setsid_result = "ok"
    except OSError as e:
        setsid_result = f"EPERM/{e.errno}"

post = {
    "phase": "post-setsid",
    "pid": os.getpid(),
    "pgid": os.getpgrp(),
    "sid": os.getsid(0),
    "already_leader": already_leader,
    "setsid_result": setsid_result,
}

with open(runfile, "w") as f:
    json.dump({"claim_pre": pre, "claim_post": post,
               "exec_target": "/bin/sleep"}, f)
    f.flush()
    os.fsync(f.fileno())

# execve: pid/pgid/sid persist, executable image changes to /bin/sleep.
os.execv("/bin/sleep", ["sleep", sleep_secs])
