---
created: 2026-08-01
status: accepted
convention: ../auth_key_access.md
---

# The resolver call is bounded at 120 seconds, one value for every resolver

## Context

Before [PR #310](https://github.com/bestdan/workflow-skills/pull/310) no asset bounded
`op read` at all; only the HTTP call had a timeout, of 15 seconds. A headless `op read`
on a machine with no session could hang the tool call.

The bound has to clear a human. `opx` deliberately blocks on a native approval dialog,
and `op read` blocks on desktop-app approval, so a timeout in the usual few-second range
would kill a resolve mid-approval.

## Decision

`_run_resolver()` passes `timeout=TIMEOUT` (120 seconds) to `subprocess.run` for every
resolver, and a timeout fails as `timeout`.

The design reasoned that 120 seconds "sits under the Bash tool's own default", so a hung
read would surface as a clean `timeout` rather than a hung tool call. The Bash tool's
default timeout is also 120 seconds, so the two are equal rather than one under the
other: a resolve that runs the full 120 seconds can lose the race to the tool's own
timeout. The value shipped as designed.

## Consequences

- Good, because an approval dialog has two minutes, enough for a human at the machine.
- Good, because a hung resolver now ends as a category instead of an unbounded wait.
- Bad, because a resolve that takes the whole bound can be killed by the Bash tool
  first, which reports a tool timeout instead of the `timeout` category.

## Revisit when

- A resolve is seen killed by the Bash tool's timeout rather than ending as `timeout`.
  Lowering `TIMEOUT` a little below the tool default, or having callers pass a longer
  Bash timeout, would fix it.
- A resolver needs a different bound from the others.

## Confirmation

`test_timeout` in `scripts/test_secret_resolve.py`, with a stubbed resolver.

## Alternatives

- **A few-second timeout:** rejected because it kills a resolve mid-approval.
- **A per-resolver timeout:** not taken; no resolver has needed a different bound.
