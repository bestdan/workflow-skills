"""The stable launcher both installs share, and the helpers both need.

`install` (the server's LaunchAgent, server_install.py) and `install
--collector-only` (the collector's LaunchAgent or systemd units,
collector_install.py) both run one file, ~/.local/bin/task-dashboard-launch,
rendered from task-dashboard-launch.sh with this machine's uv. Neither unit
names the plugin's versioned install directory; the launcher resolves it at
each start. Keeping the template, its rendering and the paths here means the
two installs cannot drift into writing different copies of the same file.
"""

import shlex
from pathlib import Path

LAUNCHER_TEMPLATE = Path(__file__).resolve().parent / "task-dashboard-launch.sh"
UV_PLACEHOLDER = "'@UV@'"
BASE_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


class InstallError(RuntimeError):
    """Something install needs is missing; the message says what."""


def launcher_path(home: Path) -> Path:
    return home / ".local/bin/task-dashboard-launch"


def log_dir(home: Path) -> Path:
    return home / ".local/state/task-dashboard"


def render_launcher(uv: str) -> str:
    text = LAUNCHER_TEMPLATE.read_text()
    if text.count(UV_PLACEHOLDER) != 1:
        raise InstallError(f"{LAUNCHER_TEMPLATE} lost its {UV_PLACEHOLDER} line")
    return text.replace(UV_PLACEHOLDER, shlex.quote(uv), 1)


def stable_tool(found: str) -> str:
    """`found`, or its mise shim when `found` is a versioned mise install.

    An activated mise puts `…/mise/installs/<tool>/<version>/…` on PATH, and
    that directory goes away on the next upgrade: the same breakage the stable
    launcher exists to avoid. The shim beside `installs/` survives it.
    """
    parts = Path(found).parts
    if "installs" in parts:
        i = len(parts) - 1 - parts[::-1].index("installs")
        shim = Path(*parts[:i], "shims", Path(found).name)
        if shim.exists():
            return str(shim)
    return found
