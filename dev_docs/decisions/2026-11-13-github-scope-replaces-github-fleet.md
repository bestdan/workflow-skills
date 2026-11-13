---
created: 2026-11-13
status: accepted
convention: ../task-dashboard.md
---

# `github.scope` replaces the GitHub half of `github.fleet`

## Context

`github.fleet` turned on `github_fleet.py`'s wide read: up to
`MAX_OPEN_ISSUES` (900) open issues per configured owner, the review queue,
every PR someone else opens on the owners' repos, design docs and recently
closed PRs. That is the right scope for repos the owner owns. For a work org
it would mirror other teams' backlogs and every colleague's PR. The same
switch also meant "this is a fleet", which is about collecting from other
hosts and has nothing to do with read width.

## Decision

How much the instance reads from GitHub is `github.scope`. `"owner"` is the
wide read on the configured owners' repos; `"me"` reads only what involves
the owner: PRs authored by or asking for the owner's review, issues assigned
to or opened by the owner, and, in the "closed today" count, only the owner's
merged PRs and closed assigned issues. The default is `"me"`, so an unset key
never mirrors a whole org, as `github.fleet` defaulting to off did.
`sync.fleet_enabled` now means `scope = "owner"`, and its callers keep their
meaning. Collecting from other hosts stays with `ingest.machines`. A config
that still sets `github.fleet`, true or false, is refused at load with an
error naming `github.scope = "owner"` and `ingest.machines`. The review-queue
rules (co-review stamp, approved milestone) apply in both scopes.

Landed in PR [#1234](https://github.com/bestdan/workflow-skills/pull/1234).

## Consequences

- Good, because a work instance can rank the owner's GitHub work without
  mirroring the org.
- Good, because the setting's name says what it controls.
- Bad, because every config that set `github.fleet` needed a hand edit; with
  two installs, both the owner's, that cost less than an alias path.

## Revisit when

- A third scope is needed, such as a team's repos within an org.
- The plugin gains installs the owner does not control, where a refused key
  costs more than an alias would.

## Confirmation

`scripts/test_task_dashboard_sync.py` refuses a config setting
`github.fleet` either way (`test_github_fleet_is_refused_true_or_false`).
`test_me_scope_reads_only_what_involves_me` in
`scripts/test_task_dashboard_fleet_gh.py` covers the `me` read.

## Alternatives

- **Turn the wide read on for every instance:** rejected, because on the
  work org it would mirror thousands of issues that are not the owner's.
- **Alias `github.fleet` to `github.scope`:** rejected, because it would
  keep the two meanings of "fleet" tangled.
