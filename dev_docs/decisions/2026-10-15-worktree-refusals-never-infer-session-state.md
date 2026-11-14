---
created: 2026-10-15
status: accepted
convention: ../../skills/worktree-entry/SKILL.md
---

# Worktree refusals give the whole route and leave the exit step to the agent

## Context

Two hooks refuse with a route to a worktree's `name`:
`scripts/guard-enter-by-path.py` on an `EnterWorktree` `path` call, and
`scripts/guard-foreign-worktree.py` on a write into a worktree the session has
not entered. `EnterWorktree` with `name` is refused while the session is
already in a worktree, so in that case the route starts with `ExitWorktree`
(`keep`).

Neither hook can see whether the harness considers the session to be in a
worktree. The hooks reference (code.claude.com/docs/en/hooks, checked
2026-10-14) documents no `PreToolUse` input field for that state. The nearest
is `cwd`, which follows the session into a worktree but does not say how it
got there, so a cwd inside a linked worktree is not the same predicate.

The costs are lopsided. A missing exit gets `name` refused, and an agent that
then falls back to `path` meets the prompt or the path guard. Naming
`ExitWorktree` unconditionally looked free, on the belief that it was a silent
no-op outside a worktree. The
[#1001 probe](../research/2026-10-14-enterworktree-deny-probe/README.md) found
that it is a no-op that still returns `is_error: true`, which reads as a
failure in a transcript.

## Decision

Both refusals name every call in the route, and neither hook evaluates
whether the session is in a worktree. The exit step is conditional in the
prose: call `ExitWorktree` with `action: keep` first only if the session is
already in a worktree, otherwise skip it, then call `EnterWorktree` with
`name: <n>`. The agent, which knows whether it entered a worktree, decides.
Landed for the path guard in
[#1021](https://github.com/bestdan/workflow-skills/pull/1021) (issue
[#989](https://github.com/bestdan/workflow-skills/issues/989)), and the write
guard adopted the same wording in
[#1026](https://github.com/bestdan/workflow-skills/pull/1026).

## Consequences

- Good, because the hooks carry no heuristic that can be wrong about the
  session's state.
- Good, because an agent outside a worktree is not told to make a call that
  reports an error.
- Bad, because the route depends on the agent knowing its own state. An agent
  that wrongly skips the exit gets `name` refused and must retry.

## Revisit when

- The hook input gains a field for the session's worktree state, so a hook can
  name only the calls that apply.
- `ExitWorktree` outside a worktree stops reporting `is_error`, which would
  make an unconditional exit step clean again.

## Confirmation

`test/guard-enter-by-path.bats` ("the exit step is conditional on already
being in a worktree") and the name-route cases in
`test/guard-foreign-worktree.bats`.

## Alternatives

- **Name `ExitWorktree` unconditionally:** rejected once the probe showed the
  no-op reports an error.
- **Infer the state from a linked-worktree cwd or the owner stamp:** cheap,
  since the write guard already tests the cwd, but not the predicate the
  harness uses to refuse `name`.
