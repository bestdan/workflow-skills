#!/bin/sh
# Probe 2, item (1): cross-uid libproc read.
# Run in a REAL terminal on the two-uid host (needs sudo + user `agent`).
#   sh dev_docs/elite-spike/fixtures/process-binding/crossuid-read-drill.sh
# Measures ONE agent-owned process three ways and prints each incarnation tuple:
#   non-root maintainer / root / agent (same-uid baseline).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
M="$HERE/incarnation.py"
PY=/usr/bin/python3          # system python, stdlib-only (ctypes -> libproc)

sudo -v || { echo "sudo auth failed"; exit 1; }   # cache creds once

sudo -u agent /bin/sleep 3600 &
sleep 0.5
APID=$(pgrep -u agent -n -x sleep)
echo "agent-owned pid = ${APID:-<none>}"
[ -n "$APID" ] || { echo "FAILED to find agent sleep"; exit 1; }

echo "== non-root maintainer (uid $(id -u)) =="
"$PY" "$M" "$APID"
echo "== root =="
sudo "$PY" "$M" "$APID"
echo "== agent baseline (same-uid) =="
sudo -u agent "$PY" "$M" "$APID"

sudo -u agent kill "$APID" 2>/dev/null
