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
import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

# The directory name has a hyphen, so it is not importable as a package; put it
# on sys.path and import the siblings as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import db  # noqa: E402
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
    return parser


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
    """Exit 0 on ok, 1 on failed, 3 on auth_failed, 2 on a config error."""
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"task-dashboard: {exc}", file=sys.stderr)
        return 2
    conn = db.connect(args.db)
    try:
        row = sync.run(args.mode, cfg, conn, datetime.now(timezone.utc))
    finally:
        conn.close()
    print(
        f"sync {row['kind']}: {row['outcome']} "
        f"({row['started_at']} -> {row['finished_at']}) {row['counts'] or '{}'}"
    )
    failed = json.loads(row["failed_keys"] or "[]")
    if failed:
        print(f"failed twice, logged: {', '.join(failed)}", file=sys.stderr)
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
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
