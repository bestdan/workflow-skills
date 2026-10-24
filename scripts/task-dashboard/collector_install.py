"""`dashboard.py install --collector-only`: schedule the collector on this machine.

macOS gets a gui-domain LaunchAgent that runs at load and every
``INTERVAL_SECONDS``; Linux gets a ``systemd --user`` service and timer. Both
run the stable launcher (task-dashboard-launch.sh, rendered into
``~/.local/bin/``), never a path inside the plugin's versioned install
directory, so a plugin update needs no reinstall.

NO SECRET IN ANY FILE THIS WRITES. The unit runs ``task-dashboard-launch
collect`` with no other argument; the token stays in the config file, which
config.load_collector() requires to be mode 0600. Install refuses to run until
that config loads, so a unit is never installed that could only fail.

A scheduler's environment has almost no PATH, so the units carry one built at
install time from the directories of ``git``, ``tmux``, ``gh`` and ``uv``: the
snapshot runs the first two, and branch-remove.sh's landed check runs ``gh``.

Every external command goes through one runner seam, and every path is under
the ``home`` argument, so the tests render and install into a temp directory
against fakes. ``--dry-run`` prints the files and the commands and does
neither.
"""

import getpass
import importlib.util
import os
import plistlib
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from github_gh import Runner, run

LABEL = "com.bestdan.task-dashboard.collector"
UNIT = "task-dashboard-collector"
INTERVAL_SECONDS = 300
LAUNCHER_TEMPLATE = Path(__file__).resolve().parent / "task-dashboard-launch.sh"
HOOK = Path(__file__).resolve().parents[1] / "session-state-hook.py"
UV_PLACEHOLDER = "'@UV@'"
BASE_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
PATH_TOOLS = ("git", "tmux", "gh", "uv")


class InstallError(RuntimeError):
    """Something install needs is missing; the message says what."""


@dataclass
class Plan:
    """Everything an install writes and runs, so --dry-run can print it."""

    files: list[tuple[Path, str, int]] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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
    """``found``, or its mise shim when ``found`` is a versioned mise install.

    An activated mise puts ``…/mise/installs/<tool>/<version>/…`` on PATH, and
    that directory goes away on the next upgrade: the same breakage the stable
    launcher exists to avoid. The shim beside ``installs/`` survives it.
    """
    parts = Path(found).parts
    if "installs" in parts:
        i = len(parts) - 1 - parts[::-1].index("installs")
        shim = Path(*parts[:i], "shims", Path(found).name)
        if shim.exists():
            return str(shim)
    return found


def search_path(which: Any = shutil.which) -> str:
    """Install-time PATH for the units: each tool's directory, then the base."""
    dirs: list[str] = []
    for tool in PATH_TOOLS:
        found = which(tool)
        if found:
            dirs.append(str(Path(stable_tool(found)).parent))
    dirs.extend(BASE_PATH)
    return ":".join(dict.fromkeys(dirs))


def render_plist(home: Path, path_env: str) -> str:
    logs = log_dir(home)
    doc = {
        "Label": LABEL,
        "ProgramArguments": [str(launcher_path(home)), "collect"],
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "ProcessType": "Background",
        "EnvironmentVariables": {"HOME": str(home), "PATH": path_env},
        "StandardOutPath": str(logs / "collector.log"),
        "StandardErrorPath": str(logs / "collector.log"),
    }
    return plistlib.dumps(doc, sort_keys=False).decode()


def render_service(home: Path, path_env: str) -> str:
    return (
        "[Unit]\n"
        "Description=task-dashboard collector: push this machine's snapshot to"
        " the fleet view\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment=PATH={path_env}\n"
        f"ExecStart={launcher_path(home)} collect\n"
    )


def render_timer() -> str:
    minutes = INTERVAL_SECONDS // 60
    return (
        "[Unit]\n"
        f"Description=Run {UNIT}.service every {minutes} minutes\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=1min\n"
        f"OnUnitActiveSec={minutes}min\n"
        "AccuracySec=30s\n"
        f"Unit={UNIT}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def plist_path(home: Path) -> Path:
    return home / "Library/LaunchAgents" / f"{LABEL}.plist"


def unit_dir(home: Path) -> Path:
    return home / ".config/systemd/user"


def hook_enabled(config_path: Path) -> bool:
    """The session-state hook's own reading of `[collector] enabled`."""
    spec = importlib.util.spec_from_file_location("session_state_hook", HOOK)
    if spec is None or spec.loader is None:
        return False
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return bool(mod.collector_enabled(config_path.read_text()))
    except OSError:
        return False


def plan(
    cfg: dict[str, Any],
    *,
    home: Path,
    platform: str,
    uv: str,
    path_env: str,
    uid: int,
    user: str,
) -> Plan:
    """What install writes and runs on ``platform``; raises InstallError."""
    if not uv:
        raise InstallError("uv is not on PATH; install it, then rerun install")
    if not os.path.isabs(uv):
        raise InstallError(f"uv must be an absolute path, got {uv!r}")
    p = Plan()
    p.files.append((launcher_path(home), render_launcher(uv), 0o755))
    warm = [uv, "run", "--no-project", "--python", ">=3.11", "python", "-c", "pass"]
    p.commands.append(warm)
    if platform == "darwin":
        plist = plist_path(home)
        p.files.append((plist, render_plist(home, path_env), 0o644))
        domain = f"gui/{uid}"
        p.commands.append(["launchctl", "bootout", f"{domain}/{LABEL}"])
        p.commands.append(["launchctl", "bootstrap", domain, str(plist)])
        p.notes.append(
            f"check it: launchctl print {domain}/{LABEL}; log:"
            f" {log_dir(home) / 'collector.log'}"
        )
    elif platform.startswith("linux"):
        units = unit_dir(home)
        p.files.append(
            (units / f"{UNIT}.service", render_service(home, path_env), 0o644)
        )
        p.files.append((units / f"{UNIT}.timer", render_timer(), 0o644))
        p.commands.append(["systemctl", "--user", "daemon-reload"])
        p.commands.append(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
        # The timer's first run is a minute after boot; run once now, as the
        # LaunchAgent's RunAtLoad does.
        p.commands.append(
            ["systemctl", "--user", "start", "--no-block", f"{UNIT}.service"]
        )
        p.commands.append(
            ["loginctl", "show-user", user, "--property=Linger", "--value"]
        )
        p.notes.append(
            f"check it: systemctl --user list-timers {UNIT}.timer;"
            f" log: journalctl --user -u {UNIT}.service"
        )
    else:
        raise InstallError(f"no collector scheduler for platform {platform!r}")
    if not hook_enabled(cfg["config_path"]):
        p.notes.append(
            "the session-state hook is off: it reads a literal `[collector]`"
            " header followed by `enabled = true`, so snapshots will carry no"
            " session events until the config says so"
        )
    return p


def _linger_note(user: str, out: str) -> str | None:
    if out.strip() == "yes":
        return None
    return (
        f"lingering is OFF for {user}: the timer stops when you log out."
        f" Turn it on with: loginctl enable-linger {user}"
        " (it may need sudo)"
    )


def execute(p: Plan, *, runner: Runner = run, log: Any = print) -> int:
    """Write the files, run the commands; 0 on success, 1 if a command failed."""
    for path, text, mode in p.files:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        log(f"wrote {path}")
    for argv in p.commands:
        rc, out, err = runner(argv, 600.0)
        shown = " ".join(shlex.quote(a) for a in argv)
        if argv[:2] == ["launchctl", "bootout"]:
            continue  # not loaded yet is the normal first-install answer
        if argv[0] == "loginctl":
            note = _linger_note(argv[2], out) if rc == 0 else (
                f"could not read lingering ({err.strip() or rc}); check it with"
                f" loginctl show-user {argv[2]} --property=Linger"
            )  # fmt: skip
            if note:
                log(f"WARNING: {note}")
            continue
        if rc != 0:
            log(f"FAILED ({rc}): {shown}\n{err.strip()}")
            return 1
        log(f"ran {shown}")
    for note in p.notes:
        log(note)
    return 0


def describe(p: Plan) -> str:
    """The --dry-run text: every file in full, then every command."""
    parts = []
    for path, text, mode in p.files:
        parts.append(f"--- {path} (mode {mode:o})\n{text}")
    parts.append("--- commands")
    parts.extend(" ".join(shlex.quote(a) for a in argv) for argv in p.commands)
    parts.extend(f"note: {n}" for n in p.notes)
    return "\n".join(parts)


def install(cfg: dict[str, Any], *, dry_run: bool, runner: Runner = run) -> int:
    """The CLI entry: this machine, this user."""
    # PATH's uv before uv run's own $UV: under mise, $UV is the versioned path.
    uv = shutil.which("uv") or os.environ.get("UV") or ""
    try:
        p = plan(
            cfg,
            home=Path.home(),
            platform=sys.platform,
            uv=stable_tool(os.path.abspath(uv)) if uv else "",
            path_env=search_path(),
            uid=os.getuid(),
            user=getpass.getuser(),
        )
    except InstallError as exc:
        print(f"task-dashboard install: {exc}", file=sys.stderr)
        return 2
    if dry_run:
        print(describe(p))
        return 0
    log_dir(Path.home()).mkdir(parents=True, exist_ok=True)
    return execute(p, runner=runner)
