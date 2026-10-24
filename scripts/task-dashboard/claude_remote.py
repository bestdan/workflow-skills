"""The cloud session list, as an optional inventory source for the fleet view.

OFF BY DEFAULT. It runs only with `[claude_remote] enabled = true`. Session
state comes from the session-state hook (sessions.py); this source adds the
sessions the hook cannot see, cloud-only ones above all, with a coarse status.
The route depends on a request Claude Code marks `@internal`: it can change or
disappear in any CLI release, and using it is a deliberate choice.

THE ROUTE, from dev_docs/research/2026-10-23-claude-remote-session-read.md
(route 5): start `claude -p --input-format stream-json --output-format
stream-json --verbose`, write an `initialize` control request and then
`list_cloud_sessions` on its stdin, and read the `control_response` whose
`request_id` is the second one's. The CLI reads with its own sign-in, so this
module holds no credential. The child inherits this process's environment
unchanged: in particular `CLAUDE_CODE_CHILD_SESSION`, which makes the CLI
refuse with `started_by_session` when the caller runs inside a Claude Code
session. That refusal is the CLI's guard and is reported, never worked around.
A `launchd` job does not carry the variable.

WHAT ONE READ IS. A single page of at most 200 sessions with a `truncated`
flag and no cursor, covering about 20 days on the owner's account, and not
ordered by `updated_at`. So every read is a full snapshot: there is no
"updated since the last run" and no paging back further.

RECONCILE. Each run upserts the page's sessions and drops the ones the page
reports `archived`. A session missing from the page is NOT archived: it may
have fallen off a truncated page. Its row stays, with `synced_at` left at the
last run that saw it, and is dropped only once no run has seen it for
RETAIN_DAYS.

THE ROW, under `source = 'claude-remote'`, `kind = 'session'`, keyed by the
session id. `summary` is the title (text from the service, which can be
private; the mirror is local to the dashboard host). `status_name` holds the
bucket and `updated` the service's `updated_at`. `raw_json` adds:

    id, title, status (as sent, or null), environment_kind, repository
    ("owner/name"), branch, machine (a label from environment_kind)
    status_bucket     working (running, pending) | waiting (requires_action)
                      | idle (idle, or a status this module does not know,
                      which is how the CLI documents reading one) | failed
    status_category   "need_input" for requires_action, else null
    needs_action      true for requires_action
    has_prompt, empty_spawn, brief, created   always null: not on this route

Run `python claude_remote.py list` (Python 3.11+, from a shell outside Claude
Code) to make one read and print its counts, with no titles.
"""

import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

SOURCE = "claude-remote"
KIND = "session"
RUN_KIND = "claude_remote"
RETAIN_DAYS = 30
DEFAULT_TIMEOUT = 45.0  # the CLI's own bound is 25 s, plus its startup

ARGV = (
    "-p", "--input-format", "stream-json", "--output-format", "stream-json",
    "--verbose",
)  # fmt: skip
LIST_ID = "list"
REQUESTS = (
    {"type": "control_request", "request_id": "init",
     "request": {"subtype": "initialize"}},
    {"type": "control_request", "request_id": LIST_ID,
     "request": {"subtype": "list_cloud_sessions"}},
)  # fmt: skip

# Reasons the CLI gives that someone fixes by signing in again.
AUTH_REASONS = ("not_signed_in", "sign_in_expired")
HINTS = {
    "started_by_session": "this process runs inside a Claude Code session; run"
    " the sync from launchd or a shell outside Claude Code",
}

BUCKETS = {
    "running": "working",
    "pending": "working",
    "requires_action": "waiting",
    "idle": "idle",
    "failed": "failed",
}
MACHINES = {
    "bridge": "remote-control",
    "anthropic_cloud": "cloud",
    "byoc": "byoc",
    "snap": "snap",
}

# (argv, stdin text, timeout) -> (returncode, stdout, stderr)
Runner = Callable[[list[str], str, float], "tuple[int, str, str]"]


class Unavailable(RuntimeError):
    """The read returned no session list; `reason` says why."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class AuthError(Unavailable):
    """The CLI is not signed in, or its sign-in expired."""


def run(argv: list[str], stdin: str, timeout: float) -> "tuple[int, str, str]":
    """The real runner. The environment is inherited, unchanged."""
    try:
        proc = subprocess.run(
            argv, input=stdin, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise Unavailable("timeout", f"no reply within {timeout:g}s") from None
    except OSError as exc:
        raise Unavailable("not_runnable", str(exc)) from None
    return proc.returncode, proc.stdout, proc.stderr


def _reply(stdout: str) -> "dict[str, Any] | None":
    """The control_response to the list request, or None."""
    for line in stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or msg.get("type") != "control_response":
            continue
        response = msg.get("response")
        if isinstance(response, dict) and response.get("request_id") == LIST_ID:
            return response
    return None


def list_sessions(
    claude: str = "claude", runner: Runner = run, timeout: float = DEFAULT_TIMEOUT
) -> "tuple[list[dict[str, Any]], bool]":
    """(sessions as the CLI sent them, truncated). Raises Unavailable/AuthError."""
    stdin = "".join(json.dumps(r) + "\n" for r in REQUESTS)
    rc, out, err = runner([claude, *ARGV], stdin, timeout)
    response = _reply(out)
    if response is None:
        tail = (err.strip().splitlines() or [""])[-1][:200]
        raise Unavailable("no_reply", f"exit {rc}; {tail}".rstrip("; "))
    if response.get("subtype") != "success":
        raise Unavailable("error", str(response.get("error") or "")[:200])
    payload = response.get("response")
    if not isinstance(payload, dict):
        raise Unavailable("malformed", "no response payload")
    if payload.get("status") != "ok":
        reason = str(payload.get("reason") or "failed")
        if reason in AUTH_REASONS:
            raise AuthError(reason)
        raise Unavailable(reason, HINTS.get(reason, ""))
    sessions = payload.get("sessions")
    truncated = payload.get("truncated")
    if not isinstance(sessions, list) or not isinstance(truncated, bool):
        raise Unavailable("malformed", "no sessions list or truncated flag")
    return [s for s in sessions if isinstance(s, dict)], truncated


def to_row(session: dict[str, Any]) -> "dict[str, Any] | None":
    """One session as a mirror row; None when it has no usable id."""
    sid = session.get("id")
    if not isinstance(sid, str) or not sid:
        return None
    status = session.get("status")
    status = status if isinstance(status, str) else None
    repo = session.get("repository")
    repository = None
    if isinstance(repo, dict) and repo.get("owner") and repo.get("name"):
        repository = f"{repo['owner']}/{repo['name']}"
    env = session.get("environment_kind")
    env = env if isinstance(env, str) else None
    waiting = status == "requires_action"
    title = session.get("title")
    return {
        "kind": KIND,
        "key": sid,
        "id": sid,
        "title": title if isinstance(title, str) and title else None,
        "status": status,
        "archived": status == "archived",
        "status_bucket": BUCKETS.get(status or "", "idle"),
        "status_category": "need_input" if waiting else None,
        "needs_action": waiting,
        "environment_kind": env,
        "machine": MACHINES.get(env or ""),
        "repository": repository,
        "branch": session.get("branch") if isinstance(session.get("branch"), str) else None,
        "updated": session.get("updated_at") if isinstance(session.get("updated_at"), str) else None,
        "created": None,
        "has_prompt": None,
        "empty_spawn": None,
        "brief": None,
    }  # fmt: skip


def fetch(config: dict[str, Any], runner: Runner = run) -> dict[str, Any]:
    """One read as mirror rows. Reads the CLI; writes nothing."""
    cr = config["claude_remote"]
    sessions, truncated = list_sessions(
        cr["claude_path"], runner, float(cr["timeout_seconds"])
    )
    rows = [r for r in (to_row(s) for s in sessions) if r is not None]
    statuses = Counter(r["status"] or "unknown" for r in rows)
    return {
        "rows": rows,
        "truncated": truncated,
        "counts": {
            "page": len(rows),
            "truncated": int(truncated),
            "archived": statuses.get("archived", 0),
            "requires_action": statuses.get("requires_action", 0),
        },
    }


def write(
    conn: sqlite3.Connection, rows: list[dict[str, Any]], now: datetime
) -> dict[str, int]:
    """Upsert the page, drop what it says is archived, and age out the unseen.

    Returns how many rows were dropped for each reason. One transaction.
    """
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = (now - timedelta(days=RETAIN_DAYS)).astimezone(timezone.utc)
    archived = [r["key"] for r in rows if r["archived"]]
    live = [r for r in rows if not r["archived"]]
    conn.execute("BEGIN IMMEDIATE")
    try:
        dropped = 0
        for key in archived:
            dropped += conn.execute(
                "DELETE FROM issues WHERE source = ? AND key = ?", (SOURCE, key)
            ).rowcount
        conn.executemany(
            "INSERT INTO issues (source, kind, key, summary, status_name,"
            " status_category, updated, raw_json, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(source, key) DO UPDATE SET kind = excluded.kind,"
            " summary = excluded.summary, status_name = excluded.status_name,"
            " status_category = excluded.status_category,"
            " updated = excluded.updated, raw_json = excluded.raw_json,"
            " synced_at = excluded.synced_at",
            [
                (
                    SOURCE, KIND, r["key"], r["title"], r["status_bucket"],
                    r["status_category"], r["updated"],
                    json.dumps(r, sort_keys=True), stamp,
                )
                for r in live
            ],
        )  # fmt: skip
        unseen = conn.execute(
            "DELETE FROM issues WHERE source = ? AND synced_at < ?",
            (SOURCE, cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).rowcount
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"dropped_archived": dropped, "dropped_unseen": unseen}


def main(argv: list[str]) -> int:
    if argv[:1] != ["list"] or len(argv) > 2:
        print("usage: claude_remote.py list [<claude path>]", file=sys.stderr)
        return 2
    claude = argv[1] if len(argv) == 2 else "claude"
    try:
        sessions, truncated = list_sessions(claude)
    except Unavailable as exc:
        print(f"unavailable: {exc}", file=sys.stderr)
        return 3 if isinstance(exc, AuthError) else 1
    rows = [r for r in (to_row(s) for s in sessions) if r is not None]
    updated = sorted(r["updated"] for r in rows if r["updated"])
    print(f"sessions: {len(rows)}; truncated: {str(truncated).lower()}")
    if updated:
        print(f"updated_at span: {updated[0]} .. {updated[-1]}")
    for name, counter in (
        ("status", Counter(r["status"] or "unknown" for r in rows)),
        ("bucket", Counter(r["status_bucket"] for r in rows)),
        ("environment_kind", Counter(r["environment_kind"] or "unknown" for r in rows)),
    ):
        print(f"{name}: " + ", ".join(f"{k} {v}" for k, v in counter.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
