#!/usr/bin/env python3
"""grok-telemetry-gate.py — co-review's gate 1 for the `grok` reviewer.

Answers one question: is this machine's Grok Build configured so that a
dispatched review does NOT ship the reviewed diff to xAI's trace channel?

Exit 0 means pinned. Anything else means co-review must SKIP grok (noted in
the run summary, never fatal). See skills/co-review/reviewers/grok.md for why
the gate is a skip rather than a warning.

This replaces a `grep -c` one-liner that had two fail-open holes, both found
by review and both reproduced before this file existed:

  1. A substring match counted COMMENTED-OUT keys, so `# trace_upload = false`
     satisfied the gate.
  2. An anchored match still ignored which TOML table a key landed in, so
     `trace_upload = false` under the wrong table satisfied the gate while
     grok used its default (uploading) settings.

Tracking the enclosing table is what closes both. The env layer is checked
too, because it OUTRANKS the file (measured layer order: env > config >
remote), so a truthy GROK_TELEMETRY_* variable re-enables uploads over an
otherwise correct config.

WHY A HAND SCANNER RATHER THAN `tomllib`: this ships to users and is executed
as bare `python3`, which puts it at the repo's consumer floor of 3.9 (see
scripts/typecheck.sh). `tomllib` is 3.11+, and a `tomli` fallback would be a
new dependency. The scanner below understands exactly the shape this gate's
own setup instructions produce — `[table]` headers and `key = true|false` —
and treats everything else as absent, which fails CLOSED (grok is skipped).
That is the safe direction: an exotic spelling costs a reviewer, never a leak.

Usage:
  scripts/grok-telemetry-gate.py [<config-path>]

  <config-path>  defaults to $GROK_HOME/config.toml, else ~/.grok/config.toml

Exit status:
  0  pinned — safe to dispatch grok
  1  not pinned — a required key is missing, wrong, or in the wrong table, or
     an environment variable overrides the file. Reason on stdout.
  2  config file missing or unreadable, or usage error. Reason on stdout.
"""

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# (table, key, required value). The table matters: a key in the wrong table is
# inert, and an earlier revision of this gate could not tell the difference.
REQUIRED: List[Tuple[str, str, bool]] = [
    ("features", "telemetry", False),
    ("telemetry", "trace_upload", False),
    ("harness", "disable_codebase_upload", True),
]

# Env vars that override the file. Anything other than a falsey spelling counts
# as "on", because an unrecognised value is not something to guess about.
ENV_FALSEY = {"0", "false", "no", "off", ""}
ENV_VARS = ["GROK_TELEMETRY_ENABLED", "GROK_TELEMETRY_TRACE_UPLOAD"]

TABLE_RE = re.compile(r"^\[([^\[\]]+)\]$")
BOOL_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*=\s*(true|false)\s*(?:#.*)?$")


def default_config_path() -> str:
    home = os.environ.get("GROK_HOME")
    base = home if home else os.path.join(os.path.expanduser("~"), ".grok")
    return os.path.join(base, "config.toml")


def scan(text: str) -> Dict[Tuple[str, str], bool]:
    """Map (table, key) -> bool for every plain boolean assignment.

    Anything this does not recognise is simply absent from the result, which
    the caller treats as "not pinned". Bare keys before the first table header
    land under "" and so never satisfy a requirement.
    """
    found: Dict[Tuple[str, str], bool] = {}
    table = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = TABLE_RE.match(line)
        if header:
            table = header.group(1).strip()
            continue
        assignment = BOOL_RE.match(line)
        if assignment:
            found[(table, assignment.group(1))] = assignment.group(2) == "true"
    return found


def main(argv: List[str]) -> int:
    if len(argv) > 2:
        print("usage: grok-telemetry-gate.py [<config-path>]")
        return 2
    path = argv[1] if len(argv) == 2 else default_config_path()

    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        print("grok telemetry pin absent — no config at {}".format(path))
        return 2
    except OSError as exc:
        print("grok config unreadable at {}: {}".format(path, exc))
        return 2

    found = scan(text)
    problems: List[str] = []

    for table, key, want in REQUIRED:
        got: Optional[bool] = found.get((table, key))
        if got is None:
            problems.append("[{}] {} is missing".format(table, key))
        elif got is not want:
            problems.append(
                "[{}] {} is {}, must be {}".format(
                    table, key, str(got).lower(), str(want).lower()
                )
            )

    for var in ENV_VARS:
        value = os.environ.get(var)
        if value is not None and value.strip().lower() not in ENV_FALSEY:
            problems.append(
                "{}={!r} overrides the config (env beats config)".format(var, value)
            )

    if problems:
        print("grok telemetry pin incomplete — " + "; ".join(problems))
        print('see reviewers/grok.md "One-time setup"')
        return 1

    print("grok telemetry pin ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
