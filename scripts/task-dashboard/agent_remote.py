"""Run the agent commands against a dashboard on another machine.

The agent commands (`items`, `brief`, `overlay`, ...) open the SQLite file
directly, so they work only where the DB is. A machine that runs only the
collector (lindev) has no DB; this module lets its agent reach the aggregator's
over HTTP, through the same CLI.

SERVER. `POST /agent` exists only when `[agent.machines]` is set. It passes
server.py's Host check, then a per-machine bearer token, compared the way
ingest.py compares its own: `dashboard.py agent-token add <host>` prints the
token once, and only its SHA-256 goes in `[agent.machines]`. The tokens are
separate from ingest's, so a machine can push snapshots without being able to
write the overlay, and either can be revoked alone.

The body is `{"argv": [...], "stdin": "..."}`. The argv is parsed with the
CLI's own parser before anything runs, and refused (400) unless it names one of
COMMANDS, sets no `--db` or `--config` (the server supplies its own), and reads
no file (`plan set --file` must be `-`, its content sent as `stdin`). Then
`dashboard.py` runs as a child process with the server's DB and config, and the
answer is `{"exit", "stdout", "stderr"}`, so the caller sees exactly what a
local run would have printed. A child is used rather than calling the command
in-process because the commands write to sys.stdout, which the server's other
threads share.

CLIENT. A `[remote]` table (`url`, `token`) in the local config, mode 0600,
makes those commands forward instead of opening a local DB. `--db` on the
command line keeps a run local, and the child the server starts never forwards
(NO_REMOTE_ENV), so a misconfigured aggregator cannot loop on itself.
"""

import argparse
import http.client
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from email.message import Message
from io import BufferedIOBase
from pathlib import Path
from typing import Any, Callable

import ingest

COMMANDS = (
    "view", "scopes", "items", "item", "brief", "plan", "overlay", "refresh",
    "tune", "health", "status", "sync-now",
)  # fmt: skip
NO_REMOTE_ENV = "TASK_DASHBOARD_NO_REMOTE"
MAX_BODY = 1024 * 1024
MAX_ARGS = 64
# refresh calls gh and sync-now waits up to its --wait (300s by default).
RUN_TIMEOUT = 330.0
CLIENT_TIMEOUT = RUN_TIMEOUT + 30.0

Result = tuple[int, dict[str, Any], "str | None"]
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ArgsRefused(Exception):
    pass


class _RaisingParser(argparse.ArgumentParser):
    """A parser that raises where argparse would print and exit."""

    def error(self, message: str) -> Any:  # type: ignore[override]
        raise ArgsRefused(message)

    def exit(self, status: int = 0, message: "str | None" = None) -> Any:
        raise ArgsRefused(message or "help and version are not run remotely")

    def _print_message(self, message: str, file: Any = None) -> None:
        pass


def check_argv(argv: Any, build_parser: Callable[..., argparse.ArgumentParser]) -> str:
    """The command `argv` runs, or ArgsRefused saying why it may not run here."""
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise ArgsRefused("argv must be a list of strings")
    if not argv or len(argv) > MAX_ARGS or any("\0" in a for a in argv):
        raise ArgsRefused(f"argv must hold 1 to {MAX_ARGS} arguments, no NUL")
    args = build_parser(_RaisingParser).parse_args(argv)
    if args.command not in COMMANDS:
        raise ArgsRefused(
            f"{args.command!r} is not run remotely; one of: {', '.join(COMMANDS)}"
        )
    for name in ("db", "config"):
        if getattr(args, name, None) is not None:
            raise ArgsRefused(f"--{name} is the server's to choose")
    if getattr(args, "file", None) not in (None, "-"):
        raise ArgsRefused("--file must be -, with the content sent as stdin")
    return args.command


class AgentRoute:
    """The route's state: each machine's token digest, and what to run."""

    def __init__(
        self,
        machines: "dict[str, str]",
        script: Path,
        config_path: "Path | None",
        db_path: Path,
        build_parser: "Callable[..., argparse.ArgumentParser] | None",
        run: Runner = subprocess.run,
    ) -> None:
        self.tokens = ingest.Tokens(machines)
        self.script = script
        self.config_path = config_path
        self.db_path = db_path
        self.build_parser = build_parser
        self.run = run

    @property
    def enabled(self) -> bool:
        return self.tokens.enabled and self.build_parser is not None

    def handle(self, headers: Message, rfile: BufferedIOBase) -> Result:
        """(status, JSON body, reason to log or None)."""
        machine = self.tokens.machine_for_header(headers)
        if machine is None:
            return 401, {"error": "unauthorized"}, "missing or unknown agent token"
        bad = ingest.length_problem(headers, MAX_BODY)
        if bad is not None:
            return bad
        length = int(headers["Content-Length"])
        try:
            doc = json.loads(rfile.read(length))
        except ValueError:
            return 400, {"error": "body is not JSON"}, None
        if not isinstance(doc, dict):
            return 400, {"error": "body must be a JSON object"}, None
        stdin = doc.get("stdin")
        if stdin is not None and not isinstance(stdin, str):
            return 400, {"error": "stdin must be a string"}, None
        assert self.build_parser is not None  # enabled says so
        try:
            command = check_argv(doc.get("argv"), self.build_parser)
        except ArgsRefused as exc:
            return 400, {"error": f"refused: {exc}"}, f"{machine}: refused: {exc}"
        env = dict(os.environ)
        env["TASK_DASHBOARD_DB"] = str(self.db_path)
        if self.config_path is not None:
            env["TASK_DASHBOARD_CONFIG"] = str(self.config_path)
        env[NO_REMOTE_ENV] = "1"
        try:
            done = self.run(
                [sys.executable, str(self.script), *doc["argv"]],
                input=stdin or "",
                capture_output=True,
                text=True,
                env=env,
                timeout=RUN_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            why = f"{machine}: {command} ran past {RUN_TIMEOUT:.0f}s"
            return 504, {"error": f"{command} timed out"}, why
        except OSError as exc:
            return 503, {"error": "could not run the command"}, f"{machine}: {exc}"
        print(
            f"task-dashboard: agent {machine}: {command} exit {done.returncode}",
            file=sys.stderr,
        )
        body = {"exit": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
        return 200, body, None


# --- client ------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

Poster = Callable[[str, str, bytes], tuple["int | None", str]]


def post(url: str, token: str, body: bytes) -> tuple["int | None", str]:
    """(status or None when nothing answered, the response body or why not)."""
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with _OPENER.open(req, timeout=CLIENT_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        with exc:
            detail = exc.read(4096).decode("utf-8", "replace") if exc.fp else ""
        return exc.code, detail or str(exc.reason)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", exc)
        return None, f"no answer from {url}: {reason}"


def forward(
    remote: "dict[str, Any]",
    argv: "list[str]",
    stdin: "str | None",
    poster: "Poster | None" = None,
) -> int:
    """Run argv on the remote dashboard, print what it printed, return its exit."""
    body = json.dumps({"argv": argv, "stdin": stdin}).encode()
    status, text = (poster or post)(remote["url"], remote["token"], body)
    where = remote["url"]
    if status == 200:
        try:
            doc = json.loads(text)
            code, out, err = doc["exit"], doc["stdout"], doc["stderr"]
        except (ValueError, KeyError, TypeError):
            print(
                f"task-dashboard: {where} answered 200 with no result", file=sys.stderr
            )
            return 1
        sys.stdout.write(out)
        sys.stderr.write(err)
        return code if isinstance(code, int) else 1
    if status is None:
        print(f"task-dashboard: {text}", file=sys.stderr)
        return 1
    try:
        detail = json.loads(text).get("error", text)
    except (ValueError, AttributeError):
        detail = text.strip()
    hint = {
        401: "; the token in [remote] is not in the server's [agent.machines]"
        " (on the server: dashboard.py agent-token add <host>)",
        404: "; the server has no /agent route: set [agent.machines] there and"
        " restart it, or update its plugin",
        403: "; the server refused the Host: add it to server.extra_allowed_hosts",
    }.get(status, "")
    print(f"task-dashboard: {where} answered {status}: {detail}{hint}", file=sys.stderr)
    return 2 if status == 400 else 1


def plan_file_stdin(args: argparse.Namespace) -> "tuple[list[str], str | None]":
    """Extra argv and stdin for `plan set`: the file's content goes as stdin.

    argparse keeps the last value of a flag, so appending `--file -` overrides
    whatever path the caller gave without rewriting their argv.
    """
    if args.command != "plan" or getattr(args, "plan_command", None) != "set":
        return [], None
    if args.file == "-":
        return [], sys.stdin.read()
    return ["--file", "-"], Path(args.file).read_text()
