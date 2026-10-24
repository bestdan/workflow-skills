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
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

# The directory name has a hyphen, so it is not importable as a package; put it
# on sys.path and import the siblings as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import collector  # noqa: E402
import collector_install  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import ingest  # noqa: E402
import overlay  # noqa: E402
import server  # noqa: E402
import sync  # noqa: E402


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
    install_p = sub.add_parser(
        "install", help="schedule this machine's dashboard process (launchd/systemd)"
    )
    install_p.add_argument(
        "--collector-only",
        action="store_true",
        help="schedule only the fleet collector, for a machine that runs no server",
    )
    install_p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the files and commands; write and run nothing",
    )
    install_p.add_argument(
        "--config",
        help="config path (default: $TASK_DASHBOARD_CONFIG or ~/.config/task-dashboard)",
    )
    return parser


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


def cmd_install(args: argparse.Namespace) -> int:
    """Exit 0 installed, 1 a command failed, 2 refused before writing anything."""
    if not args.collector_only:
        print(
            "task-dashboard: only `install --collector-only` exists so far;"
            " the server install is tracked in bestdan/workflow-skills#1093",
            file=sys.stderr,
        )
        return 2
    try:
        cfg = config.load_collector(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    return collector_install.install(cfg, dry_run=args.dry_run)


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
    its own sync_runs row, and a failure of either run makes the exit nonzero.
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
        # Nonzero when either run failed; the Jira run's code wins a tie.
        code = code or _report_sync(row, "PR lookup failed")
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
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
