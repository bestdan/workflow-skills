#!/usr/bin/env python3
"""Stub PreToolUse hook for the #1001 probe.

Denies every EnterWorktree call that carries `path`, with a fixed reason string
the probe greps for. Gives no decision on anything else. Every input it sees is
appended to a log next to this file, so the transcript can be checked against
what the hook was actually handed.
"""

import json
import os
import sys

REASON = (
    "PROBE-1001-STUB-DENY: this worktree has a name route. "
    "Call ExitWorktree with action keep, then EnterWorktree with name: probe."
)

raw = sys.stdin.read()
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stub-hook-calls.jsonl")
with open(log, "a") as fh:
    fh.write(raw.strip() + "\n")

try:
    payload = json.loads(raw)
except ValueError:
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
if payload.get("tool_name") == "EnterWorktree" and tool_input.get("path"):
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON,
            }
        },
        sys.stdout,
    )
sys.exit(0)
