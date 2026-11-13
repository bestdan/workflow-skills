---
created: 2026-11-13
status: accepted
convention: ../reviewer-quality-score-design.md
---

# The `SessionEnd` hook starts the ledger flush detached

## Context

Without a session-end flush, a machine's co-review rows reach the shared repo
only when someone runs `--push` or `/co-review-stats`. Claude Code kills a
`SessionEnd` hook after 1.5 seconds and discards its output, and a plugin cannot
raise that budget: only a `timeout` in the user's own settings or
`CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` can. A clone or push often takes
longer. Claude Code also waits on a hook whose background child keeps its
output open. Its docs do not say whether cancelling a hook kills the hook's
whole process group or session, so a detached child's survival was unverified.

## Decision

The plugin's `SessionEnd` hook runs
`co-review-ledger-sync.py --push --detach`, which forks the flush into a new
session (`os.setsid`, then a second fork, in Python because macOS has no
`setsid` command), points stdin, stdout and stderr away from the hook, and
returns at once. The receipt goes to `~/.claude/co-review/last-push.log`,
overwritten each time. If the detached flush turns out not to survive the
session's exit, the fallback is an `async: true` hook, which Claude Code
documents as running in the background with no timeout, provided `SessionEnd`
accepts it.

As shipped this differs from the design in one respect. The design said the
hook would ship only after a manual check on a real session passed.
[PR #1073](https://github.com/bestdan/workflow-skills/pull/1073) shipped it
with that check left to the owner, since a flush lost at exit costs only a
delay: the watermark is the pushed file, so the next flush sends the rows.

## Consequences

- Good, because the hook stays inside its budget whatever the network does,
  and rows reach the shared repo without anyone remembering to push.
- Good, because a killed flush loses nothing: every step's partial state is
  either rebuilt or reselected by the next flush.
- Bad, because survival rests on harness behaviour its docs do not promise,
  and a change there would silently turn the hook into a no-op.
- Bad, because the receipt is in a log file, not anywhere the session shows, so
  a failing flush is seen only by reading `last-push.log` or `/doctor`.

## Revisit when

- The manual check fails on macOS or Linux: switch to the `async: true` hook.
- Claude Code documents what it does to a cancelled hook's process group, or
  lets a plugin raise the `SessionEnd` budget.

## Confirmation

`test/co-review-ledger-sync.bats` checks that `--detach` returns within 1.5 s
against a hanging remote with empty stdout, that a detached push lands and
writes its receipt, and that `hooks/hooks.json` runs the exact command. No test
can show survival of a real session's exit. The manual check is: with
`CO_REVIEW_LEDGER_REMOTE` set, record a co-review run, exit the session, then
read `last-push.log` and the shared repo's latest commit.

That check has not been run as a controlled test, on 2026-11-13, on either
platform. One read-only observation that day on the owner's macOS machine is
consistent with survival: `last-push.log` held `ledger sync: pushed 2 runs`
stamped 11:48:14Z, and the shared repo's newest commit, that machine's
`+2 runs`, was 11:48:16Z. Only `--detach` writes that log, but nothing ties the
run to a particular session's exit.

## Alternatives

- **Ask the owner to raise the budget in their settings:** rejected. The
  budget lives in the user's settings, which the plugin cannot set.
- **Flush only by hand and before `/co-review-stats`:** rejected. That was the
  state before the hook, the one
  [issue #1068](https://github.com/bestdan/workflow-skills/issues/1068) set out
  to change.
