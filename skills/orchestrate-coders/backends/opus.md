# opus backend — native Claude subagent

The special case: no external CLI. Packets are dispatched with the **Agent
tool**, so this backend works everywhere Claude Code runs and needs no
PATH probe, login, or permission rule.

## Dispatch

One Agent call per packet:

- `subagent_type: general-purpose` (full tool access — the coder must edit
  files and run checks).
- `model:` the pinned model — `claude-opus-4-8` for plain `opus`, or whatever
  the `opus:<model>` suffix named. Any Anthropic alias or full model ID the
  session's `availableModels` allows is valid here.
- `isolation: "worktree"` whenever more than one packet is in flight, or the
  user's working tree is dirty. A single packet against a clean tree may run
  in place — but then nothing else (including the orchestrator) edits files
  until it returns.
- Independent packets go out **in one message** so they run concurrently.

## Packet prompt

The subagent gets a fresh context — it has seen none of the conversation. The
prompt must be the complete brief:

```
You are a focused implementation agent working for an orchestrator.

## Task
<packet goal>

## Scope
<files/areas in scope; anything explicitly out of scope>

## Constraints
<the CLAUDE.md/AGENTS.md rules that apply to this packet — the coder
cannot see the orchestrator's conversation, so restate them>

## Verify
Run: <exact verification command>. It must pass before you report success.

## Report
Return raw data for the orchestrator, not prose for a human: files changed,
verification output (verbatim on failure), and any decision you had to make
that the spec left open.
```

## Notes

- The subagent's final message returns to the orchestrator as the tool
  result — harvest the diff from its worktree branch, not from its prose.
- Retry protocol (SKILL.md step 5) uses **SendMessage to the same agent ID**
  with the failure output — it still has its packet context; don't respawn.
- `CLAUDE_CODE_SUBAGENT_MODEL`, if set in the environment, overrides the
  `model:` parameter for every subagent. If packets seem to run on the wrong
  model, check for it and tell the user rather than fighting it.
