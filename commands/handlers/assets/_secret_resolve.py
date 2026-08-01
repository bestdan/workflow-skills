#!/usr/bin/env python3
"""Generic auth-key resolution helper — the shared secret/pointer/resolver
contract described in dev_docs/auth_key_access.md. Every credential the
framework touches (the Linear key today, others later) resolves through this
module rather than each consumer hard-coding `op read`.

Two independent ladders, first hit wins on each:

  secret/pointer:  $<NAME> -> $<NAME>_REF -> unavailable
  resolver:        $<NAME>_RESOLVER -> "op" (default)

A failed resolve never falls through to the next rung — a misconfigured ref
or resolver is a distinct failure, not silent absence. `unconfigured` (nothing
named anywhere) is likewise its own category, separate from `not-found` (a ref
that names something which does not exist): callers report the second and stay
quiet about the first. The `.task-config*.yml`
rungs described in the design are bridged into `$<NAME>_REF` /
`$<NAME>_RESOLVER` by the calling agent; this module only ever reads env.

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

REF_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")

RESOLVERS = {
    "op": lambda ref: ["op", "read", ref],
    "opx": lambda ref: ["opx", ref],
}

TIMEOUT = 120


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


def resolve_key(name="LINEAR_API_KEY"):
    key = os.environ.get(name)
    if key:
        return key.strip()
    ref = os.environ.get(f"{name}_REF")
    if not ref:
        raise SecretUnavailable(
            "unconfigured", f"No {name} secret or reference configured."
        )
    _validate_ref(ref)
    resolver = os.environ.get(f"{name}_RESOLVER") or "op"
    return _run_resolver(resolver, ref)


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
