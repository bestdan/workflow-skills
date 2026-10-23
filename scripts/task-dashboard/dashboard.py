"""Task dashboard CLI entry point.

Run as `uv run --no-project --python ">=3.11" python scripts/task-dashboard/dashboard.py`.
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
from pathlib import Path  # noqa: E402

# The directory name has a hyphen, so it is not importable as a package; put it
# on sys.path and import the siblings as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dashboard", description="Task dashboard.")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser(
        "init-db", help="create/migrate the SQLite DB and print its path"
    )
    init.add_argument(
        "--db", help="DB path (default: $TASK_DASHBOARD_DB or the state dir)"
    )
    return parser


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-db":
        conn = db.connect(args.db)
        conn.close()
        print(db.db_path(args.db))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
