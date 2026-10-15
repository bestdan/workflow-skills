#!/usr/bin/env python3
"""PreToolUse guard: refuse ``EnterWorktree`` ``path`` when ``name`` would do.

``EnterWorktree`` with ``path`` outside ``<repo>/.claude/worktrees/`` always
raises an approval prompt. No allow rule lifts it, and neither does a
``PreToolUse`` ``allow``. Under ``claude -p`` the prompt comes back as a
permission denial, and in a Remote Control session nobody can answer it. Yet a
worktree that sits where ``worktree-create-hook.sh`` would put it is handed
back with no prompt by ``EnterWorktree`` with ``name: <n>``. So a ``path`` call
on such a worktree is refused, with the route in the reason, and the agent
gets a refusal it can act on instead of a prompt it cannot.

Whether a path has a name is ``worktree-name-for.sh``'s answer, never this
file's: co-review and the write guard ask it too, and three copies would drift.

THE ROUTE IS CONDITIONAL, IN THE PROSE. ``name`` is refused while the session
is already in a worktree, so the agent must ``ExitWorktree`` (``keep``) first
in that case. Outside a worktree, ``ExitWorktree`` is a no-op that still comes
back flagged ``is_error`` (measured in #1001), so the reason tells the agent to
exit only when it is in one. The hook itself does not decide which case
applies: no ``PreToolUse`` input field says whether the harness considers the
session to be in a worktree, and a cwd inside a linked worktree is not the same
predicate. The agent knows; the hook does not.

REFUSE ONLY WITH A NAME. With no name, the path form is the only way in, so the
hook stays silent and the harness prompts as it always has.

FAIL OPEN. A crash, a missing or failing resolver, malformed stdin, or an
unreadable config all produce no decision. Failing closed would turn a hook bug
into a refusal of every ``path`` call, including those with no other route.

Design: ``dev_docs/designs/2026-10-14-reenter-worktrees-by-name.md`` item 3.

Reads the hook payload as JSON on stdin; emits a PreToolUse decision on stdout.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

RESOLVER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "worktree-name-for.sh"
)

# The resolver only ever prints a name scripts/lib/worktree-name.sh accepts.
# This is not a second copy of that rule deciding anything; it only keeps an
# unexpected stdout (a stray line, a stub) out of the reason text.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _reason(path: str, name: str) -> str:
    return (
        f"`{path}` is the worktree that `EnterWorktree` with `name: {name}` "
        "hands back, with no approval prompt. Entering by `path` raises a "
        "prompt that no allow rule lifts, and an unattended or Remote Control "
        "session cannot answer it. If this session is already in a worktree, "
        "call `ExitWorktree` with `action: keep` first; otherwise skip that "
        "step, because outside a worktree it is a no-op that reports an error. "
        f"Then call `EnterWorktree` with `name: {name}`."
    )


def _name_for(path: str, cwd: str) -> str | None:
    proc = subprocess.run(
        [RESOLVER, path], cwd=cwd, capture_output=True, text=True, timeout=10
    )
    if proc.returncode != 0:
        return None
    name = proc.stdout.strip()
    if not NAME_RE.match(name):
        return None
    return name


def main() -> None:
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict) or data.get("tool_name") != "EnterWorktree":
            return
        tool_input = data.get("tool_input") or {}
        path = tool_input.get("path")
        if not isinstance(path, str) or not path:
            return
        cwd = data.get("cwd") or os.getcwd()
        resolved = os.path.join(cwd, os.path.expanduser(path))
        name = _name_for(resolved, cwd)
        if name is None:
            return
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": _reason(path, name),
                    }
                }
            )
        )
    except Exception:
        return  # fail open: no decision, the harness prompts as before


if __name__ == "__main__":
    main()
