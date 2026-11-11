---
created: 2026-11-10
status: accepted
convention: ../releasing.md
---

# The merge audit labels a bypass by its actor

## Context

`protect-main` requires a `co-review` success, and its bypass actor is the
admin role. Until 2026-11-07 agents ran `gh` as `bestdan`, the owner and an
admin, so any agent merge could pass the check without anyone choosing to.
`scripts/merge-audit.py --mode review` reported such a merge as `bypass` and
could not say whose hand was on it.
[Issue #1429](https://github.com/bestdan/workflow-skills/issues/1429) asked for
that gap to be closed.

The fix was made on the identity side, in `bestdan/dotfiles`
(`dev_docs/designs/2026-11-06-agent-github-identity.md`, graduating to
`dev_docs/decisions/2026-11-07-*` and `dev_docs/github-identity.md` there).
Agents now act as `bestdan-bot`, a Write collaborator with no admin role. The
owner keeps admin, used from the browser or one approved command at a time. A
bypass is therefore something only the owner's credential can do, and an
audit line can say whether that is what happened.

Two facts, checked 2026-11-10, shaped how:

- A rule suite carries `actor_name`, the login that pushed
  ([REST docs](https://docs.github.com/en/rest/repos/rule-suites)).
- Listing rule suites needs Administration (read). The bot gets a 404, so a
  run as the bot reads every gap as `unknown`. A PR's `mergedBy` needs only
  read access, and the bot can see it.

## Decision

`--mode review` splits the old `bypass` label by actor. A bypass by the owner's
login is `owner`. A bypass by any other actor is `bypass`, which is now an
alarm. The owner is `--owner`, and defaults to the repo's owner segment. The
actor is the rule suite's `actor_name`, falling back to `mergedBy`. Every line
names the actor, including `ungated` and `unknown` lines, which use
`mergedBy`. When rule suites cannot be read, the audit says so once and names
the permission it lacks. `/doctor` gains Check 11, which warns when the active
`gh` account is an admin of a personal repo that requires `co-review`, the
setup that made the old `bypass` label ambiguous. A repo with no such check
gets a `PASS`: its owner is always an admin, and there is nothing to skip.

## Consequences

- Good, because a clean week reads as zero `bypass` lines. Any `owner` lines
  are the owner's own deliberate merges.
- Good, because a run as the bot still names who merged each gap, even with
  no rule suites.
- Bad, because the split needs the owner's credential to run. As the bot, every
  gap is `unknown`, so the weekly run belongs to the owner.
- Bad, because `owner` trusts the identity split. If an agent is logged in as
  the owner again, its bypasses read as `owner`. Check 11 is what catches that.

## Revisit when

- Agents act as the owner again, or the bot gains the admin role. The labels
  would then go back to saying nothing about intent.
- GitHub exposes rule suites to Write collaborators. The run could then move
  back to the agents.
- The repo moves to an organization. Check 11 skips org repos, and `--owner`
  would need to name a person rather than the org.

## Confirmation

`scripts/test_merge_audit.py` covers the owner, other-actor, `--owner`,
`mergedBy` fallback and unreadable-rule-suite cases. Check 11 has no
automated test; it rests on `/doctor` being run.

## Alternatives

- Classify from `mergedBy` alone, with no rule suites: rejected, because
  `mergedBy` cannot tell a bypass from a merge the rules allowed.
- Raise the bot's role so it can read rule suites: rejected, because a
  collaborator gets Administration only with the admin role, and that is the
  role whose bypass this whole change is about.
