#!/bin/sh
# task-dashboard-launch — the stable entry point a LaunchAgent or a systemd
# unit runs, so neither has to name the plugin's versioned install directory.
#
# `dashboard.py install` renders this file into ~/.local/bin/ with UV set to
# the absolute path of uv on this machine (a launchd or systemd environment
# has almost no PATH). This copy in the plugin is the template; the one in
# ~/.local/bin/ is generated, and reinstalling rewrites it.
#
# Usage: task-dashboard-launch <dashboard.py arguments...>
#   e.g. task-dashboard-launch collect
#
# At every start it reads ~/.claude/plugins/installed_plugins.json (or
# $TASK_DASHBOARD_PLUGINS_JSON) for workflow-skills' installPath — the user
# scope record first, then any other whose directory holds the dashboard — and
# execs that copy's dashboard.py under uv with Python 3.11+. So a plugin update
# is picked up on the next run with nothing reinstalled.
#
# Exit status: dashboard.py's own, or 1 when the plugin cannot be found, with
# the reason on stderr.
set -eu

UV='@UV@'
MANIFEST="${TASK_DASHBOARD_PLUGINS_JSON:-$HOME/.claude/plugins/installed_plugins.json}"

root=$(
  "$UV" run --no-project --python '>=3.11' python -I - "$MANIFEST" <<'PY'
import json
import os
import sys

manifest = sys.argv[1]
try:
    with open(manifest) as fh:
        data = json.load(fh)
except FileNotFoundError:
    sys.exit(f"task-dashboard-launch: {manifest} not found; is Claude Code installed?")
except (OSError, ValueError) as exc:
    sys.exit(f"task-dashboard-launch: cannot read {manifest}: {exc}")
candidates = []
plugins = data.get("plugins") if isinstance(data, dict) else None
for name, records in (plugins if isinstance(plugins, dict) else {}).items():
    if name.split("@", 1)[0] != "workflow-skills":
        continue
    for record in records if isinstance(records, list) else [records]:
        path = record.get("installPath") if isinstance(record, dict) else None
        if isinstance(path, str) and path:
            candidates.append((record.get("scope") != "user", path))
if not candidates:
    sys.exit(f"task-dashboard-launch: workflow-skills is not installed (no entry in {manifest})")
for _, path in sorted(candidates, key=lambda c: c[0]):
    if os.path.isfile(os.path.join(path, "scripts", "task-dashboard", "dashboard.py")):
        print(path)
        sys.exit(0)
sys.exit(
    "task-dashboard-launch: no workflow-skills installPath in "
    f"{manifest} has scripts/task-dashboard/dashboard.py"
)
PY
)

exec "$UV" run --no-project --python '>=3.11' "$root/scripts/task-dashboard/dashboard.py" "$@"
