---
created: 2026-11-06
status: accepted
convention: ../auth_key_access.md
---

# `_secret_resolve.py` reads the config pointer and resolver itself, not only through the agent's bridge

## Context

The design behind [PR #310](https://github.com/bestdan/workflow-skills/pull/310), `dev_docs/designs/2026-08-01-auth-key-resolution.md` (deleted on graduation), kept
the asset scripts env-in / JSON-out and parsing no YAML. The agent read
`linear.api_key_ref` and `linear.api_key_resolver` from the config and bridged them onto
the same Bash invocation as `$LINEAR_API_KEY_REF` and `$LINEAR_API_KEY_RESOLVER`.

That is how it shipped in PR #310, and it failed in the way the contract exists to
prevent. When the agent missed the bridge, or read a truncated config, the script saw no
key and the run floored to MCP as if the host were keyless
([issue #1426](https://github.com/bestdan/workflow-skills/issues/1426)). A missed bridge
is indistinguishable from absence. The gitignored `.task-config.local.yml` also exists
only in the main checkout, so a run from a linked worktree had nothing to read.

## Decision

`resolve_key()` reads the two config rungs itself, landed in
[PR #1428](https://github.com/bestdan/workflow-skills/pull/1428). The pointer comes from
the merged view of `dev_docs/tasks/.task-config.yml` overlaid by
`.task-config.local.yml`, local winning. The resolver comes from `.task-config.local.yml`
only. A linked worktree finds the local file in the main checkout through
`git rev-parse --git-common-dir`. A bridged env value still wins, so agent bridging stays
valid. The raw-secret rung (`<service>.<credential>` in the local file) is not read by
the helper and stays with the agent's bridge. The `unconfigured` message names every
source checked and whether each file was absent or lacked the key.

This departs from the design, which had the agent bridge both config rungs and the helper
parse no YAML.

## Consequences

- Good, because a configured pointer reaches every asset whether or not the agent
  bridged it, from the main checkout or any linked worktree.
- Good, because an `unconfigured` failure now says where it looked, so a keyless host
  and a missed file are told apart.
- Bad, because the assets now carry a stdlib YAML leaf reader (`read_leaf`: a top-level
  `section:`, then `key: value` at the block's indent). A config shape it misreads
  would resolve the wrong value or none.
- Bad, because the helper now reads files the agent may not have looked at, so the
  agent's account of where a key came from can be incomplete.

## Revisit when

- The plugin takes a YAML dependency in its assets, or the config moves to a format the
  leaf reader cannot parse.
- A config shape the leaf reader misreads is found in use.

## Confirmation

`scripts/test_secret_resolve.py` covers the main-checkout and linked-worktree reads,
local beating committed, env beating config, the unconfigured message, and the leaf
reader's quoting, comment and nesting cases.

## Alternatives

- **Keep the bridge as the only route and make the agent's instructions stricter:**
  rejected because a missed bridge stays silent, so a stricter instruction cannot be
  checked.
- **Have the helper also read the raw secret from the local file:** rejected so the
  helper never reads a plaintext key from config; that rung stays an explicit agent
  bridge.
