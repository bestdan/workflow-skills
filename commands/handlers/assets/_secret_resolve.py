#!/usr/bin/env python3
"""Generic auth-key resolution helper — the shared secret/pointer/resolver
contract described in dev_docs/auth_key_access.md. Every credential the
framework touches (the Linear key today, others later) resolves through this
module rather than each consumer hard-coding `op read`.

Two independent ladders, first hit wins on each:

  secret/pointer:  $<NAME> -> $<NAME>_REF -> <service>.<credential>_ref
                   -> unavailable
  resolver:        $<NAME>_RESOLVER -> <service>.<credential>_resolver
                   -> "op" (default)

A failed resolve never falls through to the next rung — a misconfigured ref
or resolver is a distinct failure, not silent absence. `unconfigured` (nothing
named anywhere) is likewise its own category, separate from `not-found` (a ref
that names something which does not exist): callers report the second and stay
quiet about the first. The `unconfigured` message lists every source checked.

The two config rungs are read here, so a ref configured in the repo reaches
every asset whether or not the calling agent bridged it (#1426).
`<service>.<credential>` comes from the name: LINEAR_API_KEY -> linear.api_key.

  pointer:   the merged view of dev_docs/tasks/.task-config.yml overlaid
             with .task-config.local.yml (local wins)
  resolver:  .task-config.local.yml only; one in the committed file is
             reported on stderr and ignored, so a checkout cannot choose the
             program that runs

The local override is gitignored, so it exists only in the main checkout; a
linked worktree finds it there through --git-common-dir. The raw-secret rung
(<service>.<credential> in the local file) stays with the agent's bridge.

Resolver allow-list, extended only in code:
  op  -> ["op", "read", ref]
  opx -> ["opx", ref]
Anything else is a distinct `unknown-resolver` failure, never a silent
fallback to `op`.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REF_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")

RESOLVERS = {
    "op": lambda ref: ["op", "read", ref],
    "opx": lambda ref: ["opx", ref],
}

TIMEOUT = 120

CONFIG_DIR = Path("dev_docs") / "tasks"
COMMITTED_CONFIG = ".task-config.yml"
LOCAL_CONFIG = ".task-config.local.yml"


class SecretUnavailable(Exception):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def redact(ref):
    if isinstance(ref, str) and REF_RE.match(ref):
        scheme, _, rest = ref.partition("://")
        if scheme == "op":
            vault = rest.split("/", 1)[0]
            return f"op://{vault}/…"
    return "configured secret reference"


def _validate_ref(ref):
    if ref != ref.strip() or "\n" in ref or not REF_RE.match(ref):
        raise SecretUnavailable(
            "malformed-ref", f"Malformed secret reference ({redact(ref)})."
        )


def _classify_stderr(stderr):
    lowered = (stderr or "").lower()
    if "not signed in" in lowered or ("session" in lowered and "expired" in lowered):
        return "no-session"
    if (
        "isn't an item" in lowered
        or "is not an item" in lowered
        or "not found" in lowered
    ):
        return "not-found"
    return "denied"


def _run_resolver(resolver, ref):
    build_argv = RESOLVERS.get(resolver)
    if build_argv is None:
        raise SecretUnavailable(
            "unknown-resolver", f"Unknown resolver {resolver!r} ({redact(ref)})."
        )
    argv = build_argv(ref)
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise SecretUnavailable(
            "no-binary", f"Resolver binary not found for {resolver!r} ({redact(ref)})."
        )
    except subprocess.TimeoutExpired:
        raise SecretUnavailable("timeout", f"Timed out resolving {redact(ref)}.")
    if out.returncode != 0:
        category = _classify_stderr(out.stderr)
        raise SecretUnavailable(
            category, f"Could not resolve {redact(ref)} ({category})."
        )
    key = out.stdout.strip()
    if not key:
        raise SecretUnavailable(
            "empty", f"Resolver returned no value for {redact(ref)}."
        )
    return key


def _git(cwd, *args):
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def config_paths(cwd=None):
    """(committed, local) config paths for the checkout at `cwd`, or None
    outside a git checkout. The committed file is this checkout's own copy.
    The local override is this checkout's when it has one, else the main
    working tree's: it is gitignored, so a linked worktree never holds it."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    committed = Path(top) / CONFIG_DIR / COMMITTED_CONFIG
    local = Path(top) / CONFIG_DIR / LOCAL_CONFIG
    if not local.is_file():
        common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common is not None and Path(common).name == ".git":
            main_local = Path(common).parent / CONFIG_DIR / LOCAL_CONFIG
            if main_local.is_file():
                local = main_local
    return committed, local


def _scalar(raw):
    raw = raw.strip()
    quoted = re.match(r"(['\"])(.*?)\1\s*(#.*)?$", raw)
    if quoted:
        return quoted.group(2)
    value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    return None if value in ("", "~", "null") else value


def read_leaf(text, section, key):
    """The scalar at `section.key` in a YAML mapping, or None. Assets are
    stdlib-only, so this reads just the shape the task config uses: a
    top-level `section:` line, then `key: value` at the block's own indent."""
    lines = text.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(rf"{re.escape(section)}:\s*(#.*)?$", line)
        ),
        None,
    )
    if start is None:
        return None
    indent = None
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        width = len(line) - len(line.lstrip())
        if width == 0:
            return None
        indent = width if indent is None else indent
        m = re.match(rf"\s+{re.escape(key)}:(\s+.*)?$", line)
        if width == indent and m:
            return _scalar(m.group(1) or "")
    return None


def _read_config(path, section, key):
    """(value or None, what was found at `path`, for the unconfigured message)."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        return None, f"unreadable: {exc.strerror}"
    value = read_leaf(text, section, key)
    return value, ("set" if value else "not set")


def _config_name(name):
    """LINEAR_API_KEY -> ("linear", "api_key"); None for a name with no
    service prefix, which has no config rung."""
    service, _, credential = name.lower().partition("_")
    return (service, credential) if service and credential else None


def resolve_key(name="LINEAR_API_KEY", cwd=None):
    key = os.environ.get(name)
    if key:
        return key.strip()
    ref = os.environ.get(f"{name}_REF")
    config = _config_name(name)
    paths = config_paths(cwd) if config else None
    checked = [f"${name}", f"${name}_REF"]
    if not ref and config:
        section, credential = config
        leaf = f"{section}.{credential}_ref"
        if paths is None:
            checked.append(f"{leaf} (not in a git checkout)")
        else:
            found = {}
            for path in paths:
                found[path], note = _read_config(path, section, f"{credential}_ref")
                checked.append(f"{leaf} in {path} ({note})")
            ref = found[paths[1]] or found[paths[0]]
    if not ref:
        raise SecretUnavailable(
            "unconfigured",
            f"No {name} secret or reference configured; checked {', '.join(checked)}.",
        )
    _validate_ref(ref)
    resolver = os.environ.get(f"{name}_RESOLVER")
    if not resolver and config and paths is not None:
        section, credential = config
        committed, local = paths
        resolver, _ = _read_config(local, section, f"{credential}_resolver")
        if _read_config(committed, section, f"{credential}_resolver")[0]:
            print(
                f"{section}.{credential}_resolver belongs in {LOCAL_CONFIG}, not"
                " the committed config — ignoring it",
                file=sys.stderr,
            )
    return _run_resolver(resolver or "op", ref)


def _probe(name):
    try:
        resolve_key(name)
    except SecretUnavailable as e:
        print(e.category, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        sys.exit(_probe(sys.argv[2]))
    sys.exit("usage: _secret_resolve.py --probe <NAME>")
