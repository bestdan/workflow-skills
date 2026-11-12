#!/usr/bin/env python3
"""Let scripts/check.sh skip a run on a tree that already passed the full gate.

After a green full run, check.sh stores a fingerprint of the tree and of the
outside inputs listed below. The next run computes the fingerprint again, and
when it matches, check.sh reports OK without running. Tools outside that list,
such as uv, are not fingerprinted.

The fingerprint covers:

- **The working tree.** `git write-tree` over an empty temporary index after
  `git add -A`, so it holds tracked edits and untracked, non-ignored
  files, staged or not, and leaves the real index alone. Pins that live in
  tracked files (mise.toml, the mypy pin in typecheck.sh) come with it.
- **What the gate reads from outside the tree** (ENVIRONMENT below): the
  installed Claude Code that runs `claude plugin validate`, the agent-guidance
  plugin copy that dev-docs-layout.sh checks against, the Python on PATH, and
  whether the run is inside Claude Code's sandbox, where some suites skip.

A green full run satisfies any later non-eval run, since `--fast` and `--base`
only ever drop checks from it. Only a full run stores the fingerprint.

Usage:
  python3 scripts/check-cache.py key
  python3 scripts/check-cache.py lookup <record> <key>
  python3 scripts/check-cache.py store <record> <key>

`key` prints the current fingerprint. `lookup` exits 0 and prints the OK line
when <record> holds <key>, and 1 otherwise. `store` writes <key> to <record>,
but only when the tree still has that fingerprint: a file edited while the gate
ran was not what the gate checked. Exits 2 on unusable input.
"""

import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Bump when the fingerprint's contents change, so an old record cannot match.
FORMAT = "check-cache-v2"


def _run(args: List[str], env: Optional[dict] = None) -> Optional[str]:
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, env=env, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.strip()


def tree_hash() -> Optional[str]:
    """The tree of the working directory as `git add -A` would stage it.

    The temporary index starts empty, so every file is hashed. Starting from a
    copy of the real index would let git trust its cached stat data, and a copy
    has a newer mtime than the original, which defeats git's racy-entry check:
    a file rewritten at the same size within a second of the last index write
    would keep its old hash. About 0.5s on this repo.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        if _run(["git", "add", "-A"], env=env) is None:
            return None
        return _run(["git", "write-tree"], env=env)


def _agent_guidance() -> str:
    here = Path(__file__).resolve().parent
    return _run([str(here / "agent-guidance-dir.sh")]) or "absent"


ENVIRONMENT: List[Tuple[str, Callable[[], str]]] = [
    ("claude", lambda: _run(["claude", "--version"]) or "absent"),
    ("agent-guidance", _agent_guidance),
    ("python3", lambda: _run(["python3", "--version"]) or "absent"),
    ("sandboxed", lambda: "yes" if os.environ.get("SANDBOX_RUNTIME") else "no"),
]


def key() -> Optional[str]:
    tree = tree_hash()
    if tree is None:
        return None
    parts = [FORMAT, f"tree={tree}"]
    parts += [f"{name}={probe()}" for name, probe in ENVIRONMENT]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def lookup(record: Path, want: str) -> bool:
    try:
        stored = json.loads(record.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(stored, dict) or stored.get("key") != want:
        return False
    print(
        f"check.sh: OK (tree unchanged since the last green full run at "
        f"{stored.get('time', 'an unknown time')})"
    )
    print("  pass --no-cache, or set CHECK_NO_CACHE=1, to run it anyway")
    return True


def store(record: Path, want: str) -> bool:
    now = key()
    if now != want:
        print(
            "check.sh: tree changed while the gate ran; not caching this run",
            file=sys.stderr,
        )
        return False
    when = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    tmp = record.with_name(record.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"key": want, "time": when}) + "\n")
        tmp.replace(record)
    except OSError as e:
        print(f"check.sh: could not record the green run: {e}", file=sys.stderr)
        return False
    return True


def main(argv: List[str]) -> int:
    if argv == ["key"]:
        k = key()
        if k is None:
            print("check-cache: cannot fingerprint the tree", file=sys.stderr)
            return 2
        print(k)
        return 0
    if len(argv) == 3 and argv[0] in ("lookup", "store"):
        record, want = Path(argv[1]), argv[2]
        if argv[0] == "lookup":
            return 0 if lookup(record, want) else 1
        return 0 if store(record, want) else 1
    print(__doc__.split("Usage:")[1].split("`key`")[0].rstrip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
