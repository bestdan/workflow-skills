#!/bin/sh
# Probe 2, item (2): cross-uid signal privilege.
# Run in a REAL terminal on the two-uid host (needs sudo + user `agent`).
#   sh dev_docs/elite-spike/fixtures/process-binding/crossuid-signal-drill.sh
#
# Confirms the privileged half of ap-stop: a non-root maintainer CANNOT killpg
# an agent-owned process group (EPERM), root CAN, and the incarnation is dead
# afterward. The agent-side `ap-agent-exec kill-session` (tmux teardown) half is
# same-uid and already exercised by S0/S3 in scenarios.py, so it is not a new
# cross-uid fact and is not repeated here.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
M="$HERE/incarnation.py"
PY=/usr/bin/python3          # system python, stdlib-only

alive() { sudo "$PY" "$M" "$1" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["alive"])'; }

sudo -v || { echo "sudo auth failed"; exit 1; }

# agent-owned session+group leader (setsid -> pgid==pid==sid), exec /bin/sleep
sudo -u agent "$PY" -c 'import os;os.setsid();os.execv("/bin/sleep",["sleep","3600"])' &
sleep 0.6
APID=$(pgrep -u agent -n -x sleep)
echo "agent-owned pid = ${APID:-<none>}"
[ -n "$APID" ] || { echo "FAILED to find agent sleep"; exit 1; }

echo "== root measures the incarnation =="
ROOT_JSON=$(sudo "$PY" "$M" "$APID"); echo "$ROOT_JSON"
PGID=$(printf '%s' "$ROOT_JSON" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["pgid"])')
echo "pgid = $PGID (uid should be 502)"

echo "== non-root maintainer (uid $(id -u)) killpg(SIGTERM) -> expect EPERM =="
"$PY" -c 'import os,signal,sys
try:
    os.killpg(int(sys.argv[1]), signal.SIGTERM); print("SIGNALLED (UNEXPECTED)")
except PermissionError as e: print("EPERM (expected):", e)
except ProcessLookupError as e: print("ESRCH:", e)' "$PGID"
echo "alive after non-root attempt (expect True): $(alive "$APID")"

echo "== root killpg(SIGTERM), grace, then SIGKILL if needed =="
sudo "$PY" -c 'import os,signal,sys;os.killpg(int(sys.argv[1]),signal.SIGTERM)' "$PGID"
sleep 1
if [ "$(alive "$APID")" = "True" ]; then
    echo "  still alive after TERM; escalating to KILL"
    sudo "$PY" -c 'import os,signal,sys;os.killpg(int(sys.argv[1]),signal.SIGKILL)' "$PGID"
    sleep 0.5
fi
echo "== final measure (expect alive:false) =="
sudo "$PY" "$M" "$APID"

sudo -u agent kill "$APID" 2>/dev/null   # belt-and-suspenders cleanup
