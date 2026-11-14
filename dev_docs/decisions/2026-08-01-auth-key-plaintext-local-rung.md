---
created: 2026-08-01
status: accepted
convention: ../auth_key_access.md
---

# A raw key is accepted in `.task-config.local.yml` as rung 0

## Context

The pointer-and-resolver contract assumes the operator runs a secret manager. Some
operators hold a single key and do not want one. Without a supported place for a raw
value, they export it from a shell profile, which reaches every process of every login
session.

The local config is gitignored robustly (`.gitignore` ignores `dev_docs/tasks/*` and
negates only the committed config), but it sits inside the repo tree, where every agent
session, editor index and folder backup can read it. Bridging a config value into the
environment also writes it into the command the agent runs, and so into the transcript.

The rung was added at the owner's request during review of
[PR #310](https://github.com/bestdan/workflow-skills/pull/310).

## Decision

`<service>.<credential>` in `.task-config.local.yml` is a supported rung 0 of the secret
ladder, ahead of `$<NAME>`. The agent reads it from the raw local leaf and bridges it
into `$<NAME>`; `_secret_resolve.py` never reads it. In the committed `.task-config.yml`
it is refused with a loud error, never merged and never used. It is never echoed in a
diagnostic. The exposure it carries is documented where it is configured rather than
prevented, because it is the operator's call about their own token.

## Consequences

- Good, because an operator with one key and no secret manager has a supported shape
  narrower than a profile export.
- Bad, because a plaintext full-account token sits in the repo tree, and lands in the
  transcript and briefly in `ps` wherever the agent bridges it.
- Bad, because the committed-config refusal rests on the agent and on `/doctor`'s
  Provenance warning; the helper cannot see a bridged value's origin.

## Revisit when

- A plaintext key is found committed, or found in a transcript that was shared.
- The operator key file shape (see
  [`2026-09-20-typesafe-key-out-of-tree.md`](2026-09-20-typesafe-key-out-of-tree.md))
  is read by `_secret_resolve.py`, which would give the same convenience without the
  repo-tree exposure.

## Confirmation

`/doctor` Check 6 warns on an `api_key` in the committed config and recommends rotating
it. Nothing tests the agent's bridge.

## Alternatives

- **No raw rung; a secret manager is required:** rejected at the owner's request,
  because it pushes keyless-manager operators to a profile export, the broadest shape.
