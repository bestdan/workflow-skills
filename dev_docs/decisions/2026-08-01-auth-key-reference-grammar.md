---
created: 2026-08-01
status: accepted
convention: ../auth_key_access.md
---

# A secret reference must start with a scheme, and may contain spaces

## Context

The founding bug was an `api_key_ref` of the form `opx op://…`, which resolved as
`op read "opx op://…"` and failed in the same shape as a host with no key. Nothing
checked the value's shape before handing it to the resolver.

The obvious check, rejecting any whitespace, would reject valid values: 1Password item
titles routinely contain spaces, and the canonical example in `linear-config.md`,
`op://Private/Linear API/credential`, carries one.

## Decision

A reference must match `^[a-z][a-z0-9+.-]*://` at position 0, with no leading or
trailing whitespace and no newline. Whitespace inside it is legal. A value failing the
rule fails as `malformed-ref`, distinct from `unconfigured`. `_validate_ref()` in
`commands/handlers/assets/_secret_resolve.py` implements it, landed in
[PR #310](https://github.com/bestdan/workflow-skills/pull/310); the check runs before
any resolver is chosen.

## Consequences

- Good, because a command-prefixed value is caught before anything runs, with a message
  that says what is wrong.
- Good, because spaced 1Password item titles keep working.
- Bad, because the rule checks only the prefix; a reference with a valid scheme and a
  wrong path still reaches the resolver and fails as `not-found`.

## Revisit when

- A supported resolver needs a reference that is not a `<scheme>://…` URI.

## Confirmation

`test_ref_with_spaces_is_valid`, `test_malformed_ref_opx_prefix` and
`test_malformed_ref_leading_whitespace` in `scripts/test_secret_resolve.py`.

## Alternatives

- **Reject all whitespace:** rejected because it rejects the configuration the contract
  was written to make work.
