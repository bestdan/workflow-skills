---
created: 2026-11-13
status: accepted
convention: ../../skills/worktree-entry/SKILL.md
---

# The `EnterWorktree` `path` guard refuses only when a name route exists, and fails open

## Context

`scripts/guard-enter-by-path.py` refuses an `EnterWorktree` `path` call so the
agent takes the prompt-free `name` route instead
([`2026-11-13-refuse-enterworktree-path-rather-than-rewrite.md`](2026-11-13-refuse-enterworktree-path-rather-than-rewrite.md)).
Some worktrees have no `name` route: one at a path the `WorktreeCreate` hook
did not choose, one of another repo, or a hook-created worktree switched to
another branch. For those, the `path` form and its prompt are the only way
in. The hook cannot tell an attended session from a Remote Control one, so it
cannot refuse only when nobody is there to approve.

The name route has one cost. Entering by `path` never runs `WorktreeCreate`,
so the owner stamp is untouched. Entering by `name` from a session other than
the creator rewrites the stamp to `shared`, so neither session's
`ExitWorktree` removes the worktree and it needs one manual
`scripts/worktree-remove.sh`.

## Decision

The hook denies only when `scripts/worktree-name-for.sh` exits 0 with a name.
With no name it returns no decision, and the harness prompts as it always has.
Any failure also returns no decision: malformed stdin, a payload that is not
an object, a non-string `path`, a missing, crashing, non-executable or exit-2
resolver, stdout that is not a valid name, or an unreadable worktree config.
Landed in [#1021](https://github.com/bestdan/workflow-skills/pull/1021).

## Consequences

- Good, because every call the hook blocks has a prompt-free way in, so the
  hook never strands a session that `path` would have served.
- Good, because a hook bug degrades to the behaviour before the hook existed,
  not to a refusal of every `path` call.
- Bad, because a re-entry by `name` from another session leaves a `shared`
  worktree behind for a manual removal. A leftover worktree costs one command
  later; a stopped or denied session costs the run.
- Bad, because a broken resolver goes unnoticed: the only symptom is the
  prompt coming back.

## Revisit when

- The `PreToolUse` input gains a field that says whether a person can answer
  a prompt, so refusing in other cases becomes safe.
- The create hook learns to hand back a worktree on any branch. More
  worktrees would then have a name route, and the hook would refuse more
  `path` calls with no change of its own.
- Shared leftovers from cross-session re-entry pile up enough to matter.

## Confirmation

`test/guard-enter-by-path.bats` covers the no-name cases (unprefixed branch,
outside the layout, not a worktree, a `name` call, another tool) and each
fail-open case above. A stub-resolver control test proves the hook does run
the resolver, so the stub-based fail-open tests cannot pass on a hook that
skips it.

## Alternatives

- **Refuse every `path` call:** blocks the worktrees that have no other way
  in.
- **Fail closed:** turns a hook bug into a refusal of every `path` call,
  including those with no other route.
