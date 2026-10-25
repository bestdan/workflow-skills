"""Install and operate the dashboard server as a macOS LaunchAgent.

`dashboard.py install | uninstall | status | sync-now` land here. The server
runs always-on as a gui-domain LaunchAgent, `com.bestdan.task-dashboard`,
because that is where acli's OAuth login works unattended: the login keychain
is unlocked once the user has logged in (the spike in
dev_docs/research/2026-10-23-task-dashboard-acli-spike.md).

The plist never names the plugin's install directory, which carries the
plugin version and so moves on every update. It runs the stable launcher
~/.local/bin/task-dashboard-launch (rendered from task-dashboard-launch.sh with
this machine's uv), and
the launcher resolves the current installPath at each start. A plugin update
then needs only `launchctl kickstart -k`, or the next login, never a reinstall.

A launchd environment has almost no PATH, so install resolves uv, acli and gh
to absolute paths now: uv is written into the launcher, and the directories of
all three into the plist's PATH (gh is run by name, and acli by
`jira.acli_path`, which may be a bare name).

Install order matters. Any running agent is booted out first, then the
launcher runs `init-db` and a `full` sync in the foreground, and only then is
the agent bootstrapped. The foreground run warms uv's interpreter cache: a cold
uv under launchd would try to download Python at login. It also means the
agent's own start-up sync is a cheap incremental one rather than a second
~100s full sync racing this one.

Every external command goes through one runner seam and every path is under
the `home` argument, so the tests drive all of it against fakes in a temp
directory. Nothing here is exercised against the real launchd by the gate.
"""

import json
import os
import plistlib
import shlex
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import server
import sync
from github_gh import Runner, run
from launcher import (
    BASE_PATH,
    InstallError,
    launcher_path,
    log_dir,
    render_launcher,
    stable_tool,
)

LABEL = "com.bestdan.task-dashboard"
# KeepAlive restarts a crashed server; this keeps a config error from
# becoming a tight restart loop (launchd's own default is 10s).
THROTTLE_SECONDS = 30
FULL_SYNC_TIMEOUT = 900.0
COMMAND_TIMEOUT = 120.0

Log = Callable[[str], None]


def plist_path(home: Path) -> Path:
    return home / "Library/LaunchAgents" / f"{LABEL}.plist"


def url(port: int) -> str:
    return f"http://{server.BIND}:{port}/"


@dataclass
class Tools:
    """Absolute paths install resolved; `acli` or `gh` is empty when its side is off."""

    uv: str
    acli: str
    gh: str


def resolve_tools(
    cfg: dict[str, Any], which: Callable[[str], "str | None"] = shutil.which
) -> Tools:
    """uv, acli and gh as absolute paths; raises InstallError naming the gap.

    acli is not looked up with `jira.enabled` false, nor gh with
    `github.enabled` false.
    """
    uv = which("uv") or os.environ.get("UV") or ""
    if not uv:
        raise InstallError("uv is not on PATH; install it, then rerun install")
    acli = ""
    if sync.jira_enabled(cfg):
        acli_cfg = str(cfg["jira"]["acli_path"])
        acli = acli_cfg if os.path.isabs(acli_cfg) else (which(acli_cfg) or "")
        if not acli or not os.access(acli, os.X_OK):
            raise InstallError(
                f"jira.acli_path {acli_cfg!r} does not resolve to an executable;"
                " install acli, point jira.acli_path at it, or set"
                " jira.enabled = false"
            )
    gh = ""
    if cfg["github"]["enabled"]:
        gh = which("gh") or ""
        if not gh:
            raise InstallError(
                "gh is not on PATH; install it, or set github.enabled = false"
            )
    return Tools(
        uv=stable_tool(os.path.abspath(uv)),
        acli=stable_tool(os.path.abspath(acli)) if acli else "",
        gh=stable_tool(os.path.abspath(gh)) if gh else "",
    )


def versioned_mise_path(path: str) -> bool:
    """True for `…/mise/installs/<tool>/<version>/…`, which a mise upgrade deletes."""
    parts = Path(path).parts
    if "installs" not in parts:
        return False
    i = len(parts) - 1 - parts[::-1].index("installs")
    return i >= 1 and parts[i - 1] == "mise" and len(parts) > i + 2


def search_path(tools: Tools) -> str:
    """The agent's PATH: each tool's directory, then the system base."""
    dirs = [str(Path(t).parent) for t in (tools.uv, tools.acli, tools.gh) if t]
    dirs.extend(BASE_PATH)
    return ":".join(dict.fromkeys(dirs))


def agent_env(home: Path, path_env: str, overrides: dict[str, str]) -> dict[str, str]:
    """The plist's EnvironmentVariables. `overrides` carries the TASK_DASHBOARD_* paths."""
    return {"HOME": str(home), "PATH": path_env, **overrides}


# The paths install may write into the plist, and so the ones every later
# command must read back from it when no flag or env var names them.
PATH_VARS = (
    "TASK_DASHBOARD_CONFIG",
    "TASK_DASHBOARD_DB",
    "TASK_DASHBOARD_PLUGINS_JSON",
)


def installed_paths(home: Path) -> dict[str, str]:
    """The TASK_DASHBOARD_* paths in the installed plist; {} when there is none."""
    try:
        with open(plist_path(home), "rb") as fh:
            doc = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    env = doc.get("EnvironmentVariables") if isinstance(doc, dict) else None
    if not isinstance(env, dict):
        return {}
    return {k: v for k, v in env.items() if k in PATH_VARS and isinstance(v, str) and v}


def render_plist(home: Path, env: dict[str, str]) -> str:
    logs = log_dir(home)
    doc = {
        "Label": LABEL,
        "ProgramArguments": [str(launcher_path(home)), "serve"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "EnvironmentVariables": env,
        "StandardOutPath": str(logs / "server.out.log"),
        "StandardErrorPath": str(logs / "server.err.log"),
    }
    return plistlib.dumps(doc, sort_keys=False).decode()


@dataclass
class Step:
    """One command; any failure stops the install and removes what it wrote."""

    argv: list[str]
    timeout: float = COMMAND_TIMEOUT
    why: str = ""
    # True for bootstrap: once it has started, launchd may hold the agent, so
    # an exception from here on must not delete the plist from under it.
    loads: bool = False


@dataclass
class Plan:
    """Everything an install writes and runs, so --dry-run can print it."""

    uid: int = 0
    home: Path = Path("~")
    files: list[tuple[Path, str, int]] = field(default_factory=list)
    dirs: list[Path] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def plan(
    cfg: dict[str, Any],
    *,
    home: Path,
    tools: Tools,
    uid: int,
    overrides: "dict[str, str] | None" = None,
) -> Plan:
    """What install writes and runs; raises InstallError."""
    if not os.path.isabs(tools.uv):
        raise InstallError(f"uv must be an absolute path, got {tools.uv!r}")
    overrides = dict(overrides or {})
    env = agent_env(home, search_path(tools), overrides)
    launcher = str(launcher_path(home))
    domain = f"gui/{uid}"
    # The foreground runs see the same environment the agent will, so a
    # TASK_DASHBOARD_* override reaches them too; `env -i` keeps this
    # session's PATH from hiding a tool the agent will not find.
    fg = ["/usr/bin/env", "-i", *(f"{k}={v}" for k, v in env.items()), launcher]
    p = Plan(uid=uid, home=home)
    p.dirs.append(log_dir(home))
    p.files.append((launcher_path(home), render_launcher(tools.uv), 0o755))
    p.files.append((plist_path(home), render_plist(home, env), 0o644))
    p.steps.append(Step([*fg, "init-db"], why="launcher resolves the plugin; warms uv"))
    p.steps.append(
        Step(
            # --fleet-warns: the warm-up is judged on the Jira run alone; a
            # failed github.fleet step prints a warning and does not undo.
            [*fg, "sync", "--mode", "full", "--fleet-warns"],
            timeout=FULL_SYNC_TIMEOUT,
            why="first full sync",
        )
    )
    p.steps.append(
        Step(
            ["launchctl", "bootstrap", domain, str(plist_path(home))],
            why="load",
            loads=True,
        )
    )
    acli_cfg = str(cfg["jira"]["acli_path"])
    if (
        sync.jira_enabled(cfg)
        and os.path.isabs(acli_cfg)
        and versioned_mise_path(acli_cfg)
    ):
        p.notes.append(
            f"WARNING: jira.acli_path is {acli_cfg}, inside a versioned mise install"
            ' that the next mise upgrade removes; set jira.acli_path = "acli" so'
            " the agent finds acli through PATH (the mise shim)"
        )
    port = cfg["server"]["port"]
    p.notes.append(f"dashboard: {url(port)} (localhost: opens only on this machine)")
    p.notes.append(
        f"logs: {log_dir(home)}/server.err.log; after a plugin update:"
        f" launchctl kickstart -k {domain}/{LABEL}"
    )
    return p


def describe(p: Plan) -> str:
    """The --dry-run text: every file in full, then every command."""
    parts = [f"--- {path} (mode {mode:o})\n{text}" for path, text, mode in p.files]
    parts.append("--- commands")
    parts.append(" ".join(bootout_argv(p.uid)) + "   # before the files are written")
    for s in p.steps:
        parts.append(" ".join(shlex.quote(a) for a in s.argv))
    parts.extend(f"note: {n}" for n in p.notes)
    return "\n".join(parts)


def _write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def execute(
    p: Plan,
    *,
    runner: Runner = run,
    log: Log = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Boot out, write the files, run the steps; 0 installed, 1 failed.

    The old agent is booted out before any file is touched, and a bootout
    failure other than "not loaded" stops the install there: carrying on would
    rewrite the files of an agent that is still running. Once the files are
    written, any failed step (init-db, the first sync, bootstrap) removes the
    plist, and the launcher unless another agent runs it; so does an exception
    raised before bootstrap starts, which is then re-raised. A plist left
    behind would load at the next login, where KeepAlive restarts a failing
    agent.
    """
    ok, why = boot_out(p.uid, runner, sleep=sleep)
    if not ok:
        log(f"FAILED: {why}; nothing written")
        return 1
    reinstall = plist_path(p.home).exists()

    def undo(cause: str) -> None:
        log(cause)
        remove_files(p.home, log)
        if reinstall:
            log(
                "install undone, and the previous install with it: its agent was"
                " booted out and its files replaced before the failure"
            )
        else:
            log("install undone")

    loading = False
    try:
        for d in p.dirs:
            d.mkdir(parents=True, exist_ok=True)
        for path, text, mode in p.files:
            _write(path, text, mode)
            log(f"wrote {path}")
        for step in p.steps:
            loading = loading or step.loads
            rc, out, err = runner(step.argv, step.timeout)
            shown = " ".join(shlex.quote(a) for a in step.argv[-4:])
            if rc == 0:
                log(f"ran …{shown}" + (f"\n{out.strip()}" if out.strip() else ""))
                continue
            detail = "\n".join(s for s in (out.strip(), err.strip()) if s)
            undo(f"FAILED ({rc}): {step.why}: …{shown}\n{detail}")
            log("fix the cause above and run install again")
            return 1
    except BaseException as exc:
        # Ctrl-C in the long foreground sync, or an OSError writing a file:
        # undo like a failed step, unless bootstrap has started, after which
        # the agent may be loaded and its plist must stay.
        if not loading:
            undo(f"INTERRUPTED: {type(exc).__name__}: {exc}")
        raise
    for note in p.notes:
        log(note)
    return 0


# --- uninstall ---------------------------------------------------------------


def other_users_of_launcher(home: Path) -> list[Path]:
    """Other LaunchAgent plists whose ProgramArguments run the shared launcher.

    The fleet collector's agent runs the same ~/.local/bin launcher, so
    removing it under a still-installed collector would break that agent.
    """
    agents = home / "Library/LaunchAgents"
    launcher = str(launcher_path(home))
    found = []
    for plist in sorted(agents.glob("*.plist")) if agents.is_dir() else []:
        if plist.name == f"{LABEL}.plist":
            continue
        try:
            with open(plist, "rb") as fh:
                doc = plistlib.load(fh)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        args = doc.get("ProgramArguments") if isinstance(doc, dict) else None
        if isinstance(args, list) and args and args[0] == launcher:
            found.append(plist)
    return found


# `launchctl print` exits 113 for a service the domain does not have
# ("Could not find service …"; observed on macOS 27). Any other nonzero exit
# is a failure to ask, never evidence that the agent is gone.
NOT_FOUND = 113


def launchd_state(uid: int, runner: Runner) -> "tuple[str, str]":
    """("loaded" | "absent" | "error", the output); only exit 113 is "absent"."""
    rc, out, err = runner(["launchctl", "print", f"gui/{uid}/{LABEL}"], COMMAND_TIMEOUT)
    if rc == 0:
        return "loaded", out
    if rc == NOT_FOUND:
        return "absent", out
    detail = (err.strip() or out.strip() or "no output").splitlines()[0]
    return "error", f"launchctl print exited {rc}: {detail}"


def bootout_argv(uid: int) -> list[str]:
    return ["launchctl", "bootout", f"gui/{uid}/{LABEL}"]


# How long boot_out waits for launchd to drop the service after `bootout`.
BOOTOUT_WAIT_S = 5.0
BOOTOUT_POLL_S = 0.5


def boot_out(
    uid: int, runner: Runner, *, sleep: Callable[[float], None] = time.sleep
) -> "tuple[bool, str]":
    """(True, "") once the agent is not loaded; (False, why) otherwise.

    bootout's own exit codes for "not loaded" vary across macOS releases
    (3, 113, 36), so its result is judged by asking `launchctl print`. launchd
    may still list the service briefly after bootout returns, so "loaded" is
    re-checked every BOOTOUT_POLL_S for up to BOOTOUT_WAIT_S before it counts.
    """
    rc, out, err = runner(bootout_argv(uid), COMMAND_TIMEOUT)
    state, detail = launchd_state(uid, runner)
    for _ in range(int(BOOTOUT_WAIT_S / BOOTOUT_POLL_S)):
        if state != "loaded":
            break
        sleep(BOOTOUT_POLL_S)
        state, detail = launchd_state(uid, runner)
    if state == "absent":
        return True, ""
    if state == "loaded":
        msg = (err.strip() or out.strip()).splitlines()
        return False, (
            f"{LABEL} is still loaded after bootout (exit {rc}"
            + (f": {msg[0]}" if msg else "")
            + ")"
        )
    return False, detail


def remove_files(home: Path, log: Log) -> None:
    """Remove the plist, and the launcher unless another LaunchAgent runs it."""
    plist = plist_path(home)
    if plist.exists():
        plist.unlink()
        log(f"removed {plist}")
    launcher = launcher_path(home)
    users = other_users_of_launcher(home)
    if users:
        log(f"kept {launcher}: still run by {', '.join(str(u) for u in users)}")
    elif launcher.exists():
        launcher.unlink()
        log(f"removed {launcher}")


def uninstall(
    *,
    home: Path,
    uid: int,
    db_file: Path,
    config_file: Path,
    runner: Runner = run,
    log: Log = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Boot the agent out, remove the plist and launcher; 0 done, 1 not removed."""
    ok, why = boot_out(uid, runner, sleep=sleep)
    if not ok:
        log(f"FAILED: {why}; nothing removed")
        return 1
    log(f"booted out gui/{uid}/{LABEL} (or it was not loaded)")
    remove_files(home, log)
    log(
        f"kept the DB ({db_file}) and the config ({config_file}), and the logs in"
        f" {log_dir(home)}. Run `dashboard.py export` first if you mean to delete"
        " the DB: the overlay in it cannot be rebuilt."
    )
    return 0


# --- status ------------------------------------------------------------------


def _launchctl_field(out: str, name: str) -> str:
    for line in out.splitlines():
        key, sep, value = line.strip().partition(" = ")
        if sep and key == name:
            return value.strip()
    return ""


def _ro_uri(db_file: Path) -> str:
    """A read-only SQLite URI; as_uri() escapes the ?, # and % a URI would misread."""
    return Path(db_file).absolute().as_uri() + "?mode=ro"


def read_health(
    db_file: Path, interval_s: float, now: datetime
) -> "tuple[dict[str, Any] | None, str]":
    """server.sync_health from a read-only DB, or (None, why it could not)."""
    if not db_file.exists():
        return None, f"no DB at {db_file}"
    try:
        conn = sqlite3.connect(_ro_uri(db_file), uri=True)
        try:
            return server.sync_health(conn, interval_s, now), ""
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return None, f"cannot read {db_file}: {exc}"


def _age(seconds: "int | None") -> str:
    if seconds is None:
        return "never"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


@dataclass
class Status:
    installed: bool
    loaded: bool = False
    state: str = ""
    pid: str = ""
    last_ok_at: "str | None" = None
    age_seconds: "int | None" = None
    stale: bool = True
    last_run: "dict[str, Any] | None" = None
    url: str = ""
    problems: list[str] = field(default_factory=list)

    def healthy(self) -> bool:
        return self.installed and not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "healthy": self.healthy()}


def status(
    *,
    home: Path,
    uid: int,
    cfg: "dict[str, Any] | None",
    config_error: str,
    db_file: Path,
    now: datetime,
    runner: Runner = run,
) -> Status:
    """Whether the agent is installed and loaded, and how fresh its last sync is."""
    if not plist_path(home).exists():
        return Status(installed=False)
    st = Status(installed=True)
    state, out = launchd_state(uid, runner)
    st.loaded = state == "loaded"
    if st.loaded:
        st.state = _launchctl_field(out, "state")
        st.pid = _launchctl_field(out, "pid")
    elif state == "absent":
        st.problems.append(
            f"not loaded; load it: launchctl bootstrap gui/{uid} {plist_path(home)}"
        )
    else:
        st.problems.append(f"cannot read the agent's state: {out}")
    if cfg is None:
        st.problems.append(f"config unreadable: {config_error}")
        return st
    st.url = url(cfg["server"]["port"])
    interval_s = float(cfg["sync"]["interval_minutes"]) * 60
    health, why = read_health(db_file, interval_s, now)
    if health is None:
        st.problems.append(why)
        return st
    st.last_ok_at = health["last_ok_at"]
    st.age_seconds = health["age_seconds"]
    st.stale = health["stale"]
    st.last_run = health["last_run"]
    if st.stale:
        limit = server.STALE_INTERVALS * int(cfg["sync"]["interval_minutes"])
        msg = f"last successful sync {_age(st.age_seconds)} (stale past {limit}m)"
        run_ = st.last_run or {}
        # An auth_failed row's error already names its own login command
        # (acli or gh, recorded by sync.run), so it is shown as is.
        if run_.get("outcome") not in (None, "ok"):
            msg += (
                f"; last run {run_.get('outcome')}: {run_.get('error') or ''}".rstrip()
            )
        st.problems.append(msg)
    return st


def render_status(st: Status) -> str:
    if not st.installed:
        return f"task-dashboard: not installed (no {LABEL}.plist)"
    lines = [
        f"agent:     {'loaded' if st.loaded else 'NOT loaded'}"
        + (
            f" ({st.state}" + (f", pid {st.pid}" if st.pid else "") + ")"
            if st.state
            else ""
        ),
        f"last sync: {_age(st.age_seconds)}"
        + (f" ({st.last_ok_at})" if st.last_ok_at else ""),
    ]
    if st.last_run and st.last_run.get("outcome") not in (None, "ok"):
        lines.append(
            f"last run:  {st.last_run.get('outcome')}: {st.last_run.get('error') or ''}"
        )
    if st.url:
        lines.append(f"url:       {st.url} (localhost: opens only on this machine)")
    lines.extend(f"problem:   {p}" for p in st.problems)
    return "\n".join(lines)


def doctor_line(st: Status) -> str:
    """One line in /doctor's report shape; callers print nothing when not installed."""
    if st.healthy():
        pid = f", pid {st.pid}" if st.pid else ""
        return f"PASS  task-dashboard — loaded{pid}, last sync {_age(st.age_seconds)}"
    return "WARN  task-dashboard — " + "; ".join(st.problems)


# --- sync-now ----------------------------------------------------------------

Opener = Callable[[urllib.request.Request, float], Any]


def _open(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - loopback only


def _read(db_file: Path, sql: str, args: tuple = ()) -> "tuple | None":
    """One row from a read-only connection; None when absent or unreadable."""
    if not db_file.exists():
        return None
    try:
        conn = sqlite3.connect(_ro_uri(db_file), uri=True)
        try:
            row: "tuple | None" = conn.execute(sql, args).fetchone()
            return row
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def read_token(db_file: Path) -> str:
    """The server's per-install POST token, from `meta`; '' when absent."""
    row = _read(db_file, "SELECT value FROM meta WHERE key = ?", (server.TOKEN_KEY,))
    return str(row[0]) if row else ""


def _finished_after(db_file: Path, run_id: int, *, fleet: bool) -> "tuple | None":
    """The newest finished Jira run (or, with `fleet`, GitHub fleet run) after `run_id`.

    One sync request records a Jira run and, after it, a run for each fleet
    step that is on (GitHub, sessions, the cloud session list), so "the newest
    row" alone would report a fleet step's. A Jira run is one whose kind is a
    Jira mode.
    """
    kinds = (sync.FLEET_KIND,) if fleet else sync.MODES
    return _read(
        db_file,
        "SELECT id, kind, outcome, error, finished_at FROM sync_runs"
        f" WHERE finished_at IS NOT NULL AND id > ?"
        f" AND kind IN ({', '.join('?' * len(kinds))})"
        " ORDER BY id DESC LIMIT 1",
        (run_id, *kinds),
    )


def sync_now(
    *,
    port: int,
    db_file: Path,
    mode: str,
    wait_s: float,
    opener: Opener = _open,
    sleep: Callable[[float], None] = time.sleep,
    log: Log = print,
) -> int:
    """POST /sync like the header button, then wait for the run to finish.

    0 the run finished ok, 1 it failed or the server could not be reached,
    3 it did not finish within `wait_s` (it keeps running in the server).
    The outcome is read from `sync_runs`, by id above the newest finished run
    before the request: a fast run can finish before the POST even answers.
    """
    token = read_token(db_file)
    if not token:
        log(f"no server token in {db_file}; has the server ever started?")
        return 1
    # Finished runs only: a run already in flight is the one the POST joins,
    # and its id must count as "after".
    before = _read(
        db_file,
        "SELECT COALESCE(MAX(id), 0) FROM sync_runs WHERE finished_at IS NOT NULL",
    )
    before_id = int(before[0]) if before else 0
    base = url(port).rstrip("/")
    body = f"mode={mode}&token={urllib.parse.quote(token)}".encode()
    req = urllib.request.Request(
        f"{base}/sync",
        data=body,
        method="POST",
        headers={
            "Origin": base,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with opener(req, 10.0) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        log(f"cannot reach the dashboard at {base}: {exc}. Is the agent loaded?")
        return 1
    log(f"{mode} sync requested")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            with opener(urllib.request.Request(f"{base}/status"), 10.0) as resp:
                st = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log(f"lost the dashboard while waiting: {exc}")
            return 1
        if not st.get("busy"):
            break
        if time.monotonic() >= deadline:
            log(f"still running after {wait_s:g}s; it carries on in the server")
            return 3
        sleep(2.0)
    row = _finished_after(db_file, before_id, fleet=False)
    if row is None:
        log("the server went idle without recording a run; see its log")
        return 1
    _, kind, outcome, error, finished = row
    log(f"sync {kind}: {outcome} at {finished}")
    if error:
        log(str(error))
    # The fleet step is reported, but the exit code is the Jira sync's, as the
    # header's staleness is.
    fleet = _finished_after(db_file, before_id, fleet=True)
    if fleet is not None:
        log(f"sync {fleet[1]}: {fleet[2]} at {fleet[4]}")
        if fleet[3]:
            log(str(fleet[3]))
    return 0 if outcome == "ok" else 1
