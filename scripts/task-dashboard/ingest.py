"""The fleet view's `POST /ingest`: authenticate a machine, take its snapshot.

The caller is a collector on another machine, not a browser, so it has its own
check. server.py still applies the Host allow-list first; the Origin and page
token checks do not apply. The route exists only when `[ingest.machines]` is
non-empty, so the work-laptop instance never has one.

AUTHENTICATION. Each machine has its own token. `dashboard.py ingest-token add
<host>` generates it and prints it once: the token goes in that machine's
collector config, and its SHA-256 goes in this server's config as
`[ingest.machines] <host> = "sha256:<hex>"`, so the config here never holds a
usable secret. The collector sends `Authorization: Bearer <token>`. The token's
digest is compared, in constant time, against every configured machine's, and
the matching machine must be the `host` the snapshot names.

ORDER OF CHECKS, each refusing with no write: the token (401), then the declared
body size (411 missing, 413 over MAX_BODY), all before the body is read, so an
unauthenticated caller cannot make the server read anything. Then JSON (400),
`schema` (400 unless 1, 2 or 3), that schema's full shape (`snapshot.validate`, 400),
the token's machine against the body's `host` (403), a `taken_at` more than
five minutes ahead of the server's clock (400), and staleness (409, a snapshot
not newer than the last one ingested from that host). A database error is 503,
with the cause logged to stderr and never sent.
"""

import hashlib
import hmac
import json
import sqlite3
import sys
from datetime import datetime
from email.message import Message
from io import BufferedIOBase
from pathlib import Path
from typing import Any, Callable, Mapping

import db
import snapshot
import snapshot_adapter

# A snapshot is a few hundred bytes per worktree plus the prompt-history delta
# since the last post; a laptop that slept for a week stays well under this.
MAX_BODY = 4 * 1024 * 1024

Result = tuple[int, dict[str, Any], "str | None"]


def digest(token: str) -> str:
    """The config form of a token."""
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


class Ingest:
    """The route's state: each machine's token digest, and where to write."""

    def __init__(
        self,
        machines: Mapping[str, str],
        db_path: Path,
        now: Callable[[], datetime],
    ) -> None:
        self.machines = {
            host: bytes.fromhex(d.removeprefix("sha256:"))
            for host, d in machines.items()
        }
        self.db_path = db_path
        self.now = now

    @property
    def enabled(self) -> bool:
        return bool(self.machines)

    def machine_for(self, token: str) -> "str | None":
        """The machine this token belongs to; every entry is compared."""
        presented = hashlib.sha256(token.encode()).digest()
        found = None
        for host, want in self.machines.items():
            if hmac.compare_digest(presented, want):
                found = host
        return found

    def handle(self, headers: Message, rfile: BufferedIOBase) -> Result:
        """(status, JSON body, reason to log or None). Writes only on 200."""
        scheme, _, token = (headers.get("Authorization") or "").strip().partition(" ")
        machine = (
            self.machine_for(token.strip()) if scheme.lower() == "bearer" else None
        )
        if machine is None:
            return 401, {"error": "unauthorized"}, "missing or unknown ingest token"
        if headers.get("Transfer-Encoding"):
            return 411, {"error": "Content-Length required"}, None
        raw_length = headers.get("Content-Length")
        if raw_length is None:
            return 411, {"error": "Content-Length required"}, None
        try:
            length = int(raw_length)
        except ValueError:
            length = -1
        if length < 0:
            return 400, {"error": "bad Content-Length"}, None
        if length > MAX_BODY:
            return (
                413,
                {"error": f"body over {MAX_BODY} bytes"},
                f"{machine}: {length} bytes",
            )
        try:
            doc = json.loads(rfile.read(length))
        except ValueError:
            return 400, {"error": "body is not JSON"}, None
        got = doc.get("schema") if isinstance(doc, dict) else None
        if isinstance(got, bool) or got not in snapshot.ACCEPTED_SCHEMAS:
            return 400, {"error": f"unsupported schema {got!r}"}, None
        problems = snapshot.validate(doc)
        if problems:
            return 400, {"error": f"snapshot does not match schema {doc['schema']}",
                         "problems": problems[:20]}, None  # fmt: skip
        if doc["host"] != machine:
            why = f"token for {machine} sent a snapshot for {doc['host']!r}"
            return 403, {"error": "token is not for this host"}, why
        # A locked or unmigratable DB is the server's problem, not the
        # caller's: 503 so the collector retries, with the cause logged here
        # rather than sent.
        try:
            conn = db.connect(self.db_path)
        except sqlite3.Error as exc:
            return 503, {"error": "database unavailable"}, f"{machine}: {exc}"
        try:
            written = snapshot_adapter.ingest(conn, doc, self.now())
        except snapshot_adapter.StaleSnapshot as exc:
            return 409, {"error": str(exc)}, None
        except snapshot_adapter.BadTakenAt as exc:
            return 400, {"error": f"bad taken_at: {exc}"}, None
        except ValueError as exc:
            # Valid JSON that SQLite still refuses, e.g. a lone surrogate
            # (UnicodeEncodeError). Resending it fails the same way, so 400.
            why = f"{machine}: snapshot could not be stored: {exc!r}"
            return 400, {"error": "snapshot could not be stored"}, why
        except sqlite3.Error as exc:
            return 503, {"error": "database unavailable"}, f"{machine}: {exc}"
        finally:
            conn.close()
        print(f"task-dashboard: ingested {machine} {doc['taken_at']}", file=sys.stderr)
        return 200, written, None
