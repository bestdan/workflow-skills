---
created: 2026-08-01
status: accepted
convention: ../auth_key_access.md
---

# A credential's resolver is honoured only from the environment or the local config

## Context

The resolver names the program that runs on the operator's machine. The merged config
includes the committed `.task-config.yml`, which arrives with any clone. If the
committed file could set the resolver, a checkout would choose a program to run, even
from an allow-list.

A value in the wrong place that is silently ignored fails in the same shape as absence,
which is the failure class this contract exists to remove.

## Decision

`*_resolver` is honoured only from `$<NAME>_RESOLVER` or the gitignored
`.task-config.local.yml`. One found in the committed config is reported loudly and not
used. It is the only config key read from the raw local leaf rather than the merged
view.

As designed, enforcement lived only in the bridging agent, since the helper received
environment variables and could not see their origin. Since
[PR #1428](https://github.com/bestdan/workflow-skills/pull/1428) the helper also enforces
it for the config rung: `resolve_key()` reads the resolver from the local file only, and
prints `<section>.<credential>_resolver belongs in .task-config.local.yml, not the
committed config — ignoring it` to stderr when the committed file sets one. The agent
rule still applies, because an env resolver's origin remains invisible to the helper.
`/doctor` warns on a committed `api_key_resolver` (Check 6, Provenance). The rule landed
in [PR #310](https://github.com/bestdan/workflow-skills/pull/310) and the doctor check in
[PR #311](https://github.com/bestdan/workflow-skills/pull/311).

## Consequences

- Good, because an untrusted checkout cannot choose the program that runs.
- Bad, because the guarantee is narrower than it sounds, and the convention says so:
  anyone who can set `$<NAME>_RESOLVER` can already run commands, and with `op` as the
  default a committed pointer still resolves on a machine with a live `op` session.
- Bad, because the cloud boundary is not enforced here. A full-account key stays out of
  a cloud sandbox because cloud launches receive no local config, no `*_REF`, no
  `*_RESOLVER` and no raw secret. That is a property of delivery, which no resolver rule
  can replace.

## Revisit when

- The committed config stops being merged into what assets read, which would remove the
  threat this answers.
- A resolver gains a reason to be per-repo rather than per-machine.

## Confirmation

`test_committed_resolver_is_reported_and_ignored` in `scripts/test_secret_resolve.py`,
and the Provenance check in `commands/doctor.md`. The agent-side rule for env values
rests on `commands/handlers/linear-common.md` alone.

## Alternatives

- **Silently ignore a committed resolver:** rejected because a value in the wrong place
  would then look like absence.
- **Honour it from the merged view like every other key:** rejected because the checkout
  would choose the program.
