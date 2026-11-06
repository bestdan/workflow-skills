---
created: 2026-11-06
status: accepted
---

# The co-review override is the owner's say-so plus the admin bypass

## Context

`main`'s ruleset (id 17035293) requires a `co-review` commit status. Agents post
that status with `scripts/co-review-status.py`, using the owner's own `gh`
login. The owner wanted a per-repo escape hatch: a way to merge an easy PR that
has no `co-review` success, without running a full co-review round
([#1429](https://github.com/bestdan/workflow-skills/issues/1429)).

The first proposal was an override posted by an identity agents can't use: a
GitHub App, or a second account. That only helps if using the identity proves
the owner is present. Agents run unsandboxed as the owner's Unix user, so any
credential the owner can use on that machine without a fresh approval, agents
can read too. Every candidate therefore traded reach for strength:

- **GitHub App:** the key sits behind 1Password biometrics. It's strong, but
  works only from the Mac.
- **Second account:** signed in on the owner's phone and browser, it approves
  the PR, and a workflow turns that approval into the status. It works
  anywhere, but needs a second account and a workflow that an agent's PR
  could edit.
- **The admin bypass already on the ruleset:** no setup, but GitHub can't tell
  the owner's bypass from an agent's.

Two facts made the third option cheap:

- **The bypass is already live.** The ruleset's bypass list gives
  `RepositoryRole` 5 (admin) `bypass_mode: always`.
- **The co-review check is already honor-system.** Any session holding the
  owner's login can post `co-review: success` without a review. An
  identity-gated override would guard a door next to one that stays open.

The owner chose the admin bypass on 2026-11-06.

## Decision

### Who may override

The owner, by saying so in a session. The words that count are an explicit
instruction to merge a named PR without co-review, e.g. "merge #1500 without
co-review". The agent then runs `dli gh merge <repo> <pr> <head-sha>` as it
does for any merge.

That call is the REST `PUT /pulls/{n}/merge`, and GitHub honors the admin
bypass on it. #1428 (head `66d4ac7`) merged through `dli gh merge` on
2026-11-06 with no `co-review` status, and GitHub logged it as a ruleset
bypass. Before the merge, the agent states the head SHA it read, so the owner
can see what lands. An agent never overrides on its own judgment. The same
rule that keeps it from running `dli gh merge` without the owner's OK covers
this, and nothing new enforces it.

### What the override looks like on GitHub

There is no override record. The evidence is the merged PR whose head SHA
carries no `co-review` success.

- The head is pinned by the `head-sha` argument of `dli gh merge`. That is the
  head the agent read and stated when it started the merge, not one the owner
  named.
- A later push voids it. `dotfiles/scripts/gh_write.py` sends that SHA as the
  merge call's `sha` parameter, which GitHub refuses once the head has moved.
- Ruleset bypass events (`/rulesets/rule-suites?rule_suite_result=bypass`)
  aren't the record. The release workflow's direct pushes to `main` log them
  too: on 2026-11-06 they were 7 of the 10. The merge-audit check on the PR's
  head reads only PRs.

### How a repo opts in

A repo opts in through its ruleset's bypass list. A repo with no bypass entry
for the owner has no override, and its PRs wait for co-review. Nothing in
`.github/merge-policy.yml` changes.

### How the tools tell an override from a real pass

- **`scripts/land-pr.py`: no change.** It never sees an override. The lander
  lands only on `co-review: success`, and an override is a merge the owner ran.
- **`scripts/merge-audit.py`: will count a merged PR with no `co-review` success
  on its head as `bypassed`, once #1432 lands.** It won't count it as
  human-merged or would-land.
  This is the one consumer that has to tell the two apart, so the shadow tally
  isn't skewed. That change is
  [#1432](https://github.com/bestdan/workflow-skills/issues/1432).
- **The `dli gh merge` guard: no change.** It reads no status today and needs
  none: GitHub applies the bypass, and the guard's head-SHA pin is what binds
  the override to one commit.

## One-time setup

None for this repo: the admin bypass is already on ruleset 17035293. To give
another repo the override, add `RepositoryRole` admin with
`bypass_mode: always` to its ruleset's bypass list.

## Consequences

- An agent that ignores the rule can merge past co-review. That was already
  true, since it could post the status itself. This decision only declines to
  build a guard that wouldn't close the gap.
- [#1431](https://github.com/bestdan/workflow-skills/issues/1431) (an override
  identity and a `dli gh override` command) is not needed and is closed.

## Revisit when

- **Agents stop running as the owner's login.** Then an App-posted status would
  be the override, and the ruleset could pin `co-review` to an
  `integration_id`.
- **The co-review status stops being honor-system.** Then the admin bypass would
  be the one open door, and it should be narrowed.
