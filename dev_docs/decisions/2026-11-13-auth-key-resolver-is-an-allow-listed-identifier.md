---
created: 2026-11-13
status: accepted
convention: ../auth_key_access.md
---

# A credential's resolver is an allow-listed identifier, never a command

## Context

`workflow-skills` ships to other people, so no one operator's secret tooling may be
baked in. Before [PR #310](https://github.com/bestdan/workflow-skills/pull/310) every
key-consuming Linear asset ran `subprocess.run(["op", "read", ref])` itself, and
`linear.api_key_ref` was documented as an `op://` reference. Operators use plain `op`, a
1Password service account, `opx` (a wrapper that forces an approval dialog per read),
`pass`, Vault, or an exported variable; only the first two worked.

The pressure was to let the operator say which program unlocks the reference. The
obvious way to say it is to let the reference value carry the command, and one
operator's local config already did: an `api_key_ref` prefixed with `opx `, which ran as
`op read "opx op://…"`, failed, and floored every Linear fast path to MCP unnoticed.

The merged config includes the **committed** `.task-config.yml`, so whatever the config
can name, a cloned repo can name too.

## Decision

A credential has a third name beside the secret and the pointer: a **resolver**, set by
`$<NAME>_RESOLVER` or `<service>.<credential>_resolver`, whose value is an identifier
looked up in an allow-list in `commands/handlers/assets/_secret_resolve.py` (`RESOLVERS`:
`op` → `["op", "read", ref]`, `opx` → `["opx", ref]`). The default is `op`. The list is
extended in code, with a test, and never by config. An identifier not on the list fails
as `unknown-resolver` rather than falling back to `op`. A backend without an entry is
reached by injecting the resolved value into the environment (`op run`, `opx run`, an
export), which is rung 1 of the secret ladder. Arbitrary command execution never becomes
a config feature, and `shell=True` is never used.

## Consequences

- Good, because a cloned repo whose config says `curl … | sh` cannot get it executed:
  the worst a hostile value can do is name `op` or `opx`.
- Good, because redaction stays possible. A pointer is a `<scheme>://…` value that
  reduces to `op://<vault>/…`; an arbitrary command string has nothing well-formed to
  reduce, and its error path would leak the command.
- Good, because the pointer field is always data, so it can be parsed and audited one
  way.
- Bad, because supporting `pass` or Vault natively needs a code change and a release;
  until then its users wrap the invocation.

## Revisit when

- A backend cannot meet the entry contract: take the reference as its final argument,
  write exactly one value to stdout, exit non-zero when it cannot.
- Enough operators wrap invocations for one backend that an entry is cheaper than the
  wrapping.

## Confirmation

`scripts/test_secret_resolve.py` covers both allow-listed resolvers, the
`unknown-resolver` failure, and the `opx op://…` value failing as `malformed-ref`. It
stubs the resolver binaries; no real `op` runs.

## Alternatives

- **The ref value doubles as a command** (`api_key_ref: opx op://…`, split with `shlex`
  or run in a shell): rejected because the committed config would execute attacker argv
  the first time a fast path fires, redaction collapses, and a field that is sometimes
  data and sometimes a program is a permanent parsing problem.
- **Caller-injected environment only**, dropping config resolution: kept as rung 1 of
  the secret ladder, but not the only mechanism, because interactive operators will not
  wrap every invocation and the fast path would floor for everyone else.
