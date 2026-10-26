"""Task dashboard CLI entry point.

Run as `uv run --no-project --python ">=3.11" scripts/task-dashboard/dashboard.py <command>`.
The program needs 3.11 (tomllib), but the `python3` macOS ships is 3.9, so the
version guard comes before any import that could fail on it. That also means
this file must stay *parseable* by 3.9: no match statements, no parenthesised
context managers, or 3.9 raises a SyntaxError before the guard can run. And
nothing above the guard may use a 3.10+ runtime feature such as `X | Y` on
types: 3.9 parses that fine but raises TypeError when it runs, again before the
guard prints the requirement. Below the guard, 3.9 never gets that far.
"""

import sys

if sys.version_info < (3, 11):
    print(
        "task-dashboard requires Python 3.11+ "
        f"(running {sys.version_info[0]}.{sys.version_info[1]}); "
        'run it with uv run --no-project --python ">=3.11" ...',
        file=sys.stderr,
    )
    sys.exit(1)

import argparse  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import sqlite3  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

# The directory name has a hyphen, so it is not importable as a package; put it
# on sys.path and import the siblings as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import collector  # noqa: E402
import collector_install  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import health  # noqa: E402
import ingest  # noqa: E402
import overlay  # noqa: E402
import server  # noqa: E402
import server_install  # noqa: E402
import sync  # noqa: E402
import whynot  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dashboard", description="Task dashboard.")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser(
        "init-db", help="create/migrate the SQLite DB and print its path"
    )
    init.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    sync_p = sub.add_parser("sync", help="run one sync now and record it in sync_runs")
    sync_p.add_argument(
        "--mode",
        choices=("incremental", "reconcile", "full"),
        default="incremental",
        help="incremental (default; full when no run has succeeded), reconcile, full",
    )
    sync_p.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    sync_p.add_argument(
        "--fleet-warns",
        action="store_true",
        help="exit on the Jira run alone; a failed github.fleet step only warns",
    )
    sync_p.add_argument(
        "--config",
        help="config path (default: $TASK_DASHBOARD_CONFIG or ~/.config/task-dashboard)",
    )
    serve_p = sub.add_parser(
        "serve", help="serve the dashboard on 127.0.0.1 and sync on an interval"
    )
    serve_p.add_argument("--port", type=int, help="port (default: server.port)")
    serve_p.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    serve_p.add_argument(
        "--config",
        help="config path (default: $TASK_DASHBOARD_CONFIG or ~/.config/task-dashboard)",
    )
    token_p = sub.add_parser("ingest-token", help="fleet view: per-machine tokens")
    token_sub = token_p.add_subparsers(dest="token_command", required=True)
    add_p = token_sub.add_parser(
        "add", help="generate a machine's ingest token and print it once"
    )
    add_p.add_argument("host", help="the machine's short host name, as it snapshots")
    export_p = sub.add_parser(
        "export", help="write the overlay (pins, order, snoozes, notes) as JSON"
    )
    export_p.add_argument("path", nargs="?", help="output file (default: stdout)")
    export_p.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    import_p = sub.add_parser(
        "import", help="replace the overlay with an export file's contents"
    )
    import_p.add_argument("path", help="a file written by `export`")
    import_p.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    collect_p = sub.add_parser(
        "collect",
        help="fleet view: snapshot this machine and push it to [collector] ingest_url",
    )
    collect_p.add_argument(
        "--config",
        help="config path (default: $TASK_DASHBOARD_CONFIG or ~/.config/task-dashboard)",
    )
    health_p = sub.add_parser(
        "health",
        help="fleet view: the Health panel's figures for a dated window",
    )
    health_p.add_argument(
        "--since", required=True, type=date.fromisoformat, help="first day, YYYY-MM-DD"
    )
    health_p.add_argument(
        "--until",
        type=date.fromisoformat,
        help="last day, included, YYYY-MM-DD (default: today); days are local time",
    )
    health_p.add_argument(
        "--json", action="store_true", help="print the figures as JSON"
    )
    health_p.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    health_p.add_argument(
        "--config",
        help="config path (default: $TASK_DASHBOARD_CONFIG or ~/.config/task-dashboard)",
    )
    tune_p = sub.add_parser(
        "tune",
        help='fleet view: Now\'s "why not now" answers joined to their score'
        " terms, with suggested [score.fleet] changes; suggests only",
    )
    tune_p.add_argument(
        "--days",
        type=int,
        default=whynot.WINDOW_DAYS,
        help=f"window in days, ending now (default {whynot.WINDOW_DAYS})",
    )
    tune_p.add_argument("--json", action="store_true", help="print JSON")
    _paths(tune_p)
    install_p = sub.add_parser(
        "install",
        help="macOS: install the dashboard server as a LaunchAgent (full sync, then"
        " load it); --collector-only schedules only the fleet collector",
    )
    install_p.add_argument(
        "--collector-only",
        action="store_true",
        help="schedule only the fleet collector (launchd or systemd), for a machine"
        " that runs no server",
    )
    install_p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the files and commands; write and run nothing",
    )
    install_p.add_argument(
        "--plugins-json",
        help="installed_plugins.json the launcher reads (default: Claude Code's)",
    )
    _paths(install_p)
    uninstall_p = sub.add_parser(
        "uninstall",
        help="boot the server agent out, remove plist and launcher (unless another"
        " agent runs it); keep data",
    )
    _paths(uninstall_p)
    status_p = sub.add_parser(
        "status", help="is the agent loaded, how old is the last sync, the URL"
    )
    _paths(status_p)
    shape = status_p.add_mutually_exclusive_group()
    shape.add_argument("--json", action="store_true", help="print JSON")
    shape.add_argument(
        "--doctor",
        action="store_true",
        help="one PASS/WARN line for /doctor; prints nothing when not installed",
    )
    now_p = sub.add_parser(
        "sync-now", help="ask the running server for a sync and wait for it"
    )
    _paths(now_p)
    now_p.add_argument(
        "--reconcile", action="store_true", help="a reconcile, not an incremental"
    )
    now_p.add_argument(
        "--wait", type=float, default=300.0, help="seconds to wait (default 300)"
    )
    url_p = sub.add_parser(
        "url", aliases=["open"], help="print the dashboard URL (alias: open)"
    )
    url_p.add_argument("--config", help="config path")
    return parser


def _paths(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    p.add_argument(
        "--config",
        help="config path (default: $TASK_DASHBOARD_CONFIG or ~/.config/task-dashboard)",
    )


def cmd_ingest_token_add(args: argparse.Namespace) -> int:
    """Print a new token and its config digest; writes nothing."""
    if not config.HOST_RE.fullmatch(args.host):
        print(
            f"task-dashboard: {args.host!r} is not a plain host name", file=sys.stderr
        )
        return 2
    token = secrets.token_urlsafe(32)
    print(f"Ingest token for {args.host}. It is shown once and stored nowhere.")
    print(f"\n1. On {args.host}, put it in the collector's config:\n\n  {token}")
    print(
        "\n2. On the aggregator, add this under [ingest.machines] in"
        f" {config.config_path()} and restart the server:"
        f'\n\n  {args.host} = "{ingest.digest(token)}"'
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Exit 0 on success, 1 on an OS or SQLite error (an earlier file is kept)."""
    try:
        conn = db.connect(args.db)
        try:
            text = overlay.dumps(overlay.export(conn))
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        print(f"task-dashboard: export failed: {exc}", file=sys.stderr)
        return 1
    if not args.path:
        sys.stdout.write(text)
        return 0
    # Write beside the target and rename, so a failed write never truncates an
    # earlier backup of the one table set that cannot be rebuilt.
    dest = Path(args.path)
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, dest)
    except OSError as exc:
        with contextlib.suppress(OSError):  # best effort: the path may be bad too
            tmp.unlink(missing_ok=True)
        print(f"task-dashboard: cannot write {dest}: {exc}", file=sys.stderr)
        return 1
    print(f"exported overlay to {dest}", file=sys.stderr)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Exit 0 on success, 1 when the file is refused (the overlay is unchanged)."""
    try:
        doc = json.loads(Path(args.path).read_text())
    except (OSError, ValueError, RecursionError) as exc:
        print(f"task-dashboard: cannot read {args.path}: {exc}", file=sys.stderr)
        return 1
    try:
        conn = db.connect(args.db)
    except (sqlite3.Error, OSError) as exc:
        print(
            f"task-dashboard: import failed, overlay unchanged: {exc}", file=sys.stderr
        )
        return 1
    try:
        counts = overlay.import_(conn, doc)
    except (overlay.OverlayError, sqlite3.Error) as exc:
        print(
            f"task-dashboard: import refused, overlay unchanged: {exc}", file=sys.stderr
        )
        return 1
    finally:
        conn.close()
    print("imported overlay: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    """Exit 0 when everything was pushed, 1 when some waits, 2 on a config error."""
    try:
        cfg = config.load_collector(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    return collector.collect(cfg, home=Path.home(), now=datetime.now(timezone.utc))


def cmd_install_collector(args: argparse.Namespace) -> int:
    """`install --collector-only`: exit 0 installed, 1 failed, 2 refused."""
    try:
        cfg = config.load_collector(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    return collector_install.install(cfg, dry_run=args.dry_run)


def cmd_health(args: argparse.Namespace) -> int:
    """Exit 0, or 2 on a config error or a window that ends before it starts."""
    until = args.until or date.today()
    if until < args.since:
        print("task-dashboard: --until is before --since", file=sys.stderr)
        return 2
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    start, end = health.date_window(args.since, until)
    conn = db.connect(args.db)
    try:
        result = health.compute(
            conn,
            cfg["health"]["targets"],
            start,
            end,
            fleet=sync.fleet_enabled(cfg),
        )
    finally:
        conn.close()
    for fid in result["unknown_targets"]:
        print(f"task-dashboard: health.targets.{fid} names no figure", file=sys.stderr)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        sys.stdout.write(health.text_table(result))
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Exit 0, or 2 on a config error or a window under one day. Never writes
    config: what it prints is for a human or a session to act on."""
    if args.days < 1:
        print("task-dashboard: --days must be at least 1", file=sys.stderr)
        return 2
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    conn = db.connect(args.db)
    try:
        doc = whynot.tune(
            conn, cfg["score"]["fleet"], datetime.now(timezone.utc), args.days
        )
    finally:
        conn.close()
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        sys.stdout.write(whynot.text_report(doc))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run until interrupted; exit 2 on a config error."""
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    port = args.port if args.port is not None else cfg["server"]["port"]
    server.serve(cfg, db.db_path(args.db), port)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Exit 0 on ok, 1 on failed, 3 on auth_failed, 2 on a config error.

    With `github.fleet` on, the GitHub fleet step runs after the Jira sync as
    its own sync_runs row, and a failure of either run makes the exit nonzero
    — unless --fleet-warns, which install's warm-up sync passes: then only the
    Jira run decides the exit, and a failed fleet step prints a warning.
    """
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    conn = db.connect(args.db)
    try:
        jira_row, *fleet = sync.run_all(
            args.mode, cfg, conn, datetime.now(timezone.utc)
        )
    finally:
        conn.close()
    code = _report_sync(jira_row)
    for row in fleet:
        # Printed whatever the Jira outcome; the Jira run's code wins a tie.
        fleet_code = _report_sync(row, "PR lookup failed")
        if args.fleet_warns:
            if fleet_code:
                print(
                    f"WARNING: the github.fleet step ended {row['outcome']};"
                    " not counted (--fleet-warns)",
                    file=sys.stderr,
                )
            continue
        code = code or fleet_code
    return code


def _report_sync(row: dict, pr_failed: str = "CI lookup failed") -> int:
    """Print one sync_runs row; exit 0 on ok, 1 on failed, 3 on auth_failed."""
    print(
        f"sync {row['kind']}: {row['outcome']} "
        f"({row['started_at']} -> {row['finished_at']}) {row['counts'] or '{}'}"
    )
    failed = json.loads(row["failed_keys"] or "[]")
    views = [k for k in failed if not sync._is_pr_url(k)]
    ci = [k for k in failed if sync._is_pr_url(k)]
    if views:
        print(f"view failed twice, logged: {', '.join(views)}", file=sys.stderr)
    if ci:
        print(f"{pr_failed}, logged: {', '.join(ci)}", file=sys.stderr)
    if row["error"]:
        print(row["error"], file=sys.stderr)
    return {"ok": 0, "auth_failed": 3}.get(row["outcome"], 1)


def _overrides(args: argparse.Namespace) -> dict[str, str]:
    """TASK_DASHBOARD_* paths for the agent: flag, then env, then the installed plist.

    The plist is last so a reinstall without the flags keeps what the first
    install was given, and so every other command reads the agent's own paths.
    """
    baked = server_install.installed_paths(Path.home())
    out = {}
    for var, flag in (
        (config.ENV_VAR, getattr(args, "config", None)),
        (db.ENV_VAR, getattr(args, "db", None)),
        ("TASK_DASHBOARD_PLUGINS_JSON", getattr(args, "plugins_json", None)),
    ):
        value = flag or os.environ.get(var)
        if value:
            out[var] = str(Path(value).expanduser().resolve())
        elif var in baked:
            out[var] = baked[var]
    return out


def _agent_paths(args: argparse.Namespace) -> "tuple[str | None, str | None]":
    """(config, DB) as the installed agent sees them, for the post-install commands."""
    paths = _overrides(args)
    return paths.get(config.ENV_VAR), paths.get(db.ENV_VAR)


def _not_macos(verb: str) -> bool:
    if sys.platform == "darwin":
        return False
    print(
        f"task-dashboard: {verb} manages a macOS LaunchAgent; elsewhere run"
        " `dashboard.py serve` under your own supervisor",
        file=sys.stderr,
    )
    return True


def cmd_install(args: argparse.Namespace) -> int:
    """Exit 0 installed, 1 a command failed, 2 refused before writing anything.

    Plain `install` sets up the server's LaunchAgent; `--collector-only` hands
    over to the collector's install, which also covers systemd.
    """
    if args.collector_only:
        ignored = [
            f
            for f, v in (("--db", args.db), ("--plugins-json", args.plugins_json))
            if v
        ]
        if ignored:
            print(
                f"task-dashboard install: {' and '.join(ignored)} apply only to the"
                " server install, not --collector-only",
                file=sys.stderr,
            )
            return 2
        return cmd_install_collector(args)
    if _not_macos("install"):
        return 2
    try:
        # The same config the plist will name: flag, env, then a prior install's.
        cfg = config.load(_agent_paths(args)[0])
        p = server_install.plan(
            cfg,
            home=Path.home(),
            tools=server_install.resolve_tools(cfg),
            uid=os.getuid(),
            overrides=_overrides(args),
        )
    except (config.ConfigError, server_install.InstallError) as exc:
        print(f"task-dashboard install: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(server_install.describe(p))
        return 0
    return server_install.execute(p)


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Exit 0 removed, 1 the agent could not be booted out, 2 not macOS."""
    if _not_macos("uninstall"):
        return 2
    cfg_path, db_arg = _agent_paths(args)
    return server_install.uninstall(
        home=Path.home(),
        uid=os.getuid(),
        db_file=db.db_path(db_arg),
        config_file=config.config_path(cfg_path),
    )


def cmd_status(args: argparse.Namespace) -> int:
    """Exit 0 healthy, 1 a problem, 3 not installed (no plist)."""
    cfg, error = None, ""
    cfg_path, db_arg = _agent_paths(args)
    try:
        cfg = config.load(cfg_path)
    except config.ConfigError as exc:
        error = str(exc)
    st = server_install.status(
        home=Path.home(),
        uid=os.getuid(),
        cfg=cfg,
        config_error=error,
        db_file=db.db_path(db_arg),
        now=datetime.now(timezone.utc),
    )
    if args.json:
        print(json.dumps(st.as_dict(), indent=2))
    elif args.doctor:
        if st.installed:
            print(server_install.doctor_line(st))
    else:
        print(server_install.render_status(st))
    if not st.installed:
        return 3
    return 0 if st.healthy() else 1


def cmd_sync_now(args: argparse.Namespace) -> int:
    """Exit 0 ok, 1 failed or unreachable, 2 config error, 3 still running."""
    cfg_path, db_arg = _agent_paths(args)
    try:
        cfg = config.load(cfg_path)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    return server_install.sync_now(
        port=cfg["server"]["port"],
        db_file=db.db_path(db_arg),
        mode="reconcile" if args.reconcile else "incremental",
        wait_s=args.wait,
    )


def cmd_url(args: argparse.Namespace) -> int:
    cfg_path, _ = _agent_paths(args)
    try:
        cfg = config.load(cfg_path)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    print(server_install.url(cfg["server"]["port"]))
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-db":
        conn = db.connect(args.db)
        conn.close()
        print(db.db_path(args.db))
        return 0
    if args.command == "sync":
        return cmd_sync(args)
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "ingest-token":
        return cmd_ingest_token_add(args)
    if args.command == "export":
        return cmd_export(args)
    if args.command == "import":
        return cmd_import(args)
    if args.command == "collect":
        return cmd_collect(args)
    if args.command == "install":
        return cmd_install(args)
    if args.command == "uninstall":
        return cmd_uninstall(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "sync-now":
        return cmd_sync_now(args)
    if args.command in ("url", "open"):
        return cmd_url(args)
    if args.command == "health":
        return cmd_health(args)
    if args.command == "tune":
        return cmd_tune(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
