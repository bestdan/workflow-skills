---
description: Find gh-issue claims a dead session stranded (assigned or not, on status:3_started, no open PR, no pushed commits, older than a threshold) and, with --apply, release them back to status:2_ready — dry-run by default, gh-issue only, composes with /loop
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(python3 *), Glob, Grep, Read
argument-hint: "[--apply] [--hours <n>]"
---

# Sweep Stale Claims

A gh-issue session that claims an issue and dies before opening a PR leaves the
issue on `status:3_started` and its lock ref `<branch_prefix>task-<n>` held. No
later session picks it up: the candidate query needs `status:2_ready` and no
assignee. The handler's **Bail** repairs a failure the session survives;
this command repairs one it didn't.

It is a thin wrapper. The verdicts, the release order and the exit codes belong
to `gh-issue-claim.py stale-claims`, documented in
`commands/handlers/gh-issue-claim.md` → **Sweep stale claims**. Do not restate
or re-derive them here.

## Arguments

- `--apply` — release each dead claim. Without it, the run is a dry run that
  writes nothing.
- `--hours <n>` — age past which a claim with no PR and no pushed commits is
  dead. Overrides `gh-issue.stale_claim_hours`; absent both, the script's
  default of 24 applies.

## Steps

1. **Resolve the handler** from the merged task config, exactly as
   `commands/task-config.md` → "Resolving the handler" describes. Anything but
   `handler: gh-issue` → stop with: "`/sweep-stale-claims` is gh-issue only.
   linear and repo-pr recover claims through their own flows, and jira claims
   on the assignee, which a human can clear." Write nothing.
2. **Read three keys** from the same merged view: `gh-issue.repo` (else
   `gh repo view --json nameWithOwner --jq .nameWithOwner`),
   `gh-issue.branch_prefix` (else empty) and `gh-issue.stale_claim_hours`
   (else omit `--hours`). Pass the prefix on every call: a dropped prefix
   names a different lock ref (`gh-issue-claim.md` → "Branch name").
3. **Run the sweep, unsandboxed** (it calls `gh`). `$CLAUDE_PLUGIN_ROOT` falls
   back to a Glob for `**/handlers/assets/gh-issue-claim.py`:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-claim.py" stale-claims \
     --repo <repo> --prefix "<branch_prefix>" [--hours <n>] [--apply]
   ```

4. **Report** its output verbatim, then one line from its exit code: `0`
   nothing stuck, `1` dead claims found (dry run) or `stranded-with-work`
   claims that need a human, `4` a `gh` call failed (no claim was judged on a
   failed read). After a dry run that found dead claims, name
   `/sweep-stale-claims --apply` as the next step. Never `--apply` a
   `stranded-with-work` claim by hand: the script refuses to, because its
   pushed commits are work another session would redo.

## Scheduling

Run it locally, on a `/loop` (`/loop 6h /sweep-stale-claims --apply`). A cloud
routine cannot delete refs through its proxy
(`dev_docs/research/2026-08-24-routine-claim-channel.md`), so `/schedule`
cannot release a ref-held claim.
