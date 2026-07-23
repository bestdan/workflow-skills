#!/usr/bin/env python3
"""Probe 5 — the "run": a trivial surrogate process under the containment domain.

It carries `gen_token` in argv, reports its pid on the report pipe, then BLOCKS
on the start gate. It NEVER opens state.db (invariant 8) — the supervisor is the
sole writer, and no SQLite fd is inherited across the spawn.

THE START-GATE RULE (closes the DB↔spawn crash windows):

    EOF on the gate pipe means "exit without executing" — never "go".

So if the supervisor dies anywhere between the `prepared` commit and writing
"go", this child observes the write end being closed by the supervisor's death
and exits on its own instead of running unrecorded work. The uid-scan in
reconciliation is the backstop for a child that somehow survives that.

usage: runsurrogate.py <gen_token> [--escape] [--churn] [--ignore-term]
  fd 3 = report pipe (write)   fd 4 = start gate (read)
"""
import os
import signal
import sys
import time

REPORT_FD = 3
GATE_FD = 4


def main():
    argv = sys.argv[1:]
    if not argv:
        print("usage: runsurrogate.py <gen_token> [--escape] [--churn] [--ignore-term]",
              file=sys.stderr)
        return 2
    gen_token = argv[0]
    escape = "--escape" in argv
    churn = "--churn" in argv
    ignore_term = "--ignore-term" in argv

    if ignore_term:
        # Models a worker that traps TERM: the reap must still converge via the
        # KILL escalation, not stall waiting for a polite exit.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # 1. Report our pid. The supervisor MEASURES the incarnation from the kernel
    #    itself; we only say which pid to look at. We never send an identity we
    #    authored — the control plane must not trust an agent-supplied tuple.
    with os.fdopen(REPORT_FD, "w") as rep:
        rep.write(f"{os.getpid()}\n")
        rep.flush()

    # 2. Block on the start gate.
    gate = os.fdopen(GATE_FD, "rb", buffering=0)
    data = gate.read(2)
    if not data:
        return 0  # EOF => supervisor died before authorizing us. Exit, don't go.
    if data != b"go":
        return 0

    # 3. Authorized. Now behave like a run, optionally trying to escape.
    if escape:
        # Double-fork + setsid: the canonical escape from any pgroup/session
        # containment scheme. Only a uid-wide kill catches this.
        if os.fork() == 0:
            os.setsid()
            if os.fork() == 0:
                while True:
                    time.sleep(3600)
            os._exit(0)
        os.wait()

    if "--descendant" in argv:
        # A plain fork (no exec, no setsid): the child keeps our argv, so it
        # stays visible to the degraded gen_token scan. This is the "dead run ≠
        # dead workers" case — kill the parent and this survives.
        if os.fork() == 0:
            while True:
                time.sleep(3600)

    if churn:
        # Keep respawning children so a naive "signal the set we enumerated"
        # reaper never converges. Only re-scan-until-zero terminates this.
        while True:
            if os.fork() == 0:
                time.sleep(30)
                os._exit(0)
            time.sleep(0.05)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
