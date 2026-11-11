---
created: 2026-11-06
status: accepted
---

# The co-review override is the owner's admin bypass

## Context

`main`'s ruleset (id 17035293) requires a `co-review` commit status. Agents post
that status with `scripts/co-review-status.py`. The owner wanted a per-repo
escape hatch: a way to merge an easy PR that has no `co-review` success, without
running a full co-review round
([#1429](https://github.com/bestdan/workflow-skills/issues/1429)).

An override only means something if it comes from an identity agents can't use.
When this record was first drafted, agents and the owner shared the `bestdan`
login, so no such identity existed without new machinery: a GitHub App whose key
sits behind 1Password (Mac only), or a second account approving from the phone.
The draft took the ruleset's admin bypass anyway, accepting that GitHub couldn't
tell the owner's bypass from an agent's, and was held until the identity
question was settled.

It was settled by
[bestdan/dotfiles#1143](https://github.com/bestdan/dotfiles/pull/1143). Agents
now act on `bestdan` repos as `bestdan-bot`, a Write collaborator with no admin
role and no bypass. The owner's `bestdan` login stays the admin, reached through
the browser or one Duo-approved command at a time with `dli as-owner`
(dotfiles `dev_docs/decisions/2026-11-07-agents-act-as-a-non-admin-bot.md`).
That makes the admin bypass the identity agents can't use, with no setup in this
repo: the ruleset's bypass list grants it to admins, and the bot is not one.

## Decision

### Who may override

The owner, as `bestdan`, in one of two ways:

- **The browser:** the merge box's bypass checkbox on the PR.
- **A terminal on lindev:**
  `dli as-owner bestdan/<repo> pr merge <n> --squash --admin --match-head-commit <sha>`.
  It sends a Duo push that names the command, and runs it with the owner's
  token only once the owner approves.

An agent never runs either. When the owner says to merge a named PR without
co-review, the agent reads the PR's head SHA and hands the owner that command
with the SHA filled in. The agent's own `dli gh merge` runs as the bot, and
GitHub refuses it a merge past the required check (dotfiles local test 9,
2026-11-07).

### What the override looks like on GitHub

There is no override record. The evidence is a merged PR whose head SHA carries
no `co-review` success, plus the ruleset's bypass event on its merge commit,
which names the actor.

- **One head SHA:** `--match-head-commit` makes GitHub refuse the merge once
  the head has moved, so a later push voids the override. The browser merges
  the head the owner is looking at.
- **Attributable:** the bot holds no bypass, so a `bypass` rule-suite event on
  a PR's merge commit means the owner's credential was used. Release pushes to
  `main` also log bypass events, so the audit matches events to merge commits
  rather than counting them.

### How a repo opts in

A repo opts in through its ruleset's bypass list. A repo with no admin bypass
entry has no override, and its PRs wait for co-review. To give another repo the
override, add `RepositoryRole` admin with `bypass_mode: always` to its ruleset,
and never list the bot. Nothing in `.github/merge-policy.yml` changes.

### How the tools tell an override from a real pass

- **`scripts/land-pr.py`: no change.** It never sees an override. The lander
  lands only on `co-review: success`, and an override is a merge the owner ran.
- **The `dli gh merge` guard: no change.** As the bot it cannot bypass, so a
  merge without `co-review` success fails at GitHub.
- **`scripts/merge-audit.py`: follow-up,
  [#1432](https://github.com/bestdan/workflow-skills/issues/1432).**
  `--mode review` already lists every merged PR whose head lacks `co-review`
  success, and `--mode shadow` never counts one as would-land. But it labels a
  PR `bypass` from the rule-suites API, which needs admin: as the bot, that
  call returns 404 (measured 2026-11-10), so the audit now reports such PRs
  `unknown` rather than `bypass`. #1432 owns reading the rule suites with the
  owner's credential and splitting `bypass` by actor, so a bypass by `bestdan`
  reads as an owner override and any other actor raises an alarm.

## Consequences

- Good, because an agent can no longer merge without a `co-review` success
  status: GitHub refuses the bot a bypass.
- Good, because an override costs the owner one approved command, and no new
  identity, workflow or `dli gh override` command is built.
  [#1431](https://github.com/bestdan/workflow-skills/issues/1431) is closed as
  not needed.
- Bad, because the `co-review` status is still self-attested: the bot can post
  `co-review: success` without a review. This decision closes the bypass, not
  that.
- Bad, because until #1432 lands, the audit can't show an override as one.

## Revisit when

- **GitHub lets a ruleset require a status from a source the bot can't post
  as.** Then self-attestation closes too, and the override is the one door left.
- **Agents regain admin, or the bot is added to a bypass list.** Then a bypass
  no longer proves the owner, and the identity question reopens.
