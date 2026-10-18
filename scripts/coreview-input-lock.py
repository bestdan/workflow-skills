#!/usr/bin/env python3
"""coreview-input-lock.py — hold one co-review reviewer's fixed <INPUT> for a dispatch.

`agy`, `devin` and `grok` read their assembled review input from a FIXED path,
because that path is part of the exact-match allow-rule that approves their
command (skills/co-review/references/permissions.md). A fixed path is shared by
every co-review run on the machine, so two runs dispatching the same reviewer at
once truncate and append to one file, and a reviewer can review the other run's
diff, or a mix of both (#1071). This script serializes those dispatches, one
reviewer at a time, without changing the reviewer's command string.

It goes ahead of the input assembly in the reviewer's `&&` chain, in the same
shell call:

  python3 ".../coreview-input-lock.py" agy && cat ... > "<INPUT>" && ... && agy ...

and returns as soon as it holds the lock. The lock is held by a forked child
that watches the shell running that line (this script's parent) and exits when
the shell does, so the lock is released when the dispatch line ends, including
when the session dies part way through. There is no release call to forget.

WHY flock AND A WATCHER RATHER THAN A LOCK DIRECTORY: a `mkdir` lock outlives a
killed session and then blocks the reviewer on every later run, and breaking a
stale one safely needs a compare-and-swap the filesystem does not have. An
flock is released by the kernel when its last holder exits, so the only thing
to get right is which process holds it.

--max-hold bounds the hold in case the parent is not the dispatch line's shell
(a shell can exec its final command, which makes the parent something
long-lived). The default is the --non-interactive reviewer bound plus margin.

Usage:
  scripts/coreview-input-lock.py <agent> [--wait S] [--max-hold S] [--dir DIR]

  <agent>      reviewer name: lowercase letters, digits and '-'
  --wait       seconds to wait for another run to finish (default 600, under
               co-review's 15-minute reviewer bound so a busy lock is reported
               as a skip rather than a timeout)
  --max-hold   seconds after which the lock is released regardless (default 1200)
  --dir        lock directory (default $HOME/.claude/co-review-input)

Exit status:
  0  lock held until this script's parent shell exits
  2  usage error, or the lock file could not be opened
  3  another co-review run held the lock for the whole --wait; skip the reviewer
"""

import argparse
import fcntl
import os
import re
import sys
import time

POLL_S = 0.2


def parent_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def hold(fd: int, shell_pid: int, max_hold: float) -> None:
    """In the forked child: keep fd (and so the flock) until the shell exits."""
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for stream in (0, 1, 2):
        os.dup2(devnull, stream)
    deadline = time.monotonic() + max_hold
    while parent_alive(shell_pid) and time.monotonic() < deadline:
        time.sleep(POLL_S)
    os.close(fd)
    os._exit(0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("agent")
    parser.add_argument("--wait", type=float, default=600.0)
    parser.add_argument("--max-hold", type=float, default=1200.0)
    parser.add_argument(
        "--dir",
        default=os.path.join(os.path.expanduser("~"), ".claude", "co-review-input"),
    )
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z0-9-]+", args.agent):
        print(
            f"coreview-input-lock: invalid agent name {args.agent!r}", file=sys.stderr
        )
        return 2

    path = os.path.join(args.dir, f"co-review-input.{args.agent}.lock")
    try:
        os.makedirs(args.dir, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as err:
        print(f"coreview-input-lock: cannot open {path}: {err}", file=sys.stderr)
        return 2

    shell_pid = os.getppid()
    deadline = time.monotonic() + args.wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                print(
                    f"coreview-input-lock: {args.agent}'s input is in use by another "
                    f"co-review run (waited {args.wait:.0f}s); skip this reviewer",
                    file=sys.stderr,
                )
                return 3
            time.sleep(POLL_S)

    # Who holds it, for a person inspecting the file. Nothing reads it back.
    os.ftruncate(fd, 0)
    os.write(fd, f"shell_pid={shell_pid} since={int(time.time())}\n".encode())

    if os.fork() == 0:
        hold(fd, shell_pid, args.max_hold)
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
