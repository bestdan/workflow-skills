#!/usr/bin/env python3
"""08:00 canary — the PRIMARY health signal (§5.1, Decision #5).

Positive confirmation: present == healthy, absent == investigate at leisure. The
load-bearing property the probe must prove is that health is *coupled*, not a
host-is-up ping. It is healthy ONLY if every clause holds:

  1. watcher is alive        — watcher.pass mtime is fresh (a bare host that is
                               up but whose watcher died is NOT healthy);
  2. broker is fresh         — broker.token mtime within the broker threshold;
  3. no run is unsupervised  — no runfile in `active` state with a stale
                               heartbeat or past its stop_deadline.

Clauses 1 and 3 together are what defeat the false-positive leg: a wedged run
with a dead watcher fails clause 1 (no fresh pass) AND clause 3 (active + stale)
— it can never read as healthy.

The verdict is always written to the registry + local sink so a test can assert
it; in production only the healthy verdict is the 08:00 ping and an unhealthy /
missing verdict is the maintainer's async cue.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

WATCHER_FRESH_S = float(os.environ.get("PROBE3_WATCHER_FRESH_S", "30"))  # ~3 intervals
BROKER_FRESH_S = float(os.environ.get("PROBE3_BROKER_FRESH_S", "3000"))
STALE_S = float(os.environ.get("PROBE3_STALE_S", "20"))


def evaluate():
    now = time.time()
    reasons = []

    wp = common.mtime("watcher.pass")
    if wp is None:
        reasons.append("no_watcher_pass_ever")
    elif now - wp > WATCHER_FRESH_S:
        reasons.append(f"watcher_stale={round(now - wp, 1)}s")

    bt = common.mtime("broker.token")
    if bt is None:
        reasons.append("no_broker_token")
    elif now - bt > BROKER_FRESH_S:
        reasons.append(f"broker_stale={round(now - bt, 1)}s")

    rf = common.read_json("runfile.json")
    if rf and rf.get("state") == "active":
        hb = common.mtime("heartbeat")
        if hb is None or now - hb > STALE_S:
            reasons.append("active_run_heartbeat_stale")
        if now > rf.get("stop_deadline_wall", float("inf")):
            reasons.append("active_run_past_stop_deadline")

    healthy = not reasons
    return healthy, reasons


def main():
    healthy, reasons = evaluate()
    verdict = "healthy" if healthy else "unhealthy"
    common.registry_append("canary", verdict=verdict, reasons=reasons)
    if healthy:
        common.notice("canary_healthy", "08:00 canary: present — healthy")
    else:
        common.notice("canary_unhealthy",
                      f"08:00 canary: UNHEALTHY — {', '.join(reasons)}")
    print(verdict, reasons)
    # Exit non-zero when unhealthy so a launchd/kickstart caller can branch.
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
