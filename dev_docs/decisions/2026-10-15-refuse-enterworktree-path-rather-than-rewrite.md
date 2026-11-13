---
created: 2026-10-15
status: accepted
convention: ../../skills/worktree-entry/SKILL.md
---

# Refuse an `EnterWorktree` `path` call that has a name; do not rewrite it

## Context

`EnterWorktree` with a `path` outside `.claude/worktrees/` raises an approval
prompt that neither an allow rule nor a `PreToolUse` hook answering `allow`
lifts. Under `claude -p` the prompt comes back as a permission denial, and in
a Remote Control session nobody can answer it. For a worktree that sits where
`scripts/worktree-create-hook.sh` would put it, `EnterWorktree` with `name`
hands the same worktree back with no prompt. Agents still took the `path`
form, because the plugin's own write guard and the guidance told them to.

A `PreToolUse` hook on `EnterWorktree` can either deny the call with a reason
or return `updatedInput` that swaps `path` for `name`. Two facts bore on the
choice:

- Whether the harness's permission check runs on rewritten input was not
  verified.
- `name` is refused while the session is already in a worktree, so a rewrite
  would fail there, and that case needs a refusal with a route anyway.

The [#1001 probe](../research/2026-10-14-enterworktree-deny-probe/README.md)
showed that a `deny` from a `PreToolUse` hook reaches the agent with the
hook's own reason text under `claude -p`, and that the agent can follow it to
the `name` route.

## Decision

`scripts/guard-enter-by-path.py`, registered on `EnterWorktree` in
`hooks/hooks.json`, denies a `path` call when `scripts/worktree-name-for.sh`
gives that path a name. The reason names the worktree's `name` verbatim and
the calls that reach it with no prompt. It never rewrites the call. Landed in
[#1021](https://github.com/bestdan/workflow-skills/pull/1021), closing
[#989](https://github.com/bestdan/workflow-skills/issues/989).

## Consequences

- Good, because an unattended or Remote Control session gets a refusal it can
  act on immediately, in place of a prompt it cannot answer.
- Good, because the same refusal works whether or not the session is already
  in a worktree; a rewrite would have needed a second path for that case.
- Bad, because the agent spends one refused call, and sometimes an
  `ExitWorktree` turn, that a working rewrite would save.

## Revisit when

- A `claude -p` probe shows the permission check runs on `updatedInput`, so a
  rewrite to `name` would succeed without a prompt, and the extra turn proves
  costly in practice.
- The harness stops prompting for a model-supplied `path`, which removes the
  reason for the hook.

## Confirmation

`test/guard-enter-by-path.bats`: a `path` with a name is denied with a reason
quoting `<n>` and both calls, and the hook is registered for `PreToolUse` on
`EnterWorktree`.

## Alternatives

- **Rewrite `path` to `name` with `updatedInput`:** unverified against the
  permission check, and useless inside a worktree, where `name` is refused.
- **Guidance only, no hook:** leaves every agent that has not read the
  guidance stopping at the prompt.
