#!/usr/bin/env python3
"""Print the tool calls, tool results and text of a stream-json transcript."""

import json
import sys

for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    ev = json.loads(line)
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        print(
            "INIT",
            json.dumps(
                {
                    k: ev.get(k)
                    for k in ("cwd", "permissionMode", "claude_code_version", "model")
                }
            ),
        )
        continue
    if t == "result":
        print(
            "RESULT",
            json.dumps(
                {
                    k: ev.get(k)
                    for k in (
                        "subtype",
                        "is_error",
                        "num_turns",
                        "result",
                        "permission_denials",
                    )
                },
                indent=1,
            ),
        )
        continue
    if t not in ("assistant", "user"):
        if t == "system":
            print("SYSTEM", json.dumps(ev)[:600])
        continue
    for c in ev.get("message", {}).get("content", []) or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "tool_use":
            print("TOOL_USE", c.get("name"), json.dumps(c.get("input")))
        elif c.get("type") == "tool_result":
            print(
                "TOOL_RESULT is_error=%s" % c.get("is_error"),
                json.dumps(c.get("content")),
            )
        elif c.get("type") == "text":
            print("TEXT", c.get("text"))
