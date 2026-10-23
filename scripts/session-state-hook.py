#!/usr/bin/env python3
"""Session-state hook: one JSON line per prompt, turn end and notification.

Registered in hooks/hooks.json on ``UserPromptSubmit``, ``Stop`` and
``Notification``. It feeds the fleet view's collector, which reads the files
this writes; the design is ``dev_docs/designs/2026-10-23-fleet-view.md``
("Sources").

OPT-IN, AND OFF BY DEFAULT. The plugin is public and installed by people who
never asked to have their sessions logged, so the hook does nothing unless
``~/.config/task-dashboard/config.toml`` (or ``$TASK_DASHBOARD_CONFIG``, the
same override the dashboard reads) has::

    [collector]
    enabled = true

Only that form opts in: a literal ``[collector]`` table header, then an
``enabled = true`` line before the next header. The file is line-scanned, not
parsed as TOML, so a dotted key (``collector.enabled = true``), an inline
table (``collector = {enabled = true}``) and a quoted key all read as off.

Anything else — no file, no table, ``false``, a value that is not exactly
``true``, an unreadable file — is "not opted in": exit 0 having written
nothing. The file-existence test comes first, so a machine that never opted in
pays one ``stat`` and no parse. It still reads up to 64 MiB of the payload
before it exits, so the harness never writes a prompt into a closed pipe.

NEVER FAILS THE TURN. Every error is swallowed and the exit code is always 0.
The hook runs on every prompt, so it imports only the standard library, spawns
no subprocess (the branch is read from ``.git/HEAD`` directly) and does one
append. Measured on macOS, median of 21 runs, opted in: 15ms under python
3.12 and 22ms under the system 3.9; the not-opted-in path is a few ms less.

RECORDS NO PROMPT TEXT, EVER. ``UserPromptSubmit`` carries the prompt and
``Notification`` carries a message; neither is written. Each line is built from
an allowlist of fields below, never by copying the payload.

LINE FORMAT — read by the collector, so change it only with a new ``v``.
Appended to ``~/.local/state/fleet/sessions/<session_id>.jsonl``, one JSON
object per line, keys:

    v             1 — the format version.
    ts            UTC ISO 8601 with milliseconds and a ``Z`` suffix, taken when
                  the hook runs: "2026-10-23T14:05:09.123Z".
    event         "UserPromptSubmit" | "Stop" | "Notification".
    session_id    The harness's session id. A line is written only when it is a
                  plain file-name token ([A-Za-z0-9._-], not starting with "."),
                  so it is also the file's stem.
    cwd           The session's working directory, or null if absent.
    branch        The git branch checked out at ``cwd``; null when ``cwd`` is
                  not in a repository or HEAD is detached.
    notification  Notification lines only: "permission_prompt" (a harness
                  permission prompt), "idle_prompt" (Claude is waiting for
                  input), or another identifier the harness sends as
                  ``notification_type``; "other" when it cannot be classified.

An agent waiting on the owner runs from a ``Stop`` or ``Notification`` line to
the next ``UserPromptSubmit``; the owner waiting on an agent runs from a
``UserPromptSubmit`` to the next ``Stop``.
"""

import os
import sys

CONFIG_ENV = "TASK_DASHBOARD_CONFIG"
DEFAULT_CONFIG = "~/.config/task-dashboard/config.toml"
STATE_DIR = "~/.local/state/fleet/sessions"
EVENTS = ("UserPromptSubmit", "Stop", "Notification")
FORMAT_VERSION = 1
# The most a not-opted-in run reads before giving up; past it the harness is
# writing something no prompt payload resembles.
DRAIN_LIMIT = 64 * 1024 * 1024


def _config_path():
    return os.path.expanduser(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)


def collector_enabled(text):
    """True only for ``enabled = true`` inside the ``[collector]`` table.

    A line scan rather than ``tomllib``: this runs under the consumer's bare
    ``python3``, which on macOS is 3.9, and tomllib is 3.11+. One parser keeps
    the answer the same on every interpreter. It reads only what it needs:
    table headers and one key.
    """
    in_collector = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            in_collector = line == "[collector]"
            continue
        if not in_collector:
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip() == "enabled":
            return value.strip() == "true"
    return False


def git_branch(cwd):
    """The branch at ``cwd`` from ``.git/HEAD``, or None.

    Walks up to the nearest ``.git``. A linked worktree's (or submodule's)
    ``.git`` is a file naming the real git dir; follow it once.
    """
    d = os.path.abspath(cwd)
    while True:
        dot_git = os.path.join(d, ".git")
        if os.path.isdir(dot_git):
            git_dir = dot_git
            break
        if os.path.isfile(dot_git):
            with open(dot_git, encoding="utf-8") as fh:
                first = fh.readline().strip()
            if not first.startswith("gitdir:"):
                return None
            git_dir = first[len("gitdir:") :].strip()
            if not os.path.isabs(git_dir):
                git_dir = os.path.join(d, git_dir)
            break
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as fh:
        head = fh.readline().strip()
    prefix = "ref: refs/heads/"
    return head[len(prefix) :] if head.startswith(prefix) else None


def _is_token(value):
    if not isinstance(value, str) or not value or value.startswith("."):
        return False
    return all((c.isalnum() and c.isascii()) or c in "._-" for c in value)


def notification_class(payload):
    """``notification_type`` when the harness sends one, else a guess from the
    message. The message is read here and never written."""
    kind = payload.get("notification_type")
    if _is_token(kind):
        return kind
    message = payload.get("message")
    if isinstance(message, str):
        lowered = message.lower()
        if "permission" in lowered:
            return "permission_prompt"
        if "waiting for your input" in lowered:
            return "idle_prompt"
    return "other"


def build_line(payload, now):
    """The record for one hook payload, or None when it should not be written."""
    import json

    event = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    if event not in EVENTS or not _is_token(session_id):
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str):
        cwd = None
    branch = None
    if cwd:
        try:
            branch = git_branch(cwd)
        except (OSError, UnicodeError, ValueError):
            branch = None
    record = {
        "v": FORMAT_VERSION,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000),
        "event": event,
        "session_id": session_id,
        "cwd": cwd,
        "branch": branch,
    }
    if event == "Notification":
        record["notification"] = notification_class(payload)
    return json.dumps(record, separators=(",", ":")) + "\n"


def append(session_id, line):
    state_dir = os.path.expanduser(STATE_DIR)
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    path = os.path.join(state_dir, session_id + ".jsonl")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        # One write of one short line: an O_APPEND write is not interleaved
        # with a concurrent one, so two hooks for one session cannot tear a line.
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def opted_in():
    config = _config_path()
    if not os.path.isfile(config):
        return False
    with open(config, encoding="utf-8") as fh:
        return collector_enabled(fh.read())


def drain_stdin(limit=DRAIN_LIMIT):
    """Read stdin to EOF, or ``limit`` bytes, and discard it. Never raises."""
    try:
        stream = sys.stdin.buffer
        if stream.isatty():
            return
        while limit > 0:
            chunk = stream.read(min(65536, limit))
            if not chunk:
                return
            limit -= len(chunk)
    except (AttributeError, OSError, ValueError):
        pass


def main():
    try:
        enabled = opted_in()
    except (OSError, UnicodeError, ValueError):
        enabled = False
    if not enabled:
        drain_stdin()
        return

    import json
    from datetime import datetime, timezone

    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        return
    line = build_line(payload, datetime.now(timezone.utc))
    if line is not None:
        append(payload["session_id"], line)


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # never fail the user's turn, whatever went wrong
        pass
    sys.exit(0)
