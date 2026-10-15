---
created: 2026-10-14
question: "Does a PreToolUse deny on EnterWorktree `path` reach the agent under `claude -p`, and does the refusal's route (ExitWorktree keep, then EnterWorktree name) work from a session outside any worktree?"
feeds: "https://github.com/bestdan/workflow-skills/issues/989"
---

# EnterWorktree `path` deny probe (2026-10-14)

**The deny reaches the agent and the route works, but criterion 2 fails on its
letter.** A stub `PreToolUse` hook that denies `EnterWorktree` `path` puts its
own reason text in front of the agent instead of the approval prompt.
`EnterWorktree` `name` then hands back the existing worktree on its branch.
The step between them, `ExitWorktree` (`keep`) from a session that is not in a
worktree, changes nothing and the agent carries on, but the tool reports it as
an error (`is_error: true`). #989's criterion 2 asks for "returns without
error", so the verdict depends on whether that means the result flag or the
session outcome. That reading is the owner's call, so this record states the
verdict as **inconclusive** rather than choosing one.

Four `claude -p` sessions (two of them the probe proper, B and B2), on Claude Code 2.1.300 with workflow-skills 2.79.2, permission mode
`auto`.

## Question

#989 proposes a hook that denies `EnterWorktree` `path` when a `name` route
exists. A `path` outside `.claude/worktrees/` always raises an approval prompt,
and a hook `allow` cannot lift it (verified 2026-09-29). What a hook `deny` does
there had not been tested. #1001 sets three pass criteria:

1. The agent receives the hook's own reason text. A bare permission denial
   doesn't count.
2. `ExitWorktree` (`keep`) returns without error when the session is not in a
   worktree.
3. `name` returns the existing worktree's path and branch, not a new
   `.claude/worktrees/<n>`.

## Method

Everything ran in a throwaway directory, `/tmp/claude/autopilot-2026-10-14/probe-1001/`.
The owner's settings files were not edited. The scripts are in
[`references/`](references/):

- [`setup.sh`](references/setup.sh) creates a one-commit repo `probe1001` and
  writes an **untracked** `.claude/settings.json` that registers the stub on
  `PreToolUse` with matcher `EnterWorktree`.
- [`stub-deny-enter-by-path.py`](references/stub-deny-enter-by-path.py) denies
  any `EnterWorktree` call that carries `path`, with the reason
  `PROBE-1001-STUB-DENY: this worktree has a name route. Call ExitWorktree with action keep, then EnterWorktree with name: probe.`
  It returns no decision for anything else, and logs every input it receives.
- [`run.sh`](references/run.sh) runs `claude -p` in the repo with
  `timeout 600`, `--max-turns 6`, `--output-format stream-json --verbose` and
  `--allowedTools "EnterWorktree,ExitWorktree"`.
- [`summarize.py`](references/summarize.py) prints a transcript's tool calls
  and results.

The phases:

| Phase                         | Session                                                                                                                                                        |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A — create                    | `EnterWorktree` `name: "probe"`, then `ExitWorktree` `keep`. The plugin's `WorktreeCreate` hook made `$HOME/src/worktrees/probe1001/probe` on `bestdan/probe`. |
| B — probe                     | A fresh session outside any worktree: `EnterWorktree` `path: <that worktree>`, then follow the refusal's route exactly.                                        |
| B2 — probe again, with marker | The same as B, after adding commit `44e4f5d` and an untracked `MARKER.txt` to the worktree, so a recreate would be visible.                                    |
| C — control                   | The same `path` call with `--setting-sources user`, so the stub is not loaded. This shows what the agent sees without the hook.                                |

The exact command line and prompt for each phase, the marker setup before B2,
and the worktree checks after each phase with their output are in
[`references/invocations.md`](references/invocations.md).

Each transcript is in [`references/transcripts/`](references/transcripts/),
trimmed of SessionStart hook output (machine-local guidance) and reduced to the
init, assistant, user and result events. The stub's own input log is
[`stub-hook-calls.jsonl`](references/transcripts/stub-hook-calls.jsonl).

## Results

**Criterion 1 holds.** In B and B2 the `path` call came back as:

```
is_error: true
PreToolUse:EnterWorktree hook error: PROBE-1001-STUB-DENY: this worktree has a name route. Call ExitWorktree with action keep, then EnterWorktree with name: probe.
```

The harness prefixes the stub's reason with `PreToolUse:EnterWorktree hook error:`
but passes it through whole. The result event still lists the call under
`permission_denials`, so a reader of that list alone cannot tell a hook deny
from an unanswered prompt; the tool result can. In control C the same call
returned the prompt's question instead:

```
Enter the worktree at "/home/dan/src/worktrees/probe1001/probe"? This moves the session's working directory and write access there, and loads project configuration (CLAUDE.md, settings) from that location.
```

The stub's log has no entry from C's session, so the difference is the hook.

**Criterion 2 fails on the flag and holds on the outcome.** In B and B2,
`ExitWorktree` `keep` returned:

```
is_error: true
<tool_use_error>No-op: there is no active EnterWorktree session to exit. This tool only operates on worktrees created by EnterWorktree in the current session — it will not touch worktrees created manually or in a previous session. No filesystem changes were made.</tool_use_error>
```

Both agents went straight on to the `name` call, and both sessions ended
`subtype: success`. Both agents also pointed out, without being asked, that the
route's first step does nothing from a fresh session.

**Criterion 3 holds.** `EnterWorktree` `name: "probe"` returned
`Created worktree at /home/dan/src/worktrees/probe1001/probe.` in B and B2.
After B2 the worktree was still on `bestdan/probe` at `44e4f5d`, `MARKER.txt`
was still there, and `probe1001/.claude/worktrees/` did not exist. The result
text says "Created" and does not name the branch, so the branch half of this
criterion comes from git state, not from what the agent was told.

## What this means for #989

- The mechanism #989 relies on works: a deny's reason reaches the agent under
  `claude -p`, which a hook `allow` could not achieve.
- The route as #989 specifies it ("`ExitWorktree` (`keep`), then
  `EnterWorktree` with `name: <n>`" every time) produces an error-flagged no-op
  from any session that is not already in a worktree. A careful agent carries
  on; one that treats `is_error` as a stop would not. Making the exit step
  conditional in the reason text ("if this session is already in a worktree,
  `ExitWorktree` `keep` first") would avoid it without the hook having to infer
  the session's state, which #989's design rules out.
- The `name` hand-back reads "Created worktree at …", the same as a fresh
  create, so an agent cannot tell from the result whether it got the existing
  worktree.

## Verdict

**Inconclusive.** Criteria 1 and 3 pass. Criterion 2 fails if "returns without
error" means the tool's `is_error` flag, and passes if it means the agent is not
derailed. #1001 prescribes opposite actions for pass and fail, so this record
leaves #989's body and state for the owner.
