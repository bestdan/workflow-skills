#!/usr/bin/env python3
"""PostToolUse reminder: teardown ends when ``ExitWorktree`` returns.

``ExitWorktree`` removes the session's own worktree. Nothing about that call
is a reason to look at the repo's other worktrees — they are normal parallel
work, and any listing of them in the transcript is ambient output a prior
command happened to print, not a finding to act on. Left unstated, an agent
that just tore its own worktree down has been seen turning that ambient state
into a to-do list or an offer to clean up work that is not its own.

Fires only on ``ExitWorktree`` rather than living as always-loaded prose in a
skill body, because the rule only matters at the one moment this tool returns.
The matcher in ``hooks/hooks.json`` narrows the dispatch to that tool; the
``tool_name`` check below is the guard that does not depend on it, and is what
the suite exercises.

Reads the hook payload as JSON on stdin; emits a PostToolUse
``additionalContext`` on stdout.

The ``additionalContext`` channel is the contested part. The hooks
documentation lists it among the supported ``hookSpecificOutput`` fields for
``PostToolUse``, and that is what this ships on. A second reading of the same
page held that ``PostToolUse`` supports only ``systemMessage`` and
``terminalSequence``, and that the way to reach the model after a tool has run
is exit code 2 with the text on stderr. Neither reading was testable when this
was written in bestdan/dotfiles: the hook was inert until that repo's sync
copied it into the live settings, so it could not be exercised in the session
that added it. Shipped here as a plugin hook it is live on install, and
bestdan/dotfiles#882 measures the real events.

If a teardown produces no reminder, that is the answer, and the fix is to write
MESSAGE to stderr and exit 2 instead of printing this JSON. Do not conclude the
matcher is wrong before trying that -- an accepted event name with a discarded
field looks exactly like a hook that never fired.
"""

import json
import sys

# Phrased around the tool return, not the outcome: the ownership gates in
# worktree-remove-hook.sh can keep a worktree while the harness still reports
# it removed, so this hook — which knows only that ExitWorktree returned — is in
# no position to certify a teardown. The prohibition clause is the part that
# does the work, and is what the suite asserts.
MESSAGE = (
    "`ExitWorktree` has returned. Report its result and stop — don't enumerate, "
    "query, or offer to clean up the repo's other worktrees."
)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed payload: don't interfere with normal flow
    if not isinstance(data, dict) or data.get("tool_name") != "ExitWorktree":
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": MESSAGE,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
