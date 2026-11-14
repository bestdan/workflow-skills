---
created: 2026-08-01
status: accepted
convention: ../auth_key_access.md
---

# The pointer and the resolver resolve down separate ladders, and a failed resolve never falls through

## Context

A credential can be named in several places: the raw secret in `$<NAME>`, a pointer in
`$<NAME>_REF` or the config, and a resolver in `$<NAME>_RESOLVER` or the local config.
The two values come from different owners. A pointer says _which secret_ and belongs to
the checkout or the launching environment. A resolver says _how this machine unlocks
secrets_ and belongs to the machine.

Pairing them per layer (an env pointer takes the env resolver, a config pointer takes the
config resolver) breaks on an ordinary setup: a `$LINEAR_API_KEY_REF` inherited from a
terminal export suppresses the `.task-config.local.yml` resolver, and resolution drops to
plain `op read`. That read either hangs waiting on an approval that never comes or
bypasses the approval flow the operator installed `opx` to get.

The bug that prompted the work was a misconfigured pointer failing in the same shape as
a host with no key at all, so every failure looked like absence.

## Decision

Resolve the secret and pointer down one ladder (`<service>.<credential>` from
`.task-config.local.yml`, which the agent bridges into `$<NAME>` and the helper does not
read, see
[`2026-08-01-auth-key-plaintext-local-rung.md`](2026-08-01-auth-key-plaintext-local-rung.md);
then `$<NAME>`, then `$<NAME>_REF`, then `<service>.<credential>_ref` from the merged
config, then `unconfigured`) and the
resolver down another (`$<NAME>_RESOLVER`, then `<service>.<credential>_resolver` from
`.task-config.local.yml`, then `op`). First hit wins on each, and any pointer source
composes with any resolver source. A resolve that fails at a pointer rung raises its
category and stops; it never tries the next rung. The ladders landed in
[PR #310](https://github.com/bestdan/workflow-skills/pull/310), with the agent bridging
the config rungs into `$<NAME>_REF` and `$<NAME>_RESOLVER`. Since
[PR #1428](https://github.com/bestdan/workflow-skills/pull/1428), `resolve_key()` in
`commands/handlers/assets/_secret_resolve.py` reads the config rungs itself (see
[`2026-11-06-auth-key-helper-reads-config-rungs.md`](2026-11-06-auth-key-helper-reads-config-rungs.md)).

## Consequences

- Good, because an inherited `$<NAME>_REF` no longer changes which program runs.
- Good, because a misconfigured pointer surfaces as its own category instead of
  flooring as if keyless.
- Good, because the fast-path gate is unchanged: for the three read fast paths
  (`linear-ready.py`, `linear-scan.py`, `linear-relations.py`), any non-zero exit still
  means "fast path unavailable" and the verb floors to MCP, with no
  `[ -n "$LINEAR_API_KEY" ]` pre-check. `linear-archive.py` and
  `linear-false-closures.py` have no MCP floor and treat the exit as fatal.
- Good, because the unattended path is unchanged: a raw `$LINEAR_API_KEY` wins outright,
  and `$OP_SERVICE_ACCOUNT_TOKEN` plus a pointer works under the default `op` resolver.
- Bad, because a correct lower rung is ignored when a higher one is configured wrongly;
  the operator must fix the higher one rather than rely on a fallback.

## Revisit when

- A consumer needs a resolver tied to a particular pointer, for example two credentials
  on one machine held in different secret managers. The per-credential `<NAME>` prefix
  covers that today; a need within one credential would reopen pairing.

## Confirmation

`scripts/test_secret_resolve.py`: `test_no_fallthrough_on_failed_resolve`,
`test_a_malformed_config_ref_does_not_fall_through`, `test_env_ref_beats_config_ref`,
`test_local_resolver_applies_to_an_env_ref` and `test_env_resolver_beats_local_resolver`.

## Alternatives

- **Pair pointer and resolver per layer:** rejected for the inherited-export failure
  above.
- **Fall through to the next rung on a failed resolve:** rejected because it masks a
  misconfiguration as absence, which is the bug the contract exists to remove.
